"""The list of services, and what a selection in it means.

Two windows show it: the tray flyout and the dashboard. They look different and
are sized differently, but "which rows are ticked", "what does the header
checkbox show", "a row you can't see is not selected" and "act on the selection"
are the same rules in both — and were the same code, written twice. A bug found
in one copy had to be remembered in the other: `selected()` once asked
`isVisible()` instead of `isHidden()` and came back empty whenever the window
hadn't been shown yet, which was fixed in the flyout while the dashboard kept
the working version by luck.

So the rules live here once. A host provides the widgets (`_rows`, `search`,
`tick_all`, `bulk`) and the two signals, and may override `_selection_settled`
if it has to resize itself afterwards.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit,
                               QWidget)

from core import state as st
from . import theme
from .rows import BulkBar, SectionBar, ServiceRow, StackRow, is_collapsed

#: The same words in both windows, so they cannot drift apart in one of them.
SEARCH_HINT = "Search services…"
SELECT_ALL_HINT = "Select every service shown"
COLUMNS = (("SERVICE", 0, Qt.AlignLeft),
           ("STATUS", theme.COL_STATUS_W, Qt.AlignCenter),
           ("ACTIONS", theme.COL_ACTIONS_W, Qt.AlignRight))


def _can_kill(cfg, machine: str) -> bool:
    """Can a process be ended on that machine?

    On this computer, always. On another it takes a transport that carries a kill, and for
    Windows that is WinRM — a switch per machine, off by default, because every call it makes
    writes a logon record to that machine's Security log. Linux over SSH always can.

    Read from the switch rather than probed: probing costs a PowerShell process and a row is
    drawn far more often than a machine changes. The switch is the decision anyway — Test
    connection sets it from a real probe.
    """
    if not machine:
        return True
    found = cfg.machine(machine)
    if found is None:
        return False
    return bool(found.is_linux or getattr(found, "winrm", False))


class ServiceListMixin:
    """Grouping, visibility and selection for a list of ServiceRows.

    Expected on the host: `self._rows` (dict keyed by `service.key`), `self.search`,
    `self.tick_all`, `self.bulk`, and the signals `action_requested` and
    `bulk_requested`.
    """

    # -- the parts above the list ------------------------------------------
    def _make_search(self) -> QLineEdit:
        """The search box. The host places it; where it sits differs, what it
        does does not."""
        self.search = QLineEdit()
        self.search.setPlaceholderText(SEARCH_HINT)
        self.search.textChanged.connect(self._filter)
        return self.search

    def _make_column_header(self) -> QWidget:
        """SERVICE · STATUS · ACTIONS, with the box that selects them all."""
        cols = QWidget()
        cols.setObjectName("columnHeader")
        cols.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(cols)
        lay.setContentsMargins(*theme.HEAD_PAD)
        lay.setSpacing(10)
        self.tick_all = QCheckBox()
        self.tick_all.setTristate(True)
        self.tick_all.setToolTip(SELECT_ALL_HINT)
        self.tick_all.clicked.connect(self._toggle_all)
        lay.addWidget(self.tick_all)
        for text, width, align in COLUMNS:
            label = QLabel(text)
            label.setProperty("role", "section")
            label.setAlignment(align | Qt.AlignVCenter)
            if width:
                label.setFixedWidth(width)
                lay.addWidget(label)
            else:
                lay.addWidget(label, 1)
        return cols

    def _make_bulk_bar(self) -> BulkBar:
        """What appears once rows are ticked. It belongs directly under the
        header, because the box that selects everything is right above it."""
        self.bulk = BulkBar()
        self.bulk.chosen.connect(self._bulk)
        self.bulk.cleared.connect(lambda: self._set_all(False))
        return self.bulk

    # -- building the list -------------------------------------------------
    def _add_service_groups(self, cfg, add) -> list:
        """Fill `self._rows` from the config; return the section bars created.

        `add` places one widget in the host's list layout, because the flyout and
        the dashboard each keep a stretch at the end.
        """
        bars = []
        groups = cfg.grouped_services()
        # A heading only when there is more than one group: a lone "No category"
        # bar above every service says nothing.
        show_headings = len(groups) > 1 or bool(cfg.categories)
        for name, title, members in groups:
            if show_headings:
                running = sum(1 for s in members
                              if self._store.status_of(s.name, s.machine)
                              == st.RUNNING)
                bar = SectionBar(name, title, len(members), running)
                bar.toggled.connect(self._section_toggled)
                bars.append(bar)
                add(bar)
            for svc in members:
                row = ServiceRow(svc)
                row.can_kill = _can_kill(cfg, svc.machine)
                row.category = name
                row.act.connect(self.action_requested)
                row.picked.connect(self._selection_changed)
                self._rows[svc.key] = row
                add(row)
        return bars

    def _add_stack_section(self, stacks, services, add) -> list:
        """The STACKS heading and its rows, in the same scrolling list — a whole
        sequence one click from where the statuses are read."""
        if not stacks:
            return []
        made = []
        bar = QWidget()
        bar.setObjectName("sectionBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(*theme.BAR_PAD)
        head = QLabel("STACKS")
        head.setProperty("role", "section")
        bl.addWidget(head)
        bl.addStretch(1)
        made.append(bar)
        add(bar)
        for stack in stacks:
            row = StackRow(stack, services)
            row.run.connect(self.run_stack)
            made.append(row)
            add(row)
        return made

    # -- visibility --------------------------------------------------------
    def _apply_visibility(self) -> None:
        """Hide rows of folded groups and rows the search excludes.

        Search wins: a matched row shows even if its group is shut, otherwise
        searching looks broken. And a hidden row is unticked — a tick you can't
        see is a bulk action you didn't mean.
        """
        query = (self.search.text() or "").strip().lower()
        for row in self._rows.values():
            if not isinstance(row, ServiceRow):
                continue
            matches = (query in row.service.display().lower()
                       or query in row.service.name.lower())
            folded = is_collapsed(getattr(row, "category", ""))
            row.setVisible(matches and (not folded or bool(query)))
            if row.isHidden() and row.tick.isChecked():
                row.tick.blockSignals(True)
                row.tick.setChecked(False)
                row.tick.blockSignals(False)

    def _filter(self, _text: str = "") -> None:
        # Visibility has two inputs — the search box and folded groups — so one
        # place decides it, and the selection is re-read because rows appeared
        # or vanished.
        self._apply_visibility()
        self._selection_changed(settled=False)

    def _section_toggled(self, _category: str = "", _folded: bool = False) -> None:
        self._apply_visibility()
        self._selection_changed(settled=False)

    # -- selection ---------------------------------------------------------
    def _service_rows(self, visible_only: bool = True) -> list:
        # isHidden(), not isVisible(): a row in a window that hasn't been shown
        # yet is invisible without having been filtered out, and asking the wrong
        # question there makes the selection silently empty.
        return [r for r in self._rows.values() if isinstance(r, ServiceRow)
                and (not r.isHidden() or not visible_only)]

    def selected(self) -> list:
        """The ticked rows, in the order they are shown."""
        return [r for r in self._service_rows() if r.tick.isChecked()]

    def _set_all(self, on: bool) -> None:
        for row in self._service_rows():
            row.tick.blockSignals(True)
            row.tick.setChecked(on)
            row.tick.blockSignals(False)
        self._selection_changed()

    def _toggle_all(self) -> None:
        # A tristate box clicks through to Partial, which as a *command* means
        # nothing — so treat any click as "select all unless all are selected".
        rows = self._service_rows()
        self._set_all(not (rows and all(r.tick.isChecked() for r in rows)))

    def _selection_changed(self, settled: bool = True) -> None:
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
        self._selection_settled(settled)

    def _selection_settled(self, settled: bool) -> None:
        """Called after the selection is re-read. The flyout resizes itself here;
        a window the user sized has nothing to do."""

    def _bulk(self, action: str) -> None:
        targets = [(r.service.name, r.service.machine) for r in self.selected()]
        if not targets:
            return
        self.bulk_requested.emit(action, targets)
        self._set_all(False)

    # -- live status -------------------------------------------------------
    def mark_busy(self, name: str, machine: str, label: str) -> None:
        row = self._rows.get((machine or "", name))
        if isinstance(row, ServiceRow):
            row.set_status(row.status, busy_label=label)

    def clear_busy(self, name: str, machine: str) -> None:
        """Our action has reported back, so the row can show the truth again.

        Explicit rather than expiring on the next repaint: the whole point is that
        it outlives repaints, so something has to say when it is over.
        """
        row = self._rows.get((machine or "", name))
        if isinstance(row, ServiceRow):
            row._busy = ""
