"""The dashboard: everything the tray flyout offers, with room to breathe.

The flyout is 466px wide and lives for a few seconds. This is the same list in a
window you can leave open — same rows, same rules about which buttons may be
pressed, because both use ui/rows.py.

It reads the *saved* config and the live status store, so it shows what is
actually being monitored. Edits made on the Services page and not yet saved
deliberately don't appear here: acting on a service the app isn't watching yet
would be a lie about what happened.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QScrollArea, QVBoxLayout,
                               QWidget)

from core import state as st
from . import theme
from .rows import ServiceRow
from .servicelist import ServiceListMixin
from .widgets import Chip, button as _button, label as _label


class DashboardPage(ServiceListMixin, QWidget):
    """Live status and controls for every monitored service.

    The rows, the grouping and what a tick means are shared with the tray flyout
    through ServiceListMixin. What differs is the room: this window is sized by
    the user, so nothing here resizes itself.
    """

    action_requested = Signal(str, str, str)     # action, service, machine
    bulk_requested = Signal(str, list)           # action, [(service, machine), …]
    run_stack = Signal(str)
    refresh_requested = Signal()
    open_services_mmc = Signal()

    def __init__(self, config_getter, store, parent=None):
        super().__init__(parent)
        self._config = config_getter
        self._store = store
        self._rows: dict = {}
        self._extras: list = []          # section bars, stack rows, placeholders

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 0)
        root.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(10)
        title = _label("Dashboard", "title")
        head.addWidget(title)
        self.badge = Chip("", "running")
        head.addWidget(self.badge)
        head.addStretch(1)
        head.addWidget(_button(f"{theme.GLYPH_REFRESH}  Refresh", "quiet",
                               self.refresh_requested.emit))
        head.addWidget(_button(f"{theme.GLYPH_SERVICES}  Services", "quiet",
                               self.open_services_mmc.emit))
        root.addLayout(head)

        self.summary = _label("", "hint")
        root.addSpacing(4)
        root.addWidget(self.summary)
        root.addSpacing(14)

        root.addWidget(self._make_search())
        root.addSpacing(10)
        root.addWidget(self._make_column_header())
        # Under the header it belongs to, same as the tray flyout's.
        root.addWidget(self._make_bulk_bar())

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.list = QWidget()
        self.list_lay = QVBoxLayout(self.list)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(0)
        self.list_lay.addStretch(1)
        self.scroll.setWidget(self.list)
        root.addWidget(self.scroll, 1)
        self.rebuild()

    # -- content -----------------------------------------------------------
    def rebuild(self) -> None:
        for w in list(self._rows.values()) + self._extras:
            w.setParent(None)
            w.deleteLater()
        self._rows.clear()
        self._extras.clear()

        cfg = self._config()

        def add(widget):
            self.list_lay.insertWidget(self.list_lay.count() - 1, widget)

        self._extras.extend(self._add_service_groups(cfg, add))

        if not cfg.services:
            empty = _label("Nothing is being monitored yet — add services on the "
                           "Services page.", "hint")
            empty.setAlignment(Qt.AlignCenter)
            empty.setContentsMargins(0, 30, 0, 30)
            self._extras.append(empty)
            add(empty)

        self._extras.extend(self._add_stack_section(cfg.stacks, cfg.services, add))

        self._filter()
        self.apply_states()

    def apply_states(self) -> None:
        cfg = self._config()
        running = stopped = 0
        for svc in cfg.services:
            row = self._rows.get(svc.key)
            if not isinstance(row, ServiceRow):
                continue
            status = self._store.status_of(svc.name, svc.machine)
            row.set_status(status,
                           disabled=self._store.is_disabled(svc.name, svc.machine),
                           health=self._store.health_of(svc.name, svc.machine),
                           health_detail=self._store.health_detail(svc.name,
                                                                   svc.machine))
            if status == st.RUNNING:
                running += 1
            elif status == st.STOPPED:
                stopped += 1

        total = len(cfg.services)
        self.badge.set_state(
            f"{running} of {total} running" if total else "no services",
            "running" if total and running == total
            else "stopped" if not running else "pending")
        parts = [f"{total} service{'s' if total != 1 else ''}",
                 f"{running} running", f"{stopped} stopped"]
        other = total - running - stopped
        if other > 0:
            parts.append(f"{other} other")
        disabled = sum(1 for s in cfg.services
                       if self._store.is_disabled(s.name, s.machine))
        if disabled:
            parts.append(f"{disabled} disabled in Windows")
        # Worth its own count: a service that is running and not answering is the
        # one somebody is about to ring up about.
        sick = sum(1 for s in cfg.services
                   if self._store.health_of(s.name, s.machine) == "unhealthy")
        if sick:
            parts.append(f"{sick} not responding")
        self.summary.setText("  ·  ".join(parts))

    # Grouping, visibility, selection and bulk actions come from
    # ServiceListMixin, shared with the tray flyout.
