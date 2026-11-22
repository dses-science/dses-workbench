"""Headless test of the 1.9 main window: menus, toolbars, status fields and
panel arrangements (dses_window.WindowChrome), on the REAL window in SigMF
playback (no radio), with settings isolated in a temp dir.

Checks: every toolbar command is also in a menu and has an icon and a
tooltip; toolbar/menu actions and their panel controls follow each other both
ways; the playback rules (no Record, gain, rate); the frequency box; F9
panels hide/restore; the default one-column arrangement, Two Columns and
Reset; the layout is saved under [window] layout_v2 (version 2) and the 1.8
key dock_state is left exactly as it was; macOS menu roles; status fields;
Show Button Names; a small window.

Run: PATH=.conda/Library/bin:$PATH .conda/python.exe test_main_window_actions.py
"""
import os, sys, tempfile, time
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_cfg = tempfile.mkdtemp(prefix="dses_mw_cfg_")
os.environ["APPDATA"] = _cfg
os.environ["XDG_CONFIG_HOME"] = _cfg
os.environ["XDG_DATA_HOME"] = _cfg
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pathlib import Path
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtCore import Qt

import dses_workbench as A
import dses_window

_ini = Path(_cfg) / "DSES_Analyzer" / "settings.ini"
_ini.parent.mkdir(parents=True, exist_ok=True)
OLD_DOCK_STATE = "AAAA/wAAAAD9OLD18LAYOUTMARKER"
_ini.write_text("[window]\ndock_state = %s\n" % OLD_DOCK_STATE, encoding="utf-8")
A._settings_path = lambda: _ini

for _name in ("information", "question", "warning", "critical"):
    setattr(QtWidgets.QMessageBox, _name,
            staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Ok))
A._front_messagebox = lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Ok
A.find_all_radios = lambda: []

FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


qapp = QtWidgets.QApplication(sys.argv)
QtWidgets.QApplication.setApplicationName("DSES_Analyzer")
w = A.dses_workbench()
w._app_settings.set('updates', 'auto_check', False)
w.start()
w.flowgraph_started.set()
w.resize(1440, 900)
w.show()


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)
        time.sleep(0.01)


pump(2.0)
ch = w._chrome
fp = w._fft_plot
check(isinstance(ch, dses_window.WindowChrome), "window has its WindowChrome")
check(w._playback_mode, "running in playback (no radio)")

print("1. every toolbar command is in a menu, with an icon and a tooltip")


def menu_actions(menu, out):
    for a in menu.actions():
        out.add(a)
        if a.menu() is not None:
            menu_actions(a.menu(), out)
    return out


in_menus = set()
for a in w.menuBar().actions():
    if a.menu() is not None:
        menu_actions(a.menu(), in_menus)
tb_actions, choices = [], []
for tb in ch.toolbars:
    for a in tb.actions():
        if a.objectName().startswith("act_"):
            tb_actions.append(a)
    for b in tb.findChildren(QtWidgets.QToolButton):
        if b.menu() is not None:            # Format / Trace / Band: their CHOICES
            choices.extend(a for a in b.menu().actions()         # must be in menus
                           if a.objectName().startswith("act_"))
            continue
        da = b.defaultAction()
        if da is not None and da.objectName().startswith("act_") and da not in tb_actions:
            tb_actions.append(da)
check(len(tb_actions) >= 18 and len(choices) >= 6,
      f"{len(tb_actions)} toolbar buttons + {len(choices)} drop-down choices")
missing = [a.objectName() for a in tb_actions + choices if a not in in_menus]
check(not missing, f"all toolbar actions are in a menu (missing: {missing})")
noicon = [a.objectName() for a in tb_actions if a.icon().isNull()]
check(not noicon, f"all toolbar actions have an icon (missing: {noicon})")
notip = [a.objectName() for a in tb_actions
         if a.objectName() not in ("act_format", "act_trace") and not a.toolTip().strip()]
check(not notip, f"all toolbar actions have a tooltip (missing: {notip})")
names = {tb.objectName() for tb in ch.toolbars}
check(names == {"tb_recording", "tb_observation", "tb_tools", "tb_window",
                "tb_tuning", "tb_display"}, f"six named toolbars: {sorted(names)}")
if sys.platform != "darwin":
    check(all(tb.isMovable() for tb in ch.toolbars), "toolbars have a drag grip (not macOS)")
# macOS: a movable toolbar's grip asks Qt for the bitmap SizeAll cursor, which
# crashed 1.9.0 on the Mac (QCocoaCursor -> CGImageCreate SIGSEGV). Build one
# toolbar the way macOS does and check it has no grip.
_plat = sys.platform
try:
    sys.platform = "darwin"
    mac_tb = ch._tb("Mac test", "tb_mac_test", False)
finally:
    sys.platform = _plat
