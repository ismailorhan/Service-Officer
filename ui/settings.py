"""Settings window — sectioned, per docs/settings-mockup.html.

Structure follows the redesign: a sidebar of sections, and inside Services and
Stacks a list that opens a detail page rather than a second split pane. Numbers
live inside sentences ("Try up to 3 times, waiting 10 seconds first…") because
six labelled rows for one rule read as clutter.

Edits are made on a copy of the config; nothing reaches disk until Save.
"""

from __future__ import annotations

import copy

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QScrollArea, QSpinBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from core import config as cfg_mod
from core import control, history
from core import state as st
from . import icons, theme
from .widgets import Duration, SearchableList, Spin, button as _button, label as _label


def _spin(value, lo, hi, width=64, step=1):
    s = Spin(value, lo, hi, width)
    s.setSingleStep(step)
    return s


def _dspin(value, lo, hi, width=64):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(1)
    s.setSingleStep(0.5)
    s.setValue(float(value))
    s.setFixedWidth(width)
    s.setAlignment(Qt.AlignCenter)
    s.setButtonSymbols(QDoubleSpinBox.NoButtons)
    return s


def _sentence(*parts) -> QWidget:
    """Lay widgets and text fragments out as one line of prose."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    for p in parts:
        lay.addWidget(_label(p) if isinstance(p, str) else p)
    lay.addStretch(1)
    return w


def _hline():
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f"background:{theme.LINE}; border:none;")
    return f


class _ListRow(QWidget):
    """A row in a master list: dot, name, secondary line, chevron."""

    def __init__(self, name: str, secondary: str, category: str = None):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 9, 4, 9)
        lay.setSpacing(11)
        if category:
            dot = QLabel()
            dot.setPixmap(icons.status_dot(category, 8))
            lay.addWidget(dot)
        col = QVBoxLayout()
        col.setSpacing(2)
        n = _label(name, "strong")
        s = _label(secondary, "hint")
        col.addWidget(n)
        col.addWidget(s)
        lay.addLayout(col, 1)
        lay.addWidget(_label("›", "hint"))


# ── service picker ─────────────────────────────────────────────────────────
class ServicePicker(QDialog):
    """Choose one or more installed services. Multi-select is the point: adding
    a SAP stack means adding five services, not repeating a dialog five times."""

    def __init__(self, taken, parent=None, machine=""):
        super().__init__(parent)
        self.setWindowTitle("Add services")
        self.resize(520, 560)
        self.picked = []
        self._all = []
        self._taken = set(taken)
        self._machine = machine

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(9)
        lay.addWidget(_label("Pick the services to monitor", "h2"))
        lay.addWidget(_label("Search by display name or service name. "
                             "Ctrl/Shift-click to choose several.", "hint", wrap=True))

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.textChanged.connect(self._populate)
        lay.addWidget(self.search)

        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        lay.addWidget(self.list, 1)

        self.count = _label("", "hint")
        lay.addWidget(self.count)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(_button("Cancel", None, self.reject))
        row.addWidget(_button("Add", "primary", self.accept))
        lay.addLayout(row)

        self._load()

    def _load(self):
        try:
            self._all = control.list_all_services(self._machine)
        except Exception as exc:
            QMessageBox.warning(self, "Service Officer",
                                f"Could not list services:\n{exc}")
            self._all = []
        self._populate()

    def _populate(self):
        q = self.search.text().strip().lower()
        self.list.clear()
        shown = 0
        for s in self._all:
            if s["name"] in self._taken:
                continue
            if q and q not in s["display"].lower() and q not in s["name"].lower():
                continue
            item = QListWidgetItem(f"  {s['display']}   ·   {s['name']}   ({s['status']})")
            item.setData(Qt.UserRole, s)
            self.list.addItem(item)
            shown += 1
        self.count.setText(f"{shown} services")

    def accept(self):
        self.picked = [i.data(Qt.UserRole) for i in self.list.selectedItems()]
        if not self.picked:
            return
        super().accept()


# ── pages ──────────────────────────────────────────────────────────────────
class _Page(QWidget):
    def __init__(self, title: str, desc: str):
        super().__init__()
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(28, 24, 28, 20)
        self.root.setSpacing(0)
        self.head = QVBoxLayout()
        self.head.setSpacing(4)
        self.head.addWidget(_label(title, "h2"))
        if desc:
            self.head.addWidget(_label(desc, "hint", wrap=True))
        self.root.addLayout(self.head)
        self.root.addSpacing(18)


class ServicesPage(QWidget):
    """List of services; opens a detail page for the selected one."""

    changed = Signal()

    def __init__(self, cfg_ref):
        super().__init__()
        self.cfg = cfg_ref
        self.stack = QStackedWidget()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)

        self.list_page = _Page("Services",
                               "These appear in the tray flyout with live status. "
                               "Open one to set how it should recover when it stops "
                               "on its own.")
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda _i: self._open_selected())
        self.list_page.root.addWidget(self.list, 1)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(_button("Add services…", "primary", self._add))
        bar.addWidget(_button("Open", None, self._open_selected))
        bar.addWidget(_button("Remove", "danger", self._remove))
        bar.addStretch(1)
        bar.addWidget(_button("↑", "quiet", lambda: self._move(-1)))
        bar.addWidget(_button("↓", "quiet", lambda: self._move(1)))
        self.list_page.root.addSpacing(14)
        self.list_page.root.addLayout(bar)

        self.detail = ServiceDetail()
        self.detail.back.connect(self._show_list)
        self.detail.changed.connect(self._refresh_and_signal)

        self.stack.addWidget(self.list_page)
        self.stack.addWidget(self.detail)
        self.refresh()

    # -- list --------------------------------------------------------------
    def refresh(self):
        keep = self.list.currentRow()
        self.list.clear()
        for svc in self.cfg().services:
            rec = svc.recovery
            if not rec.enabled:
                note = "no automatic recovery"
            elif rec.max_attempts:
                note = f"recovers automatically, up to {rec.max_attempts} attempts"
            else:
                note = "recovers automatically, unlimited attempts"
            where = f"{svc.machine}\\" if svc.machine else ""
            item = QListWidgetItem()
            widget = _ListRow(svc.display(), f"{where}{svc.name} · {note}", "none")
            item.setSizeHint(widget.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, widget)
        if 0 <= keep < self.list.count():
            self.list.setCurrentRow(keep)

    def _refresh_and_signal(self):
        self.refresh()
        self.changed.emit()

    def _selected_rows(self):
        return sorted(i.row() for i in self.list.selectedIndexes())

    def _add(self):
        cfg = self.cfg()
        dlg = ServicePicker({s.name for s in cfg.services}, self)
        if dlg.exec() != QDialog.Accepted:
            return
        for s in dlg.picked:
            cfg.services.append(cfg_mod.Service(name=s["name"], label=s["display"]))
        self._refresh_and_signal()

    def _remove(self):
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "Service Officer",
                                    "Select a service in the list first.")
            return
        cfg = self.cfg()
        names = [cfg.services[r].display() for r in rows]
        msg = (f'Stop monitoring "{names[0]}"?' if len(names) == 1
               else f"Stop monitoring these {len(names)} services?")
        if QMessageBox.question(self, "Remove service", msg) != QMessageBox.Yes:
            return
        for r in reversed(rows):
            del cfg.services[r]
        self._refresh_and_signal()

    def _move(self, delta):
        rows = self._selected_rows()
        if len(rows) != 1:
            return
        i = rows[0]
        j = i + delta
        services = self.cfg().services
        if 0 <= j < len(services):
            services[i], services[j] = services[j], services[i]
            self._refresh_and_signal()
            self.list.setCurrentRow(j)

    def _open_selected(self):
        rows = self._selected_rows()
        if len(rows) != 1:
            QMessageBox.information(self, "Service Officer",
                                    "Select one service to open.")
            return
        self.detail.load(self.cfg().services[rows[0]])
        self.stack.setCurrentWidget(self.detail)

    def _show_list(self):
        self.refresh()
        self.stack.setCurrentWidget(self.list_page)


class ServiceDetail(_Page):
    back = Signal()
    changed = Signal()

    def __init__(self):
        super().__init__("", "")
        self.svc = None

        crumb = QHBoxLayout()
        crumb.setSpacing(6)
        b = _button("Services", "quiet", self.back.emit)
        crumb.addWidget(b)
        crumb.addWidget(_label("›", "hint"))
        self.crumb_name = _label("", "hint")
        crumb.addWidget(self.crumb_name)
        crumb.addStretch(1)
        self.head.insertLayout(0, crumb)

        self.title = _label("", "h2")
        self.short = _label("", "mono")
        self.head.addWidget(self.title)
        self.head.addWidget(self.short)

        body = QVBoxLayout()
        body.setSpacing(0)

        body.addWidget(_label("DISPLAY", "section"))
        body.addSpacing(8)
        self.label_edit = QLineEdit()
        self.label_edit.setMaximumWidth(340)
        self.label_edit.textChanged.connect(self._label_changed)
        body.addWidget(self.label_edit)
        body.addSpacing(24)

        body.addWidget(_label("RECOVERY", "section"))
        body.addSpacing(10)
        self.keep = QCheckBox("Keep this service running")
        self.keep.toggled.connect(self._keep_toggled)
        body.addWidget(self.keep)
        body.addWidget(_label("If it stops on its own, start it again.", "hint"))
        body.addSpacing(10)

        self.attempts = _spin(3, 0, 99)
        self.delay = _spin(10, 0, 3600)
        self.backoff = _dspin(2.0, 1.0, 10.0)
        self.flap_count = _spin(5, 2, 50)
        self.flap_window = _spin(30, 1, 1440)
        for w in (self.attempts, self.delay, self.flap_count, self.flap_window):
            w.valueChanged.connect(self._save_rules)
        self.backoff.valueChanged.connect(self._save_rules)

        self.rules = QWidget()
        rl = QVBoxLayout(self.rules)
        rl.setContentsMargins(24, 0, 0, 0)
        rl.setSpacing(10)
        rl.addWidget(_sentence("Try up to", self.attempts, "times, waiting",
                               self.delay, "seconds first"))
        rl.addWidget(_sentence("and multiplying that wait by", self.backoff,
                               "each time."))
        rl.addWidget(_sentence("Give up if it stops", self.flap_count,
                               "times within", self.flap_window, "minutes."))
        body.addWidget(self.rules)
        body.addSpacing(16)

        self.clean = QCheckBox("Also restart after a clean stop")
        self.clean.toggled.connect(self._save_rules)
        body.addWidget(self.clean)
        body.addWidget(_label(
            "Off by default, only crashes are recovered — a non-zero exit code. "
            "A service you stopped yourself in services.msc is left alone.",
            "hint", wrap=True))

        body.addStretch(1)
        self.root.addLayout(body, 1)

    def load(self, svc):
        self.svc = None                     # suppress signals while populating
        self.title.setText(svc.display())
        self.crumb_name.setText(svc.display())
        self.short.setText(f"{svc.machine}\\{svc.name}" if svc.machine else svc.name)
        self.label_edit.setText(svc.label or svc.name)
        r = svc.recovery
        self.keep.setChecked(r.enabled)
        self.attempts.setValue(r.max_attempts)
        self.delay.setValue(r.delay_seconds)
        self.backoff.setValue(r.backoff)
        self.flap_count.setValue(r.flap_threshold)
        self.flap_window.setValue(r.flap_window_minutes)
        self.clean.setChecked(r.restart_on_clean_stop)
        self.svc = svc
        self._sync_enabled()

    def _sync_enabled(self):
        on = self.keep.isChecked()
        self.rules.setEnabled(on)
        self.clean.setEnabled(on)

    def _keep_toggled(self, on):
        self._sync_enabled()
        self._save_rules()

    def _label_changed(self, text):
        if self.svc is not None:
            self.svc.label = text.strip() or self.svc.name
            self.title.setText(self.svc.display())
            self.crumb_name.setText(self.svc.display())
            self.changed.emit()

    def _save_rules(self, *_):
        if self.svc is None:
            return
        self.svc.recovery = cfg_mod.Recovery(
            enabled=self.keep.isChecked(),
            max_attempts=self.attempts.value(),
            delay_seconds=self.delay.value(),
            backoff=self.backoff.value(),
            restart_on_clean_stop=self.clean.isChecked(),
            flap_threshold=self.flap_count.value(),
            flap_window_minutes=self.flap_window.value(),
        )
        self.changed.emit()


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
        crumb.addWidget(_button("Stacks", "quiet", self.back.emit))
        crumb.addWidget(_label("›", "hint"))
        self.crumb_name = _label("", "hint")
        crumb.addWidget(self.crumb_name)
        crumb.addStretch(1)
        self.head.insertLayout(0, crumb)

        self.title = _label("", "h2")
        self.head.addWidget(self.title)
        self.head.addWidget(_label(
            "Each step starts, then waits before the next one begins. Some "
            "services report Running before they can actually serve, so a fixed "
            "wait is sometimes the honest answer.", "hint", wrap=True))

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
        bar.addWidget(_button("↑", "quiet", lambda: self._move(-1)))
        bar.addWidget(_button("↓", "quiet", lambda: self._move(1)))
        bar.addWidget(_button("Remove step", "danger", self._remove_step))
        bar.addStretch(1)
        # Hand over the stack being edited, not its name: a test run has to use
        # what's on screen, otherwise it silently tests the last saved values.
        bar.addWidget(_button("Test run ▸", None,
                              lambda: self.test_run.emit(self.stack, "start")))
        self.root.addSpacing(12)
        self.root.addLayout(bar)

        self._rows = []
        self._selected = -1

    def load(self, stack):
        self.stack = stack
        self.title.setText(stack.name)
        self.crumb_name.setText(stack.name)
        self._rebuild()

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
            row.setStyleSheet(f"#steprow:hover {{ background:{theme.BG_HOVER}; }}")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(2, 6, 2, 6)
            rl.setSpacing(11)

            num = _label(str(i))
            num.setFixedSize(24, 24)
            num.setAlignment(Qt.AlignCenter)
            num.setStyleSheet(f"background:#232323; border:1px solid {theme.LINE2};"
                              f"border-radius:12px; color:{theme.FG3};"
                              f"font-family:'{theme.MONO}'; font-size:8.5pt;")
            rl.addWidget(num)

            col = QVBoxLayout()
            col.setSpacing(3)

            # name + what to do to it
            first = QWidget()
            fl = QHBoxLayout(first)
            fl.setContentsMargins(0, 0, 0, 0)
            fl.setSpacing(8)
            fl.addWidget(_label(labels.get(step.service, step.service), "strong"))
            act = QComboBox()
            act.addItems(["start", "stop", "restart"])
            act.setCurrentIndex(["start", "stop", "restart"].index(step.action))
            act.setFixedWidth(96)
            act.setToolTip("What this step does to the service. The stack runs "
                           "these in order, so it reads as a script.")

            def commit_action(_=None, s=step, a=act):
                s.action = ["start", "stop", "restart"][a.currentIndex()]
                self._rebuild()          # the wait's target state changed with it
                self.changed.emit()
            act.currentIndexChanged.connect(commit_action)
            fl.addWidget(act)
            fl.addStretch(1)
            col.addWidget(first)

            # The wait describes the gap *to the next step*, so the last row has
            # nothing to configure — with a single step there is no transition.
            if i < len(self.stack.steps):
                col.addWidget(self._gap_editor(step))
            else:
                col.addWidget(_label(
                    f"last step — verified {step.target_state.lower()}, up to "
                    f"{step.timeout_seconds}s", "hint"))
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

        lay.addWidget(_label("then wait", "hint"))
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
            row.setStyleSheet(
                f"#steprow {{ border-left:2px solid "
                f"{theme.RUN if on else 'transparent'}; }}"
                f"#steprow:hover {{ background:{theme.BG_HOVER}; }}")

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

    def _move(self, delta):
        i = self._selected
        j = i + delta
        if 0 <= i < len(self.stack.steps) and 0 <= j < len(self.stack.steps):
            steps = self.stack.steps
            steps[i], steps[j] = steps[j], steps[i]
            self._selected = j
            self._rebuild()
            self.changed.emit()


class HistoryPage(_Page):
    changed = Signal()

    def __init__(self, cfg_ref):
        super().__init__("History",
                         "Every state change, with its cause — evidence for a "
                         "ticket, and the only way to see a service that keeps "
                         "dying quietly.")
        self.cfg = cfg_ref

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

        filt = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by service…")
        self.filter.setMaximumWidth(260)
        self.filter.textChanged.connect(self.reload)
        filt.addWidget(self.filter)
        filt.addWidget(_button("Refresh", "quiet", self.reload))
        filt.addWidget(_button("Export…", "quiet", self._export))
        filt.addStretch(1)
        self.root.addLayout(filt)
        self.root.addSpacing(10)

        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.host = QWidget()
        self.rows = QVBoxLayout(self.host)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(0)
        self.rows.addStretch(1)
        self.area.setWidget(self.host)
        self.root.addWidget(self.area, 1)

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
        self.reload()

    def reload(self):
        while self.rows.count() > 1:
            item = self.rows.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        svc = self.filter.text().strip() or None
        try:
            rows = history.read(limit=300, service=svc)
        except Exception:
            rows = []
        if not rows:
            self.rows.insertWidget(0, _label("Nothing recorded yet.", "hint"))
            return

        last_day = None
        for r in rows:
            ts = str(r.get("ts", ""))
            day, _, clock = ts.partition("T")
            if day != last_day:
                last_day = day
                header = _label(day.upper(), "section")
                header.setContentsMargins(0, 12, 0, 6)
                self.rows.insertWidget(self.rows.count() - 1, header)
                self.rows.insertWidget(self.rows.count() - 1, _hline())

            line = QWidget()
            ll = QHBoxLayout(line)
            ll.setContentsMargins(2, 7, 2, 7)
            ll.setSpacing(12)
            t = _label(clock[:8], "mono")
            t.setFixedWidth(62)
            ll.addWidget(t)

            what = f"{r.get('service', '')} — {r.get('to', '')}"
            if r.get("exit_code"):
                what += f" · exit code {r['exit_code']}"
            if r.get("note"):
                what += f" · {r['note']}"
            body = _label(what)
            body.setWordWrap(True)
            ll.addWidget(body, 1)

            src = _label(str(r.get("source", "")), "hint")
            ll.addWidget(src)
            self.rows.insertWidget(self.rows.count() - 1, line)

    def _export(self):
        dest, _ = QFileDialog.getSaveFileName(self, "Export history", "history.csv",
                                              "CSV files (*.csv)")
        if not dest:
            return
        try:
            n = history.export_csv(dest, service=self.filter.text().strip() or None)
            QMessageBox.information(self, "Service Officer", f"Exported {n} rows.")
        except Exception as exc:
            QMessageBox.warning(self, "Service Officer", f"Export failed:\n{exc}")


class GeneralPage(_Page):
    changed = Signal()

    def __init__(self, cfg_ref):
        super().__init__("General", "How the app itself behaves.")
        self.cfg = cfg_ref

        self.root.addWidget(_label("STARTUP", "section"))
        self.root.addSpacing(9)
        self.auto = QCheckBox("Start automatically when Windows starts")
        self.auto.toggled.connect(self._set_auto)
        self.root.addWidget(self.auto)
        self.root.addSpacing(24)

        self.root.addWidget(_label("NOTIFICATIONS", "section"))
        self.root.addSpacing(9)
        self.on_crash = QCheckBox("A service stopped unexpectedly")
        self.on_recovery = QCheckBox("Recovery succeeded")
        self.on_give_up = QCheckBox("Recovery gave up")
        for box, attr in ((self.on_crash, "on_crash"),
                          (self.on_recovery, "on_recovery"),
                          (self.on_give_up, "on_give_up")):
            box.toggled.connect(lambda on, a=attr: self._set_note(a, on))
            self.root.addWidget(box)
            self.root.addSpacing(4)

        self.root.addSpacing(22)
        self.root.addWidget(_label("OTHER MACHINES", "section"))
        self.root.addSpacing(9)
        self.root.addWidget(_label(
            "Reserved for managing services on your other servers from this same "
            "window. Every call we make already accepts a machine name, so the "
            "groundwork is done.", "hint", wrap=True))
        self.root.addStretch(1)

    def _set_auto(self, on):
        self.cfg().auto_start = on
        self.changed.emit()

    def _set_note(self, attr, on):
        setattr(self.cfg().notifications, attr, on)
        self.changed.emit()

    def load_from(self, cfg):
        for box, value in ((self.auto, cfg.auto_start),
                           (self.on_crash, cfg.notifications.on_crash),
                           (self.on_recovery, cfg.notifications.on_recovery),
                           (self.on_give_up, cfg.notifications.on_give_up)):
            box.blockSignals(True)
            box.setChecked(value)
            box.blockSignals(False)


# ── the window ─────────────────────────────────────────────────────────────
class SettingsWindow(QDialog):
    saved = Signal(object)               # the new Config
    test_run = Signal(object, str)       # the stack being edited, action

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Service Officer — Settings")
        self.setWindowIcon(icons.base_icon("green"))
        self.resize(860, 620)
        self._cfg = copy.deepcopy(cfg)    # edit a copy; Save commits it
        # Saved state to compare against, so Save can be disabled when there is
        # nothing to write.
        self._baseline = cfg_mod.to_dict(self._cfg)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        nav = QWidget()
        nav.setFixedWidth(186)
        nav.setStyleSheet(f"background:{theme.BG_SIDE};"
                          f"border-right:1px solid {theme.LINE};")
        nl = QVBoxLayout(nav)
        nl.setContentsMargins(0, 14, 0, 14)
        nl.setSpacing(0)

        self.pages = QStackedWidget()
        get = lambda: self._cfg

        self.services_page = ServicesPage(get)
        self.stacks_page = StacksPage(get)
        self.history_page = HistoryPage(get)
        self.general_page = GeneralPage(get)
        self.services_page.changed.connect(self.stacks_page.refresh)
        self.stacks_page.test_run.connect(self.test_run)
        for page in (self.services_page, self.stacks_page,
                     self.history_page, self.general_page):
            page.changed.connect(self._refresh_save_state)

        self._nav_buttons = []
        sections = [("Monitoring", None, None),
                    ("Services", "services", self.services_page),
                    ("Stacks", "stacks", self.stacks_page),
                    ("History", "history", self.history_page),
                    ("Application", None, None),
                    ("General", "general", self.general_page)]
        for text, kind, page in sections:
            if page is None:
                cap = _label(text.upper(), "section")
                cap.setContentsMargins(16, 14, 16, 6)
                nl.addWidget(cap)
                continue
            self.pages.addWidget(page)
            b = QPushButton("  " + text)
            b.setProperty("kind", "nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            # Drawn icons, not text glyphs: a glyph inside the label can't be
            # sized on its own and reads as a speck next to 13pt text.
            b.setIcon(icons.nav_icon(kind, 19))
            b.setIconSize(QSize(19, 19))
            b.clicked.connect(lambda _=False, p=page, btn=b: self._select(p, btn))
            nl.addWidget(b)
            self._nav_buttons.append(b)
        nl.addStretch(1)

        body.addWidget(nav)
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)

        foot = QWidget()
        foot.setStyleSheet(f"background:#1b1b1b; border-top:1px solid {theme.LINE};")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(16, 11, 16, 11)
        self.status = _label("", "hint")
        fl.addWidget(self.status)
        fl.addStretch(1)
        fl.addWidget(_button("Close", None, self.reject))
        self.save_button = _button("Save", "primary", self._save)
        fl.addWidget(self.save_button)
        outer.addWidget(foot)

        self.history_page.load_from(self._cfg)
        self.general_page.load_from(self._cfg)
        self._select(self.services_page, self._nav_buttons[0])
        self._refresh_save_state()

    def _select(self, page, button):
        self.pages.setCurrentWidget(page)
        for b in self._nav_buttons:
            b.setChecked(b is button)

    def config(self):
        return self._cfg

    def is_dirty(self) -> bool:
        return cfg_mod.to_dict(self._cfg) != self._baseline

    def _refresh_save_state(self):
        dirty = self.is_dirty()
        self.save_button.setEnabled(dirty)
        self.status.setText("Unsaved changes" if dirty else "")

    def _save(self):
        """Save without closing: you usually want to keep adjusting, and a
        window that vanishes on Save makes you reopen it to check."""
        self.saved.emit(self._cfg)
        self._baseline = cfg_mod.to_dict(self._cfg)
        self._refresh_save_state()
        self.status.setText("Saved")

    def reject(self):
        if self.is_dirty():
            answer = QMessageBox.question(
                self, "Service Officer",
                "You have unsaved changes. Save them before closing?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if answer == QMessageBox.Cancel:
                return
            if answer == QMessageBox.Save:
                self._save()
        super().reject()
