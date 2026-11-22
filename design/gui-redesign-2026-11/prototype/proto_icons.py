"""Toolbar icons DRAWN in code (design prototype for the GUI redesign).

Why drawn: the dock title-bar icons in dses_workbench.py are drawn because
unicode glyphs came out wrong or missing on macOS and Linux (2026-08-04).
Same rule here: no font glyphs, no image files, no SVG plugin. Each icon is a
few QPainter strokes on a 24 x 24 grid, rendered at several pixel sizes so Qt
picks a sharp one at any display scale. Nothing to license and nothing to add
to the release zip except this one module.

    from proto_icons import icon
    act.setIcon(icon("record"))

Run this file to write the contact sheet: mockups/icon_sheet.png
"""
import math
import os

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
from PySide6 import QtCore, QtGui
from PySide6.QtCore import Qt, QPointF, QRectF

NAVY = "#1e3a5f"     # dock-header navy (already the house ink colour)
TEAL = "#156082"     # DSES teal
RED = "#d93025"
GREEN = "#1f9d55"
AMBER = "#c77700"
PALE = "#dbeafe"     # dock-header pastel
SIZES = (16, 20, 24, 32, 48, 64)


def _pen(p, color=NAVY, w=1.8, cap=Qt.RoundCap):
    pen = QtGui.QPen(QtGui.QColor(color))
    pen.setWidthF(w)
    pen.setCapStyle(cap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)


def _poly(p, pts):
    p.drawPolyline(QtGui.QPolygonF([QPointF(x, y) for x, y in pts]))


def _fill(p, color):
    p.setBrush(QtGui.QBrush(QtGui.QColor(color)))


def _arrow_head(p, x, y, dx, dy, s=3.0):
    """Two short strokes forming an arrow head at (x, y) pointing (dx, dy)."""
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n
    for sgn in (1, -1):
        bx = x - s * ux + sgn * s * 0.75 * (-uy)
        by = y - s * uy + sgn * s * 0.75 * (ux)
        p.drawLine(QPointF(x, y), QPointF(bx, by))


# ---------------------------------------------------------------- recording
def i_record(p):
    _pen(p, "#8f1d15", 1.4); _fill(p, RED)
    p.drawEllipse(QPointF(12, 12), 7.2, 7.2)


def i_stop(p):
    _pen(p, NAVY, 1.4); _fill(p, NAVY)
    p.drawRoundedRect(QRectF(6, 6, 12, 12), 1.5, 1.5)


def i_quicklook(p):
    _pen(p, NAVY, 2.0)
    p.drawEllipse(QPointF(10, 10), 6.5, 6.5)
    p.drawLine(QPointF(15, 15), QPointF(20.5, 20.5))
    _pen(p, TEAL, 1.5)
    _poly(p, [(6, 11.5), (8.6, 11.5), (10, 7), (11.4, 11.5), (14, 11.5)])


def i_folder(p):
    path = QtGui.QPainterPath()
    path.moveTo(3, 7); path.lineTo(9, 7); path.lineTo(11, 9.5)
    path.lineTo(21, 9.5); path.lineTo(21, 19); path.lineTo(3, 19)
    path.closeSubpath()
    _pen(p, NAVY, 1.6); _fill(p, "#f6d98b")
    p.drawPath(path)


def i_folder_set(p):
    i_folder(p)
    _pen(p, TEAL, 1.8)
    p.drawLine(QPointF(12, 12), QPointF(12, 17)); _arrow_head(p, 12, 17, 0, 1, 2.4)


def i_format(p):
    path = QtGui.QPainterPath()
    path.moveTo(6, 3); path.lineTo(14, 3); path.lineTo(18, 7)
    path.lineTo(18, 21); path.lineTo(6, 21); path.closeSubpath()
    _pen(p, NAVY, 1.6); _fill(p, "#ffffff")
    p.drawPath(path)
    _poly(p, [(14, 3), (14, 7), (18, 7)])
    _pen(p, TEAL, 1.5)
    for y in (11, 14, 17):
        p.drawLine(QPointF(8.5, y), QPointF(15.5, y))


