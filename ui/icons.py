"""Tray icons and the transition spinner.

The shipped icons composite a state badge over the gear, so the badge can't be
masked off without biting a hole in the teeth — the spinning gear is therefore
drawn here instead. Under Qt it is drawn live with QPainter rather than as a
dozen pre-rendered PNGs, and rotation/scale come from the painter transform.
"""

from __future__ import annotations

import base64
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QIcon, QImage, QPainter, QPainterPath,
                           QPen, QPixmap)

import _icon_data
from . import theme

_ICON_B64 = {
    "green":  _icon_data.ICON_GREEN,   # all running
    "yellow": _icon_data.ICON_YELLOW,  # mixed
    "red":    _icon_data.ICON_RED,     # all stopped
}

_GEAR_FILL = QColor(244, 246, 248)
_GEAR_LINE = QColor(22, 38, 63)
_TEETH = 8
_PULSE = 0.06        # breathe 6% smaller at mid-cycle
_FRAMES = 12

_cache: dict = {}


def clear_cache() -> None:
    """Drop everything painted with palette colours — call after a theme change."""
    for key in [k for k in _cache if k[0] in ("nav", "dot", "info")]:
        del _cache[key]


def base_pixmap(colour: str = "green", size: int = 64) -> QPixmap:
    key = ("base", colour, size)
    if key not in _cache:
        img = QImage.fromData(base64.b64decode(_ICON_B64.get(colour, _ICON_B64["green"])))
        _cache[key] = QPixmap.fromImage(img).scaled(
            size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return _cache[key]


def base_icon(colour: str = "green") -> QIcon:
    key = ("icon", colour)
    if key not in _cache:
        _cache[key] = QIcon(base_pixmap(colour))
    return _cache[key]


def colour_for(running: int, total: int) -> str:
    if not total:
        return "green"
    if running == total:
        return "green"
    return "red" if running == 0 else "yellow"


def _gear_path(size: int, teeth: int = _TEETH) -> QPainterPath:
    c = size / 2.0
    r_out, r_root = size * 0.46, size * 0.36
    path = QPainterPath()
    steps = teeth * 24
    for i in range(steps):
        frac = i / steps
        a = 2 * math.pi * frac
        r = r_out if (frac * teeth) % 1.0 < 0.45 else r_root
        pt = QPointF(c + r * math.cos(a), c + r * math.sin(a))
        path.moveTo(pt) if i == 0 else path.lineTo(pt)
    path.closeSubpath()
    hole = size * 0.145
    path.addEllipse(QRectF(c - hole, c - hole, hole * 2, hole * 2))
    path.setFillRule(Qt.OddEvenFill)
    return path


def gear_frame(index: int, size: int = 64) -> QPixmap:
    """One frame of the gear turning clockwise while breathing slightly.

    Eight teeth means the shape repeats every 45°, so a 45° sweep loops
    seamlessly; the scale pulse shares that period so it loops too.
    """
    key = ("gear", index % _FRAMES, size)
    if key in _cache:
        return _cache[key]

    frac = (index % _FRAMES) / _FRAMES
    angle = (360.0 / _TEETH) * frac                     # clockwise
    scale = 1.0 - _PULSE * (1 - math.cos(2 * math.pi * frac)) / 2

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.translate(size / 2.0, size / 2.0)
    p.rotate(angle)
    p.scale(scale, scale)
    p.translate(-size / 2.0, -size / 2.0)
    p.setBrush(QBrush(_GEAR_FILL))
    p.setPen(QPen(_GEAR_LINE, max(1.0, size * 0.045)))
    p.drawPath(_gear_path(size))
    p.end()

    _cache[key] = pm
    return pm


#: The emergency gear: a white rim and a red body. Not from the palette — a tray icon sits on
#: the taskbar, which is nobody's theme, and these two have to hold against both.
_ALARM_FILL = QColor(214, 47, 39)
_ALARM_LINE = QColor(255, 255, 255)


def emergency_pixmap(size: int = 64) -> QPixmap:
    """The gear turned red, with nothing on it.

    The three shipped icons are one gear with a small badge in the corner, and the badge is a
    statement about services: this many are running, that many stopped. An unreachable hub is
    not that statement. Nothing has answered, so every count is *unknown* rather than bad, and
    a badge saying "0 of 9" about nine services that are almost certainly running fine is the
    kind of wrong that gets somebody out of bed.

    So the gear itself goes red and the badge goes away. Whole-icon rather than a corner, and
    the one shape here that cannot be read as a count.
    """
    key = ("alarm", size)
    if key in _cache:
        return _cache[key]

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(_ALARM_FILL))
    # Half of a centred stroke falls outside the path, so a rim this wide reads at 16px —
    # which is the only size that matters, since that is what the taskbar asks for.
    p.setPen(QPen(_ALARM_LINE, max(1.0, size * 0.07)))
    p.drawPath(_gear_path(size))
    p.end()

    _cache[key] = pm
    return pm


