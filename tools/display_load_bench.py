"""Live-display load bench: what does the spectrum display actually deliver on a
loaded host? (Written 2026-11-04 from Ray's "doesn't seem to work in real time".)

A synthetic flowgraph (live white noise + a +6%, 60 kHz bump standing in for
the hydrogen line, 0.253 dB) feeds the display path, optionally beside an
EzraTxtSink recording (4096 bins x 31000 frames, the app default), with the
process pinned to N cores and optional competing busy threads. It reports
tick timing, the FFT blocks the display integrated per tick (coverage of the
stream), the displayed trace's per-bin noise sigma (dB) against the
radiometer expectation, and the bump height / sigma = how visible a 6% HI
line is on screen.

Engines:
  app     the real 1.7 display chain: DisplayChannelizer (compiled FFT in the
          flowgraph) + SpectrumProcessor, exactly as the application wires it.
  legacy  the 1.1.7-1.6.x design, kept here verbatim for comparison: a Python
          sink queues raw sample chunks and the GUI tick Welch-averages them in
          numpy under a per-tick CPU budget (cut to a third while recording).
  none    the source chain alone (CPU baseline to subtract).
("cpp" and "python" are accepted as aliases of app and legacy.)

Run from the repo with the env's DLLs on PATH:
  PATH=.conda/Library/bin:$PATH .conda/python.exe tools/display_load_bench.py \\
      --fft 4096 --cores 1 --busy 1 --record [--engine legacy]
Results of the 2026-11-04 matrix (dev box + the Haswell Pi 5): ROADMAP.md,
"Constant-statistics display".
"""
import os, sys, time, argparse, ctypes, threading
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
ap = argparse.ArgumentParser()
ap.add_argument("--cores", type=int, default=0)
ap.add_argument("--record", action="store_true")
ap.add_argument("--rate", type=float, default=8e6)
ap.add_argument("--secs", type=float, default=20)
ap.add_argument("--fft", type=int, default=1024)
ap.add_argument("--hz", type=float, default=10.0)
ap.add_argument("--alpha", type=float, default=0.1)
ap.add_argument("--engine", default="app",
                choices=["none", "app", "legacy", "cpp", "python"])
ap.add_argument("--src", default="noise", choices=["noise", "vector"],
                help="vector = repeating 1M-sample buffer (cheap source for keep-up "
                     "tests; sigma is then meaningless)")
ap.add_argument("--rec-engine", default="app", choices=["app", "legacy"],
                help="--record through the 1.7 EzraChain (app) or the 1.6.x EzraTxtSink (legacy)")
ap.add_argument("--busy", type=int, default=0,
                help="spawn N busy-loop threads (GIL-free) as competing load")
args = ap.parse_args()
args.engine = {"cpp": "app", "python": "legacy"}.get(args.engine, args.engine)
if args.cores:
    if sys.platform == "win32":
        mask = (1 << args.cores) - 1
        import ctypes.wintypes as w
        k32 = ctypes.windll.kernel32
        k32.SetProcessAffinityMask.argtypes = [w.HANDLE, ctypes.c_size_t]
        k32.SetProcessAffinityMask.restype = w.BOOL
        k32.GetCurrentProcess.restype = w.HANDLE
        ok = bool(k32.SetProcessAffinityMask(k32.GetCurrentProcess(), mask))
    else:
        os.sched_setaffinity(0, range(args.cores)); ok = True
    print("affinity", args.cores, "cores ->", ok)

import numpy as np
from PySide6.QtCore import QCoreApplication, QTimer, QObject, Signal
app = QCoreApplication([])
import dses_workbench as A
from gnuradio import gr, blocks, analog
from gnuradio.filter import firdes
from scipy import fft as scipy_fft

fs = args.rate
n = args.fft
sigma = 0.05
tb = gr.top_block()
if args.src == "noise":
    white = analog.noise_source_c(analog.GR_GAUSSIAN, sigma, 1)
else:
    _rng = np.random.default_rng(7)
    _buf = (_rng.standard_normal(1 << 20) + 1j * _rng.standard_normal(1 << 20)).astype(np.complex64) * sigma
    white = blocks.vector_source_c(_buf.tolist(), True)
# The HI-like bump: band-limited noise 60 kHz wide at +0.5 MHz, precomputed
# into a repeating 1M-sample buffer (cheap). A live GR noise source + FFT
# filter here cost ~2 Pi 5 cores at 16 MS/s and masked the engine comparison.
_brng = np.random.default_rng(11)
_bn = (_brng.standard_normal(1 << 20) + 1j * _brng.standard_normal(1 << 20)).astype(np.complex64) * (sigma * np.sqrt(0.06))
taps = firdes.low_pass(1.0, fs, 60e3, 40e3)
_bf = np.convolve(_bn, np.asarray(taps, dtype=np.complex64), mode="same").astype(np.complex64)
_bf *= np.exp(2j * np.pi * 0.5e6 / fs * np.arange(_bf.size)).astype(np.complex64)
bump_src = blocks.vector_source_c(_bf.tolist(), True)
add = blocks.add_cc()
thr = blocks.throttle(gr.sizeof_gr_complex, fs, True)
tb.connect(white, (add, 0))
tb.connect(bump_src, (add, 1))
tb.connect(add, thr)

