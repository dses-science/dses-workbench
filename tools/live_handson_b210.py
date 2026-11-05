"""Headless hands-on pass through the REAL application on an attached radio
(written 2026-11-05 to take the mechanical steps of the 1.7 pass off Rick's
desk). Isolated settings, startup notices auto-accepted, offscreen Qt.

Steps, each reported with numbers:
  1. .fil recording at the Pulsar L-band preset (16 MS/s, 2044 ch): the
     display's blocks-per-tick (coverage) before and DURING the recording,
     rows written, gap events, file size vs expected.
  2. The same at the Pulsar UHF preset (20 MS/s, 256 ch x16).
  3. ezRA drift-scan recording at the Hydrogen preset until at least one row.
  4. Sweep mode in and out: steps advance, the wide trace fills, live frames
     resume on exit.
  5. B210 Self Test (standard bench parameters, 30 s capture) through the
     dialog's own begin / finish path, PRESTO fold and grade; radio state
     restored afterwards.
Recordings are deleted afterwards (the self-test fold PDF is kept).

  PATH=.conda/Library/bin:$PATH UHD_IMAGES_DIR=... .conda/python.exe tools/live_handson_b210.py [--skip selftest,sweep,...] [--fil-secs 10]
"""
import os, sys, tempfile, time, argparse, glob
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ap = argparse.ArgumentParser()
ap.add_argument("--skip", default="", help="comma list of: lband,uhf,ezra,sweep,selftest")
ap.add_argument("--fil-secs", type=float, default=10.0)
ap.add_argument("--selftest-secs", type=int, default=30)
ap.add_argument("--driver", default=None, help="with several radios attached, use only this driver (airspy, sdrplay, uhd...)")
ap.add_argument("--legacy-fil", action="store_true",
                help="A/B: record the Self Test through the 1.6.x sample-domain FilterbankSink instead of the compiled chain")
args = ap.parse_args()
skip = set(s.strip() for s in args.skip.split(",") if s.strip())
_cfg = tempfile.mkdtemp(prefix="dses_handson_cfg_")
os.environ["XDG_CONFIG_HOME"] = _cfg
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from PySide6 import QtWidgets, QtCore
from pathlib import Path
import dses_workbench as A
import sigproc_fil as _sf
if args.driver:
    _all = A.find_all_radios
    A.find_all_radios = lambda: [d for d in _all() if d["driver"] == args.driver]

if args.legacy_fil:
    class _LegacyAsChain(_sf.FilterbankSink):
        """FilterbankSink wearing FilterbankChain's connect/disconnect coat."""
        def connect(self, tb, upstream):
            tb.connect((upstream, 0), (self, 0))
        def disconnect(self, tb, upstream=None):
            tb.disconnect((upstream, 0), (self, 0))
    _sf.FilterbankChain = _LegacyAsChain
    print("A/B: Self Test recording through the LEGACY sample-domain FilterbankSink")

(Path(_cfg) / "DSES_Analyzer").mkdir(parents=True, exist_ok=True)
A._settings_path = lambda: Path(_cfg) / "DSES_Analyzer" / "settings.ini"
A._front_messagebox = lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Ok
for _name in ("information", "question", "warning", "critical"):
    setattr(QtWidgets.QMessageBox, _name,
            staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes))

qapp = QtWidgets.QApplication(sys.argv)
QtWidgets.QApplication.setApplicationName("DSES_Analyzer")
tb = A.dses_workbench()
assert not tb._playback_mode, "no live radio found"
tb.start(); tb.flowgraph_started.set(); tb.show()
print(f"radio: {tb._source.display_label}; recordings -> {tb.recording_dir}")

frames = []
last_frame = {}
def _on_frame(avg, mx, mn):
    frames.append(time.perf_counter()); last_frame["avg"] = np.asarray(avg)
tb._processor.frame_ready.connect(_on_frame)


def carrier_report(expect_hz=None):
    """Where the strongest bin of the last display frame sits (a bench
    carrier, e.g. the E4438C at 1420.500 MHz) and how far from expectation."""
    f = last_frame.get("avg")
    if f is None:
        return "no frame"
    n = len(f)
    freqs = tb.center_freq + np.fft.fftshift(np.fft.fftfreq(n, 1.0 / tb.samp_rate))
    c = n // 2
    body = f.copy(); body[c - 8: c + 9] = -999       # ignore the DC bin region
    i = int(np.argmax(body))
    floor = float(np.median(f))
    s = f"strongest bin {freqs[i]/1e6:.4f} MHz, {f[i]-floor:+.1f} dB above the median floor (bin {tb.samp_rate/n/1e3:.2f} kHz)"
    if expect_hz:
        s += f"; expected {expect_hz/1e6:.4f} MHz -> offset {(freqs[i]-expect_hz)/1e3:+.2f} kHz"
    return s