def i_schedule(p):
    _pen(p, NAVY, 1.8)
    p.drawEllipse(QPointF(12, 13), 7.5, 7.5)
    _pen(p, TEAL, 1.8)
    _poly(p, [(12, 8.5), (12, 13), (15.5, 15)])
    _pen(p, NAVY, 1.8)
    p.drawLine(QPointF(9.5, 3), QPointF(14.5, 3))


# -------------------------------------------------------------- observation
def i_preset(p):
    """A dish: the 'what am I observing' selector."""
    p.save()
    p.translate(12, 11); p.rotate(-35); p.translate(-12, -11)
    bowl = QtGui.QPainterPath()
    bowl.moveTo(4, 11)
    bowl.arcTo(QRectF(4, 5, 16, 12), 180, 180)
    bowl.closeSubpath()
    _pen(p, NAVY, 1.6); _fill(p, PALE)
    p.drawPath(bowl)
    _pen(p, TEAL, 1.5)
    p.drawLine(QPointF(12, 11), QPointF(12, 5))
    _fill(p, TEAL); p.drawEllipse(QPointF(12, 4.5), 1.2, 1.2)
    p.restore()
    _pen(p, NAVY, 1.7)
    _poly(p, [(8, 21), (12.5, 16), (17, 21)])
    p.drawLine(QPointF(6, 21), QPointF(19, 21))


def i_live(p):
    _pen(p, TEAL, 1.9)
    _poly(p, [(3, 17), (8, 16.5), (10.5, 15), (12, 5.5), (13.5, 15),
              (16, 16.5), (21, 17)])
    _pen(p, NAVY, 1.4)
    p.drawLine(QPointF(3, 20.5), QPointF(21, 20.5))


def i_sweep(p):
    _pen(p, AMBER, 1.7)
    _poly(p, [(3, 12), (5, 7), (7, 12)])
    _poly(p, [(9, 12), (11.5, 4.5), (14, 12)])
    _poly(p, [(16, 12), (18.5, 8), (21, 12)])
    _pen(p, NAVY, 1.7)
    p.drawLine(QPointF(4, 18), QPointF(20, 18))
    _arrow_head(p, 20, 18, 1, 0, 3.0); _arrow_head(p, 4, 18, -1, 0, 3.0)


def i_planner(p):
    """A pulsar above the horizon: disc with two opposed beams."""
    _pen(p, TEAL, 1.2); _fill(p, "#7fc4dd")
    for sgn in (1, -1):
        tri = QtGui.QPolygonF([QPointF(12, 9),
                               QPointF(12 + sgn * 8.5, 9 - sgn * 4.0),
                               QPointF(12 + sgn * 7.0, 9 - sgn * 7.0)])
        p.drawPolygon(tri)
    _pen(p, NAVY, 1.5); _fill(p, NAVY)
    p.drawEllipse(QPointF(12, 9), 2.6, 2.6)
    _pen(p, NAVY, 1.7)
    hz = QtGui.QPainterPath(); hz.moveTo(2, 21); hz.quadTo(12, 15, 22, 21)
    p.drawPath(hz)


def i_selftest(p):
    _pen(p, NAVY, 1.6); _fill(p, "#ffffff")
    p.drawRoundedRect(QRectF(3, 7, 13, 11), 1.5, 1.5)
    _pen(p, NAVY, 1.4)
    p.drawLine(QPointF(6, 4), QPointF(6, 7)); p.drawLine(QPointF(9.5, 4), QPointF(9.5, 7))
    p.drawLine(QPointF(13, 4), QPointF(13, 7))
    _pen(p, GREEN, 2.6)
    _poly(p, [(10, 14.5), (14, 19), (21.5, 9)])


