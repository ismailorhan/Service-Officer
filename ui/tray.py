"""Tray icon: menu, spinner, and the bridge from core events onto the GUI thread.

QSystemTrayIcon replaces pystray, and with it go the patched Win32 message
handler, the tooltip length arithmetic and the hand-rolled foreground tracking.
The tray tooltip is left empty on purpose so only our hover card appears.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from core import state as st
from . import icons


class StateBridge(QObject):
    """Core publishes from a worker thread; Qt queues this onto the GUI thread."""
    changed = Signal(object)          # core.state.Event

    def attach(self, store: st.Store) -> None:
        store.subscribe(self.changed.emit)


class Tray(QObject):
    left_clicked = Signal()
    hover = Signal()
    quit_requested = Signal()
    #: open the management panel, optionally on a named page
    panel_requested = Signal(str)
    services_requested = Signal()
    refresh_requested = Signal()
    stack_requested = Signal(str)          # stack name — a stack is one script
    menu_opened = Signal()

    def __init__(self, config_getter, store: st.Store, parent=None):
        super().__init__(parent)
        self._config = config_getter
        self._store = store

        self.icon = QSystemTrayIcon(icons.base_icon("green"))
        self.icon.setToolTip("")       # our hover card does this job
        self.icon.activated.connect(self._activated)

        self._menu = QMenu()
        self.icon.setContextMenu(self._menu)
        self.rebuild_menu()

        self._spin = QTimer(self)
        self._spin.setInterval(110)
        self._spin.timeout.connect(self._tick)
        self._frame = 0
        self._busy = 0                 # actions we started that are still running

        # Qt gives no mouse-over signal for a tray icon, so poll cheaply: only
        # while the pointer is inside the icon's rect does anything happen.
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(250)
        self._hover_timer.timeout.connect(self._check_hover)
        self._hover_timer.start()
        self._hovering = False

    # -- lifecycle ---------------------------------------------------------
    def show(self):
        self.icon.show()

    def hide(self):
        self.icon.hide()

    def geometry(self):
        return self.icon.geometry()

    # -- menu --------------------------------------------------------------
    def rebuild_menu(self):
        self._menu.clear()
        stacks = self._config().stacks
        if stacks:
            # One entry per stack, no submenu: each step already says what it
            # does, so there is nothing left to choose.
            for stack in stacks:
                action = self._menu.addAction(
                    stack.name, lambda s=stack.name: self.stack_requested.emit(s))
                action.setToolTip(stack.describe(self._config().services))
            self._menu.addSeparator()
        # The panel is the app's main window, so it leads the menu — and its
        # sections are offered directly, since "open the panel then click
        # History" is two steps for one intention.
        opener = self._menu.addAction(
            "Service Management Panel", lambda: self.panel_requested.emit(""))
        font = opener.font()
        font.setBold(True)
        opener.setFont(font)
        for text, page in (("Schedule…", "schedule"), ("History…", "history"),
                           ("Settings…", "general")):
            self._menu.addAction(text, lambda p=page: self.panel_requested.emit(p))
        self._menu.addSeparator()
        self._menu.addAction("Open Services", self.services_requested.emit)
        self._menu.addAction("Refresh", self.refresh_requested.emit)
        self._menu.addSeparator()
        self._menu.addAction("Quit", self.quit_requested.emit)

    # -- icon state --------------------------------------------------------
    def apply_state(self):
        """Colour reflects how many services are running; the spinner takes over
        while anything is mid-transition or an action of ours is in flight."""
        if self._should_spin():
            if not self._spin.isActive():
                self._spin.start()
            return
        if self._spin.isActive():
            self._spin.stop()
        running, total = self._store.counts()
        self.icon.setIcon(icons.base_icon(icons.colour_for(running, total)))

    def _should_spin(self) -> bool:
        """Spin while something is genuinely in flight.

        A pending state is only trusted for a while: if the SCM's final
        notification is ever missed, a stale "Stopping" would otherwise leave the
        icon spinning forever with nothing happening.
        """
        if self._busy > 0:
            return True
        if not self._store.any_pending():
            return False
        oldest = min((s.since for s in self._store.snapshot().values()
                      if st.is_pending(s.status)), default=None)
        return oldest is None or (time.monotonic() - oldest) < 120

    def _tick(self):
        self._frame = (self._frame + 1) % icons.frame_count()
        self.icon.setIcon(icons.gear_icon(self._frame))

    def action_started(self):
        self._busy += 1
        self.apply_state()

    def action_finished(self):
        self._busy = max(0, self._busy - 1)
        self.apply_state()

    def notify(self, title: str, text: str):
        try:
            self.icon.showMessage(title, text, icons.base_icon("green"), 6000)
        except Exception:
            pass

    # -- input -------------------------------------------------------------
    def _activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            # A single click stays instant — the flyout is the thing you want
            # nine times out of ten, and deferring it by the double-click
            # interval to tell the two apart would cost that every time. The
            # price is that the flyout may flicker open first.
            self.panel_requested.emit("")
        elif reason == QSystemTrayIcon.Trigger:
            self.left_clicked.emit()
        elif reason == QSystemTrayIcon.Context:
            # The menu is about to cover the same corner as the hover card.
            self.menu_opened.emit()

    def _check_hover(self):
        from PySide6.QtGui import QCursor
        if self._menu.isVisible():          # don't fight the context menu
            self._hovering = False
            return
        rect = self.icon.geometry()
        inside = bool(rect) and not rect.isNull() and rect.contains(QCursor.pos())
        if inside:
            self.hover.emit()
        self._hovering = inside
