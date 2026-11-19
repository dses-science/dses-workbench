"""Headless run of a repeating recording series through the REAL main window
(playback mode, settings isolated), with the recorder itself replaced by a
stub and the day compressed to a few seconds.

Ray Uberecken's ask (2026-11-19): the same recording for many days, each the
same length, the start moving by itself to follow the sky. This drives the
whole state machine: Record arms the series, each recording starts itself,
auto-stops at its length, re-arms for the next start counted from the FIRST
one, and the series ends by itself; a user Stop cancels it; a start that
cannot record is skipped; a schedule that cannot work is refused.

Run: PATH=.conda/Library/bin:$PATH .conda/python.exe test_rec_series.py
"""
import os, sys, tempfile, time
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_cfg = tempfile.mkdtemp(prefix="dses_series_cfg_")
os.environ["XDG_CONFIG_HOME"] = _cfg
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pathlib import Path
from PySide6 import QtWidgets, QtCore
import dses_workbench as A

(Path(_cfg) / "DSES_Analyzer").mkdir(parents=True, exist_ok=True)
A._settings_path = lambda: Path(_cfg) / "DSES_Analyzer" / "settings.ini"
A._front_messagebox = lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Ok
warnings = []
QtWidgets.QMessageBox.warning = staticmethod(
    lambda *a, **k: warnings.append(a[2] if len(a) > 2 else "") or QtWidgets.QMessageBox.StandardButton.Ok)
for _n in ("information", "question", "critical"):
    setattr(QtWidgets.QMessageBox, _n, staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes))
A.find_all_radios = lambda: []                      # playback: no radio needed

qapp = QtWidgets.QApplication(sys.argv)
QtWidgets.QApplication.setApplicationName("DSES_Analyzer")
tb = A.dses_workbench()
tb.start(); tb.flowgraph_started.set(); tb.show()
checks = 0


def ok(cond, msg):
    global checks
    assert cond, msg
    checks += 1


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)
        time.sleep(0.01)


# --- stand-ins: a "radio" and a recorder that only keeps a log --------------
log = []                                             # (event, unix time)
tb._playback_mode = False
tb.uhd_usrp_source_0 = object()
tb._record_format = 'sigmf'
fail_next = {"n": 0}


def fake_start():
    if fail_next["n"] > 0:                           # the radio is "gone" for this start
        fail_next["n"] -= 1
        log.append(("failed", time.time()))
        return
    log.append(("start", time.time()))
    tb._recording_status.setText("Recording -> stub")
    tb._on_recording_started()


def fake_stop():
    log.append(("stop", time.time()))
    tb._recording_status.setText("Saved stub")
    tb._on_recording_stopped()


tb._start_sigmf_recording = fake_start
tb._stop_sigmf_recording = fake_stop
tb._begin_realtime_mode = lambda: None
tb._end_realtime_mode = lambda: None

INTERVAL, DUR = 6.0, 2
real_interval = A.dses_workbench._repeat_interval_s
A.dses_workbench._repeat_interval_s = staticmethod(lambda mode, custom="": INTERVAL)


def setup(mode, count, start_text="", dur_text="0:00:02"):
    tb._rec_duration_edit.setText(dur_text); tb._on_rec_duration_changed()
    tb._rec_start_edit.setText(start_text); tb._on_rec_start_changed()
    tb._rec_repeat_combo.setCurrentIndex(tb._rec_repeat_combo.findData(mode))
    tb._rec_repeat_count_spin.setValue(count)
    log.clear(); warnings.clear()


# --- 1. a schedule that cannot work is refused -------------------------------
setup('sidereal', 3, dur_text="")
tb.set_record(1); pump(0.3)
ok(tb.record == 0 and tb._rec_series is None and warnings and "Record for" in warnings[0],
   f"no duration should be refused: {warnings}")
setup('sidereal', 3, dur_text="0:00:10")              # 10 s recording in a 6 s 'day'
tb.set_record(1); pump(0.3)
ok(tb.record == 0 and tb._rec_series is None and warnings and "shorter" in warnings[0],
   f"duration >= interval should be refused: {warnings}")
A.dses_workbench._repeat_interval_s = staticmethod(real_interval)
tb._rec_repeat_custom_edit.setText("nonsense"); setup('custom', 3)
tb._rec_repeat_custom_edit.setText("nonsense"); tb._on_rec_repeat_changed()
tb.set_record(1); pump(0.3)
ok(tb.record == 0 and warnings and "Custom interval" in warnings[0], f"bad custom interval: {warnings}")
ok(tb._rec_repeat_custom_edit.isVisible(), "custom interval box should show in Custom mode")
A.dses_workbench._repeat_interval_s = staticmethod(lambda mode, custom="": INTERVAL)
print("1. refused: no Record for; Record for longer than the interval; unreadable custom interval")

