"""Tests for the spectrum plot's trace detector and the Line view (1.8).

Origin: Ray Uberecken's SDR Console comparison (2026-11-18). At 65536 points
the plot drew the highest and lowest of ~91 bins under every pixel column, a
band 0.57 dB thick that hid his 0.5 dB hydrogen line; SDR Console drew a thin
line. The trace detector reduces bins to pixels by MEAN POWER ('average'),
keeps the old behaviour as 'peak', and offers both.

  1. 'average' on Ray's numbers (65536 bins, 0.1 dB per-bin scatter, a
     +0.47 dB hump 150 kHz wide): at most one point per pixel, scatter cut
     by about sqrt(bins per pixel), the hump recovered.
  2. The mean is a POWER mean (a strong narrow carrier keeps its power).
  3. 'peak' hands the full array to the curve (legacy); 'both' shows the
     envelope behind the mean line; a forced detector overrides (Sweep).
  4. Zoomed in until bins <= 2 per pixel, the bins are drawn one by one.
  5. Linear scale converts after the reduction.
  6. Line view: FFT size for ~1 kHz RBW at 2 / 6 / 8 / 16 MS/s, the controls
     it sets, the zoom on 1420.406 MHz only when the span is wide enough,
     the tight Y fit, and switching it off restores everything.

Run: PATH=.conda/Library/bin:$PATH .conda/python.exe test_trace_detector.py
     (--render <dir> also saves before/after PNGs of the plot)
"""
import os, sys
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from PySide6 import QtWidgets, QtCore
import dses_workbench as A

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
RENDER = sys.argv[sys.argv.index("--render") + 1] if "--render" in sys.argv else None
checks = 0


def ok(cond, msg):
    global checks
    assert cond, msg
    checks += 1


def pump(ms=60):
    end = QtCore.QTime.currentTime().addMSecs(ms)
    while QtCore.QTime.currentTime() < end:
        app.processEvents()


CENTER, RATE, N = 1420.4e6, 2e6, 65536


def given(curve):
    """What the widget handed the curve (the curve's own data accessor
    returns pyqtgraph's downsampled, clipped display copy instead)."""
    x, y = curve.getOriginalDataset()
    return np.asarray(x), np.asarray(y)

rng = np.random.default_rng(7)
freqs = CENTER + np.fft.fftshift(np.fft.fftfreq(N, d=1.0 / RATE))
hump = 0.47 * np.exp(-0.5 * ((freqs - 1420.333e6) / (150e3 / 2.355)) ** 2)
ushape = 1.2 * ((freqs - CENTER) / 1e6) ** 4            # HackRF-like passband
frame = -95.3 + ushape + hump + rng.normal(0.0, 0.1, N)
spur_i = int(np.argmin(np.abs(freqs - 1420.0e6)))
frame[spur_i] += 20.0                                   # the 1420.000 carrier

w = A.FftPlotWidget(CENTER, RATE)
panel = w.take_panel()                                   # as the main window does
w.resize(900, 420)
w.show()
w.set_frequency_range(CENTER, RATE)
w._plot.setYRange(-96.5, -93.1, padding=0)
pump(150)
vb = w._plot.getPlotItem().getViewBox()
W = int(vb.width())
ok(W > 300, f"plot view only {W} px wide")

# --- 3a. peak = legacy: the curve gets every bin --------------------------
w._trace_combo.setCurrentIndex(w._trace_combo.findData('peak'))
w.on_frame(frame, None, None); pump()
x, y = given(w._curve)
ok(len(y) == N, f"peak mode handed {len(y)} points, expected {N}")
ok(not w._env_curve.isVisible(), "envelope visible in peak mode")
if RENDER:
    w._plot.grab().save(os.path.join(RENDER, "trace_peak_ray_settings.png"))

# --- 1. average on Ray's numbers -------------------------------------------
w._trace_combo.setCurrentIndex(w._trace_combo.findData('average'))
ok(w._trace_detector == 'average', "combo did not set the detector")
w.on_frame(frame, None, None); pump()
x, y = given(w._curve)
bins_per_px = N / len(y)
ok(len(y) <= W and len(y) > W / 2.5, f"{len(y)} points for a {W}px view")
flat = (x > 1420.50e6) & (x < 1420.85e6)
resid = y[flat] - np.polyval(np.polyfit(x[flat], y[flat], 3), x[flat])
ok(resid.std() < 0.1 / np.sqrt(bins_per_px) * 1.6,
   f"scatter {resid.std():.4f} dB vs expected ~{0.1/np.sqrt(bins_per_px):.4f}")
