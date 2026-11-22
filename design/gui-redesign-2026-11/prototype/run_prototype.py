"""Open the prototype on screen so it can be clicked.

  run_prototype.py A     two toolbar rows, text under the big icons (recommended)
  run_prototype.py A2    one toolbar row, icons only
  run_prototype.py B     ribbon
  run_prototype.py old   the window as it is today, for comparison

Playback of the bundled sample only (no radio is opened), with a private
settings file under prototype/_proto_settings/ — your real settings.ini is
never read or written. Every toolbar and menu item drives the same control
as the panels; Record and gain report "not available in playback".

From the repo root:
  PATH="$PWD/.conda/Library/bin:$PWD/.conda:$PATH" ./.conda/python.exe \
      design/gui-redesign-2026-11/prototype/run_prototype.py A
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import proto_harness as H

from PySide6 import QtWidgets


def main(which):
    qapp, win, pump = H.build_real_window(show=False)
    win.resize(1440, 900)
    if which != "old":
        import proto_redesign as R
        win._proto = R.apply(win, which)
        win.setWindowTitle(win.windowTitle() + f"   [GUI PROTOTYPE {which}]")
    win.show()
    qapp.exec()
    H.shutdown(win)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "A")