def i_driftscan(p):
    _pen(p, NAVY, 1.6)
    _poly(p, [(4, 3.5), (4, 20), (21, 20)])
    _pen(p, TEAL, 2.0)
    c = QtGui.QPainterPath(); c.moveTo(5.5, 18)
    c.cubicTo(10, 18, 10, 6.5, 13, 6.5); c.cubicTo(16, 6.5, 16, 18, 20.5, 18)
    p.drawPath(c)
    pen = QtGui.QPen(QtGui.QColor(AMBER)); pen.setWidthF(1.2)
    pen.setStyle(Qt.DashLine); p.setPen(pen)
    p.drawLine(QPointF(13, 4), QPointF(13, 19.5))


# -------------------------------------------------------------------- radio
def i_device(p):
    _pen(p, NAVY, 1.6); _fill(p, "#ffffff")
    p.drawRoundedRect(QRectF(3, 10, 18, 10), 1.8, 1.8)
    p.drawLine(QPointF(7, 10), QPointF(5, 3))
    _pen(p, TEAL, 1.5)
    p.drawEllipse(QPointF(16, 15), 2.4, 2.4)
    p.drawLine(QPointF(6, 13.5), QPointF(11, 13.5))
    p.drawLine(QPointF(6, 16.5), QPointF(11, 16.5))


def i_band(p):
    _pen(p, NAVY, 1.7)
    p.drawLine(QPointF(3, 16), QPointF(21, 16))
    for x, h in ((3, 4), (7.5, 2.5), (12, 4), (16.5, 2.5), (21, 4)):
        p.drawLine(QPointF(x, 16), QPointF(x, 16 + h))
    _pen(p, RED, 1.2); _fill(p, RED)
    p.drawPolygon(QtGui.QPolygonF([QPointF(11, 5), QPointF(17, 5), QPointF(14, 13)]))


def i_gain(p):
    _pen(p, NAVY, 1.7); _fill(p, "#ffffff")
    p.drawPolygon(QtGui.QPolygonF([QPointF(6, 4), QPointF(6, 20), QPointF(20, 12)]))
    _pen(p, TEAL, 1.6)
    p.drawLine(QPointF(8.5, 12), QPointF(13.5, 12)); p.drawLine(QPointF(11, 9.5), QPointF(11, 14.5))
    _pen(p, NAVY, 1.7)
    p.drawLine(QPointF(2, 12), QPointF(6, 12)); p.drawLine(QPointF(20, 12), QPointF(22.5, 12))


def i_step_left(p):
    _pen(p, NAVY, 2.2); _poly(p, [(14.5, 6), (8.5, 12), (14.5, 18)])


def i_step_right(p):
    _pen(p, NAVY, 2.2); _poly(p, [(9.5, 6), (15.5, 12), (9.5, 18)])


# ------------------------------------------------------------------ display
def i_lineview(p):
    _pen(p, TEAL, 1.9)
    c = QtGui.QPainterPath(); c.moveTo(6.5, 17)
    c.cubicTo(10.5, 17, 10.5, 6, 12, 6); c.cubicTo(13.5, 6, 13.5, 17, 17.5, 17)
    p.drawPath(c)
    _pen(p, NAVY, 1.7)
    _poly(p, [(6, 4), (3.5, 4), (3.5, 20), (6, 20)])
    _poly(p, [(18, 4), (20.5, 4), (20.5, 20), (18, 20)])


def i_autoscale(p):
    _pen(p, NAVY, 1.8)
    p.drawLine(QPointF(5.5, 4), QPointF(5.5, 20))
    _arrow_head(p, 5.5, 4, 0, -1, 3.0); _arrow_head(p, 5.5, 20, 0, 1, 3.0)
    _pen(p, TEAL, 1.8)
    _poly(p, [(10, 16), (13, 8.5), (16, 13), (20.5, 6)])


