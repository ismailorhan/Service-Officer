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
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QScrollArea, QVBoxLayout, QWidget)

from core import state as st
from . import theme
from .rows import (BulkBar, SectionBar, ServiceRow, StackRow, is_collapsed)
from .widgets import Chip, button as _button, label as _label


class DashboardPage(QWidget):
    """Live status and controls for every monitored service."""

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
        head.addWidget(_button("↻  Refresh", "quiet", self.refresh_requested.emit))
        head.addWidget(_button("▤  Services", "quiet", self.open_services_mmc.emit))
        root.addLayout(head)

        self.summary = _label("", "hint")
        root.addSpacing(4)
        root.addWidget(self.summary)
        root.addSpacing(14)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search services…")
        self.search.textChanged.connect(self._filter)
        root.addWidget(self.search)
        root.addSpacing(10)

        cols = QWidget()
        cols.setObjectName("columnHeader")
        cols.setAttribute(Qt.WA_StyledBackground, True)
        cl = QHBoxLayout(cols)
        cl.setContentsMargins(14, 4, 14, 4)
        cl.setSpacing(10)
        self.tick_all = QCheckBox()
        self.tick_all.setTristate(True)
        self.tick_all.setToolTip("Select every service shown")
        self.tick_all.clicked.connect(self._toggle_all)
        cl.addWidget(self.tick_all)
        for text, width, align in (("SERVICE", 0, Qt.AlignLeft),
                                   ("STATUS", theme.COL_STATUS_W, Qt.AlignCenter),
                                   ("ACTIONS", theme.COL_ACTIONS_W, Qt.AlignRight)):
            lb = _label(text, "section")
            lb.setAlignment(align | Qt.AlignVCenter)
            if width:
                lb.setFixedWidth(width)
                cl.addWidget(lb)
            else:
                cl.addWidget(lb, 1)
        root.addWidget(cols)

        # Under the header it belongs to, same as the tray panel's.
        self.bulk = BulkBar()
        self.bulk.chosen.connect(self._bulk)
        self.bulk.cleared.connect(lambda: self._set_all(False))
        root.addWidget(self.bulk)

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

        groups = cfg.grouped_services()
        # A lone "No category" bar above every service says nothing; once there
        # is real grouping the headings and their tallies carry their weight.
        show_headings = len(groups) > 1 or bool(cfg.categories)
        for name, group_title, members in groups:
            if show_headings:
                running = sum(1 for s in members
                              if self._store.status_of(s.name, s.machine)
                              == st.RUNNING)
                bar = SectionBar(name, group_title, len(members), running)
                bar.toggled.connect(lambda *_a: self._filter())
                self._extras.append(bar)
                add(bar)
            for svc in members:
                row = ServiceRow(svc)
                row.category = name
                row.act.connect(self.action_requested)
                row.picked.connect(self._selection_changed)
                self._rows[svc.key] = row
                add(row)

        if not cfg.services:
            empty = _label("Nothing is being monitored yet — add services on the "
                           "Services page.", "hint")
            empty.setAlignment(Qt.AlignCenter)
            empty.setContentsMargins(0, 30, 0, 30)
            self._extras.append(empty)
            add(empty)

        if cfg.stacks:
            bar = QWidget()
            bar.setObjectName("sectionBar")
            bar.setAttribute(Qt.WA_StyledBackground, True)
            bl = QHBoxLayout(bar)
            bl.setContentsMargins(*theme.BAR_PAD)
            bl.addWidget(_label("STACKS", "section"))
            bl.addStretch(1)
            self._extras.append(bar)
            add(bar)
            for stack in cfg.stacks:
                row = StackRow(stack, cfg.services)
                row.run.connect(self.run_stack)
                self._extras.append(row)
                add(row)

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

    def mark_busy(self, name: str, machine: str, label: str) -> None:
        row = self._rows.get((machine or "", name))
        if isinstance(row, ServiceRow):
            row.set_status(row.status, busy_label=label)

    # -- visibility and selection ------------------------------------------
    def _filter(self, _text: str = "") -> None:
        query = (self.search.text() or "").strip().lower()
        for row in self._rows.values():
            matches = (query in row.service.display().lower()
                       or query in row.service.name.lower())
            folded = is_collapsed(getattr(row, "category", ""))
            row.setVisible(matches and (not folded or bool(query)))
            if row.isHidden() and row.tick.isChecked():
                row.tick.blockSignals(True)
                row.tick.setChecked(False)
                row.tick.blockSignals(False)
        self._selection_changed()

    def _service_rows(self) -> list:
        return [r for r in self._rows.values() if not r.isHidden()]

    def selected(self) -> list:
        return [r for r in self._service_rows() if r.tick.isChecked()]

    def _set_all(self, on: bool) -> None:
        for row in self._service_rows():
            row.tick.blockSignals(True)
            row.tick.setChecked(on)
            row.tick.blockSignals(False)
        self._selection_changed()

    def _toggle_all(self) -> None:
        rows = self._service_rows()
        self._set_all(not (rows and all(r.tick.isChecked() for r in rows)))

    def _selection_changed(self) -> None:
        rows = self._service_rows()
        chosen = [r for r in rows if r.tick.isChecked()]
        self.bulk.set_count(len(chosen))
        self.tick_all.blockSignals(True)
        if not chosen:
            self.tick_all.setCheckState(Qt.Unchecked)
        elif len(chosen) == len(rows):
            self.tick_all.setCheckState(Qt.Checked)
        else:
            self.tick_all.setCheckState(Qt.PartiallyChecked)
        self.tick_all.blockSignals(False)

    def _bulk(self, action: str) -> None:
        targets = [(r.service.name, r.service.machine) for r in self.selected()]
        if not targets:
            return
        self.bulk_requested.emit(action, targets)
        self._set_all(False)
