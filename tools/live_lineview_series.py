"""Live hands-on check of the 1.8 features on the attached radio, headless,
through the REAL main window (settings isolated in a temp folder):

  1. Trace detector on live noise at a 65536-point FFT: the drawn scatter in
     Peak (the pre-1.8 envelope) against Average (mean power per pixel).
  2. Line view on the Hydrogen preset via the View-menu action: FFT size,
     controls, a tight Y fit, frames still flowing; off restores.
  3. A repeating recording series through the real .fil recorder with the
     "day" compressed (default 12 s apart, 4 s each, 2 recordings): the files
     exist, the starts are one interval apart, the series ends by itself.

Screenshots of the plot go to --out (default: a temp folder, printed).
Recordings go to a temp folder and are deleted.

Run under radioconda for Soapy radios, either env for a B210:
  python tools/live_lineview_series.py [--driver uhd_b200] [--out DIR]
"""
import argparse, glob, os, sys, tempfile, time
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ap = argparse.ArgumentParser()
ap.add_argument("--driver", default=None)
ap.add_argument("--out", default=None)
ap.add_argument("--interval", type=float, default=12.0)
ap.add_argument("--carrier-mhz", type=float, default=None,
                help="a CW test carrier is present at this frequency: also report how it reads "
                     "in Peak and Average, and that Line view lets it run off the top")
ap.add_argument("--only-carrier", action="store_true", help="skip the recording series")
args = ap.parse_args()
_cfg = tempfile.mkdtemp(prefix="dses_live18_cfg_")
os.environ["XDG_CONFIG_HOME"] = _cfg
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from pathlib import Path
from PySide6 import QtWidgets, QtCore
import dses_workbench as A

if args.driver:
    _all = A.find_all_radios
    A.find_all_radios = lambda: [d for d in _all() if d["driver"] == args.driver]
(Path(_cfg) / "DSES_Analyzer").mkdir(parents=True, exist_ok=True)
A._settings_path = lambda: Path(_cfg) / "DSES_Analyzer" / "settings.ini"
A._front_messagebox = lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Ok
for _n in ("information", "question", "warning", "critical"):
    setattr(QtWidgets.QMessageBox, _n,
            staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes))
out_dir = args.out or tempfile.mkdtemp(prefix="dses_live18_out_")
rec_dir = tempfile.mkdtemp(prefix="dses_live18_rec_")

qapp = QtWidgets.QApplication(sys.argv)
QtWidgets.QApplication.setApplicationName("DSES_Analyzer")
tb = A.dses_workbench()
assert not tb._playback_mode, "no live radio found"
tb.resize(1500, 900)
tb.start(); tb.flowgraph_started.set(); tb.show()
fp = tb._fft_plot
frames = []
tb._processor.frame_ready.connect(lambda a, m, n: frames.append(time.perf_counter()))
results = []


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)
        time.sleep(0.01)


def drawn():
    x, y = fp._curve.getOriginalDataset()
    return np.asarray(x, float), np.asarray(y, float)


def flat_scatter():
    """Scatter of the drawn main trace about a smooth fit, away from the
    band edges and the centre."""
    x, y = drawn()
    lo, hi = tb.center_freq - 0.35 * tb.samp_rate, tb.center_freq - 0.08 * tb.samp_rate
    m = (x > lo) & (x < hi) & np.isfinite(y)
    r = y[m] - np.polyval(np.polyfit(x[m] - x[m].mean(), y[m], 3), x[m] - x[m].mean())
    return float(np.std(r)), int(m.sum())


def record(label, ok, text):
    results.append((label, ok))
    print(("PASS " if ok else "FAIL ") + label + ": " + text, flush=True)


def carrier_over_floor():
    """(dB above the local floor, frequency) of the drawn trace's highest
    point within 30 kHz of --carrier-mhz."""
    x, y = drawn()
    f0 = args.carrier_mhz * 1e6
    near = np.abs(x - f0) < 30e3
    ring = (np.abs(x - f0) > 60e3) & (np.abs(x - f0) < 200e3) & np.isfinite(y)
    if not near.any() or not ring.any():
        return float("nan"), float("nan")
    i = np.nanargmax(np.where(near, y, -np.inf))
    return float(y[i] - np.median(y[ring])), float(x[i])


