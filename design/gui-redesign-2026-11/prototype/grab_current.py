"""Grab the CURRENT main window (no redesign) to mockups/00_current_window.png.

Run from the repo root:
  PATH=.conda/Library/bin:$PATH .conda/python.exe design/gui-redesign-2026-11/prototype/grab_current.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import proto_harness as H

OUT = H.HERE.parent / "mockups"
OUT.mkdir(exist_ok=True)

qapp, win, pump = H.build_real_window(offscreen_grab=True)
win.resize(1440, 900)
pump(4.0)
pm = win.grab()
dst = OUT / "00_current_window.png"
pm.save(str(dst))
print("saved", dst, pm.width(), "x", pm.height(),
      "menus:", [a.text() for a in win.menuBar().actions()])
H.shutdown(win)