# --- 2. three recordings, starts counted from the first ----------------------
setup('sidereal', 3)                                 # Start at blank: the first one starts now
t0 = time.time()
tb.set_record(1); pump(0.5)
ok(tb._rec_series is not None and tb._rec_start_time is not None and log and log[0][0] == "start",
   "first recording of the series did not start at once")
ok(not tb._rec_repeat_widget.isEnabled() and not tb._rec_start_widget.isEnabled(),
   "schedule controls must lock while a series runs")
pump(3.2)                                            # past the first auto-stop
ok(tb.record == 1 and tb._rec_sched_ts is not None and tb._rec_start_time is None,
   "after the first auto-stop the series must be ARMED for the next start")
ok("2 of 3" in tb._recording_status.text(), f"armed status: {tb._recording_status.text()!r}")
ok("first start" in tb._recording_status.toolTip(), "armed tooltip should give the series anchor")
pump(2 * INTERVAL + 1.5)
starts = [t for e, t in log if e == "start"]; stops = [t for e, t in log if e == "stop"]
ok(len(starts) == 3 and len(stops) == 3, f"expected 3 recordings, log {[(e, round(t - t0, 1)) for e, t in log]}")
anchor = starts[0]
for k, st in enumerate(starts):
    ok(abs(st - (anchor + k * INTERVAL)) < 1.3, f"start {k} at +{st - anchor:.2f} s, wanted +{k * INTERVAL:.0f}")
for st, sp in zip(starts, stops):
    ok(1.0 <= sp - st <= 3.6, f"recording ran {sp - st:.2f} s, wanted about {DUR}")
ok(tb.record == 0 and tb._rec_series is None and tb._rec_sched_ts is None, "series should end by itself")
ok("series complete: 3" in tb._recording_status.text(), f"final status: {tb._recording_status.text()!r}")
ok(tb._rec_repeat_widget.isEnabled(), "schedule controls must unlock when the series ends")
print(f"2. series of 3: starts at +{', +'.join(f'{s - anchor:.1f}' for s in starts)} s "
      f"(wanted +0, +{INTERVAL:.0f}, +{2 * INTERVAL:.0f}), each {DUR} s, ended by itself")

# --- 3. Stop while armed cancels the whole series ----------------------------
setup('sidereal', 5)
tb.set_record(1); pump(3.4)                          # first made, armed for the second
ok(tb._rec_sched_ts is not None, "should be armed")
tb.set_record(0); pump(0.3)
ok(tb._rec_series is None and tb._rec_sched_ts is None and "series cancelled" in tb._recording_status.text(),
   f"Stop while armed: {tb._recording_status.text()!r}")
pump(INTERVAL)
ok(len([1 for e, _ in log if e == "start"]) == 1, "a cancelled series must not record again")
# ... and Stop DURING a recording cancels it too
setup('sidereal', 5)
tb.set_record(1); pump(0.8)
tb.set_record(0); pump(INTERVAL + 1.0)
ok(tb._rec_series is None and len([1 for e, _ in log if e == "start"]) == 1 and tb.record == 0,
   "Stop during a recording must end the series")
print("3. Stop cancels the series, armed or recording")

# --- 4. a start that cannot record is skipped, the series goes on ------------
setup('sidereal', 3)
tb.set_record(1); pump(3.2)                          # recording 1 done, armed for 2
fail_next["n"] = 1                                   # recording 2: no radio
pump(2 * INTERVAL + 1.5)
ev = [e for e, _ in log]
ok(ev.count("start") == 2 and ev.count("failed") == 1 and ev.count("stop") == 2, f"log {ev}")
starts = [t for e, t in log if e == "start"]
ok(abs((starts[1] - starts[0]) - 2 * INTERVAL) < 1.3, "the recording after the missed one must keep its slot")
ok(tb.record == 0 and tb._rec_series is None, "series should end after its three slots")
print("4. a missed start is skipped; the next recording keeps its place in the schedule")

# --- 5. Once is untouched ----------------------------------------------------
setup('once', 3)
tb.set_record(1); pump(3.4)
ok([e for e, _ in log] == ["start", "stop"] and tb.record == 0 and tb._rec_series is None
   and "series" not in tb._recording_status.text(), f"Once: {log} {tb._recording_status.text()!r}")
ok(not tb._rec_repeat_count_spin.isEnabled(), "the count is meaningless for Once")
print("5. Repeat: Once behaves exactly as before")

A.dses_workbench._repeat_interval_s = staticmethod(real_interval)
tb.stop(); tb.wait(); tb.close()
print(f"\nALL RECORDING-SERIES TESTS PASSED ({checks} checks)")