rec = None
if args.record:
    import ezra_txt
    _rec_kw = dict(fft_bins=4096, integ_frames=31000, samp_rate=fs,
                   center_freq_mhz=1422.0, lat_deg=38.8, lon_deg=-104.7, amsl=1800,
                   site_name="bench", az_deg=180, el_deg=5, keep_fraction=0.8)
    _rec_path = os.path.join(os.environ.get("TEMP", "."), "display_load_bench_rec.txt")
    if args.rec_engine == "legacy":
        rec = ezra_txt.EzraTxtSink(_rec_path, **_rec_kw)
        tb.connect(thr, rec)
    else:
        rec = ezra_txt.EzraChain(_rec_path, **_rec_kw)
        rec.connect(tb, thr)

ticks = []       # (t0, duration, blocks_used, stride, dropped)
frames = []      # (t, avg_db)


# ---------------------------------------------------------------------------
# legacy engine: the pre-1.7 design, verbatim (SampleBufferSink + Welch tick)
# ---------------------------------------------------------------------------
CHUNK_SIZE = 65536
MAX_PENDING_CHUNKS = 96


class LegacySampleBufferSink(gr.sync_block):
    def __init__(self, chunk_size=CHUNK_SIZE):
        gr.sync_block.__init__(self, name="sample_buffer_sink",
                               in_sig=[(np.complex64, chunk_size)], out_sig=None)
        self._lock = threading.Lock()
        self._pending = []
        self._dropped = 0

    def work(self, input_items, output_items):
        chunks = input_items[0]
        with self._lock:
            for c in chunks:
                if len(self._pending) >= MAX_PENDING_CHUNKS:
                    self._pending.pop(0); self._dropped += 1
                self._pending.append(np.array(c, dtype=np.complex64))
        return len(chunks)

    def drain(self):
        with self._lock:
            p, self._pending = self._pending, []
            d, self._dropped = self._dropped, 0
        return p, d