print(f"radio: {tb._source.display_label}; screenshots -> {out_dir}", flush=True)
names = [n for n, _ in A.OBSERVATION_PRESETS]
tb._apply_observation(next(i for i, n in enumerate(names) if n.startswith("Hydrogen")))
pump(2.0)
print(f"   Hydrogen preset: centre {tb.center_freq/1e6:.3f} MHz, {tb.samp_rate/1e6:g} MS/s, "
      f"LO offset {tb.lo_offset/1e6:g} MHz", flush=True)

# --- 1. trace detector on live noise, 65536-point FFT ----------------------
fp._fft_size_combo.setCurrentIndex(fp._fft_size_combo.findData(65536))
fp._avg_slider.setValue(50)
vb = fp._plot.getPlotItem().getViewBox()
fp.set_frequency_range(tb.center_freq, tb.samp_rate)
fp._trace_combo.setCurrentIndex(fp._trace_combo.findData('peak'))
pump(6.0)
fp._on_autoscale_y(); pump(0.3)
x, y = drawn()
W = int(vb.width())
# Peak mode hands every bin over; what the eye sees is the per-pixel span.
g = max(1, len(y) // W)
span = np.array([y[i:i + g].max() - y[i:i + g].min() for i in range(len(y) // 4, len(y) // 2, g)])
peak_band = float(np.median(span))
fp._plot.grab().save(os.path.join(out_dir, "live_1_peak_65536.png"))
c_peak = carrier_over_floor() if args.carrier_mhz else None
fp._trace_combo.setCurrentIndex(fp._trace_combo.findData('average'))
pump(1.5)
if args.carrier_mhz:
    c_avg = carrier_over_floor()
    dilute = 10 * np.log10(g)
    # one bright bin among g: the mean power reads lower by up to 10*log10(g)
    # (less when the carrier is strong enough to dominate the column)
    record("1c. narrow carrier, Peak vs Average",
           np.isfinite(c_peak[0]) and c_peak[0] > 6 and 0 < c_peak[0] - c_avg[0] <= dilute + 1.5 and c_avg[0] > 0.3,
           f"carrier at {c_peak[1]/1e6:.4f} MHz reads +{c_peak[0]:.1f} dB over the floor in Peak, "
           f"+{c_avg[0]:.1f} dB in Average ({g} bins per pixel: up to {dilute:.1f} dB lower by the power mean)")
sc_avg, npts = flat_scatter()
xa, ya = drawn()
fp._plot.grab().save(os.path.join(out_dir, "live_2_average_65536.png"))
record("1. trace detector live", len(ya) <= W and sc_avg < peak_band / 8,
       f"65536 bins on {W} px: Peak draws a band {peak_band:.2f} dB thick ({g} bins per pixel); "
       f"Average draws {len(ya)} points with {sc_avg:.3f} dB scatter")

# --- 2. Line view through the View-menu action ------------------------------
before = (fp._fft_size_combo.currentData(), fp._avg_slider.value(), fp._smooth_spin.value(), fp._trace_detector)
fp.LINE_VIEW_SETTLE_MS = 3500; fp._line_view_timer.setInterval(3500)
frames.clear()
tb._line_view_act.setChecked(True)
pump(6.0)
fps = len(frames) / 6.0
y0, y1 = vb.viewRange()[1]
sc_lv, _ = flat_scatter()
want = fp.line_view_fft_size()
ok2 = (tb._display.fft_size == want and fp._avg_slider.value() == 50 and fp._smooth_spin.value() == 9
       and fp._trace_detector == 'average' and fp._line_view_btn.isChecked() and fps > 6 and (y1 - y0) < 4.0)
fp._plot.grab().save(os.path.join(out_dir, "live_3_line_view.png"))
record("2a. Line view on", ok2,
       f"FFT {tb._display.fft_size} (RBW {tb.samp_rate/tb._display.fft_size:.0f} Hz), alpha 0.05, smooth 9, "
       f"{fps:.1f} fps, Y fitted to {y0:.2f}..{y1:.2f} dB ({y1-y0:.2f} dB tall), trace scatter {sc_lv:.3f} dB")
if args.carrier_mhz:
    c_lv = carrier_over_floor()
    xr = vb.viewRange()[0]
    in_view = xr[0] < args.carrier_mhz * 1e6 < xr[1]
    xx, yy = drawn()
    top = float(np.nanmax(yy[np.abs(xx - args.carrier_mhz * 1e6) < 30e3])) if in_view else float("nan")
    record("2c. Line view with a carrier in view", in_view and top > y1 and (y1 - y0) < 4.0,
           f"carrier +{c_lv[0]:.1f} dB over the floor tops out at {top:.2f} dB, above the fitted "
           f"top {y1:.2f} dB: it runs off the plot and the scale stays {y1-y0:.2f} dB tall")
tb._line_view_act.setChecked(False); pump(2.0)
after = (fp._fft_size_combo.currentData(), fp._avg_slider.value(), fp._smooth_spin.value(), fp._trace_detector)
record("2b. Line view off", after == before and tb._display.fft_size == before[0] and not fp._line_view_btn.isChecked(),
       f"restored FFT {after[0]}, alpha {after[1]/1000:g}, smooth {after[2]}, trace {after[3]}")

# --- 3. a real series through the .fil recorder, the day compressed ----------
if args.only_carrier:
    tb.stop(); tb.wait(); tb.close()
    bad = [l for l, ok in results if not ok]
    print("ALL LIVE 1.8 CHECKS PASSED" if not bad else "FAILED: " + "; ".join(bad), flush=True)
    sys.exit(1 if bad else 0)
fp._fft_size_combo.setCurrentIndex(fp._fft_size_combo.findData(4096)); pump(1.0)
tb.recording_dir = rec_dir
tb.set_record_format('fil')
tb._analyze_check.setChecked(False)
tb._rec_duration_edit.setText("0:00:04"); tb._on_rec_duration_changed()
tb._rec_start_edit.setText(""); tb._on_rec_start_changed()
tb._rec_repeat_combo.setCurrentIndex(tb._rec_repeat_combo.findData('sidereal'))
tb._rec_repeat_count_spin.setValue(2)
real = A.dses_workbench._repeat_interval_s
A.dses_workbench._repeat_interval_s = staticmethod(lambda mode, custom="": args.interval)
starts, paths, was = [], [], False
frames.clear()
tb.set_record(1)
end = time.monotonic() + args.interval + 4 + 4
armed_text = ""
while time.monotonic() < end:
    qapp.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50); time.sleep(0.01)
    live = tb._fil_sink is not None
    if live and not was:
        starts.append(time.time()); paths.append(tb._fil_sink_path)
    was = live
    if tb._rec_sched_ts is not None and not armed_text:
        armed_text = tb._recording_status.text()
A.dses_workbench._repeat_interval_s = staticmethod(real)
sizes = [os.path.getsize(p) if os.path.exists(p) else 0 for p in paths]
expect = 4.0 * tb.samp_rate * 4.0 / max(1, tb._fil_integrate)      # float32 per sample, 4 s
gap = (starts[1] - starts[0]) if len(starts) == 2 else float("nan")
ok3 = (len(paths) == 2 and len(set(paths)) == 2 and all(0.6 * expect < s < 1.5 * expect for s in sizes)
       and abs(gap - args.interval) < 1.6 and tb.record == 0 and tb._rec_series is None
       and "2 of 2" in armed_text and "series complete" in tb._recording_status.text())
record("3. series through the real recorder", ok3,
       f"{len(paths)} files {[round(s/1e6, 1) for s in sizes]} MB (about {expect/1e6:.0f} expected), starts {gap:.1f} s apart "
       f"(wanted {args.interval:g}), armed status {armed_text!r}, final {tb._recording_status.text()!r}, "
       f"display {len(frames)/(args.interval+8):.1f} fps throughout")
for p in glob.glob(os.path.join(rec_dir, "*")):
    try:
        os.remove(p)
    except OSError:
        pass

tb.stop(); tb.wait(); tb.close()
bad = [l for l, ok in results if not ok]
print("ALL LIVE 1.8 CHECKS PASSED" if not bad else "FAILED: " + "; ".join(bad), flush=True)
sys.exit(1 if bad else 0)