blocks_log = []
tick_dur = []
orig_tick = tb._processor._tick
def logged_tick():
    t0 = time.perf_counter()
    orig_tick()
    tick_dur.append(time.perf_counter() - t0)
    blocks_log.append(tb._processor._last_blocks_used)
# The automatic PRESTO analysis after a .fil stop would hold the analysis
# slot for minutes and block the Self Test; this pass checks recording, not
# analysis.
tb._analyze_check.setChecked(False)
tb._processor._timer.timeout.disconnect()
tb._processor._timer.timeout.connect(logged_tick)


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)
        time.sleep(0.01)


def coverage_window(seconds):
    """Frames/s and median display coverage over the next `seconds`."""
    frames.clear(); blocks_log.clear()
    pump(seconds)
    n = tb._processor.fft_size
    cov = np.array(blocks_log[2:]) * n / (tb.samp_rate * tb._processor._timer.interval() * 1e-3)
    return len(frames) / seconds, (float(np.median(cov)) * 100 if cov.size else 0.0)


def preset_index(substr):
    for i, (label, cfg) in enumerate(A.OBSERVATION_PRESETS):
        if substr in label:
            return i
    raise KeyError(substr)


def rm(path):
    for p in (path, path + ".gaps.json"):
        try:
            os.remove(p)
        except OSError:
            pass


results = []


def fil_step(tag, label_substr):
    tb._apply_observation(preset_index(label_substr))
    pump(2.0)
    fps0, cov0 = coverage_window(3.0)
    tb._rec_start_text = ""
    tb.set_record(1)
    assert tb._fil_sink is not None, "recording did not start"
    path = tb._fil_sink_path
    fps1, cov1 = coverage_window(args.fil_secs)
    rows = tb._fil_sink.nrows; gaps = tb._fil_sink.gap_events; broken = tb._fil_sink.timebase_broken
    tb.set_record(0)
    size = os.path.getsize(path)
    expect = 4.0 * tb.samp_rate * args.fil_secs / max(1, tb._fil_integrate)
    # Coverage is judged RELATIVE to what the link delivers: a B210 on a
    # USB 2 link (2026-11-05: behind the two-hub extension cable) tops out
    # near 4 MS/s and overflows above it, which the recorder pads as tagged
    # gaps; the display must keep the same share of the stream while
    # recording as before, and the file must be complete in time.
    ok = rows > 0 and size > 0.85 * expect and fps1 > 8 and cov1 >= 0.9 * cov0
    results.append((tag, ok))
    link = "" if cov0 > 90 else f" (link delivers ~{cov0/100*tb.samp_rate/1e6:.1f} MS/s of {tb.samp_rate/1e6:g}: USB 2?)"
    print(f"{'PASS' if ok else 'FAIL'} {tag}: {tb.samp_rate/1e6:g} MS/s / {tb._fil_nchans} ch x{tb._fil_integrate}; "
          f"display before REC {fps0:.1f} fps at {cov0:.0f}% coverage, DURING REC {fps1:.1f} fps at {cov1:.0f}%{link}; "
          f"{rows} rows, {size/1e6:.0f} MB ({size/expect*100:.0f}% of expected), gaps {gaps}, timebase_broken {broken}")
    rm(path)


if "lband" not in skip:
    fil_step("1. .fil L-band preset", "L-band")
if "uhf" not in skip:
    fil_step("2. .fil UHF preset", "UHF")

if "ezra" not in skip:
    tb._apply_observation(preset_index("Hydrogen"))
    pump(3.0)
    print(f"   HI preset on {tb._source.display_label}: centre {tb.center_freq/1e6:.3f} MHz, {tb.samp_rate/1e6:g} MS/s, "
          f"LO offset {tb.lo_offset/1e6:g} MHz, rates offered {[r/1e6 for r in tb._source.samp_rate_options]}")
    print("   carrier:", carrier_report(1420.5e6))
    row_s = tb._ez_fft_bins * tb._ez_integ_frames / tb.samp_rate
    tb._rec_start_text = ""
    tb.set_record(1)
    assert tb._ezra_sink is not None, "ezRA recording did not start"
    path = tb._ezra_sink_path
    fps1, cov1 = coverage_window(row_s + 8.0)
    rows = tb._ezra_sink.nrows
    tb.set_record(0)
    hdr = open(path, encoding="utf-8", errors="replace").read(400).splitlines()[:4]
    ok = rows >= 1 and cov1 > 90
    results.append(("3. ezRA Hydrogen preset", ok))
    print(f"{'PASS' if ok else 'FAIL'} 3. ezRA Hydrogen preset: {tb.samp_rate/1e6:g} MS/s, centre {tb.center_freq/1e6:.3f} MHz, "
          f"LO offset {tb.lo_offset/1e6:g} MHz, {row_s:.0f} s/row -> {rows} row(s) in {row_s+8:.0f} s; display {fps1:.1f} fps at {cov1:.0f}%; "
          f"header: {' | '.join(hdr[:3])}")
    rm(path)

