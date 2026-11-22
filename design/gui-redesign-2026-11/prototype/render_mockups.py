"""Render the proposed main window, one variant per process.

  render_mockups.py A        -> mockups/01_A_two_toolbars.png
  render_mockups.py A2       -> mockups/02_A2_one_icon_toolbar.png
  render_mockups.py B        -> mockups/03_B_ribbon.png
  render_mockups.py menus    -> mockups/04_menus.png (every top menu opened)

Run from the repo root with the project env:
  PATH="$PWD/.conda/Library/bin:$PWD/.conda:$PATH" ./.conda/python.exe \
      design/gui-redesign-2026-11/prototype/render_mockups.py A
Playback, private settings, no radio, nothing on screen.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import proto_harness as H  # sets PYQTGRAPH_QT_LIB first

from PySide6 import QtCore, QtGui, QtWidgets

OUT = H.HERE.parent / "mockups"
OUT.mkdir(exist_ok=True)
NAMES = {"A": "01_A_two_toolbars.png", "A2": "02_A2_one_icon_toolbar.png",
         "B": "03_B_ribbon.png"}


def main(which):
    import proto_redesign as R
    qapp, win, pump = H.build_real_window(offscreen_grab=True)
    win.resize(1440, 900)
    pump(1.0)
    variant = "A" if which == "menus" else which
    red = R.apply(win, variant, illustrate_rec=(which == "A"))
    pump(4.0)
    if which == "menus":
        render_menus(red)
    else:
        pm = win.grab()
        dst = OUT / NAMES[which]
        pm.save(str(dst))
        print("saved", dst, pm.width(), "x", pm.height())
    H.shutdown(win)


def render_menus(red):
    """Every top-level menu, opened, side by side, with the submenus the
    reader needs (View > Trace, Radio > Band, Observe > Preset, Recording >
    Format) drawn next to their parent."""
    tops = red.top_menus
    want_sub = {"View": "Trace", "Radio": "Band", "Observe": "Preset",
                "Recording": "Format"}
    shots = []
    for m in tops:
        title = m.title().replace("&", "")
        pair = [grab_menu(m)]
        sub_title = want_sub.get(title)
        if sub_title:
            for sub in m.findChildren(QtWidgets.QMenu):
                if sub.title().replace("&", "") == sub_title:
                    pair.append(grab_menu(sub))
                    break
        shots.append((title, pair))
    pad, head = 18, 34
    widths = [sum(p.width() for p in pair) + 6 * (len(pair) - 1) for _, pair in shots]
    h = max(max(p.height() for p in pair) for _, pair in shots) + head + pad
    w = sum(widths) + pad * (len(shots) + 1)
    img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor("#eef2f5"))
    p = QtGui.QPainter(img)
    f = QtGui.QFont("Segoe UI", 11); f.setBold(True); p.setFont(f)
    x = pad
    for (title, pair), cw in zip(shots, widths):
        p.setPen(QtGui.QColor("#1e3a5f"))
        p.drawText(x, 24, title)
        xx = x
        for i, pm in enumerate(pair):
            p.drawPixmap(xx, head, pm)
            xx += pm.width() + 6
        x += cw + pad
    p.end()
    dst = OUT / "04_menus.png"
    img.save(str(dst))
    print("saved", dst, w, "x", h)


def grab_menu(menu):
    menu.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)
    menu.popup(QtCore.QPoint(0, 0))
    QtWidgets.QApplication.processEvents()
    pm = menu.grab()
    menu.hide()
    return pm


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "A")
