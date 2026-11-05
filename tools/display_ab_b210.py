"""A/B of the 1.6.1 display math against the 1.7 compiled chain on ONE live
B210, same samples, same instant: both engines hang off the same usrp_source.
Reports the noise-floor level, the DC region (peak above floor and its width
at +3/+6 dB), and the levels of any spurs, from each engine, at the user's
settings (Rick's 2026-11-04 view: 16 MS/s, gain 45, RX2, 1420.406 MHz,
65536-point Rectangular, dB averaging).

  PATH=.conda/Library/bin:$PATH UHD_IMAGES_DIR=... .conda/python.exe tools/display_ab_b210.py [--rate 16e6] [--gain 45] [--fft 65536] [--secs 10]
"""
import os, sys, time, argparse, threading
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ap = argparse.ArgumentParser()
ap.add_argument("--rate", type=float, default=16e6)
ap.add_argument("--gain", type=float, default=45.0)
ap.add_argument("--freq", type=float, default=1420.406e6)
ap.add_argument("--fft", type=int, default=65536)
ap.add_argument("--window", default="rectangular")
ap.add_argument("--secs", type=float, default=10.0)
ap.add_argument("--antenna", default="RX2")
args = ap.parse_args()
import numpy as np
from PySide6.QtCore import QCoreApplication
app = QCoreApplication([])
import dses_workbench as A
import dses_radio as R
from gnuradio import gr, blocks
from scipy import fft as scipy_fft

devs = R.find_b200_uhd()
assert devs, "no B210 found (is the app still holding it?)"
serial = devs[0]["serial"]
src = R.UhdB200Source(serial=serial, samp_rate=args.rate, center_freq=args.freq,
                      gain=args.gain, antenna=args.antenna)
n = args.fft
win = A.WINDOWS[args.window](n).astype(np.float64)
scale = float(np.sum(win)) ** 2
tb = gr.top_block()

# --- 1.7 engine: the app's own chain ---------------------------------------
chan = A.DisplayChannelizer(n, win, decim=A.display_decim_for(args.rate))
tb.connect(src.block, chan.head)
chan.connect_chain(tb)
proc = A.SpectrumProcessor(chan, window_name=args.window, update_hz=10.0,
                           power_avg=False, unit='dbfs')
proc._timer.stop(); proc.set_average_alpha(1.0); proc.set_dc_suppress(False)

# --- 1.6.1 engine: raw chunks + the numpy Welch tick (as shipped) -----------
CHUNK = 65536


class LegacySink(gr.sync_block):
    def __init__(self):
        gr.sync_block.__init__(self, name="legacy_sink", in_sig=[(np.complex64, CHUNK)], out_sig=None)
        self.lock = threading.Lock(); self.pending = []; self.dropped = 0

    def work(self, inp, out):
        with self.lock:
            for c in inp[0]:
                if len(self.pending) >= 96:
                    self.pending.pop(0); self.dropped += 1
                self.pending.append(np.array(c, dtype=np.complex64))
        return len(inp[0])

    def drain(self):
        with self.lock:
            p, self.pending = self.pending, []
            d, self.dropped = self.dropped, 0
        return p, d


lsink = LegacySink()
s2v = blocks.stream_to_vector(gr.sizeof_gr_complex, CHUNK)
keep = blocks.keep_one_in_n(gr.sizeof_gr_complex * CHUNK, max(1, int(round(args.rate / CHUNK / 400.0))))
tb.connect(src.block, s2v, keep, lsink)
win32 = win.astype(np.float32)
sec_per_block = 2e-8 * n


