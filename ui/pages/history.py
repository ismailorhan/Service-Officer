"""History: what the app did, and where it wrote it down."""

from __future__ import annotations

import os

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QMessageBox, QTableWidget,
                               QTableWidgetItem)

from core import clock, history

from .. import theme
from ..widgets import button as _button, label as _label
from .base import _Page, _spin


class _TimeCell(QTableWidgetItem):
    """A time column that sorts by the moment, not by the text it shows.

    The header is clickable, and comparing local strings puts the two 02:30s of a
    daylight-saving night in the wrong order — as it does for any row still
    carrying an older local offset. The moment is kept beside the text.
    """

    def __init__(self, text: str, stored: str):
        super().__init__(text)
        self._when = clock.sort_key(stored)

    def __lt__(self, other):
        if isinstance(other, _TimeCell):
            return self._when < other._when
        return super().__lt__(other)


class HistoryPage(_Page):
    changed = Signal()
    #: Another machine's event log came back. Carries nothing: the records are in the cache,
    #: and a signal is how a worker thread asks the GUI thread to draw them.
    remote_logs_arrived = Signal()

    # "Asked by" is last and hidden unless something in view fills it: on a
    # single-machine install nothing ever does, and an always-empty column would
    # only take width away from Detail.
    COLUMNS = ("Time", "Service", "Event", "Detail", "Source", "Asked by")
    RANGES = (("Last hour", 1), ("Last 8 hours", 8), ("Last 24 hours", 24),
              ("Last 7 days", 24 * 7), ("Last 30 days", 24 * 30), ("Everything", 0))
    #: which cause produced the row. "observed" covers anything we didn't do —
    #: a crash, or someone stopping the service outside this app.
    SOURCES = (("Any trigger", None),
               ("You, from the panel", "you, from the panel"),
               ("Scheduled trigger", "scheduled trigger"),
               ("Stack run", "stack run"),
               ("Watchdog", "watchdog"),
               ("Outside this app", "observed"),
               ("Windows event log", "Windows event log"))

    def __init__(self, cfg_ref):
        super().__init__("History",
                         "Every state change, with its cause — evidence for a "
                         "ticket, and the only way to see a service that keeps "
                         "dying quietly.")
        self.cfg = cfg_ref
        #: Filling the table means reading the history file. The panel opens on
        #: the Dashboard, so that read happened before anyone had asked to see
        #: it — 340 ms of a 300 ms window open, on a 2.8 MB file. Load when the
        #: page is first shown instead, and again whenever it goes stale.
        self._stale = True
        #: {machine: [records]} for machines whose logs have been read, and the key those
        #: were read for. Kept so sorting a column does not cost a WinRM call.
        self._remote_logs: dict = {}
        self._remote_key = None
        self._remote_busy = False
        self.remote_logs_arrived.connect(self._draw)

        row = QHBoxLayout()
        row.setSpacing(9)
        self.enabled = QCheckBox("Record state changes")
        self.enabled.toggled.connect(self._set_enabled)
        row.addWidget(self.enabled)
        row.addStretch(1)
        row.addWidget(_label("keep", "hint"))
        self.retention = _spin(30, 1, 365)
        self.retention.valueChanged.connect(self._set_retention)
        row.addWidget(self.retention)
        row.addWidget(_label("days", "hint"))
        self.root.addLayout(row)
        self.root.addSpacing(14)

        # Two rows, not one. Ten controls side by side needed 1,044 px, which set
        # the whole window's minimum width to 1,286 — wider than a 1280 screen, on
        # a tool that is used over RDP to customer servers. Split by question:
        # which rows to show, then how much of each and what to do with them.
        filt = QHBoxLayout()
        filt.setSpacing(8)
        self.service_filter = QComboBox()
        self.service_filter.setMinimumWidth(200)
        self.service_filter.currentIndexChanged.connect(self.reload)
        filt.addWidget(_label("Service", "hint"))
        filt.addWidget(self.service_filter)

        self.range_filter = QComboBox()
        for text, hours in self.RANGES:
            self.range_filter.addItem(text, hours)
        self.range_filter.setCurrentIndex(2)
        self.range_filter.currentIndexChanged.connect(self.reload)
        filt.addWidget(_label("Range", "hint"))
        filt.addWidget(self.range_filter)

        self.source_filter = QComboBox()
        for text, value in self.SOURCES:
            self.source_filter.addItem(text, value)
        self.source_filter.currentIndexChanged.connect(self.reload)
        filt.addWidget(_label("Trigger", "hint"))
        filt.addWidget(self.source_filter)

        # Only offered once something is actually filtered — a permanently
        # visible "clear" invites the question of what it would clear.
        self.clear_filters = _button(f"Clear filters {theme.GLYPH_KILL}",
                                     "quiet", self._clear_filters)
        self.clear_filters.setToolTip("Back to all services, the last 24 hours, "
                                      "any trigger.")
        self.clear_filters.setVisible(False)
        filt.addWidget(self.clear_filters)
        filt.addStretch(1)
        self.root.addLayout(filt)
        self.root.addSpacing(8)

        opts = QHBoxLayout()
        opts.setSpacing(8)
        self.full_detail = QCheckBox("Full detail")
        self.full_detail.setToolTip(
            "Every state the SCM reported, including the halfway ones. Off, a "
            "restart reads as “restart requested” then “Running” instead of four "
            "rows saying the same thing. Nothing is left out of the file either "
            "way.")
        self.full_detail.toggled.connect(self.reload)
        opts.addWidget(self.full_detail)

        self.include_windows = QCheckBox("Windows event log")
        self.include_windows.setToolTip(
            "Merge what Windows recorded about these services — the SCM's "
            "\"terminated unexpectedly\", and errors the service itself logged. "
            "This is usually where the reason is.")
        self.include_windows.toggled.connect(self.reload)
        opts.addWidget(self.include_windows)

        opts.addStretch(1)
        opts.addWidget(_button("Refresh", "quiet", self.reload))
        opts.addWidget(_button("Export CSV…", "quiet", self._export))
        self.root.addLayout(opts)
        self.root.addSpacing(10)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)   # time
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)   # service
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)   # event
        header.setSectionResizeMode(3, QHeaderView.Stretch)            # detail
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)   # source
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)   # asked by
        # Hidden until a loaded row fills it (see reload). An empty table has nobody
        # to attribute, and the page is built long before it is first read.
        self.table.setColumnHidden(self.COLUMNS.index("Asked by"), True)
        self.root.addWidget(self.table, 1)

        self.count = _label("", "hint")
        self.root.addSpacing(6)
        self.root.addWidget(self.count)

        # Where this actually lives on disk, spelled out — the log is evidence,
        # and evidence you can't find is no use in a ticket.
        where = QHBoxLayout()
        where.setSpacing(8)
        self.path_label = _label("", "hint")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        where.addWidget(self.path_label)
        where.addWidget(_button("Open folder", "quiet", self._open_folder))
        where.addStretch(1)
        self.root.addLayout(where)
        self._show_path()

    # -- filters -----------------------------------------------------------
    DEFAULT_RANGE = 2                      # index of "Last 24 hours"

    def _filtered(self) -> bool:
        return (self.service_filter.currentData() is not None
                or self.range_filter.currentIndex() != self.DEFAULT_RANGE
                or self.source_filter.currentData() is not None
                or self.include_windows.isChecked()
                or self.full_detail.isChecked())

    def _clear_filters(self):
        widgets = (self.service_filter, self.range_filter, self.source_filter,
                   self.include_windows, self.full_detail)
        for widget in widgets:
            widget.blockSignals(True)
        self.service_filter.setCurrentIndex(0)
        self.range_filter.setCurrentIndex(self.DEFAULT_RANGE)
        self.source_filter.setCurrentIndex(0)
        self.include_windows.setChecked(False)
        self.full_detail.setChecked(False)
        for widget in widgets:
            widget.blockSignals(False)
        self.reload()

    # -- the file on disk ---------------------------------------------------
    def _set_path_state(self, state: str) -> None:
        """"error" turns the line red, via the sheet rather than an inline colour."""
        if self.path_label.property("state") != state:
            self.path_label.setProperty("state", state)
            self.path_label.style().unpolish(self.path_label)
            self.path_label.style().polish(self.path_label)

    def _show_path(self):
        path = history.path()
        try:
            size = os.path.getsize(path)
            note = f"  ·  {size / 1024:.0f} KB"
        except OSError:
            note = "  ·  not written yet"
        broken = history.last_error()
        if broken:
            # An empty timeline that turns out to be a permissions problem reads
            # as "nothing happened" until somebody checks, so say it here.
            self.path_label.setText(
                f"Cannot write the history — {broken}. Is Service Officer "
                f"running as administrator?")
            self._set_path_state("error")
            return
        self._set_path_state("")
        self.path_label.setText(f"Written to  {path}{note}")

    def _open_folder(self):
        folder = os.path.dirname(history.path())
        try:
            os.startfile(folder)                       # noqa: S606 - Explorer
        except Exception as exc:
            QMessageBox.warning(self, "Service Officer",
                                f"Could not open\n{folder}\n\n{exc}")

    def _set_enabled(self, on):
        self.cfg().history.enabled = on
        self.changed.emit()

    def _set_retention(self, days):
        self.cfg().history.retention_days = days
        self.changed.emit()

    def load_from(self, cfg):
        self.enabled.blockSignals(True)
        self.retention.blockSignals(True)
        self.enabled.setChecked(cfg.history.enabled)
        self.retention.setValue(cfg.history.retention_days)
        self.enabled.blockSignals(False)
        self.retention.blockSignals(False)

        self.service_filter.blockSignals(True)
        self.service_filter.clear()
        self.service_filter.addItem("All services", None)
        for svc in cfg.services:
            self.service_filter.addItem(svc.display(), svc.name)
        self.service_filter.blockSignals(False)
        # The filters are cheap and must be right before the page is shown; the
        # table itself waits until somebody looks at it.
        self._stale = True
        if not self.isHidden():
            self.reload()

    def showEvent(self, ev):
        super().showEvent(ev)
        if self._stale:
            self.reload()

    def _current_rows(self) -> list:
        cfg = self.cfg()
        names = [s.name for s in cfg.services]
        labels = [s.display() for s in cfg.services]
        try:
            rows = history.query(
                service_names=names, labels=labels,
                service=self.service_filter.currentData(),
                hours=self.range_filter.currentData() or None,
                include_windows=self.include_windows.isChecked(),
                # Which of them are on this computer, because the Windows event log
                # is only this computer's — see history.query.
                local_services=[s.name for s in cfg.services if not s.machine],
                # Read already, on a worker thread — see _fetch_remote_logs.
                remote_events=self._remote_logs if self.include_windows.isChecked() else None,
                full=self.full_detail.isChecked())
        except Exception:
            return []
        wanted = self.source_filter.currentData()
        if wanted:
            rows = [r for r in rows if r.get("source", "").startswith(wanted)]
        return rows

    def reload(self):
        """Draw what is here, then go and ask the machines that are not."""
        self._stale = False
        self._draw()
        self._fetch_remote_logs()

    def _remote_machines(self) -> list:
        """The machines a timeline would have to ask, and what to ask them for.

        Only where the switch is on: reading a log is not worth starting a PowerShell process
        against a machine whose owner has said no. And only for services actually in view, so
        filtering to one service asks one machine rather than all of them.
        """
        cfg = self.cfg()
        chosen = self.service_filter.currentData()
        wanted = []
        for svc in cfg.services:
            if not svc.machine or (chosen and svc.name != chosen):
                continue
            machine = cfg.machine(svc.machine)
            if machine is None or machine.is_linux or not getattr(machine, "winrm", False):
                continue
            wanted.append((svc.machine, svc.name, svc.display()))
        return wanted

    def _fetch_remote_logs(self) -> None:
        """One worker for the whole set, if the answer is not already the right one."""
        if not self.include_windows.isChecked():
            return
        hours = self.range_filter.currentData() or None
        wanted = self._remote_machines()
        key = (tuple(sorted(wanted)), hours)
        if not wanted or key == self._remote_key or self._remote_busy:
            return
        self._remote_busy = True
        threading.Thread(target=self._read_remote_logs, args=(wanted, hours, key),
                         daemon=True).start()

    def _read_remote_logs(self, wanted, hours, key) -> None:
        """On a worker thread. Never raises into it — a failed read is no rows, and the
        machine's own page is where a broken WinRM is explained."""
        from core import control

        found: dict = {}
        try:
            cfg = self.cfg()
            for machine_name, service_name, label in wanted:
                record = cfg.machine(machine_name)
                try:
                    got = control.log_records(service_name, machine_name, label,
                                              hours or 168, record=record)
                except Exception:
                    got = []
                if got:
                    found.setdefault(machine_name, []).extend(got)
        finally:
            self._remote_logs = found
            self._remote_key = key
            self._remote_busy = False
            self.remote_logs_arrived.emit()

    def _draw(self):
        rows = self._current_rows()
        self._rows_cache = rows
        self.clear_filters.setVisible(self._filtered())
        self._show_path()

        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for r in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            stored = r.get("ts", "")
            cells = [clock.local_text(stored), r.get("label") or r.get("service", ""),
                     r.get("event", ""), r.get("detail", ""), r.get("source", ""),
                     r.get("actor", "")]
            for col, text in enumerate(cells):
                item = (_TimeCell(text, stored) if col == 0
                        else QTableWidgetItem(text))
                if col == 0:
                    item.setFont(QFont(theme.MONO, 8))
                if r.get("level") == "Error":
                    item.setForeground(QColor(theme.STOP_FG))
                elif r.get("level") == "Warning":
                    item.setForeground(QColor(theme.PEND_FG))
                elif r.get("kind") == "windows":
                    item.setForeground(QColor(theme.FG2))
                if col == 3 and text:
                    item.setToolTip(text)           # messages can be long
                self.table.setItem(row, col, item)
        self.table.setSortingEnabled(True)
        self.table.setColumnHidden(
            self.COLUMNS.index("Asked by"),
            not any(r.get("actor") for r in rows))

        shown = len(rows)
        extra = sum(1 for r in rows if r.get("kind") == "windows")
        parts = [f"{shown} entr{'y' if shown == 1 else 'ies'}"]
        if extra:
            parts.append(f"{extra} from the Windows event log")
        self.count.setText("  ·  ".join(parts) if shown else "Nothing recorded yet.")

    def _export(self):
        excel = "For Excel (*.csv)"
        plain = "Plain comma-separated (*.csv)"
        dest, chosen = QFileDialog.getSaveFileName(
            self, "Export history", "history.csv", f"{excel};;{plain}")
        if not dest:
            return
        try:
            # Export exactly what is on screen, filters and all.
            n = history.export_csv(dest, rows=self._current_rows(),
                                   for_excel=(chosen != plain))
            QMessageBox.information(self, "Service Officer",
                                    f"Exported {n} rows to\n{dest}")
        except Exception as exc:
            QMessageBox.warning(self, "Service Officer", f"Export failed:\n{exc}")
