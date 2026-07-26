"""Stacks: an ordered run of services, and the steps that make one up."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem,
                               QMessageBox, QScrollArea, QStackedWidget,
                               QVBoxLayout, QWidget)

from core import config as cfg_mod

from .. import theme
from ..widgets import (Duration, Grip, SearchableList, button as _button,
                       label as _label)
from .base import _ListRow, _Page


class StacksPage(QWidget):
    """Stack list → stack detail with the ordered steps."""

    changed = Signal()
    test_run = Signal(object, str)   # the stack being edited, action

    def __init__(self, cfg_ref):
        super().__init__()
        self.cfg = cfg_ref
        self.stack_widget = QStackedWidget()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack_widget)

        self.list_page = _Page("Stacks",
                               "An ordered group you can start, stop or restart in "
                               "one go. Stopping walks the order backwards.")
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self._open())
        self.list_page.root.addWidget(self.list, 1)
        bar = QHBoxLayout()
        bar.addWidget(_button("New stack…", "primary", self._new))
        bar.addWidget(_button("Open", None, self._open))
        bar.addWidget(_button("Delete", "danger", self._delete))
        bar.addStretch(1)
        self.list_page.root.addSpacing(14)
        self.list_page.root.addLayout(bar)

        self.detail = StackDetail(cfg_ref)
        self.detail.back.connect(self._show_list)
        self.detail.changed.connect(self._refresh_and_signal)
        self.detail.test_run.connect(self.test_run)

        self.stack_widget.addWidget(self.list_page)
        self.stack_widget.addWidget(self.detail)
        self.refresh()

    def refresh(self):
        self.list.clear()
        services = self.cfg().services
        for s in self.cfg().stacks:
            item = QListWidgetItem()
            widget = _ListRow(s.name, s.summary(services) or "no steps yet")
            item.setSizeHint(widget.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, widget)

    def _refresh_and_signal(self):
        self.refresh()
        self.changed.emit()

    def _new(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New stack", "Name:")
        name = (name or "").strip()
        if not ok or not name:
            return
        if self.cfg().stack(name):
            QMessageBox.information(self, "Service Officer",
                                    "A stack with that name already exists.")
            return
        self.cfg().stacks.append(cfg_mod.Stack(name=name))
        self._refresh_and_signal()

    def _delete(self):
        row = self.list.currentRow()
        if row < 0:
            return
        stack = self.cfg().stacks[row]
        if QMessageBox.question(self, "Delete stack",
                                f'Delete "{stack.name}"?') != QMessageBox.Yes:
            return
        del self.cfg().stacks[row]
        self._refresh_and_signal()

    def _open(self):
        row = self.list.currentRow()
        if row < 0:
            return
        self.detail.load(self.cfg().stacks[row])
        self.stack_widget.setCurrentWidget(self.detail)

    def _show_list(self):
        self.refresh()
        self.stack_widget.setCurrentWidget(self.list_page)


class StackDetail(_Page):
    back = Signal()
    changed = Signal()
    test_run = Signal(object, str)

    def __init__(self, cfg_ref):
        super().__init__("", "")
        self.cfg = cfg_ref
        self.stack = None

        crumb = QHBoxLayout()
        crumb.setSpacing(6)
        crumb.addWidget(_button(f"{theme.GLYPH_BACK}  Stacks", "quiet", self.back.emit))
        crumb.addWidget(_label(theme.GLYPH_CRUMB, "hint"))
        self.crumb_name = _label("", "hint")
        crumb.addWidget(self.crumb_name)
        crumb.addStretch(1)
        self.head.insertLayout(0, crumb)

        self.title = _label("", "h2")
        self.head.addWidget(self.title)
        self.head.addWidget(_label(
            "Each step starts, then waits before the next one begins. Some "
            "services report Running before they can actually serve, so a fixed "
            "wait is sometimes the honest answer. Drag a step by its handle to "
            "change the order.", "hint", wrap=True))
        self.in_flyout = QCheckBox("Show in the tray panel")
        self.in_flyout.setToolTip("Offer this stack with a Run button under the "
                                  "services, next to where you notice a problem.")
        self.in_flyout.toggled.connect(self._toggle_flyout)
        self.head.addWidget(self.in_flyout)

        self.steps_area = QScrollArea()
        self.steps_area.setWidgetResizable(True)
        self.steps_host = QWidget()
        self.steps_lay = QVBoxLayout(self.steps_host)
        self.steps_lay.setContentsMargins(0, 0, 0, 0)
        self.steps_lay.setSpacing(8)
        self.steps_lay.addStretch(1)
        self.steps_area.setWidget(self.steps_host)
        self.root.addWidget(self.steps_area, 1)

        bar = QHBoxLayout()
        bar.addWidget(_button("Add step…", "primary", self._add_step))
        bar.addWidget(_button("Remove step", "danger", self._remove_step))
        bar.addStretch(1)
        # Hand over the stack being edited, not its name: a test run has to use
        # what's on screen, otherwise it silently tests the last saved values.
        bar.addWidget(_button(f"Test run {theme.GLYPH_FOLDED}", None,
                              lambda: self.test_run.emit(self.stack, "start")))
        self.root.addSpacing(12)
        self.root.addLayout(bar)

        self._rows = []
        self._selected = -1
        self._drop_at = -1

    def load(self, stack):
        self.stack = None
        self.title.setText(stack.name)
        self.crumb_name.setText(stack.name)
        self.in_flyout.blockSignals(True)
        self.in_flyout.setChecked(stack.show_in_flyout)
        self.in_flyout.blockSignals(False)
        self.stack = stack
        self._rebuild()

    def _toggle_flyout(self, on):
        if self.stack is not None:
            self.stack.show_in_flyout = on
            self.changed.emit()

    def _rebuild(self):
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows = []
        labels = {s.name: s.display() for s in self.cfg().services}

        for i, step in enumerate(self.stack.steps, start=1):
            row = QWidget()
            row.setObjectName("steprow")
            row.setAttribute(Qt.WA_StyledBackground, True)
            # Scope the selector to this widget: a bare "QWidget {...}" here is
            # inherited by every child, which is what painted green borders
            # around each inner control.
            rl = QHBoxLayout(row)
            rl.setContentsMargins(2, 6, 2, 6)
            rl.setSpacing(11)

            grip = Grip(i - 1, lambda: [r for r in self._rows
                                        if not isinstance(r, QLabel)])
            grip.dragging.connect(self._show_drop)
            grip.moved.connect(self._reorder)
            rl.addWidget(grip)

            num = _label(str(i))
            num.setFixedSize(24, 24)
            num.setAlignment(Qt.AlignCenter)
            # theme.BG_RAISE, not a fixed dark grey: the step number's disc was
            # near-black on a white page in light mode.
            num.setObjectName("stepNum")
            num.setAttribute(Qt.WA_StyledBackground, True)
            rl.addWidget(num)

            col = QVBoxLayout()
            col.setSpacing(3)
            col.addWidget(_label(labels.get(step.service, step.service), "strong"))

            # Second line reads as a sentence: "Start this service, then wait …"
            # The wait describes the gap *to the next step*, so the last row has
            # nothing to configure — a single step has no transition at all.
            is_last = (i == len(self.stack.steps))
            col.addWidget(self._step_line(step, is_last))
            rl.addLayout(col, 1)

            def select(_ev=None, idx=i - 1):
                self._selected = idx
                self._highlight()
            row.mousePressEvent = select

            self.steps_lay.insertWidget(self.steps_lay.count() - 1, row)
            self._rows.append(row)

        if not self.stack.steps:
            empty = _label("No steps yet — add the first service to start.", "hint")
            self.steps_lay.insertWidget(self.steps_lay.count() - 1, empty)
            self._rows.append(empty)
        self._highlight()

    def _step_line(self, step, is_last: bool) -> QWidget:
        """`[start ▾] this service, then wait …` — the action leads the sentence."""
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        act = QComboBox()
        act.addItems(["start", "stop", "restart"])
        act.setCurrentIndex(["start", "stop", "restart"].index(step.action))
        act.setFixedWidth(96)
        act.setToolTip("What this step does to the service. The stack runs these "
                       "in order, so it reads as a script.")

        def commit_action(_=None, s=step, a=act):
            s.action = ["start", "stop", "restart"][a.currentIndex()]
            self._rebuild()              # the wait's target state changed with it
            self.changed.emit()
        act.currentIndexChanged.connect(commit_action)
        lay.addWidget(act)

        if is_last:
            lay.addWidget(_label(
                f"this service — verified {step.target_state.lower()}, up to "
                f"{step.timeout_seconds}s", "hint"))
            lay.addStretch(1)
        else:
            lay.addWidget(_label("this service, then wait", "hint"))
            lay.addWidget(self._gap_editor(step), 1)
        return row

    def _gap_editor(self, step) -> QWidget:
        """Controls for the gap between this step and the next.

        "until running" is two numbers, not one: how much longer to wait once it
        reports Running (services often need a moment more), and how long to keep
        waiting before abandoning the run. "a fixed" is one number and ignores
        the status entirely.
        """
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        mode = QComboBox()
        # One option covers both directions: "applied" means the state the step
        # was trying to produce — Running for start/restart, Stopped for stop.
        mode.addItems(["until applied", "a fixed"])
        mode.setCurrentIndex(0 if step.wait == "applied" else 1)
        mode.setFixedWidth(120)
        mode.setToolTip(f"until applied — wait until this service is "
                        f"{step.target_state.lower()}.\n"
                        f"a fixed — wait a set time, whatever it reports.")
        lay.addWidget(mode)

        plus = _label("+", "hint")
        grace = Duration(step.grace_seconds)
        grace.setToolTip("Extra pause once it reports Running, before the next "
                         "step starts. Many services need a moment more.")
        fixed = Duration(step.delay_seconds)
        fixed.setToolTip("Wait exactly this long, whatever the service reports.")
        sep = _label("·  give up after", "hint")
        timeout = Duration(step.timeout_seconds, minimum=1)
        timeout.setToolTip("If it hasn't reached its target state by then, stop "
                           "the run here — later steps are not attempted.")

        for w in (plus, grace, fixed, sep, timeout):
            lay.addWidget(w)
        lay.addStretch(1)

        def sync():
            running = mode.currentIndex() == 0
            plus.setVisible(running)
            grace.setVisible(running)
            fixed.setVisible(not running)
            sep.setVisible(running)
            timeout.setVisible(running)

        def commit(*_):
            running = mode.currentIndex() == 0
            step.wait = "applied" if running else "delay"
            if running:
                step.grace_seconds = grace.seconds()
                step.timeout_seconds = max(1, timeout.seconds())
            else:
                step.delay_seconds = fixed.seconds()
            sync()
            self.changed.emit()

        mode.currentIndexChanged.connect(commit)
        grace.changed.connect(commit)
        fixed.changed.connect(commit)
        timeout.changed.connect(commit)
        sync()
        return row

    def _highlight(self):
        """Selection is a quiet marker on the left edge — nothing else. Tinting
        the row and its controls green was noisy to look at."""
        for i, row in enumerate(self._rows):
            if isinstance(row, QLabel):
                continue
            on = (i == self._selected)
            drop = (i == self._drop_at)
            # Properties, not a stylesheet per row: the colours live in the sheet
            # so a theme change is still a single pass.
            for name, value in (("sel", on), ("drop", drop)):
                if row.property(name) != ("true" if value else "false"):
                    row.setProperty(name, "true" if value else "false")
                    row.style().unpolish(row)
                    row.style().polish(row)

    def _show_drop(self, index):
        """Outline where a dragged step would land. -1 clears it."""
        if index != self._drop_at:
            self._drop_at = index
            self._highlight()

    def _reorder(self, source, target):
        steps = self.stack.steps
        if not (0 <= source < len(steps) and 0 <= target < len(steps)):
            return
        steps.insert(target, steps.pop(source))
        self._selected = target
        self._drop_at = -1
        self._rebuild()
        self.changed.emit()

    def _add_step(self):
        # Duplicates are allowed on purpose: a stack may legitimately touch the
        # same service twice (stop it early, start it again later).
        options = self.cfg().services
        if not options:
            QMessageBox.information(self, "Service Officer",
                                    "Add the services on the Services page first.")
            return
        dlg = SearchableList("Add step", "Which service?",
                            [(f"{s.display()}  ·  {s.name}", s.name) for s in options],
                            self, multi=True)
        if dlg.exec() != QDialog.Accepted:
            return
        for name in dlg.picked:
            self.stack.steps.append(cfg_mod.Step(service=name))
        self._rebuild()
        self.changed.emit()

    def _remove_step(self):
        if 0 <= self._selected < len(self.stack.steps):
            del self.stack.steps[self._selected]
            self._selected = -1
            self._rebuild()
            self.changed.emit()