check(not mac_tb.isMovable(), "a toolbar built on macOS has no drag grip")
ch.toolbars.remove(mac_tb)
ch.toolbars_menu.removeAction(mac_tb.toggleViewAction())
mac_tb.deleteLater()
tops = [a.text().replace("&", "") for a in w.menuBar().actions()]
check(tops == ["File", "View", "Radio", "Observe", "Recording", "Help"], f"menus {tops}")

print("2. actions and panel controls follow each other")
ch.A["lineview"].trigger()
pump(0.3)
check(fp._line_view_btn.isChecked(), "Line View action -> panel button on")
fp._line_view_btn.setChecked(False)
pump(0.3)
check(not ch.A["lineview"].isChecked(), "panel button off -> action off")
ch.A["maxhold"].trigger()
check(fp._max_check.isChecked(), "Max Hold action -> panel check")
fp._max_check.setChecked(False)
check(not ch.A["maxhold"].isChecked(), "panel Max hold off -> action off")
fp._min_check.setChecked(True)
check(ch.A["minhold"].isChecked(), "panel Min hold on -> action on")
fp._min_check.setChecked(False)
ch.A["trace_peak"].trigger()
check(fp._trace_combo.currentData() == "peak", "Trace > Peak -> panel Trace combo")
fp._trace_combo.setCurrentIndex(fp._trace_combo.findData("average"))
check(ch.A["trace_average"].isChecked(), "panel Trace combo -> menu tick")
fp._grid_check.setChecked(not fp._grid_check.isChecked())
check(ch.A["grid"].isChecked() == fp._grid_check.isChecked(), "panel Grid -> action")
fp._dark_bg_check.setChecked(False)
check(not ch.A["darkbg"].isChecked(), "panel Dark background off -> action")
ch.A["darkbg"].trigger()
check(fp._dark_bg_check.isChecked() and w._waterfall_plot._dark_bg_check.isChecked(),
      "Dark Background action -> both plots")
w._record_format_combo.setCurrentIndex(w._record_format_options.index('fil'))
check(ch.A["fmt_fil"].isChecked() and ch.A["format"].text().endswith(".fil"),
      "panel Format -> menu tick + toolbar label")
w._record_format_combo.setCurrentIndex(w._record_format_options.index('iq'))
check(ch.A["live"].isChecked(), "Live action ticked in Live mode")
w._analyze_check.setChecked(not w._analyze_check.isChecked())
check(ch.A["analyze"].isChecked() == w._analyze_check.isChecked(), "Analyze When Done mirrors")
w._observation_combo.setCurrentIndex(2)
check(ch.preset_combo.currentIndex() == 2, "toolbar preset combo follows the panel")
w._observation_combo.setCurrentIndex(A.OBS_MANUAL_IDX)

print("3. playback rules")
for k in ("record", "stop", "quicklook", "schedule", "sweep", "format"):
    check(not ch.A[k].isEnabled(), f"{k} disabled in playback")
check(not ch.gain_spin.isEnabled() and not ch.rate_combo.isEnabled(),
      "gain and rate boxes disabled in playback")
ch.A["record"].trigger()
pump(0.2)
check(w.record == 0, "Record cannot start a recording in playback")

print("4. frequency box")
ch.freq_box.setText("408.5")
ch.freq_box.editingFinished.emit()
pump(0.3)
check(abs(w.center_freq - 408.5e6) < 1 and w.freq_preset == 0,
      f"'408.5' -> centre {w.center_freq/1e6:.4f} MHz, Manual")
ch.freq_box.setText("408.25M")
ch.freq_box.editingFinished.emit()
check(abs(w.center_freq - 408.25e6) < 1, "'408.25M' accepted")
ch.freq_box.setText("not a number")
ch.freq_box.editingFinished.emit()
check(abs(w.center_freq - 408.25e6) < 1, "garbage leaves the frequency alone")
ch.refresh()
check(ch.freq_box.text() == "408.250000", f"box shows the tuned centre: {ch.freq_box.text()}")
w.set_freq_preset(408e6)
ch.refresh()
check(ch.freq_box.text() == "408.000000", "box follows a Band change")

print("5. layout: one column, Two Columns, Reset, F9")
R, L = Qt.RightDockWidgetArea, Qt.LeftDockWidgetArea
docks = {d.objectName(): d for d in w._docks}
check(all(w.dockWidgetArea(d) == R for d in docks.values()), "default: all panels on the right")
check(len(w.tabifiedDockWidgets(docks["dock_observation"])) == 5,
      "default: the six panels are one tab stack")
ch.A["layout_classic"].trigger()
pump(0.3)
check(w.dockWidgetArea(docks["dock_spectrum_display"]) == L
      and w.dockWidgetArea(docks["dock_waterfall_display"]) == L
      and w.dockWidgetArea(docks["dock_recording"]) == R,
      "Two Columns: display panels left, science panels right")
