"""Schedule: triggers, what they run, and when they will next fire."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QHeaderView,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QStackedWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from core import clock
from core import config as cfg_mod
from core import history
from core.i18n import t

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
        bar.addWidget(_button(t("New trigger…"), "primary", self._new))
        bar.addWidget(_button(t("Open"), None, self._open))
        bar.addWidget(_button(t("Delete"), "danger", self._delete))
        bar.addStretch(1)
        self.list_page.root.addSpacing(14)
        self.list_page.root.addLayout(bar)

        # What actually happened, so a schedule isn't something you have to
        # trust blindly.
        self.list_page.root.addSpacing(22)
        exec_head = QHBoxLayout()
        exec_head.addWidget(_label(t("RECENT EXECUTIONS"), "section"))
        exec_head.addStretch(1)
        exec_head.addWidget(_button(t("Refresh"), "quiet", self.refresh_executions))
        self.list_page.root.addLayout(exec_head)
        self.list_page.root.addSpacing(8)
        self.executions = QTableWidget(0, 5)
        self.executions.setHorizontalHeaderLabels(
            [t("Time"), t("What"), t("Name"), t("Outcome"), t("Took")])
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
            took = rec.get("seconds") or 0
            cells = [clock.local_text(rec.get("ts", "")), rec.get("run", ""),
                     rec.get("name", ""),
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
        for trigger in self.cfg().triggers:
            item = QListWidgetItem()
            secondary = trigger.summary(services)
            if not trigger.enabled:
                secondary += "  ·  " + t("off")
            widget = _ListRow(trigger.name, secondary)
            item.setSizeHint(widget.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, widget)

    def _refresh_and_signal(self):
        self.refresh()
        self.changed.emit()

    def _new(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, t("New trigger"), t("Name:"))
        name = (name or "").strip()
        if not ok or not name:
            return
        if self.cfg().trigger(name):
            QMessageBox.information(self, "Service Officer",
                                    t("A trigger with that name already exists."))
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
        if QMessageBox.question(self, t("Delete trigger"),
                                t('Delete "{name}"?',
                                  name=trigger.name)) != QMessageBox.Yes:
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
        crumb.addWidget(_button(f"{theme.GLYPH_BACK}  Schedule", "quiet", self.back.emit))
        crumb.addWidget(_label(theme.GLYPH_CRUMB, "hint"))
        self.crumb_name = _label("", "hint")
        crumb.addWidget(self.crumb_name)
        crumb.addStretch(1)
        self.head.insertLayout(0, crumb)
        self.title = _label("", "h2")
        self.head.addWidget(self.title)

        body = QVBoxLayout()
        body.setSpacing(0)

        self.enabled = QCheckBox(t("Enabled"))
        self.enabled.toggled.connect(self._commit)
        body.addWidget(self.enabled)
        body.addSpacing(22)

        # -- when ----------------------------------------------------------
        body.addWidget(_label(t("WHEN"), "section"))
        body.addSpacing(10)
        # When, and its parameters, on one line — the choice and its detail
        # belong together.
        self.when = QComboBox()
        # The index picks the value out of WHENS, so translating the words is safe.
        self.when.addItems([t("Windows starts"), t("At a time of day")])
        self.when.setFixedWidth(170)
        self.when.currentIndexChanged.connect(self._when_changed)

        self.startup_delay = Duration(30)
        self.startup_delay.changed.connect(self._commit)
        # "Wait [30s]", not "after [30s]": Turkish puts "sonra" *after* the duration, and a
        # sentence assembled from fragments has no way to reorder them. A verb both languages
        # put first says the same thing. Same lesson as the Recovery rows.
        self.startup_row = _sentence(t("Wait"), self.startup_delay)

        self.hour = PadSpin(3, 0, 23, 48)
        self.minute = PadSpin(0, 0, 59, 48)
        self.hour.valueChanged.connect(self._commit)
        self.minute.valueChanged.connect(self._commit)
        self.repeat = Duration(0)
        self.repeat.changed.connect(self._commit)
        self.time_inline = _sentence(t("at"), self.hour, ":", self.minute,
                                     t("· then every"), self.repeat,
                                     t("(leave 0 for once a day)"))

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
        dl.addWidget(_label(t("on"), "hint"))
        self.day_boxes = []
        for index, name in enumerate(self.DAYS):
            box = QCheckBox(t(name))          # a class constant; translated where drawn
            box.toggled.connect(self._commit)
            self.day_boxes.append(box)
            dl.addWidget(box)
        dl.addStretch(1)
        tl.addWidget(days)
        tl.addWidget(_label(t("Leave every day unticked to mean every day. A trigger missed "
                              "while the machine was asleep runs when it wakes, if it is less "
                              "than half an hour late."), "hint", wrap=True))
        body.addWidget(self.time_row)
        body.addSpacing(24)

        # -- action --------------------------------------------------------
        body.addWidget(_label(t("ACTION"), "section"))
        body.addSpacing(10)
        # Action and its parameters on one line too.
        self.action = QComboBox()
        self.action.addItems([t("Run a stack"), t("Act on one service")])
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

        body.addWidget(_label(t("TELL ME"), "section"))
        body.addSpacing(9)
        self.notify = QComboBox()
        # The index picks the value out of NOTIFY, so these are only words on screen.
        self.notify.addItems([t("Never"), t("On success"), t("On failure"),
                              t("When skipped"), t("On failure or skipped"),
                              t("On success or failure"), t("Every run, even skipped")])
        self.notify.setFixedWidth(240)
        self.notify.currentIndexChanged.connect(self._commit)
        body.addWidget(self.notify)
        body.addWidget(_label(t("Skipped means there was nothing to do — asking a service "
                                "to start when it is already running."),
                              "hint", wrap=True))
        body.addSpacing(20)
        self.summary = _label("", "hint", wrap=True)
        body.addWidget(self.summary)
        body.addSpacing(12)
        run = QHBoxLayout()
        run.addWidget(_button(f"{t('Run now')} {theme.GLYPH_FOLDED}", None,
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
        trigger = self.trigger
        trigger.enabled = self.enabled.isChecked()
        trigger.when = self.WHENS[self.when.currentIndex()]
        trigger.delay_seconds = self.startup_delay.seconds()
        trigger.time_of_day = f"{self.hour.value():02d}:{self.minute.value():02d}"
        trigger.days = [i for i, box in enumerate(self.day_boxes) if box.isChecked()]
        trigger.repeat_seconds = self.repeat.seconds()
        trigger.notify = self.NOTIFY[self.notify.currentIndex()]
        trigger.action = "stack" if self.action.currentIndex() == 0 else "service"
        trigger.stack = self.stack_pick.currentData() or ""
        trigger.service = self.service_pick.currentData() or ""
        trigger.service_action = ["start", "stop", "restart"][
            self.service_action.currentIndex()]
        self._update_summary()
        self.changed.emit()

    def _update_summary(self):
        if self.trigger is None:
            return
        text = t("In words: {summary}",
                 summary=self.trigger.summary(self.cfg().services))
        # Say when it will actually happen — the schedule is easy to get wrong
        # and there is no way to tell by looking at the fields.
        from core.schedule import Scheduler
        probe = Scheduler(self.cfg, lambda _t: None)
        when = probe.next_run_at(self.trigger)
        if when is not None:
            text += "\n" + t("Next run: {when}",
                             when=when.strftime(t("%a %d %b, %H:%M")))
        elif self.trigger.when == "startup":
            text += "\n" + t("Next run: the next time Windows starts")
        elif not self.trigger.enabled:
            text += "\n" + t("Next run: never — this trigger is switched off")
        self.summary.setText(text)
