"""Headless verification of the display chain (1.7: compiled FFT in the
flowgraph; before that, the v1.1.7 numpy Welch path this test was written for).

Builds the REAL DisplayChannelizer (stream_to_vector -> keep_one_in_n ->
fft_vcc -> complex_to_mag_squared -> SpectrumAccumulator) in a small
top_block fed by a repeating vector source, drives SpectrumProcessor._tick by
hand, and checks:
  1. dBFS calibration + bin orientation: a full-scale tone at +fs/8 reads
     ~0 dBFS in bin n/2 + n/8 (fft_vcc's shift matches np.fft.fftshift).
  2. Sensitivity: the tick frame's noise-floor sigma shrinks ~1/sqrt(Nblocks)
     — every block the flowgraph delivered is in the average.
  3. flush() (retune barrier): the first non-empty take after it is blanked.
  4. DC suppress bridges the window's main lobe at the centre bin.
  5. Peak-hold detector: per-block extremes reach the max trace.
  6. FFT-size swap: a second chain replaces the first under lock/unlock on a
     running flowgraph and the processor follows it.
  7. set_recording_active is a no-op (no budget cut while recording).

Run: PATH=.conda/Library/bin:$PATH .conda/python.exe test_display_sensitivity.py
"""
import os, sys, time
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from PySide6.QtCore import QCoreApplication

app = QCoreApplication([])

import dses_workbench as A
from gnuradio import gr, blocks

rng = np.random.default_rng(42)
N = 1024
FS = 8e6
BUF = 1 << 18


def run_chain(samples, fft_size=N, window="blackman-harris", seconds=0.6,
              decim=1, extremes=False, processor=None):
    """Feed `samples` (repeating) through a fresh DisplayChannelizer for
    `seconds`; return (processor, channelizer, top_block-stopped)."""
    chan = A.DisplayChannelizer(fft_size, A.WINDOWS[window](fft_size), decim=decim)
    if processor is None:
        proc = A.SpectrumProcessor(chan, window_name=window, update_hz=10.0,
                                   power_avg=True, unit='dbfs')
        proc._timer.stop()           # drive _tick by hand
        proc.set_average_alpha(1.0)  # no EMA: inspect single-tick frames
    else:
        proc = processor
        proc.attach(chan)
    chan.accumulator.set_track_extremes(extremes)
    tb = gr.top_block()
    src = blocks.vector_source_c(samples.astype(np.complex64).tolist(), True)
    thr = blocks.throttle(gr.sizeof_gr_complex, FS, True)
    tb.connect(src, thr, chan.head)
    chan.connect_chain(tb)
    tb.start()
    time.sleep(seconds)
    tb.stop(); tb.wait()
    return proc, chan


captured = []


def grab(avg, mx, mn):
    captured.append((np.asarray(avg), None if mx is None else np.asarray(mx)))


