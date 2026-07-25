"""Hover card for the tray icon.

Windows' own tray tooltip holds 128 UTF-16 units, which can't list more than two
or three services, so this replaces it. It must never take focus — under Qt that
is `WA_ShowWithoutActivating` plus a `Qt.ToolTip` window, rather than the
`ShowWindow(SW_SHOWNOACTIVATE)` dance the tkinter version needed.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from core import state as st
from . import icons, theme

SHOW_DELAY_MS = 350


class HoverCard(QWidget):
    def __init__(self, config_getter, store: st.Store, parent=None):
        super().__init__(parent, Qt.ToolTip | Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self._config = config_getter
        self._store = store
        self._rect = None

        self.setStyleSheet(f"QWidget#card {{ background:{theme.BG}; "
                           f"border:1px solid #3a3a3a; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QWidget()
        card.setObjectName("card")
        outer.addWidget(card)

        self._lay = QVBoxLayout(card)
        self._lay.setContentsMargins(13, 10, 13, 11)
        self._lay.setSpacing(4)
        self.title = QLabel("Service Officer")
        self.title.setStyleSheet(f"color:{theme.FG}; font-weight:600; font-size:9.5pt;")
        self._lay.addWidget(self.title)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{theme.LINE}; border:none;")
        self._lay.addWidget(line)
        self._rows = QVBoxLayout()
        self._rows.setSpacing(3)
        self._lay.addLayout(self._rows)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._show_now)

        # One rule decides when it goes away: the pointer is over neither the
        # tray icon nor this card. Relying on leaveEvent only worked when the
        # pointer happened to cross the card — moving off the icon sideways
        # along the taskbar left the card up indefinitely.
        self._watch = QTimer(self)
        self._watch.setInterval(150)
        self._watch.timeout.connect(self._check_pointer)

    # -- API ---------------------------------------------------------------
    def request(self, icon_rect=None) -> None:
        """Pointer is over the tray icon; show after it rests a moment."""
        self._rect = icon_rect
        if self.isVisible():
            self.refresh()
            return
        if not self._timer.isActive():
            self._timer.start(SHOW_DELAY_MS)

    def dismiss(self) -> None:
        self._timer.stop()
        self._watch.stop()
        self.hide()

    #: where the pointer is; replaceable so a test needn't move the real one
    cursor_pos = staticmethod(QCursor.pos)

    def pointer_is_near(self) -> bool:
        """Over the tray icon, or over the card itself."""
        where = self.cursor_pos()
        if self._rect is not None and not self._rect.isNull():
            # The icon rect is a few pixels tall; a little slack stops the card
            # flickering off when the pointer sits on its edge.
            if self._rect.adjusted(-4, -4, 4, 4).contains(where):
                return True
        return self.isVisible() and self.geometry().contains(where)

    def refresh(self) -> None:
        if self.isVisible():
            self._render()
            self.adjustSize()
            self.move(self._anchor())

    # -- internals ---------------------------------------------------------
    def _check_pointer(self) -> None:
        if not self.isVisible():
            self._watch.stop()
            return
        if not self.pointer_is_near():
            self.dismiss()

    def _show_now(self) -> None:
        self._render()
        self.adjustSize()
        self.move(self._anchor())
        self.show()
        self._watch.start()

    def _render(self) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        services = self._config().services
        running = 0
        for svc in services:
            status = self._store.status_of(svc.name, svc.machine)
            if status == st.RUNNING:
                running += 1
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(8)
            dot = QLabel()
            dot.setPixmap(icons.status_dot(st.category(status), 8))
            rl.addWidget(dot)
            name = QLabel(svc.display())
            name.setStyleSheet(f"color:{theme.FG}; font-size:9pt;")
            rl.addWidget(name)
            rl.addStretch(1)
            state = QLabel(status)
            state.setStyleSheet(f"color:{theme.FG2}; font-size:8.5pt;")
            rl.addWidget(state)
            self._rows.addWidget(row)

        if not services:
            empty = QLabel("No services configured")
            empty.setProperty("role", "hint")
            self._rows.addWidget(empty)

        self.title.setText(f"Service Officer  —  {running}/{len(services)} running"
                           if services else "Service Officer")

    def _anchor(self) -> QPoint:
        screen = self.screen().availableGeometry()
        w, h = self.width(), self.height()
        if self._rect is not None and not self._rect.isNull():
            x = self._rect.center().x() - w // 2
            y = self._rect.top() - h - 6
        else:
            x, y = screen.right() - w - 12, screen.bottom() - h - 12
        x = max(screen.left() + 4, min(x, screen.right() - w - 4))
        y = max(screen.top() + 4, y)
        return QPoint(int(x), int(y))

    def leaveEvent(self, ev):
        # The poll is what decides; this just makes leaving the card immediate
        # rather than waiting out a tick.
        if not self.pointer_is_near():
            self.dismiss()
        super().leaveEvent(ev)