def emergency_icon() -> QIcon:
    key = ("alarm-icon",)
    if key not in _cache:
        _cache[key] = QIcon(emergency_pixmap())
    return _cache[key]


def gear_icon(index: int) -> QIcon:
    return QIcon(gear_frame(index))


def frame_count() -> int:
    return _FRAMES


def dot(colour: str, size: int = 9) -> QPixmap:
    """A small status dot, used in lists and the hover flyout."""
    key = ("dot", colour, size)
    if key in _cache:
        return _cache[key]
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(colour))
    p.drawEllipse(0, 0, size - 1, size - 1)
    p.end()
    _cache[key] = pm
    return pm


def status_dot(category: str, size: int = 9) -> QPixmap:
    return dot(theme.dot_colour(category), size)


def info_dot(size: int = 15, colour: str = None) -> QIcon:
    """A lower-case i in a circle. Drawn, like every other icon here, because a font glyph
    cannot be sized against a 15px row and reads as a speck or a blot depending on the
    machine's fonts."""
    key = ("info", size, colour)
    if key in _cache:
        return _cache[key]
    ink = QColor(colour or theme.FG3)
    scale = 3                                   # drawn large and scaled down, for the curve
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = float(size * scale)
    pen = QPen(ink)
    pen.setWidthF(s * 0.085)
    p.setPen(pen)
    p.drawEllipse(QRectF(pen.widthF(), pen.widthF(),
                         s - pen.widthF() * 2, s - pen.widthF() * 2))
    # The dot and the stem of the i, drawn rather than typed for the same reason.
    p.setBrush(ink)
    p.drawEllipse(QRectF(s * 0.44, s * 0.24, s * 0.12, s * 0.12))
    pen.setWidthF(s * 0.11)
    p.setPen(pen)
    p.drawLine(QPointF(s * 0.5, s * 0.43), QPointF(s * 0.5, s * 0.73))
    p.end()
    _cache[key] = QIcon(pm.scaled(size, size, Qt.KeepAspectRatio,
                                  Qt.SmoothTransformation))
    return _cache[key]