# --- 1. calibration + orientation: full-scale complex tone at +fs/8 -------
t = np.arange(BUF, dtype=np.float64)
tone = np.exp(2j * np.pi * (N // 8) / N * t)          # bin-centred for every FFT size that divides BUF
proc, chan = run_chain(tone)
proc.set_dc_suppress(False)
proc.frame_ready.connect(grab)
proc._tick()
frame = captured[-1][0]
peak_db = frame.max()
peak_bin = int(frame.argmax())
assert abs(peak_db) < 0.1, f"calibration broke: peak {peak_db:+.3f} dBFS (want ~0)"
assert peak_bin == N // 2 + N // 8, f"bin orientation: peak in bin {peak_bin}, want {N//2 + N//8}"
nblk1 = proc._last_blocks_used
assert nblk1 > 100, f"only {nblk1} blocks reached the accumulator in 0.6 s at 8 MS/s"
print(f"1. calibration OK: full-scale tone -> {peak_db:+.4f} dBFS in bin {peak_bin} "
      f"(fftshift order), {nblk1} blocks integrated")

# --- 2. sensitivity: per-bin sigma of a noise frame vs 1/sqrt(Nblocks) -----
captured.clear()
noise = (rng.standard_normal(BUF) + 1j * rng.standard_normal(BUF)) * 0.01
proc, chan = run_chain(noise)
proc.set_dc_suppress(False)
proc.frame_ready.connect(grab)
proc._tick()
welch = captured[-1][0]
nblk = proc._last_blocks_used
# The repeating buffer holds BUF/N distinct blocks; blocks beyond that
# repeat, so the sigma floor is set by the distinct count, not nblk.
distinct = min(nblk, BUF // N)
sigma_db = float(np.std(welch[N // 8: 7 * N // 8]))
expect = 10 / np.log(10) / np.sqrt(distinct)
assert sigma_db < 2.0 * expect, (f"Welch sigma {sigma_db:.3f} dB vs expected ~{expect:.3f} "
                                  f"({distinct} distinct blocks): blocks are not all in the average")
assert sigma_db > 0.3 * expect, f"sigma {sigma_db:.3f} dB suspiciously small"
print(f"2. sensitivity OK: {nblk} blocks/tick ({distinct} distinct) -> floor sigma "
      f"{sigma_db:.3f} dB (single block would be {10/np.log(10):.2f} dB)")

# --- 3. flush(): first non-empty take blanked ------------------------------
acc = chan.accumulator
acc._sum[:] = 1.0; acc._count = 5
acc.flush()
s, mx, mn, c = acc.take()
assert c == 0, "flush must empty the accumulator"
acc._sum[:] = 1.0; acc._count = 5          # 'in-flight' blocks arriving after the flush
s, mx, mn, c = acc.take()
assert c == 0, "the first non-empty take after flush must be blanked"
acc._sum[:] = 2.0; acc._count = 7
s, mx, mn, c = acc.take()
assert c == 7 and s[0] == 2.0, "the take after the blank must pass data"
captured.clear()
proc.reset_averaging()                     # flush via the processor API
proc._tick()                               # empty accumulator -> no frame
assert not captured, "a tick with nothing accumulated must not emit"
print("3. flush/blank OK: retune barrier discards in-flight blocks; empty tick keeps the last frame")

# --- 4. DC suppress bridges the main lobe at the centre bin -----------------
dc = np.full(BUF, 0.5 + 0j) + (rng.standard_normal(BUF) + 1j * rng.standard_normal(BUF)) * 0.001
proc, chan = run_chain(dc)
proc.frame_ready.connect(grab)
captured.clear(); proc.set_dc_suppress(False); proc._tick()
raw = captured[-1][0]
# The flowgraph is stopped, so a second tick would find nothing: apply the
# bridge to the captured frame's linear power the way _tick does.
bridged = 10.0 * np.log10(proc._bridge_dc(np.power(10.0, raw / 10.0)) + 1e-20)
c = N // 2
assert raw[c] > raw[c - 20] + 40, "test setup: DC spike not present"
assert bridged[c] < raw[c - 20] + 3, f"DC not suppressed: centre bin {bridged[c]:.1f} vs floor {raw[c-20]:.1f} dB"
assert abs(bridged[c - 20] - raw[c - 20]) < 1e-9, "DC bridge touched bins outside the main lobe"
print(f"4. DC suppress OK: centre bin {raw[c]:+.1f} -> {bridged[c]:+.1f} dBFS, neighbours untouched")

# --- 5. peak-hold detector sees per-block extremes --------------------------
burst = noise.copy()
burst[: 4 * N] += 0.3 * np.exp(2j * np.pi * (N // 4) / N * np.arange(4 * N))   # 4 blocks of a strong tone
proc, chan = run_chain(burst, extremes=True)
proc.set_dc_suppress(False)
proc.set_max_hold(True)
proc.set_hold_detector('peak')
assert chan.accumulator._extremes, "peak detector must switch the accumulator's extremes on"
proc.frame_ready.connect(grab)
captured.clear(); proc._tick()
avg, mx = captured[-1]
b = N // 2 + N // 4
assert mx is not None and mx[b] > avg[b] + 10, (f"max hold did not catch the 4-block burst: "
                                                f"max {mx[b]:.1f} vs mean {avg[b]:.1f} dB")
proc.set_hold_detector('average')
assert not chan.accumulator._extremes, "'average' detector must switch the extremes off"
print(f"5. peak detector OK: burst in 4 of {proc._last_blocks_used} blocks -> max {mx[b]:.1f} dB vs mean {avg[b]:.1f} dB")

# --- 6. FFT-size swap under lock/unlock on a running flowgraph --------------
chan1 = A.DisplayChannelizer(1024, A.WINDOWS["hann"](1024))
proc = A.SpectrumProcessor(chan1, window_name="hann", update_hz=10.0, power_avg=True, unit='dbfs')
proc._timer.stop(); proc.set_average_alpha(1.0); proc.set_dc_suppress(False)
proc.frame_ready.connect(grab)
tb = gr.top_block()
src = blocks.vector_source_c(tone.astype(np.complex64).tolist(), True)
thr = blocks.throttle(gr.sizeof_gr_complex, FS, True)
tb.connect(src, thr, chan1.head); chan1.connect_chain(tb)
tb.start(); time.sleep(0.3)
captured.clear(); proc._tick()
assert len(captured[-1][0]) == 1024
chan2 = A.DisplayChannelizer(4096, A.WINDOWS["hann"](4096))
tb.lock()
try:
    tb.disconnect(thr, chan1.head); chan1.disconnect_chain(tb)
    tb.connect(thr, chan2.head); chan2.connect_chain(tb)
finally:
    tb.unlock()
proc.attach(chan2)
time.sleep(0.4)
captured.clear(); proc._tick()
tb.stop(); tb.wait()
f2 = captured[-1][0]
assert len(f2) == 4096 and abs(f2.max()) < 0.1 and int(f2.argmax()) == 4096 // 2 + 4096 // 8, \
    f"after the swap: len {len(f2)}, peak {f2.max():+.2f} dBFS in bin {int(f2.argmax())}"
print(f"6. FFT-size swap OK: 1024 -> 4096 live, tone {f2.max():+.3f} dBFS in bin {int(f2.argmax())}")

# --- 7. recording no longer shrinks the display ------------------------------
proc.set_recording_active(True)
assert not hasattr(proc, '_tick_budget_frac'), "the per-tick CPU budget must be gone"
print("7. recording OK: set_recording_active is a no-op (no coverage cut while recording)")

print("ALL DISPLAY CHECKS PASSED")