base = np.interp(x, [1420.15e6, 1420.55e6],
                 [y[np.argmin(abs(x - 1420.15e6))], y[np.argmin(abs(x - 1420.55e6))]])
win = (x > 1420.25e6) & (x < 1420.42e6)
pk = (y - base)[win].max(); fpk = x[win][np.argmax((y - base)[win])]
ok(0.38 < pk < 0.56 and abs(fpk - 1420.333e6) < 25e3, f"hump {pk:.2f} dB at {fpk/1e6:.3f}")
print(f"1. average: {len(y)} points for {W} px ({bins_per_px:.0f} bins each), scatter "
      f"{resid.std():.4f} dB (per-bin 0.1), hump +{pk:.2f} dB at {fpk/1e6:.3f} MHz")
if RENDER:
    w._plot.grab().save(os.path.join(RENDER, "trace_average_ray_settings.png"))

# --- 2. power mean: a 20 dB carrier in one bin of g keeps its power --------
g = int(round(bins_per_px))
col = int(np.argmin(np.abs(x - 1420.0e6)))
expect = 10 * np.log10((g - 1 + 10 ** 2.0) / g)
got = y[col] - np.median(y[max(0, col - 8): col + 9])
ok(abs(got - expect) < 0.6, f"carrier column +{got:.2f} dB, power mean predicts +{expect:.2f}")
print(f"2. power mean OK: 20 dB single-bin carrier reads +{got:.2f} dB over {g} bins (predicted +{expect:.2f})")

# --- 3b. both: envelope behind the mean line; forced detector overrides ----
w._trace_combo.setCurrentIndex(w._trace_combo.findData('both'))
w.on_frame(frame, None, None); pump()
ok(w._env_curve.isVisible() and len(given(w._env_curve)[1]) == N, "no envelope in 'both'")
ok(len(given(w._curve)[1]) <= W, "mean line not reduced in 'both'")
if RENDER:
    w._plot.grab().save(os.path.join(RENDER, "trace_both_ray_settings.png"))
w.on_frame(frame, None, None, detector='peak'); pump()
ok(len(given(w._curve)[1]) == N and not w._env_curve.isVisible(),
   "forced 'peak' (Sweep) did not override the Trace setting")
print("3. peak = full array, both = mean line + envelope, forced detector overrides: OK")

# --- 4. zoom until bins <= 2 per pixel: bins drawn one by one --------------
w._trace_combo.setCurrentIndex(w._trace_combo.findData('average'))
w.on_frame(frame, None, None); pump()
w._plot.setXRange(1420.30e6, 1420.33e6, padding=0); pump()     # 30 kHz = 983 bins
x, y = given(w._curve)
ok(len(y) == N, f"zoomed view should plot raw bins, got {len(y)} points")
w._plot.setXRange(1420.0e6, 1420.8e6, padding=0); pump()       # re-reduced on the zoom itself
x, y = given(w._curve)
ok(len(y) <= W and x.min() > 1419.99e6 - 5e3 and x.max() < 1420.81e6 + 5e3,
   f"after zooming out: {len(y)} points spanning {x.min()/1e6:.3f}-{x.max()/1e6:.3f}")
print(f"4. zoom OK: raw bins when 2 or fewer per pixel; re-reduced on the zoom ({len(y)} points for 0.8 MHz)")
w.set_frequency_range(CENTER, RATE); pump()

# --- 5. linear scale converts after the reduction --------------------------
w._linear_check.setChecked(True); pump()      # the Y axis re-labels, so the view width may change
w.on_frame(frame, None, None); pump()
ylin = given(w._curve)[1]
red = w._reduce_mean(freqs, frame)             # the same view the frame was drawn into
ok(red is not None and len(ylin) == len(red[1]) and np.allclose(ylin, 10 ** (red[1] / 20.0), rtol=1e-9),
   "linear != 10^(dB/20) of the reduced trace")
w._linear_check.setChecked(False); pump()
print("5. linear scale OK")

# --- 6. Line view -----------------------------------------------------------
for rate, want in ((2e6, 2048), (6e6, 8192), (8e6, 8192), (16e6, 16384), (250e3, 256)):
    w._samp_rate = rate
    ok(w.line_view_fft_size() == want, f"{rate/1e6:g} MS/s -> {w.line_view_fft_size()}, want {want}")