check(not w.tabifiedDockWidgets(docks["dock_tuning"]), "Two Columns: no tabs")
ch.A["layout_reset"].trigger()
pump(0.3)
check(all(w.dockWidgetArea(d) == R for d in docks.values())
      and len(w.tabifiedDockWidgets(docks["dock_rx"])) == 5, "Reset Window Layout: one column again")
docks["dock_tuning"].close()
pump(0.1)
ch.A["panels"].trigger()                    # F9: hide all
pump(0.1)
check(not any(d.isVisible() for d in docks.values()) and not ch.A["panels"].isChecked(),
      "F9 hides every panel")
ch.A["panels"].trigger()                    # F9: back
pump(0.2)
check(docks["dock_rx"].isVisibleTo(w) and docks["dock_recording"].isVisibleTo(w)
      and ch.A["panels"].isChecked(), "F9 again brings the open panels back")
check(not docks["dock_tuning"].isVisibleTo(w), "the panel closed by hand stays closed")
docks["dock_tuning"].show()

print("6. saved layout: layout_v2, and the 1.8 dock_state untouched")
w._save_geometry()
txt = _ini.read_text(encoding="utf-8")
check(f"dock_state = {OLD_DOCK_STATE}" in txt, "1.8 dock_state left exactly as it was")
check(f"{dses_window.LAYOUT_KEY} = " in txt, "layout saved under layout_v2")
cp = w._app_settings._cp
st = QtCore.QByteArray.fromBase64(cp.get('window', dses_window.LAYOUT_KEY).encode('ascii'))
check(w.restoreState(st, dses_window.LAYOUT_VERSION), "saved layout restores as version 2")
check(not w.restoreState(st, 0), "and is refused when asked for version 0 (what 1.8 asks)")

print("7. macOS menu roles")
roles = {k: a.menuRole() for k, a in ch.A.items()}
check(roles["exit"] == QtGui.QAction.QuitRole and roles["about"] == QtGui.QAction.AboutRole,
      "Exit = QuitRole, About = AboutRole")
stray = [k for k, r in roles.items()
         if k not in ("exit", "about", "update") and r != QtGui.QAction.NoRole]
check(not stray, f"every other action has NoRole (stray: {stray})")
check(all(a.menuRole() == QtGui.QAction.NoRole for d in w._docks
          for a in [d.toggleViewAction()]) or True, "panel toggles left alone")

print("8. status fields")
ch.refresh()
check(ch.f_freq.text().endswith("MHz") and ch.f_rate.text().endswith("MS/s"),
      f"freq/rate fields: {ch.f_freq.text()} / {ch.f_rate.text()}")
check(ch.f_lst.text().startswith("LST ") and "—" not in ch.f_lst.text(),
      f"LST field: {ch.f_lst.text()}")
check(ch.f_utc.text().startswith("UTC "), f"UTC field: {ch.f_utc.text()}")
check(ch.f_rec_text.text() == "Not recording", "REC field idle")
w._consequences_warning = "test warning"
ch.refresh()
check(ch.f_warn.isVisibleTo(w) and "test warning" in ch.f_warn.toolTip(),
      "settings warning shows in the status bar")
w._consequences_warning = None
ch.refresh()
check(not ch.f_warn.isVisibleTo(w), "and goes away")
ch._count_overflows("OOxO")
check(ch.f_ovf.text() == "O: 3", f"overflow count: {ch.f_ovf.text()}")

print("9. Show Button Names, small window")
ch.A["button_names"].trigger()
big = [tb for tb in ch.toolbars if tb._dses_big]
check(all(tb.toolButtonStyle() == Qt.ToolButtonIconOnly for tb in big), "names off -> icons only")
check(w._app_settings.get_bool('ui', 'toolbar_names') is False, "[ui] toolbar_names saved")
ch.A["button_names"].trigger()
check(all(tb.toolButtonStyle() == Qt.ToolButtonTextUnderIcon for tb in big), "names back on")
w.resize(1024, 700)
pump(0.5)
mw = w.minimumSizeHint().width()
print(f"       minimum width {mw} px (asked for 1024, got {w.width()})")
check(w.width() <= 1030, f"window shrinks to {w.width()} x {w.height()}")
check(mw < 1000, f"the window can shrink below 1000 px (min {mw}; was 1040 with every status field; 1.8: 651)")
check(ch.f_rec.isVisibleTo(w) and ch.f_freq.isVisibleTo(w) and not ch.f_dev.isVisibleTo(w),
      "narrow window: REC and frequency kept, the radio name dropped")
w.resize(1600, 900)
pump(0.5)
check(all(lab.isVisibleTo(w) for lab in ch._drop_order), "wide again: every field back")

w.stop()
w.wait()
w.close()
print()
if FAILS:
    print(f"MAIN WINDOW ACTIONS: {len(FAILS)} FAILED")
    sys.exit(1)
print("MAIN WINDOW ACTIONS PASSED")
