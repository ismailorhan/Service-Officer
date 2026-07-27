"""One service, in full: what it is, how it recovers, how it is checked.

Four separate concerns, which is why this is tabbed rather than one long scroll —
a page that grows a section every release stops being readable. It came out of
ui/pages/services.py, where the list and the detail together ran to 932 lines.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout,
                               QLineEdit, QMenu, QMessageBox, QPushButton,
                               QScrollArea, QStackedWidget, QVBoxLayout,
                               QWidget)

from core import config as cfg_mod
from core import control
from core import state as st

from .. import theme
from ..widgets import (Duration, FlatFactor, FlatSpin, button as _button,
                       label as _label)
from .base import _Page, _sentence, _spin


class ServiceDetail(_Page):
    back = Signal()
    changed = Signal()

    def __init__(self, store=None):
        super().__init__("", "")
        self.svc = None
        self._store = store
        self._open_checks = set()
        self._summaries = {}

        crumb = QHBoxLayout()
        crumb.setSpacing(6)
        b = _button(f"{theme.GLYPH_BACK}  Services", "quiet", self.back.emit)
        crumb.addWidget(b)
        crumb.addWidget(_label(theme.GLYPH_CRUMB, "hint"))
        self.crumb_name = _label("", "hint")
        crumb.addWidget(self.crumb_name)
        crumb.addStretch(1)
        self.head.insertLayout(0, crumb)

        self.title = _label("", "h2")
        self.short = _label("", "mono")
        self.head.addWidget(self.title)
        self.head.addWidget(self.short)

        # Three tabs, named as the Windows service properties dialog names its
        # own, because this page is the better version of that dialog and people
        # already know where to look. One page of everything reached 1200px once
        # health checks were on it: you had to scroll to discover that Health
        # existed at all, which is the wrong way round.
        self.tabs = QHBoxLayout()
        self.tabs.setSpacing(2)
        self.tabs.setContentsMargins(0, 0, 0, 0)
        self.pages = QStackedWidget()
        self._tab_buttons = {}
        self.root.addLayout(self.tabs)
        self.root.addSpacing(2)
        self.root.addWidget(self._hline())
        self.root.addSpacing(14)
        self.root.addWidget(self.pages, 1)

        general = self._tab("General")
        recovery = self._tab("Recovery")
        health_tab = self._tab("Health")
        self.tabs.addStretch(1)          # tabs sit left, like a tab strip should

        body = general
        body.addWidget(_label("DISPLAY", "section"))
        body.addSpacing(8)
        self.label_edit = QLineEdit()
        self.label_edit.setMaximumWidth(340)
        self.label_edit.textChanged.connect(self._label_changed)
        body.addWidget(self.label_edit)
        body.addSpacing(18)

        body.addWidget(_label("CATEGORY", "section"))
        body.addSpacing(8)
        self.category = QComboBox()
        self.category.setFixedWidth(240)
        self.category.currentIndexChanged.connect(self._category_changed)
        body.addWidget(self.category)
        body.addWidget(_label("Groups this service under a heading in the "
                              "dashboard and the tray panel. Define the headings "
                              "on the Categories page.", "hint", wrap=True))
        body.addStretch(1)

        body = recovery
        body.addWidget(_label("RECOVERY", "section"))
        body.addSpacing(10)
        self.keep = QCheckBox("Keep this service running")
        self.keep.toggled.connect(self._keep_toggled)
        body.addWidget(self.keep)
        body.addWidget(_label("If it stops on its own, start it again.", "hint"))
        body.addSpacing(10)

        # Same treatment as the stack editor: values read as text and only turn
        # into controls when clicked.
        self.attempts = FlatSpin(3, 0, 99)
        self.delay = Duration(10)
        self.backoff = FlatFactor(2.0)
        self.flap_count = FlatSpin(5, 2, 50)
        self.flap_window = Duration(30 * 60, minimum=60)
        for w in (self.attempts, self.delay, self.backoff,
                  self.flap_count, self.flap_window):
            w.changed.connect(self._save_rules)

        self.rules = QWidget()
        rl = QVBoxLayout(self.rules)
        rl.setContentsMargins(24, 0, 0, 0)
        rl.setSpacing(10)
        rl.addWidget(_sentence("Try up to", self.attempts, "times, waiting",
                               self.delay, "first"))
        rl.addWidget(_sentence("and multiplying that wait by", self.backoff,
                               "each time."))
        rl.addWidget(_sentence("Give up if it stops", self.flap_count,
                               "times within", self.flap_window, "."))
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

        # -- health ---------------------------------------------------------
        body = health_tab
        head_row = QHBoxLayout()
        head_row.addWidget(_label("HEALTH", "section"))
        head_row.addStretch(1)
        # A master switch, so watching can be stopped for an afternoon without
        # deleting the checks — turning something off should never cost you the
        # configuration you need to turn it back on.
        self.h_enabled = QCheckBox("Watch this service")
        self.h_enabled.setToolTip("Off keeps the checks below but stops asking. "
                                  "Nothing is reported as unhealthy while it is "
                                  "off.")
        self.h_enabled.toggled.connect(self._health_switched)
        head_row.addWidget(self.h_enabled)
        body.addLayout(head_row)
        body.addSpacing(9)
        body.addWidget(_label(
            "Windows reports Running as soon as a process exists. These say "
            "whether anyone can actually use it — the “running but dead” case "
            "that a service list cannot show. Every check has to pass.",
            "hint", wrap=True))
        body.addSpacing(12)

        # What has actually happened, in plain times. Without this the schedule is
        # something you infer from the settings and then have to trust.
        self.h_status = _label("", "hint", wrap=True)
        self.h_status.setObjectName("healthStatus")
        self.h_status.setContentsMargins(10, 8, 10, 8)
        self.h_status.setAttribute(Qt.WA_StyledBackground, True)
        body.addWidget(self.h_status)
        #: while the Health tab is showing, keep the times honest
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(2000)
        self._status_timer.timeout.connect(self._refresh_health_status)
        body.addSpacing(14)

        # The rules come first and the list last, so nothing sits below a list
        # that grows: with five checks the settings scrolled off the bottom and
        # you had to know they were there to go looking.
        self.health_rules = QWidget()
        hl = QVBoxLayout(self.health_rules)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(10)
        self.h_interval = Duration(60, minimum=5)
        self.h_interval.setToolTip("How often to run the checks below, while the "
                                   "service is running.")
        self.h_grace = Duration(60)
        self.h_grace.setToolTip("Time to allow after the service reaches Running "
                                "before its answers count. Zero judges it "
                                "immediately.")
        self.h_failures = FlatSpin(3, 1, 20)
        self.h_failures.setToolTip("Consecutive failures before it is called "
                                   "unhealthy. One bad answer is usually load, "
                                   "not death.")
        for w in (self.h_interval, self.h_grace, self.h_failures):
            w.changed.connect(self._save_health)
        # No trailing full stop: _sentence joins with spaces, so one would sit a
        # space away from the number and look like a typo.
        hl.addWidget(_sentence("Ask every", self.h_interval))
        # "Ask every 1 min, starting 1 min after it comes up" was asked about, and
        # fairly: two durations in one sentence, and "starting" reads as starting
        # the service. Its own line, with the reason underneath.
        hl.addWidget(_sentence("Ignore the first", self.h_grace,
                               "after it starts."))
        hl.addWidget(_label(
            "A service that has just come up hasn't opened its port yet, so "
            "asking straight away would report every restart as a failure.",
            "hint", wrap=True))
        hl.addWidget(_sentence("Call it unhealthy after", self.h_failures,
                               "failures in a row."))
        self.h_action = QComboBox()
        self.h_action.addItem("Just tell me", "notify")
        self.h_action.addItem("Restart the service", "restart")
        self.h_action.setFixedWidth(220)
        self.h_action.currentIndexChanged.connect(self._save_health)
        hl.addWidget(_sentence("Then:", self.h_action))

        # This was a hidden five-minute constant, and it made a delayed restart
        # look like the checks were unreliable. It belongs on screen.
        self.h_cooldown = Duration(300)
        self.h_cooldown.setToolTip("A service a restart cannot fix must not be "
                                   "restarted every minute for ever. Zero means "
                                   "restart on every verdict.")
        self.h_cooldown.changed.connect(self._save_health)
        self.h_restart_rule = _sentence("…but no more often than every",
                                        self.h_cooldown)
        hl.addWidget(self.h_restart_rule)
        body.addWidget(self.health_rules)
        body.addSpacing(20)

        # The actions, then the list. Nothing goes below the list — it grows.
        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        # One button with a menu, not a combo next to a button: a combo standing
        # there reading "A port answers" describes the service rather than
        # offering to add something, and was invisible in plain sight.
        # No arrow in the text either — setMenu draws Qt's own indicator.
        self.add_button = QPushButton("Add check")
        self.add_button.setProperty("kind", "primary")
        self.add_button.setCursor(Qt.PointingHandCursor)
        self.add_menu = QMenu(self.add_button)
        #: kind -> the action, so a kind the service's machine cannot do can be
        #: greyed rather than offered and then failed
        self._kind_actions = {}
        for text, kind in (("A port answers", "tcp"),
                           ("A URL answers", "http"),
                           ("It has a process", "process"),
                           ("A file is being written", "file"),
                           ("A command succeeds", "command")):
            action = self.add_menu.addAction(text, lambda k=kind: self._add_check(k))
            self._kind_actions[kind] = action
        self.add_button.setMenu(self.add_menu)
        add_row.addWidget(self.add_button)
        self.check_now_button = _button("Check now", None, self._check_now)
        add_row.addWidget(self.check_now_button)
        add_row.addStretch(1)
        body.addLayout(add_row)
        body.addSpacing(12)

        self.health_note = _label("", "hint", wrap=True)
        body.addWidget(self.health_note)
        self.checks_host = QWidget()
        self.checks_lay = QVBoxLayout(self.checks_host)
        self.checks_lay.setContentsMargins(0, 0, 0, 0)
        self.checks_lay.setSpacing(6)
        body.addWidget(self.checks_host)
        body.addStretch(1)

        # Last, once every tab's contents exist: selecting a tab touches them.
        self._select_tab("General")

    # -- tabs --------------------------------------------------------------
    @staticmethod
    def _hline() -> QFrame:
        line = QFrame()
        line.setObjectName("hline")
        line.setFrameShape(QFrame.HLine)
        line.setFixedHeight(1)
        return line

    def _tab(self, name: str) -> QVBoxLayout:
        """Add a tab and return the layout its contents go into.

        Each tab scrolls on its own: three health checks are taller than the
        window, and content that is simply clipped is content nobody knows is
        there.
        """
        button = _button(name, "tab")
        button.setCheckable(True)
        button.clicked.connect(lambda _=False, n=name: self._select_tab(n))
        self.tabs.addWidget(button)
        self._tab_buttons[name] = button

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 12, 8)     # room for the scrollbar
        layout.setSpacing(0)
        scroll.setWidget(holder)
        self.pages.addWidget(scroll)
        self._tab_pages = getattr(self, "_tab_pages", {})
        self._tab_pages[name] = scroll
        return layout

    def _select_tab(self, name: str) -> None:
        page = getattr(self, "_tab_pages", {}).get(name)
        if page is not None:
            self.pages.setCurrentWidget(page)
        for tab_name, button in self._tab_buttons.items():
            button.setChecked(tab_name == name)
        # Only tick while the times are on screen; a timer running behind a hidden
        # tab is work nobody asked for.
        if name == "Health":
            self._refresh_health_status()
            self._status_timer.start()
        else:
            self._status_timer.stop()

    def load(self, svc, categories=()):
        self.svc = None                     # suppress signals while populating
        self.title.setText(svc.display())
        self.crumb_name.setText(svc.display())
        self.short.setText(f"{svc.machine}\\{svc.name}" if svc.machine else svc.name)
        self.label_edit.setText(svc.label or svc.name)
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem(cfg_mod.NO_CATEGORY_TITLE, cfg_mod.NO_CATEGORY)
        for cat in categories:
            self.category.addItem(cat.name, cat.name)
        wanted = self.category.findData(svc.category or cfg_mod.NO_CATEGORY)
        self.category.setCurrentIndex(wanted if wanted >= 0 else 0)
        self.category.blockSignals(False)
        r = svc.recovery
        self.keep.setChecked(r.enabled)
        self.attempts.setValue(r.max_attempts)
        self.delay.set_seconds(r.delay_seconds)
        self.backoff.setValue(r.backoff)
        self.flap_count.setValue(r.flap_threshold)
        self.flap_window.set_seconds(r.flap_window_minutes * 60)
        self.clean.setChecked(r.restart_on_clean_stop)

        h = svc.health
        self.h_enabled.blockSignals(True)
        self.h_enabled.setChecked(h.enabled)
        self.h_enabled.blockSignals(False)
        for widget, value in ((self.h_interval, h.interval_seconds),
                              (self.h_grace, h.grace_seconds)):
            widget.set_seconds(value)
        self.h_failures.setValue(h.failures_before_acting)
        self.h_cooldown.set_seconds(h.min_restart_interval_seconds)
        self.h_action.blockSignals(True)
        wanted = self.h_action.findData(h.action)
        self.h_action.setCurrentIndex(wanted if wanted >= 0 else 0)
        self.h_action.blockSignals(False)

        self.svc = svc
        #: which check rows are open for editing; closed is the reading view
        self._open_checks = set()
        self._rebuild_checks()
        self._sync_enabled()
        self._sync_health_enabled()
        self._refresh_health_status()

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

    def _category_changed(self, _index):
        if self.svc is not None:
            self.svc.category = self.category.currentData() or cfg_mod.NO_CATEGORY
            self.changed.emit()

    # -- health ------------------------------------------------------------
    #: what each kind needs typing in, and what to call it
    CHECK_FIELDS = {
        "tcp": [("host", "Host (blank = this service’s machine)"),
                ("port", "Port")],
        "http": [("url", "URL"), ("expect_status", "Expect status (0 = any 2xx/3xx)"),
                 ("expect_text", "Must contain (optional)")],
        "process": [],
        "file": [("path", "File"), ("max_age_seconds", "Written within (seconds)")],
        "command": [("command", "Command"), ("expect_exit", "Expect exit code")],
    }

    def _rebuild_checks(self):
        self._summaries = {}
        while self.checks_lay.count():
            item = self.checks_lay.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        if self.svc is None:
            return
        checks = self.svc.health.checks
        if not checks:
            self.health_note.setText(
                "No checks yet. Use Add check — until then this service is judged "
                "only by whether Windows says it is running.")
        else:
            count = len(checks)
            self.health_note.setText(
                f"{count} CHECK{'S' if count != 1 else ''}, "
                f"ALL OF WHICH MUST PASS")
        # Reads as a heading once there is a list under it, as a hint when empty.
        self.health_note.setProperty("role", "section" if checks else "hint")
        self.health_note.style().unpolish(self.health_note)
        self.health_note.style().polish(self.health_note)
        self._offer_only_what_the_machine_can_do()
        for index, check in enumerate(checks):
            self.checks_lay.addWidget(self._check_row(index, check))
        self.health_rules.setVisible(bool(checks))
        self._sync_health_enabled()

    #: Kinds that need to reach *into* the machine rather than at it over the
    #: network, and the reason a remote Windows machine cannot do them. Over SSH both
    #: are ordinary — which is what makes `su - hdbadm -c "HDB info"` a health check
    #: rather than a note in a runbook.
    REACHES_INSIDE = ("file", "command")
    NOT_HERE = ("not available on another Windows machine — reaching into it needs "
                "something we do not have there yet")

    def _machine_record(self):
        """The service's machine, from the config this page is editing."""
        page = self.parent()
        while page is not None and not hasattr(page, "cfg"):
            page = page.parent()
        if page is None or self.svc is None:
            return None
        return page.cfg().machine(self.svc.machine or "")

    def _cannot_reach_inside(self) -> bool:
        machine = self._machine_record()
        if machine is None:
            return False
        return bool(machine.name) and not machine.is_linux

    def _offer_only_what_the_machine_can_do(self) -> None:
        """Grey the kinds this service's machine cannot answer, and say why.

        Not hidden: a menu that changes shape depending on which service is selected
        is a menu nobody trusts. Greyed with a reason is a smaller surprise than a
        check that can only ever fail — and until today those checks did not fail,
        they answered from this computer and called it the other machine's.
        """
        blocked = self._cannot_reach_inside()
        for kind, action in self._kind_actions.items():
            unavailable = blocked and kind in self.REACHES_INSIDE
            action.setEnabled(not unavailable)
            action.setToolTip(self.NOT_HERE if unavailable else "")
        self.add_menu.setToolTip(self.NOT_HERE if blocked else "")

    #: what to call each kind in the one-line summary
    KIND_NAMES = {"tcp": "PORT", "http": "URL", "process": "PROCESS",
                  "file": "FILE", "command": "COMMAND"}

    def _check_row(self, index: int, check) -> QWidget:
        """One line per check, opening to reveal its fields.

        A list of five checks each showing four labelled boxes is a wall. The
        line says what the check *is* — kind, what it looks at, how long it
        waits — and the boxes only appear when you are editing that one.
        """
        row = QWidget()
        row.setObjectName("steprow")
        row.setAttribute(Qt.WA_StyledBackground, True)
        outer = QVBoxLayout(row)
        outer.setContentsMargins(8, 4, 6, 4)
        outer.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)
        on = QCheckBox()
        on.setChecked(check.enabled)
        on.setToolTip("Include this check")
        on.toggled.connect(lambda state, c=check: self._set_check(c, "enabled",
                                                                  state))
        head.addWidget(on)

        kind = _label(self.KIND_NAMES.get(check.kind, check.kind.upper()),
                      "section")
        kind.setFixedWidth(78)
        head.addWidget(kind)
        summary = _label(check.describe(), "strong")
        self._summaries[summary] = check
        head.addWidget(summary, 1)
        head.addWidget(_label(f"gives up after {check.timeout_seconds}s", "hint"))

        expanded = index in self._open_checks
        toggle = _button("Close" if expanded else "Edit", "quiet")
        toggle.setFixedWidth(56)
        toggle.clicked.connect(lambda _=False, i=index: self._toggle_check(i))
        head.addWidget(toggle)
        remove = _button("Remove", "quiet")
        remove.clicked.connect(lambda _=False, i=index: self._remove_check(i))
        head.addWidget(remove)
        outer.addLayout(head)

        # A check that cannot work where this service lives — configured before the
        # service moved, or before the kinds were filtered. Shown, not hidden: losing
        # somebody's setting silently is worse than telling them it cannot pass.
        if check.kind in self.REACHES_INSIDE and self._cannot_reach_inside():
            outer.addWidget(_label(self.NOT_HERE, "hint", wrap=True))

        # Double-clicking the row opens it too. The Edit button stays, because a
        # double-click is not something anyone can see is available.
        row.mouseDoubleClickEvent = (
            lambda _ev, i=index: self._toggle_check(i))
        row.setToolTip("Double-click to edit")

        if not expanded:
            return row

        fields = QHBoxLayout()
        fields.setContentsMargins(86, 4, 0, 6)      # line up under the summary
        fields.setSpacing(8)
        for attr, caption in self.CHECK_FIELDS.get(check.kind, []):
            box = QVBoxLayout()
            box.setSpacing(2)
            box.addWidget(_label(caption, "hint"))
            current = getattr(check, attr)
            if isinstance(current, int):
                editor = _spin(current, 0, 999999)
                editor.valueChanged.connect(
                    lambda value, c=check, a=attr: self._set_check(c, a, value))
                editor.setFixedWidth(150)
            else:
                editor = QLineEdit(str(current))
                editor.setMinimumWidth(200)
                editor.textChanged.connect(
                    lambda text, c=check, a=attr: self._set_check(c, a, text))
            box.addWidget(editor)
            fields.addLayout(box)

        timeout = QVBoxLayout()
        timeout.setSpacing(2)
        timeout.addWidget(_label("Give up after (seconds)", "hint"))
        secs = _spin(check.timeout_seconds, 1, 120)
        secs.setFixedWidth(110)
        secs.valueChanged.connect(
            lambda value, c=check: self._set_check(c, "timeout_seconds", value))
        timeout.addWidget(secs)
        fields.addLayout(timeout)

        if check.kind == "http":
            insecure = QCheckBox("Accept any certificate")
            insecure.setToolTip("For an internal server with its own certificate.")
            insecure.setChecked(check.insecure)
            insecure.toggled.connect(
                lambda state, c=check: self._set_check(c, "insecure", state))
            fields.addWidget(insecure, 0, Qt.AlignBottom)

        fields.addStretch(1)
        outer.addLayout(fields)
        return row

    def _refresh_health_status(self):
        """Last check, next check, and what is holding anything back."""
        if self.svc is None or self._store is None:
            self.h_status.setText("")
            return
        svc, health_cfg = self.svc, self.svc.health
        if not health_cfg.checks:
            self.h_status.setText("Nothing is being checked yet.")
            return
        if not health_cfg.enabled:
            self.h_status.setText("Watching is switched off — the checks below are "
                                  "kept but never run.")
            return

        status = self._store.status_of(svc.name, svc.machine)
        if status != st.RUNNING:
            self.h_status.setText(
                f"{svc.display()} is {status.lower()}. Checks only run while it is "
                f"running — a stopped service isn't unhealthy, it is stopped.")
            return

        facts = self._store.health_timing(svc.name, svc.machine)
        verdict = self._store.health_of(svc.name, svc.machine)
        lines = []
        last = facts.get("last")
        if last is None:
            lines.append("Not checked yet — "
                         + (facts.get("detail") or "waiting for it to settle"))
        else:
            outcome = ("passed" if facts.get("passed") else
                       f"failed ({facts.get('failures', 0)} in a row)")
            lines.append(f"Last checked {last.strftime('%H:%M:%S')} — {outcome}")
        nxt = facts.get("next")
        if nxt is not None:
            seconds = int((nxt - datetime.now()).total_seconds())
            when = nxt.strftime("%H:%M:%S")
            lines.append(f"Next check {when}"
                         + (f", in {max(0, seconds)}s" if seconds < 600 else ""))
        said = {"unhealthy": "Currently: not responding",
                "healthy": "Currently: responding",
                "unknown": "Currently: no verdict yet"}
        lines.append(said.get(verdict, verdict))
        if verdict == "unhealthy" and health_cfg.action == "restart":
            gap = health_cfg.min_restart_interval_seconds
            lines.append(f"Restarts are limited to one every {gap}s.")
        self.h_status.setText("   ·   ".join(lines))

    def _health_switched(self, on: bool):
        if self.svc is not None:
            self.svc.health.enabled = on
            self.changed.emit()
        self._sync_health_enabled()

    def _sync_health_enabled(self):
        """Off greys everything out but leaves it on screen: the point of the
        switch is that the configuration survives being turned off.

        The single authority on what is enabled here. Two rules apply — the switch
        is off, or there is nothing to check — and having them set the same button
        from two places meant whichever ran last won.
        """
        on = self.h_enabled.isChecked()
        has_checks = bool(self.svc.health.checks) if self.svc is not None else False
        for widget in (self.health_rules, self.checks_host, self.add_button):
            widget.setEnabled(on)
        self.check_now_button.setEnabled(on and has_checks)

    def _toggle_check(self, index: int):
        if index in self._open_checks:
            self._open_checks.discard(index)
        else:
            self._open_checks.add(index)
        self._rebuild_checks()

    def _set_check(self, check, attr, value):
        setattr(check, attr, value)
        # The summary line quotes these values, so it has to keep up — but only
        # the closed rows are redrawn, or the box being typed into would lose
        # focus on every keystroke.
        for widget, shown in list(self._summaries.items()):
            if shown is check:
                widget.setText(check.describe())
        self.changed.emit()

    def _add_check(self, kind: str = "tcp"):
        if self.svc is None:
            return
        # A new check opens straight away — it has nothing in it yet, so there is
        # nothing to read and everything to fill in.
        self._open_checks = {len(self.svc.health.checks)}
        check = cfg_mod.HealthCheck(kind=kind)
        # Sensible starting points, so the row isn't a set of empty boxes.
        if kind == "file":
            check.max_age_seconds = 300
        elif kind == "http":
            # This endpoint took four seconds and once eight, measured — five
            # would have produced false alarms, so URL checks start generous.
            check.timeout_seconds = 15
        self.svc.health.checks.append(check)
        self._rebuild_checks()
        self.changed.emit()

    def _remove_check(self, index: int):
        if self.svc is None or not (0 <= index < len(self.svc.health.checks)):
            return
        del self.svc.health.checks[index]
        # Indexes shifted, so a remembered "open" one would open the wrong row.
        self._open_checks = set()
        self._rebuild_checks()
        self.changed.emit()

    def _save_health(self, *_):
        if self.svc is None:
            return
        h = self.svc.health
        h.interval_seconds = max(5, self.h_interval.seconds())
        h.grace_seconds = self.h_grace.seconds()
        h.failures_before_acting = max(1, self.h_failures.value())
        h.action = self.h_action.currentData() or "notify"
        h.min_restart_interval_seconds = self.h_cooldown.seconds()
        # The restart limit only means anything when restarting is the action.
        self.h_restart_rule.setVisible(h.action == "restart")
        self.changed.emit()
        self._refresh_health_status()

    def _check_now(self):
        """Run the checks as they are on screen, not as last saved — otherwise
        you are testing yesterday's settings."""
        if self.svc is None:
            return
        from core import health as health_mod
        ok, results = health_mod.run_all(self.svc, control)
        lines = [r.line() for r in results] or ["nothing to check"]
        QMessageBox.information(
            self, "Health check",
            ("All checks pass." if ok else "Something is not answering.")
            + "\n\n" + "\n".join(lines))

    def _save_rules(self, *_):
        if self.svc is None:
            return
        self.svc.recovery = cfg_mod.Recovery(
            enabled=self.keep.isChecked(),
            max_attempts=self.attempts.value(),
            delay_seconds=self.delay.seconds(),
            backoff=self.backoff.value(),
            restart_on_clean_stop=self.clean.isChecked(),
            flap_threshold=self.flap_count.value(),
            flap_window_minutes=max(1, self.flap_window.seconds() // 60),
        )
        self.changed.emit()
