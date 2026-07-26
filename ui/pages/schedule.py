"""Schedule: triggers, what they run, and when they will next fire."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QHeaderView,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QStackedWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from core import config as cfg_mod
from core import history

from .. import theme
from ..widgets import Duration, PadSpin, button as _button, label as _label
from .base import _ListRow, _Page, _sentence


class SchedulePage(QWidget):
    """Triggers: a When and an Action, listed then edited on their own page."""

    changed = Signal()
    run_now = Signal(object)          # the trigger being edited

    def __init__(self, cfg_ref):
        super().__init__()
        self.cfg = cfg_ref
        self.stack_widget = QStackedWidget()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack_widget)

        self.list_page = _Page("Schedule",
                               "Make something happen without anyone watching — "
                               "after Windows starts, or at a time of day.")
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self._open())
        self.list_page.root.addWidget(self.list, 1)
        bar = QHBoxLayout()
        bar.addWidget(_button("New trigger…", "primary", self._new))
        bar.addWidget(_button("Open", None, self._open))
        bar.addWidget(_button("Delete", "danger", self._delete))
        bar.addStretch(1)
        self.list_page.root.addSpacing(14)
        self.list_page.root.addLayout(bar)

        # What actually happened, so a schedule isn't something you have to
        # trust blindly.
        self.list_page.root.addSpacing(22)
        exec_head = QHBoxLayout()
        exec_head.addWidget(_label("RECENT EXECUTIONS", "section"))
        exec_head.addStretch(1)
        exec_head.addWidget(_button("Refresh", "quiet", self.refresh_executions))
        self.list_page.root.addLayout(exec_head)
        self.list_page.root.addSpacing(8)
        self.executions = QTableWidget(0, 5)
        self.executions.setHorizontalHeaderLabels(
            ["Time", "What", "Name", "Outcome", "Took"])
        self.executions.verticalHeader().setVisible(False)
        self.executions.setShowGrid(False)
        self.executions.setEditTriggers(QTableWidget.NoEditTriggers)
        self.executions.setSelectionBehavior(QTableWidget.SelectRows)
        self.executions.setMaximumHeight(190)
        head = self.executions.horizontalHeader()
        for col in (0, 1, 3, 4):
            head.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(2, QHeaderView.Stretch)
        self.list_page.root.addWidget(self.executions)

        self.detail = TriggerDetail(cfg_ref)
        self.detail.back.connect(self._show_list)
        self.detail.changed.connect(self._refresh_and_signal)
        self.detail.run_now.connect(self.run_now)

        self.stack_widget.addWidget(self.list_page)
        self.stack_widget.addWidget(self.detail)
        self.refresh()

    OUTCOME_COLOUR = {"failed": "STOP_FG", "skipped": "PEND_FG",
                      "cancelled": "PEND_FG", "success": "RUN_FG"}

    def refresh_executions(self):
        try:
            rows = history.runs(limit=60)
        except Exception:
            rows = []
        self.executions.setRowCount(0)
        for rec in rows:
            row = self.executions.rowCount()
            self.executions.insertRow(row)
            ts = str(rec.get("ts", ""))
            day, _, clock = ts.partition("T")
            took = rec.get("seconds") or 0
            cells = [f"{day} {clock[:8]}", rec.get("run", ""), rec.get("name", ""),
                     rec.get("outcome", ""), f"{took:g}s" if took else ""]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if col == 0:
                    item.setFont(QFont(theme.MONO, 8))
                if col == 3:
                    colour = self.OUTCOME_COLOUR.get(rec.get("outcome", ""))
                    if colour:
                        item.setForeground(QColor(getattr(theme, colour)))
                if rec.get("detail"):
                    item.setToolTip(rec["detail"])
                self.executions.setItem(row, col, item)

    def refresh(self):
        self.refresh_executions()
        self.list.clear()
        services = self.cfg().services
        for t in self.cfg().triggers:
            item = QListWidgetItem()
            secondary = t.summary(services)
            if not t.enabled:
                secondary += "  ·  off"
            widget = _ListRow(t.name, secondary)
            item.setSizeHint(widget.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, widget)

    def _refresh_and_signal(self):
        self.refresh()
        self.changed.emit()

    def _new(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New trigger", "Name:")
        name = (name or "").strip()
        if not ok or not name:
            return
        if self.cfg().trigger(name):
            QMessageBox.information(self, "Service Officer",
                                    "A trigger with that name already exists.")
            return
        stacks = self.cfg().stacks
        self.cfg().triggers.append(cfg_mod.Trigger(
            name=name, stack=stacks[0].name if stacks else ""))
        self._refresh_and_signal()

    def _delete(self):
        row = self.list.currentRow()
        if row < 0:
            return
        trigger = self.cfg().triggers[row]
        if QMessageBox.question(self, "Delete trigger",
                                f'Delete "{trigger.name}"?') != QMessageBox.Yes:
            return
        del self.cfg().triggers[row]
        self._refresh_and_signal()

    def _open(self):
        row = self.list.currentRow()
        if row < 0:
            return
        self.detail.load(self.cfg().triggers[row])
        self.stack_widget.setCurrentWidget(self.detail)

    def _show_list(self):
        self.refresh()
        self.stack_widget.setCurrentWidget(self.list_page)


class TriggerDetail(_Page):
    back = Signal()
    changed = Signal()
    run_now = Signal(object)

    WHENS = ("startup", "time")
    DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    #: same order as the combo below
    NOTIFY = ("never", "success", "failed", "skipped", "failed_skipped",
              "both", "all")

    def __init__(self, cfg_ref):
        # Scrolls: When, Action, Tell me, the summary and the executions table
        # only just fitted a 640px window, and any longer summary pushed the Run
        # now button off the bottom.
        super().__init__("", "", scroll=True)
        self.cfg = cfg_ref
        self.trigger = None

        crumb = QHBoxLayout()
        crumb.setSpacing(6)
        crumb.addWidget(_button("Schedule", "quiet", self.back.emit))
        crumb.addWidget(_label("›", "hint"))
        self.crumb_name = _label("", "hint")
        crumb.addWidget(self.crumb_name)
        crumb.addStretch(1)
        self.head.insertLayout(0, crumb)
        self.title = _label("", "h2")
        self.head.addWidget(self.title)

        body = QVBoxLayout()
        body.setSpacing(0)

        self.enabled = QCheckBox("Enabled")
        self.enabled.toggled.connect(self._commit)
        body.addWidget(self.enabled)
        body.addSpacing(22)

        # -- when ----------------------------------------------------------
        body.addWidget(_label("WHEN", "section"))
        body.addSpacing(10)
        # When, and its parameters, on one line — the choice and its detail
        # belong together.
        self.when = QComboBox()
        self.when.addItems(["Windows starts", "At a time of day"])
        self.when.setFixedWidth(170)
        self.when.currentIndexChanged.connect(self._when_changed)

        self.startup_delay = Duration(30)
        self.startup_delay.changed.connect(self._commit)
        self.startup_row = _sentence("after", self.startup_delay)

        self.hour = PadSpin(3, 0, 23, 48)
        self.minute = PadSpin(0, 0, 59, 48)
        self.hour.valueChanged.connect(self._commit)
        self.minute.valueChanged.connect(self._commit)
        self.repeat = Duration(0)
        self.repeat.changed.connect(self._commit)
        self.time_inline = _sentence("at", self.hour, ":", self.minute,
                                     "· then every", self.repeat,
                                     "(leave 0 for once a day)")

        when_line = QWidget()
        wl = QHBoxLayout(when_line)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(10)
        wl.addWidget(self.when)
        wl.addWidget(self.startup_row)
        wl.addWidget(self.time_inline)
        wl.addStretch(1)
        body.addWidget(when_line)
        body.addSpacing(8)

        self.time_row = QWidget()
        tl = QVBoxLayout(self.time_row)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(10)
        days = QWidget()
        dl = QHBoxLayout(days)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(6)
        dl.addWidget(_label("on", "hint"))
        self.day_boxes = []
        for index, name in enumerate(self.DAYS):
            box = QCheckBox(name)
            box.toggled.connect(self._commit)
            self.day_boxes.append(box)
            dl.addWidget(box)
        dl.addStretch(1)
        tl.addWidget(days)
        tl.addWidget(_label("Leave every day unticked to mean every day. A trigger "
                            "missed while the machine was asleep runs when it wakes, "
                            "if it is less than half an hour late.", "hint", wrap=True))
        body.addWidget(self.time_row)
        body.addSpacing(24)

        # -- action --------------------------------------------------------
        body.addWidget(_label("ACTION", "section"))
        body.addSpacing(10)
        # Action and its parameters on one line too.
        self.action = QComboBox()
        self.action.addItems(["Run a stack", "Act on one service"])
        self.action.setFixedWidth(170)
        self.action.currentIndexChanged.connect(self._action_changed)

        self.stack_pick = QComboBox()
        self.stack_pick.setMinimumWidth(220)
        self.stack_pick.currentIndexChanged.connect(self._commit)
        self.service_action = QComboBox()
        self.service_action.addItems(["start", "stop", "restart"])
        self.service_action.setFixedWidth(96)
        self.service_action.currentIndexChanged.connect(self._commit)
        self.service_pick = QComboBox()
        self.service_pick.setMinimumWidth(220)
        self.service_pick.currentIndexChanged.connect(self._commit)

        action_line = QWidget()
        al = QHBoxLayout(action_line)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(10)
        al.addWidget(self.action)
        al.addWidget(self.stack_pick)
        al.addWidget(self.service_action)
        al.addWidget(self.service_pick)
        al.addStretch(1)
        body.addWidget(action_line)
        self.stack_row = self.stack_pick          # visibility handled per widget
        self.service_row = self.service_pick
        body.addSpacing(20)

        body.addWidget(_label("TELL ME", "section"))
        body.addSpacing(9)
        self.notify = QComboBox()
        self.notify.addItems(["Never", "On success", "On failure",
                              "When skipped", "On failure or skipped",
                              "On success or failure", "Every run, even skipped"])
        self.notify.setFixedWidth(240)
        self.notify.currentIndexChanged.connect(self._commit)
        body.addWidget(self.notify)
        body.addWidget(_label("Skipped means there was nothing to do — asking a "
                              "service to start when it is already running.",
                              "hint", wrap=True))
        body.addSpacing(20)
        self.summary = _label("", "hint", wrap=True)
        body.addWidget(self.summary)
        body.addSpacing(12)
        run = QHBoxLayout()
        run.addWidget(_button("Run now ▸", None,
                              lambda: self.run_now.emit(self.trigger)))
        run.addStretch(1)
        body.addLayout(run)

        body.addStretch(1)
        self.root.addLayout(body, 1)

    # -- loading -----------------------------------------------------------
    def load(self, trigger):
        self.trigger = None                  # quiet while populating
        self.title.setText(trigger.name)
        self.crumb_name.setText(trigger.name)
        self.enabled.setChecked(trigger.enabled)
        self.when.setCurrentIndex(self.WHENS.index(trigger.when))
        self.startup_delay.set_seconds(trigger.delay_seconds)
        try:
            hour, minute = (int(p) for p in trigger.time_of_day.split(":"))
        except ValueError:
            hour, minute = 3, 0
        self.hour.setValue(hour)
        self.minute.setValue(minute)
        self.repeat.set_seconds(trigger.repeat_seconds)
        self.notify.setCurrentIndex(self.NOTIFY.index(
            trigger.notify if trigger.notify in self.NOTIFY else "failed"))
        for index, box in enumerate(self.day_boxes):
            box.setChecked(index in trigger.days)

        cfg = self.cfg()
        self.stack_pick.clear()
        for s in cfg.stacks:
            self.stack_pick.addItem(s.name, s.name)
        if trigger.stack:
            idx = self.stack_pick.findData(trigger.stack)
            if idx >= 0:
                self.stack_pick.setCurrentIndex(idx)

        self.service_pick.clear()
        for s in cfg.services:
            self.service_pick.addItem(s.display(), s.name)
        if trigger.service:
            idx = self.service_pick.findData(trigger.service)
            if idx >= 0:
                self.service_pick.setCurrentIndex(idx)
        self.service_action.setCurrentIndex(
            ["start", "stop", "restart"].index(trigger.service_action))

        self.action.setCurrentIndex(0 if trigger.action == "stack" else 1)
        self.trigger = trigger
        self._sync_visibility()
        self._update_summary()

    # -- editing -----------------------------------------------------------
    def _sync_visibility(self):
        by_time = self.when.currentIndex() == 1
        self.startup_row.setVisible(not by_time)
        self.time_inline.setVisible(by_time)
        self.time_row.setVisible(by_time)
        by_service = self.action.currentIndex() == 1
        self.stack_pick.setVisible(not by_service)
        self.service_action.setVisible(by_service)
        self.service_pick.setVisible(by_service)

    def _when_changed(self, _index):
        self._sync_visibility()
        self._commit()

    def _action_changed(self, _index):
        self._sync_visibility()
        self._commit()

    def _commit(self, *_):
        if self.trigger is None:
            return
        t = self.trigger
        t.enabled = self.enabled.isChecked()
        t.when = self.WHENS[self.when.currentIndex()]
        t.delay_seconds = self.startup_delay.seconds()
        t.time_of_day = f"{self.hour.value():02d}:{self.minute.value():02d}"
        t.days = [i for i, box in enumerate(self.day_boxes) if box.isChecked()]
        t.repeat_seconds = self.repeat.seconds()
        t.notify = self.NOTIFY[self.notify.currentIndex()]
        t.action = "stack" if self.action.currentIndex() == 0 else "service"
        t.stack = self.stack_pick.currentData() or ""
        t.service = self.service_pick.currentData() or ""
        t.service_action = ["start", "stop", "restart"][
            self.service_action.currentIndex()]
        self._update_summary()
        self.changed.emit()

    def _update_summary(self):
        if self.trigger is None:
            return
        text = "In words: " + self.trigger.summary(self.cfg().services)
        # Say when it will actually happen — the schedule is easy to get wrong
        # and there is no way to tell by looking at the fields.
        from core.schedule import Scheduler
        probe = Scheduler(self.cfg, lambda _t: None)
        when = probe.next_run_at(self.trigger)
        if when is not None:
            text += f"\nNext run: {when.strftime('%a %d %b, %H:%M')}"
        elif self.trigger.when == "startup":
            text += "\nNext run: the next time Windows starts"
        elif not self.trigger.enabled:
            text += "\nNext run: never — this trigger is switched off"
        self.summary.setText(text)