w._samp_rate = RATE
w._fft_size_combo.setCurrentIndex(w._fft_size_combo.findData(65536))
w._avg_slider.setValue(3); w._smooth_spin.setValue(0)
w._trace_combo.setCurrentIndex(w._trace_combo.findData('peak'))
w._max_check.setChecked(True)
w._plot.setYRange(-96.53, -93.11, padding=0); pump()
before = (w._fft_size_combo.currentData(), w._avg_slider.value(), w._smooth_spin.value(),
          w._trace_detector, w._max_check.isChecked(), tuple(round(v, 3) for v in vb.viewRange()[1]))
seen = []
w.line_view_changed.connect(seen.append)
w.LINE_VIEW_SETTLE_MS = 150; w._line_view_timer.setInterval(150)
w.set_line_view(True)
ok(w._fft_size_combo.currentData() == 2048 and w._avg_slider.value() == 50
   and w._smooth_spin.value() == 9 and w._trace_detector == 'average'
   and not w._max_check.isChecked() and w._dc_check.isChecked() and w._power_avg_check.isChecked(),
   "Line view did not set its controls")
(x0, x1), _ = vb.viewRange()
ok(abs((x1 - x0) - RATE) < 1e3, f"2 MS/s span must stay full, got {(x1-x0)/1e6:.3f} MHz")
# the processor would now deliver 2048-point frames with ~0.02 dB scatter
f2 = CENTER + np.fft.fftshift(np.fft.fftfreq(2048, d=1.0 / RATE))
frame2 = (-95.3 + 1.2 * ((f2 - CENTER) / 1e6) ** 4
          + 0.47 * np.exp(-0.5 * ((f2 - 1420.333e6) / (150e3 / 2.355)) ** 2) + rng.normal(0, 0.02, 2048))
frame2[int(np.argmin(np.abs(f2 - 1420.0e6)))] += 6.0
w.on_frame(frame2, None, None); pump(400)
y0, y1 = vb.viewRange()[1]
# fitted to the middle 80% of the view: the hump stays inside, the band edges may not
ok(0.5 < (y1 - y0) < 2.0 and y0 < -95.3 and y1 > -94.80, f"tight Y fit gave {y0:.2f}..{y1:.2f}")
ok(y1 < frame2[:40].mean(), "the rising band edge should be allowed to run off the top")
ok(y1 < frame2.max() - 2.0, "the narrow carrier should run off the top of a tight fit")
ok(seen == [True], f"line_view_changed emitted {seen}")
if RENDER:
    w._plot.grab().save(os.path.join(RENDER, "line_view_2Msps.png"))
print(f"6a. Line view on: FFT 2048, alpha 0.05, smooth 9, average, holds off; Y fitted to {y0:.2f}..{y1:.2f} dB")
w.set_line_view(False); pump()
after = (w._fft_size_combo.currentData(), w._avg_slider.value(), w._smooth_spin.value(),
         w._trace_detector, w._max_check.isChecked(), tuple(round(v, 3) for v in vb.viewRange()[1]))
ok(after == before, f"Line view off did not restore: {after} vs {before}")
ok(seen == [True, False], f"line_view_changed emitted {seen}")
# wide span (the HackRF 'Ray configuration': 1422.0 at 8 MS/s): zoom on the line
w.set_frequency_range(1422.0e6, 8e6); pump()
w.set_line_view(True); pump()
(x0, x1), _ = vb.viewRange()
ok(abs(x0 - (A.HI_REST_HZ - 0.9e6)) < 2e3 and abs(x1 - (A.HI_REST_HZ + 0.9e6)) < 2e3,
   f"8 MS/s view should zoom to the line +-0.9 MHz, got {x0/1e6:.3f}-{x1/1e6:.3f}")
ok(w._fft_size_combo.currentData() == 8192, "8 MS/s line view FFT size")
w.set_line_view(False); pump()
# line out of band: no zoom
w.set_frequency_range(408e6, 8e6); pump()
w.set_line_view(True); pump()
(x0, x1), _ = vb.viewRange()
ok(abs((x1 - x0) - 8e6) < 1e3, "line out of band: the span must stay full")
w.set_line_view(False); pump()
print("6b. Line view off restores every setting; zooms on 1420.406 only when the span allows; full span off the line")

print(f"\nALL TRACE-DETECTOR TESTS PASSED ({checks} checks)")
