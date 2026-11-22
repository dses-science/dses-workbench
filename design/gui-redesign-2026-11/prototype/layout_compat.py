"""What happens to saved window layouts when the toolbars arrive?

Answers, by running the real window (playback, private settings):
  1. a NEW window (toolbars + one tabbed column) restoring a 1.8-era
     dock_state — the prototype's own fresh one, and Rick's real one from
     %APPDATA%/DSES_Analyzer/settings.ini (read only, never written);
  2. the same new window restoring a state saved under a different version
     number (what restoreState does with a version mismatch);
  3. an OLD (unchanged) window restoring a state the NEW window saved —
     i.e. someone goes back from 1.9 to 1.8.

  layout_compat.py new   -> runs 1 and 2, writes _new_state.b64
  layout_compat.py old   -> runs 3 from _new_state.b64
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import proto_harness as H

from PySide6 import QtCore
from PySide6.QtCore import Qt

STATE_FILE = H.HERE / "_proto_settings" / "_new_state.b64"
AREAS = {Qt.LeftDockWidgetArea: "left", Qt.RightDockWidgetArea: "right",
         Qt.TopDockWidgetArea: "top", Qt.BottomDockWidgetArea: "bottom",
         Qt.NoDockWidgetArea: "none"}


def describe(win, label):
    print(f"--- {label}")
    for d in win._docks:
        tabs = [t.objectName() for t in win.tabifiedDockWidgets(d)]
        print(f"   {d.objectName():24s} area={AREAS.get(win.dockWidgetArea(d), '?'):6s}"
              f" visible={d.isVisible()!s:5s} floating={d.isFloating()!s:5s}"
              f" tabbed_with={len(tabs)}")
    from PySide6 import QtWidgets
    tbs = win.findChildren(QtWidgets.QToolBar)
    named = [t for t in tbs if t.objectName().startswith("tb_")]
    for t in named:
        print(f"   toolbar {t.objectName():16s} visible={t.isVisible()} "
              f"area={win.toolBarArea(t)}")


def real_dock_state():
    ini = Path(os.environ.get("APPDATA", "")) / "DSES_Analyzer" / "settings.ini"
    if not ini.is_file():
        return None
    import configparser
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(ini, encoding="utf-8")
    if cp.has_option("window", "dock_state"):
        return QtCore.QByteArray.fromBase64(cp.get("window", "dock_state").encode("ascii"))
    return None


def run_new():
    import proto_redesign as R
    qapp, win, pump = H.build_real_window(offscreen_grab=True)
    win.resize(1440, 900)
    pump(1.5)
    old_state = win.saveState()            # what 1.8 would have saved (version 0)
    R.apply(win, "A")
    pump(1.0)
    describe(win, "NEW window, its own default arrangement")
    new_state = win.saveState()
    STATE_FILE.write_bytes(bytes(new_state.toBase64()))

    ok = win.restoreState(old_state)
    pump(0.5)
    describe(win, f"NEW window after restoreState(1.8 default state) -> {ok}")

    real = real_dock_state()
    if real is not None:
        win.restoreState(new_state); pump(0.3)
        ok = win.restoreState(real)
        pump(0.5)
        describe(win, f"NEW window after restoreState(Rick's real 1.8 state) -> {ok}")

    win.restoreState(new_state); pump(0.3)
    v2 = win.saveState(2)
    ok = win.restoreState(v2, 0)
    print(f"--- restoreState(state saved as version 2, asked for version 0) -> {ok}")
    ok = win.restoreState(v2, 2)
    print(f"--- restoreState(version 2, version 2) -> {ok}")
    H.shutdown(win)


def run_old():
    qapp, win, pump = H.build_real_window(offscreen_grab=True)
    win.resize(1440, 900)
    pump(1.5)
    describe(win, "OLD window, default")
    st = QtCore.QByteArray.fromBase64(STATE_FILE.read_bytes())
    ok = win.restoreState(st)
    pump(0.5)
    describe(win, f"OLD window after restoreState(state saved by the NEW window) -> {ok}")
    H.shutdown(win)


if __name__ == "__main__":
    (run_old if (len(sys.argv) > 1 and sys.argv[1] == "old") else run_new)()
