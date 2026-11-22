"""Shared start-up for the GUI-redesign prototype (throwaway, design step only).

Builds the REAL main window from dses_workbench.py without changing that file:
  * settings are read from and written to a PRIVATE prototype settings.ini
    (design/gui-redesign-2026-11/prototype/_proto_settings/), never the
    user's real one;
  * with force_playback=True no radio is opened: the bundled SigMF sample is
    played back, so the prototype can run beside another session's radio.

PySide6 only (CLAUDE.md). The pyqtgraph backend is forced before any Qt import.
"""
import os
import sys
from pathlib import Path

os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
PROTO_CFG = HERE / "_proto_settings" / "DSES_Analyzer"

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))


def build_real_window(force_playback=True, fresh_settings=True, show=True,
                      offscreen_grab=False):
    """Return (qapp, window, pump). pump(seconds) runs the event loop."""
    import time
    from PySide6 import QtWidgets, QtCore
    import dses_workbench as A

    PROTO_CFG.mkdir(parents=True, exist_ok=True)
    ini = PROTO_CFG / "settings.ini"
    if fresh_settings and ini.exists():
        ini.unlink()
    A._settings_path = lambda: ini

    if force_playback:
        # Never open a radio from the prototype; answer the "no radio ->
        # playback" notice by itself.
        A.find_all_radios = lambda: []
        A._front_messagebox = (
            lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Ok)
        for _n in ("information", "question", "warning", "critical"):
            setattr(QtWidgets.QMessageBox, _n, staticmethod(
                lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Ok))

    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    QtWidgets.QApplication.setApplicationName("DSES_Analyzer")
    QtWidgets.QApplication.setApplicationDisplayName(A.APP_NAME)

    win = A.dses_workbench()
    # The prototype never checks for or installs updates.
    try:
        win._app_settings.set('updates', 'auto_check', False)
    except Exception:
        pass
    if offscreen_grab:
        # Render with the native style and fonts, without putting a window
        # on the user's screen.
        win.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)
    win.start()
    win.flowgraph_started.set()
    if show:
        win.show()

    def pump(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            qapp.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)
            time.sleep(0.01)

    return qapp, win, pump


def shutdown(win):
    try:
        win.stop()
        win.wait()
    except Exception:
        pass
    # Do not let closeEvent write a layout the prototype invented into any
    # settings file that matters: the private ini is the only target anyway.
    win.close()
