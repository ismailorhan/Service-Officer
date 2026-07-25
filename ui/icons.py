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
    for key in [k for k in _cache if k[0] in ("nav", "dot")]:
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
    elif kind == "schedule":                     # a calendar
        p.drawRect(QRectF(s * 0.15, s * 0.22, s * 0.7, s * 0.63))
        p.drawLine(QPointF(s * 0.15, s * 0.4), QPointF(s * 0.85, s * 0.4))
        p.drawLine(QPointF(s * 0.33, s * 0.14), QPointF(s * 0.33, s * 0.28))
        p.drawLine(QPointF(s * 0.67, s * 0.14), QPointF(s * 0.67, s * 0.28))
        p.drawEllipse(QRectF(s * 0.44, s * 0.56, s * 0.12, s * 0.12))
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