def i_autoscale_wf(p):
    g = QtGui.QLinearGradient(0, 4, 0, 20)
    g.setColorAt(0.0, QtGui.QColor("#fde725")); g.setColorAt(0.5, QtGui.QColor("#21918c"))
    g.setColorAt(1.0, QtGui.QColor("#440154"))
    _pen(p, NAVY, 1.3); p.setBrush(QtGui.QBrush(g))
    p.drawRect(QRectF(3, 4, 11, 16))
    _pen(p, NAVY, 1.8)
    p.drawLine(QPointF(19, 5), QPointF(19, 19))
    _arrow_head(p, 19, 5, 0, -1, 2.8); _arrow_head(p, 19, 19, 0, 1, 2.8)


_JAG = [(3, 0), (6, -3), (9, 1), (12, -5), (15, 0), (18, -4), (21, -1)]


def i_maxhold(p):
    _pen(p, NAVY, 1.4); _poly(p, [(x, 17 + y) for x, y in _JAG])
    _pen(p, GREEN, 2.0); _poly(p, [(3, 9), (8, 7.5), (12, 5), (17, 6.5), (21, 8)])


def i_minhold(p):
    _pen(p, NAVY, 1.4); _poly(p, [(x, 10 + y) for x, y in _JAG])
    _pen(p, RED, 2.0); _poly(p, [(3, 17), (8, 18.5), (12, 20), (17, 18.5), (21, 17.5)])


def i_reset(p):
    _pen(p, NAVY, 2.0)
    p.drawArc(QRectF(5, 5, 14, 14), 60 * 16, 290 * 16)
    a = math.radians(60)
    x, y = 12 + 7 * math.cos(a), 12 - 7 * math.sin(a)
    _arrow_head(p, x, y, 1.0, 0.45, 3.4)


def i_trace(p):
    """Trace detector: a faint thick band (peak envelope) with the mean line."""
    c = QtGui.QPainterPath(); c.moveTo(3, 14)
    c.cubicTo(8, 8, 10, 18, 14, 11); c.cubicTo(16, 8, 19, 12, 21, 9)
    pen = QtGui.QPen(QtGui.QColor(21, 96, 130, 70)); pen.setWidthF(7.0)
    pen.setCapStyle(Qt.RoundCap); p.setPen(pen); p.setBrush(Qt.NoBrush)
    p.drawPath(c)
    _pen(p, NAVY, 1.7); p.drawPath(c)


def i_markers_clear(p):
    _pen(p, AMBER, 2.0)
    p.drawLine(QPointF(4, 14), QPointF(14, 14)); p.drawLine(QPointF(9, 9), QPointF(9, 19))
    _pen(p, RED, 2.0)
    p.drawLine(QPointF(15, 4), QPointF(21, 10)); p.drawLine(QPointF(21, 4), QPointF(15, 10))


def i_home(p):
    """Reset axes: back to the full span."""
    _pen(p, NAVY, 1.8)
    _poly(p, [(3, 12), (12, 4), (21, 12)])
    _poly(p, [(6, 10.5), (6, 20), (18, 20), (18, 10.5)])
    _pen(p, TEAL, 1.6); _poly(p, [(10.5, 20), (10.5, 14.5), (13.5, 14.5), (13.5, 20)])


def i_darkbg(p):
    _pen(p, NAVY, 1.7); _fill(p, "#ffffff")
    p.drawEllipse(QPointF(12, 12), 8, 8)
    half = QtGui.QPainterPath(); half.moveTo(12, 4)
    half.arcTo(QRectF(4, 4, 16, 16), 90, 180); half.closeSubpath()
    _fill(p, NAVY); p.drawPath(half)


def i_grid(p):
    _pen(p, NAVY, 1.3)
    p.drawRect(QRectF(4, 4, 16, 16))
    for v in (9.3, 14.7):
        p.drawLine(QPointF(v, 4), QPointF(v, 20)); p.drawLine(QPointF(4, v), QPointF(20, v))


