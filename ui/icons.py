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