def nav_icon(kind: str, size: int = 19, colour: str = None) -> QIcon:
    """Sidebar icons, drawn rather than typed.

    A text glyph inside a button label can't be sized independently of the
    label, and at label size these read as specks.
    """
    key = ("nav", kind, size, colour)
    if key in _cache:
        return _cache[key]

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(colour or theme.FG2), 1.7)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    s = size

    if kind == "services":                      # a list
        for i in range(3):
            y = s * (0.28 + i * 0.22)
            p.drawEllipse(QRectF(s * 0.12, y - s * 0.045, s * 0.09, s * 0.09))
            p.drawLine(QPointF(s * 0.34, y), QPointF(s * 0.86, y))
    elif kind == "stacks":                       # ordered, both directions
        p.drawLine(QPointF(s * 0.32, s * 0.18), QPointF(s * 0.32, s * 0.82))
        p.drawLine(QPointF(s * 0.32, s * 0.18), QPointF(s * 0.2, s * 0.34))
        p.drawLine(QPointF(s * 0.32, s * 0.18), QPointF(s * 0.44, s * 0.34))
        p.drawLine(QPointF(s * 0.68, s * 0.82), QPointF(s * 0.68, s * 0.18))
        p.drawLine(QPointF(s * 0.68, s * 0.82), QPointF(s * 0.56, s * 0.66))
        p.drawLine(QPointF(s * 0.68, s * 0.82), QPointF(s * 0.8, s * 0.66))
    elif kind == "history":                      # a clock
        p.drawEllipse(QRectF(s * 0.14, s * 0.14, s * 0.72, s * 0.72))
        p.drawLine(QPointF(s * 0.5, s * 0.5), QPointF(s * 0.5, s * 0.28))
        p.drawLine(QPointF(s * 0.5, s * 0.5), QPointF(s * 0.68, s * 0.58))
    elif kind == "machines":                     # two stacked boxes
        p.drawRect(QRectF(s * 0.16, s * 0.2, s * 0.68, s * 0.24))
        p.drawRect(QRectF(s * 0.16, s * 0.56, s * 0.68, s * 0.24))
        p.drawPoint(QPointF(s * 0.72, s * 0.32))
        p.drawPoint(QPointF(s * 0.72, s * 0.68))
    elif kind == "hub":                          # a centre with three spokes: what a hub
        c = s / 2.0                              # is — one thing everything else reads
        p.drawEllipse(QRectF(c - s * 0.13, c - s * 0.13, s * 0.26, s * 0.26))
        for degrees in (90, 210, 330):
            a = math.radians(degrees)
            p.drawLine(QPointF(c + math.cos(a) * s * 0.17,
                               c + math.sin(a) * s * 0.17),
                       QPointF(c + math.cos(a) * s * 0.36,
                               c + math.sin(a) * s * 0.36))
            p.drawEllipse(QRectF(c + math.cos(a) * s * 0.36 - s * 0.07,
                                 c + math.sin(a) * s * 0.36 - s * 0.07,
                                 s * 0.14, s * 0.14))
    elif kind == "clients":                      # a key: what a client is given
        p.drawEllipse(QRectF(s * 0.16, s * 0.34, s * 0.3, s * 0.3))
        p.drawLine(QPointF(s * 0.45, s * 0.49), QPointF(s * 0.86, s * 0.49))
        p.drawLine(QPointF(s * 0.7, s * 0.49), QPointF(s * 0.7, s * 0.66))
        p.drawLine(QPointF(s * 0.82, s * 0.49), QPointF(s * 0.82, s * 0.62))
    elif kind == "close":                        # an X, drawn so no font can
        m = s * 0.3                              # fail to provide the glyph
        p.drawLine(QPointF(m, m), QPointF(s - m, s - m))
        p.drawLine(QPointF(s - m, m), QPointF(m, s - m))
    elif kind == "schedule":                     # a calendar
        p.drawRect(QRectF(s * 0.15, s * 0.22, s * 0.7, s * 0.63))
        p.drawLine(QPointF(s * 0.15, s * 0.4), QPointF(s * 0.85, s * 0.4))
        p.drawLine(QPointF(s * 0.33, s * 0.14), QPointF(s * 0.33, s * 0.28))
        p.drawLine(QPointF(s * 0.67, s * 0.14), QPointF(s * 0.67, s * 0.28))
        p.drawEllipse(QRectF(s * 0.44, s * 0.56, s * 0.12, s * 0.12))
    elif kind in ("pin", "unpin"):               # a drawing pin, head and point
        # Pinned stands upright; unpinned leans, which reads as "not stuck down"
        # without needing a second colour to tell them apart.
        if kind == "pin":
            p.drawEllipse(QRectF(s * 0.34, s * 0.14, s * 0.32, s * 0.32))
            p.drawLine(QPointF(s * 0.5, s * 0.46), QPointF(s * 0.5, s * 0.86))
        else:
            p.drawEllipse(QRectF(s * 0.44, s * 0.14, s * 0.3, s * 0.3))
            p.drawLine(QPointF(s * 0.52, s * 0.44), QPointF(s * 0.28, s * 0.82))
    elif kind == "dashboard":                    # four panes
        p.drawRect(QRectF(s * 0.14, s * 0.14, s * 0.31, s * 0.31))
        p.drawRect(QRectF(s * 0.55, s * 0.14, s * 0.31, s * 0.31))
        p.drawRect(QRectF(s * 0.14, s * 0.55, s * 0.31, s * 0.31))
        p.drawRect(QRectF(s * 0.55, s * 0.55, s * 0.31, s * 0.31))
    elif kind == "categories":                   # a folder
        p.drawLine(QPointF(s * 0.14, s * 0.28), QPointF(s * 0.42, s * 0.28))
        p.drawLine(QPointF(s * 0.42, s * 0.28), QPointF(s * 0.5, s * 0.4))
        p.drawRect(QRectF(s * 0.14, s * 0.4, s * 0.72, s * 0.42))
        p.drawLine(QPointF(s * 0.14, s * 0.28), QPointF(s * 0.14, s * 0.4))
    else:                                        # gear
        c = s / 2.0
        p.drawEllipse(QRectF(c - s * 0.17, c - s * 0.17, s * 0.34, s * 0.34))
        for k in range(8):
            a = math.radians(k * 45)
            p.drawLine(QPointF(c + math.cos(a) * s * 0.26,
                               c + math.sin(a) * s * 0.26),
                       QPointF(c + math.cos(a) * s * 0.40,
                               c + math.sin(a) * s * 0.40))
    p.end()
    _cache[key] = QIcon(pm)
    return _cache[key]