# ------------------------------------------------------------------- window
def i_panels(p):
    _pen(p, NAVY, 1.6); _fill(p, "#ffffff")
    p.drawRect(QRectF(3, 5, 18, 14))
    _fill(p, PALE); p.drawRect(QRectF(14.5, 5, 6.5, 14))


def i_layout_reset(p):
    """The window-with-side-panel mark, smaller, with a turn-back arrow
    above it (the same arrow as 'reset')."""
    _pen(p, NAVY, 1.5); _fill(p, "#ffffff")
    p.drawRect(QRectF(2.5, 9, 14, 11))
    _fill(p, PALE); p.drawRect(QRectF(11.5, 9, 5, 11))
    _pen(p, TEAL, 1.8)
    p.drawArc(QRectF(13, 2, 8, 8), 90 * 16, 270 * 16)
    _arrow_head(p, 17, 2, -1.0, 0.0, 2.6)


def i_fullscreen(p):
    _pen(p, NAVY, 2.0)
    _poly(p, [(4, 9), (4, 4), (9, 4)]); _poly(p, [(15, 4), (20, 4), (20, 9)])
    _poly(p, [(20, 15), (20, 20), (15, 20)]); _poly(p, [(9, 20), (4, 20), (4, 15)])


def i_guide(p):
    _pen(p, NAVY, 1.7); _fill(p, "#ffffff")
    p.drawEllipse(QPointF(12, 12), 9, 9)
    q = QtGui.QPainterPath(); q.moveTo(9.2, 9.6)
    q.arcTo(QRectF(9.2, 6.8, 5.6, 5.6), 180, -180)
    q.quadTo(14.8, 11.6, 12, 12.9); q.lineTo(12, 14.4)
    _pen(p, TEAL, 2.0); p.drawPath(q)
    _fill(p, TEAL); p.setPen(Qt.NoPen); p.drawEllipse(QPointF(12, 17.4), 1.25, 1.25)


def i_about(p):
    _pen(p, NAVY, 1.7); _fill(p, "#ffffff")
    p.drawEllipse(QPointF(12, 12), 9, 9)
    _pen(p, TEAL, 2.2); p.drawLine(QPointF(12, 11), QPointF(12, 17))
    _fill(p, TEAL); p.setPen(Qt.NoPen); p.drawEllipse(QPointF(12, 7.6), 1.35, 1.35)


def i_update(p):
    _pen(p, TEAL, 2.0)
    p.drawLine(QPointF(12, 3.5), QPointF(12, 14)); _arrow_head(p, 12, 14, 0, 1, 3.6)
    _pen(p, NAVY, 1.9); _poly(p, [(4, 15), (4, 20), (20, 20), (20, 15)])


def i_prefs(p):
    _pen(p, NAVY, 3.0, Qt.FlatCap)
    for k in range(8):
        a = math.radians(45 * k)
        p.drawLine(QPointF(12 + 6.0 * math.cos(a), 12 + 6.0 * math.sin(a)),
                   QPointF(12 + 9.2 * math.cos(a), 12 + 9.2 * math.sin(a)))
    _pen(p, NAVY, 2.2); _fill(p, "#ffffff")
    p.drawEllipse(QPointF(12, 12), 6.2, 6.2)
    _pen(p, TEAL, 1.6); p.drawEllipse(QPointF(12, 12), 2.3, 2.3)


def i_exit(p):
    _pen(p, NAVY, 1.7)
    _poly(p, [(13, 4), (5, 4), (5, 20), (13, 20)])
    _pen(p, RED, 1.9)
    p.drawLine(QPointF(10, 12), QPointF(20, 12)); _arrow_head(p, 20, 12, 1, 0, 3.2)