def legacy_tick(dc_suppress, chunks=None):
    global sec_per_block
    t0 = time.perf_counter()
    if chunks is None:
        chunks, dropped = lsink.drain()
    else:
        dropped = 0
    if not chunks:
        return None, 0, 0
    blk = np.concatenate(chunks).reshape(-1, n)
    budget = 0.1 * 0.4
    cap = max(1, int(budget / max(sec_per_block, 1e-9)))
    stride = -(-blk.shape[0] // cap)
    if stride > 1:
        blk = blk[::stride]
    if dc_suppress:
        blk = blk - blk.mean(axis=1, keepdims=True)
    spec = scipy_fft.fft(blk * win32, axis=1, workers=-1)
    pb = np.abs(spec) ** 2
    power = np.fft.fftshift(np.mean(pb, axis=0, dtype=np.float64)) / scale
    nblk = blk.shape[0]
    sec_per_block = 0.7 * sec_per_block + 0.3 * (time.perf_counter() - t0) / max(1, nblk)
    return 10.0 * np.log10(power + 1e-20), nblk, dropped


tb.start()
time.sleep(1.0)        # settle; also the UHD stream start
frames_new, frames_old, frames_old_dc, blocks_new, blocks_old = [], [], [], [], []
t_end = time.monotonic() + args.secs
while time.monotonic() < t_end:
    time.sleep(0.1)
    psum, pk, mn, c = chan.accumulator.take()
    if c:
        frames_new.append(10.0 * np.log10(psum / c / scale + 1e-20)); blocks_new.append(c)
    chunks, _ = lsink.drain()
    f, nb, dr = legacy_tick(dc_suppress=False, chunks=chunks)
    if f is not None:
        frames_old.append(f); blocks_old.append(nb)
        f2, _, _ = legacy_tick(dc_suppress=True, chunks=chunks)   # 1.6.1's per-block mean subtraction
        frames_old_dc.append(f2)
tb.stop(); tb.wait()
try:
    src.close()
except Exception:
    pass

freqs = args.freq + np.fft.fftshift(np.fft.fftfreq(n, 1.0 / args.rate))
off = -args.gain          # dBm (approx) = dBFS - gain + cal(0)


def report(tag, frames, blocks):
    F = np.array(frames)
    avg = F.mean(axis=0) + off                        # dB-domain average, like 'Power (RMS)' off
    c = n // 2
    far = np.r_[avg[c - 6000: c - 2000], avg[c + 2000: c + 6000]]
    floor = float(np.median(far))
    peak = float(avg[c - 50: c + 51].max())
    # width of the centre feature at +3 and +6 dB above the floor
    def width(th):
        above = avg[c - 3000: c + 3001] > floor + th
        idx = np.flatnonzero(above)
        if idx.size == 0:
            return 0.0
        # contiguous run containing the centre
        k = 3000
        lo = k
        while lo - 1 >= 0 and above[lo - 1]:
            lo -= 1
        hi = k
        while hi + 1 < above.size and above[hi + 1]:
            hi += 1
        return (hi - lo + 1) * args.rate / n / 1e3
    spurs = []
    for f_mhz in (1416.6, 1420.9, 1424.0):
        i = int(np.argmin(np.abs(freqs - f_mhz * 1e6)))
        seg = avg[i - 400: i + 400]
        spurs.append((freqs[i - 400 + int(seg.argmax())] / 1e6, float(seg.max()) - floor))
    print(f"{tag}: {len(frames)} frames, {np.mean(blocks):.0f} blocks/frame "
          f"({np.mean(blocks) * n / (args.rate * 0.1) * 100:.0f}% coverage); "
          f"floor {floor:.2f} dBm; per-bin sigma {np.std(far):.2f} dB")
    print(f"      centre: peak {peak - floor:+.1f} dB above floor, width {width(3):.1f} kHz at +3 dB, "
          f"{width(6):.1f} kHz at +6 dB, {width(10):.1f} kHz at +10 dB")
    print("      spurs: " + ", ".join(f"{f:.4f} MHz {d:+.1f} dB" for f, d in spurs))
    return avg


a_new = report("1.7 chain ", frames_new, blocks_new)
a_old = report("1.6.1 math", frames_old, blocks_old)
d = a_new - a_old
print(f"difference new-old: median {np.median(d):+.3f} dB, p5..p95 {np.percentile(d,5):+.2f}..{np.percentile(d,95):+.2f} dB, "
      f"centre bin {d[n//2]:+.2f} dB")

# --- DC suppress, each engine's own method on the same samples -------------
print("\nDC suppress ON:")
report("1.6.1 mean-sub", frames_old_dc, blocks_old)
mean_lin = np.mean(np.power(10.0, np.array(frames_new) / 10.0), axis=0)
bridged = 10.0 * np.log10(proc._bridge_dc(mean_lin) + 1e-20)
report("1.7 bridge    ", [bridged], [np.mean(blocks_new)])