class LegacyWelch(QObject):
    """The 1.1.7-1.6.x SpectrumProcessor._tick, trimmed to the averaging path:
    per-tick CPU budget (0.4 of the interval, 0.12 while recording), learned
    per-block cost, stride shedding, numpy Welch average, EMA."""
    frame_ready = Signal(object, object, object)

    def __init__(self, sink, fft_size, interval_ms, alpha, recording):
        super().__init__()
        self._sink = sink; self._n = fft_size; self._interval = interval_ms
        self._alpha = alpha
        self._budget = 0.12 if recording else 0.4
        self._sec_per_block = 2e-8 * fft_size
        self._window32 = A.WINDOWS["blackman-harris"](fft_size).astype(np.float32)
        wsum = float(np.sum(self._window32)); self._scale = wsum * wsum
        self._avg = None
        self.last_blocks = 0; self.stride = 1; self.dropped = 0

    def tick(self):
        n = self._n
        t0 = time.perf_counter()
        chunks, dropped = self._sink.drain()
        self.dropped = dropped
        if not chunks:
            return
        blk = np.concatenate(chunks).reshape(-1, n)
        budget = self._interval * 1e-3 * self._budget
        cap = max(1, int(budget / max(self._sec_per_block, 1e-9)))
        stride = -(-blk.shape[0] // cap)
        if stride > 1:
            blk = blk[::stride]
        self.stride = stride
        blk = blk - blk.mean(axis=1, keepdims=True)          # DC suppress
        spec = scipy_fft.fft(blk * self._window32, axis=1, workers=-1)
        pb = np.abs(spec) ** 2
        power = np.fft.fftshift(np.mean(pb, axis=0, dtype=np.float64)) / self._scale
        nblk = blk.shape[0]
        self.last_blocks = nblk
        el = time.perf_counter() - t0
        self._sec_per_block = 0.7 * self._sec_per_block + 0.3 * el / max(1, nblk)
        a = self._alpha
        self._avg = power if self._avg is None else a * power + (1 - a) * self._avg
        self.frame_ready.emit(10.0 * np.log10(self._avg + 1e-20), None, None)


interval_ms = max(20, int(1000.0 / args.hz))
timer = QTimer()
timer.setInterval(interval_ms)

if args.engine == "none":
    tb.connect(thr, blocks.null_sink(gr.sizeof_gr_complex))
elif args.engine == "legacy":
    sink = LegacySampleBufferSink()
    s2v = blocks.stream_to_vector(gr.sizeof_gr_complex, CHUNK_SIZE)
    keep = blocks.keep_one_in_n(gr.sizeof_gr_complex * CHUNK_SIZE,
                                max(1, int(round(fs / CHUNK_SIZE / 400.0))))
    tb.connect(thr, s2v, keep, sink)
    eng = LegacyWelch(sink, n, interval_ms, args.alpha, args.record)

    def legacy_tick():
        t0 = time.perf_counter()
        eng.tick()
        ticks.append((t0, time.perf_counter() - t0, eng.last_blocks, eng.stride, eng.dropped))
    timer.timeout.connect(legacy_tick)
    eng.frame_ready.connect(lambda avg, mx, mn: frames.append((time.perf_counter(), np.asarray(avg).copy())))
else:
    chan = A.DisplayChannelizer(n, A.WINDOWS["blackman-harris"](n), decim=A.display_decim_for(fs))
    tb.connect(thr, chan.head)
    chan.connect_chain(tb)
    proc = A.SpectrumProcessor(chan, window_name="blackman-harris", update_hz=args.hz,
                               power_avg=True, unit='dbfs')
    proc._timer.stop()
    proc.set_average_alpha(args.alpha)
    proc.set_recording_active(args.record)

    def app_tick():
        t0 = time.perf_counter()
        proc._tick()
        ticks.append((t0, time.perf_counter() - t0, proc._last_blocks_used, chan.decim, 0))
    timer.timeout.connect(app_tick)
    proc.frame_ready.connect(lambda avg, mx, mn: frames.append((time.perf_counter(), np.asarray(avg).copy())))

busy = []
if args.busy:
    def spin():
        x = np.random.standard_normal(4096).astype(np.complex64)
        while True:
            np.fft.fft(x)
    for _ in range(args.busy):
        th = threading.Thread(target=spin, daemon=True); th.start(); busy.append(th)

cpu0 = time.process_time(); t_start = time.perf_counter()
tb.start()
timer.start()
QTimer.singleShot(int(args.secs * 1000), app.quit)
app.exec()
timer.stop()
tb.stop(); tb.wait()
cpu_pct = (time.process_time() - cpu0) / (time.perf_counter() - t_start) * 100
if rec is not None:
    rec.close()

# ---- analysis ----
hdr = (f"=== engine={args.engine} src={args.src} rec={args.rec_engine if args.record else "-"} cpu={cpu_pct:.0f}% cores={args.cores or 'all'} "
       f"record={args.record} busy={args.busy} rate={fs/1e6:g} MS/s fft={n} {args.hz:g} Hz alpha={args.alpha}")
if args.engine == "none":
    print("\n" + hdr + "  (source chain only)")
    sys.exit(0)
tk = np.array([(t, d, b, s, dr) for (t, d, b, s, dr) in ticks if t - t_start > 3.0])
interval = np.diff(tk[:, 0])
dur, blocks_used, stride, dropped = tk[:, 1], tk[:, 2], tk[:, 3], tk[:, 4]
full_blocks_per_tick = fs * np.median(interval) / n
coverage = blocks_used * n / (fs * np.median(interval))
freqs = np.fft.fftshift(np.fft.fftfreq(n, 1 / fs))
flat = (np.abs(freqs) > 0.15 * fs) & (np.abs(freqs) < 0.4 * fs) & (np.abs(freqs - 0.5e6) > 0.2e6)
bump = np.abs(freqs - 0.5e6) < 40e3
settled = [f for (t, f) in frames if t - t_start > 8.0]
F = np.array(settled)
sig_db = float(np.mean(np.std(F[:, flat], axis=0)))
ref = (np.abs(freqs - 0.5e6) > 80e3) & (np.abs(freqs - 0.5e6) < 300e3)
bump_db = np.mean(F[:, bump], axis=1) - np.mean(F[:, ref], axis=1)
jag = float(np.mean(np.std(np.diff(F[:, flat], axis=0), axis=0)))
n_eff_ema = (2 - args.alpha) / args.alpha
theory_sigma_full = 10 / np.log(10) / np.sqrt(full_blocks_per_tick * n_eff_ema)
print("\n" + hdr)
print(f"ticks: {len(tk)}  interval med {np.median(interval)*1e3:.0f} ms  max {interval.max()*1e3:.0f} ms"
      f"  tick dur med {np.median(dur)*1e3:.1f} ms  p95 {np.percentile(dur,95)*1e3:.1f} ms  max {dur.max()*1e3:.1f} ms")
print(f"blocks/tick med {np.median(blocks_used):.0f} of {full_blocks_per_tick:.0f} possible -> coverage med "
      f"{np.median(coverage)*100:.1f}%  min {coverage.min()*100:.1f}%  stride/decim med {np.median(stride):.0f}"
      f"  dropped chunks total {dropped.sum():.0f}")
print(f"display noise sigma (flat, dB): {sig_db:.3f}   theory at full coverage: {theory_sigma_full:.3f}"
      f"   frame-to-frame jag: {jag:.3f} dB")
print(f"bump: {np.mean(bump_db):.3f} dB, bump/sigma = {np.mean(bump_db)/max(sig_db,1e-9):.1f}")
if rec is not None:
    print(f"recorder: rows {rec.nrows} dropped_samples {rec.dropped_samples}")