if "sweep" not in skip:
    tb._apply_observation(A.OBS_MANUAL_IDX)
    tb._mode_sweep_btn.setChecked(True)
    pump(0.5)
    active = tb._sweep_active
    pump(8.0)
    steps = tb._sweep_step_idx
    wide = getattr(tb, "_sweep_wide_db", None)
    filled = int(np.sum(wide > -149.0)) if wide is not None else 0
    tb._mode_live_btn.setChecked(True)
    pump(0.5)
    tick_dur.clear()
    windows = [coverage_window(2.0) for _ in range(4)]
    fps2, cov2 = windows[-1]
    slow = max(tick_dur) * 1e3 if tick_dur else 0.0
    ok = active and (steps > 0 or filled > 0) and not tb._sweep_active and fps2 > 8
    results.append(("4. Sweep in/out", ok))
    print(f"{'PASS' if ok else 'FAIL'} 4. Sweep in/out: entered {active}, step index {steps}, wide-trace bins filled {filled}"
          f"{' of ' + str(wide.size) if wide is not None else ''}; back to Live over 4 x 2 s: "
          + ", ".join(f"{f:.1f} fps/{c:.0f}%" for f, c in windows)
          + f"; slowest tick {slow:.0f} ms; rate {tb.samp_rate/1e6:g} MS/s, fft {tb._processor.fft_size}, decim {tb._display.decim}")

if "selftest" not in skip:
    class Dlg:
        def __init__(self):
            self.msgs = []; self.result = None; self.failed = None
        def on_progress(self, m): self.msgs.append(m)
        def on_failed(self, m): self.failed = m
        def on_result(self, ok, checks, res, pdf): self.result = (ok, checks, res, pdf)
    before = (tb.samp_rate, tb.gain, tb.center_freq, tb._source.current_antenna)
    dlg = Dlg()
    err = tb._selftest_begin(dlg)
    if err:
        results.append(("5. Self Test", False)); print(f"FAIL 5. Self Test: could not begin: {err}")
    else:
        fps3, cov3 = coverage_window(args.selftest_secs)
        gaps = tb._selftest_gap_events()
        tb._selftest_finish_capture(dlg)
        t0 = time.monotonic()
        while dlg.result is None and dlg.failed is None and time.monotonic() - t0 < 600:
            pump(1.0)
        after = (tb.samp_rate, tb.gain, tb.center_freq, tb._source.current_antenna)
        if dlg.failed:
            results.append(("5. Self Test", False)); print(f"FAIL 5. Self Test: {dlg.failed}")
        else:
            ok, checks, res, pdf = dlg.result
            results.append(("5. Self Test", ok and before == after))
            print(f"{'PASS' if ok else 'FAIL'} 5. Self Test (standard, {args.selftest_secs} s): verdict {res.get('verdict')}, "
                  f"chi2_red {res.get('chi2_red')}, P {res.get('best_p_s')}, DM {res.get('best_dm')}; capture gaps {gaps}; "
                  f"display during capture {fps3:.1f} fps at {cov3:.0f}%; radio state restored {before == after}; "
                  f"fold took {time.monotonic()-t0:.0f} s; pdf {pdf}")
            for c in checks[1:]:
                if c:
                    print("      ", c)
            for m in dlg.msgs:
                if "Injection path" in m:
                    print("       probe:", m)
        # the self-test .fil is large; keep the PDF. Print the gap sidecar
        # (what was padded, where, how much) before removing it.
        for f in glob.glob(os.path.join(tb.recording_dir, "self_test", "*.fil")):
            sc = f + ".gaps.json"
            if os.path.exists(sc):
                print("      gaps sidecar:", open(sc).read().replace(chr(10), " ")[:600])
            rm(f)

tb.stop(); tb.wait(); tb.close()
print("\nSUMMARY: " + ", ".join(f"{t} {'PASS' if ok else 'FAIL'}" for t, ok in results))
print("ALL HANDS-ON STEPS PASSED" if all(ok for _, ok in results) else "SOME STEPS FAILED")