ICONS = {
    "record": (i_record, "Start recording"),
    "stop": (i_stop, "Stop recording"),
    "quicklook": (i_quicklook, "Quick look (fold what is recorded so far)"),
    "format": (i_format, "Recording format"),
    "schedule": (i_schedule, "Timed / scheduled / repeating recording"),
    "folder": (i_folder, "Open recordings folder"),
    "folder_set": (i_folder_set, "Set recording folder"),
    "preset": (i_preset, "Observation preset"),
    "live": (i_live, "Live mode"),
    "sweep": (i_sweep, "Sweep mode"),
    "planner": (i_planner, "Pulsars in View"),
    "selftest": (i_selftest, "B210 Self Test"),
    "driftscan": (i_driftscan, "Drift-Scan Review"),
    "device": (i_device, "Change device"),
    "band": (i_band, "Band (feed) preset"),
    "gain": (i_gain, "RX gain"),
    "step_left": (i_step_left, "Tune down one step"),
    "step_right": (i_step_right, "Tune up one step"),
    "lineview": (i_lineview, "Line view"),
    "autoscale": (i_autoscale, "Autoscale spectrum Y"),
    "autoscale_wf": (i_autoscale_wf, "Autoscale waterfall intensity"),
    "maxhold": (i_maxhold, "Max hold"),
    "minhold": (i_minhold, "Min hold"),
    "reset": (i_reset, "Reset holds"),
    "trace": (i_trace, "Trace detector (Average / Peak / Both)"),
    "markers_clear": (i_markers_clear, "Clear markers A and B"),
    "home": (i_home, "Reset axes (full span)"),
    "darkbg": (i_darkbg, "Dark / light plot background"),
    "grid": (i_grid, "Plot grid"),
    "panels": (i_panels, "Show / hide the settings panels"),
    "layout_reset": (i_layout_reset, "Reset window layout"),
    "fullscreen": (i_fullscreen, "Full screen"),
    "prefs": (i_prefs, "Preferences"),
    "guide": (i_guide, "User Guide"),
    "update": (i_update, "Check for updates"),
    "about": (i_about, "About"),
    "exit": (i_exit, "Exit"),
}

_cache = {}


def pixmap(name, px):
    pm = QtGui.QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    p.scale(px / 24.0, px / 24.0)
    ICONS[name][0](p)
    p.end()
    return pm


def icon(name):
    if name not in _cache:
        ic = QtGui.QIcon()
        for s in SIZES:
            ic.addPixmap(pixmap(name, s))
        _cache[name] = ic
    return _cache[name]


def write_sheet(path):
    """Contact sheet: every icon at 32 px and 16 px, enabled and disabled."""
    names = list(ICONS)
    cols, cw, ch = 3, 430, 46
    rows = (len(names) + cols - 1) // cols
    img = QtGui.QImage(cols * cw + 20, rows * ch + 70, QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor("#f3f3f3"))
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    f = QtGui.QFont("Segoe UI", 10); p.setFont(f)
    p.setPen(QtGui.QColor("#222222"))
    p.drawText(12, 26, "DSES Workbench toolbar icons, drawn in code "
               "(32 px, 24 px, 16 px, 24 px disabled)")
    for i, n in enumerate(names):
        r, c = i % rows, i // rows
        x, y = 12 + c * cw, 48 + r * ch
        p.drawPixmap(x, y, pixmap(n, 32))
        p.drawPixmap(x + 42, y + 4, pixmap(n, 24))
        p.drawPixmap(x + 74, y + 8, pixmap(n, 16))
        dis = icon(n).pixmap(QtCore.QSize(24, 24), QtGui.QIcon.Disabled)
        p.drawPixmap(x + 98, y + 4, dis)
        p.drawText(x + 134, y + 21, f"{n}  -  {ICONS[n][1]}")
    p.end()
    img.save(str(path))


if __name__ == "__main__":
    import sys
    from pathlib import Path
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication(sys.argv)
    out = Path(__file__).resolve().parent.parent / "mockups"
    out.mkdir(exist_ok=True)
    write_sheet(out / "icon_sheet.png")
    print("wrote", out / "icon_sheet.png", "-", len(ICONS), "icons")
