"""The Service Management Panel — the app's main window.

This began as a settings dialog and outgrew it: the pages that matter are
Services, Stacks, Schedule and History, which are operational rather than
configuration. So the window is the panel now, and the settings live in it as one
section of its own menu instead of naming the whole thing.

Sectioned, per docs/settings-mockup.html.

Structure follows the redesign: a sidebar of sections, and inside Services and
Stacks a list that opens a detail page rather than a second split pane. Numbers
live inside sentences ("Try up to 3 times, waiting 10 seconds first…") because
six labelled rows for one rule read as clutter.

Edits are made on a copy of the config; nothing reaches disk until Save.
"""

from __future__ import annotations

import copy
import os

from datetime import datetime

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFileDialog, QFrame, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMenu, QMessageBox, QPushButton, QScrollArea,
                               QSpinBox,
                               QStackedWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from core import config as cfg_mod
from core import control, history, version
from core import state as st
from . import icons, theme
from .dashboard import DashboardPage
from .widgets import (Chip, Duration, FlatEdit, FlatFactor, FlatSpin, Grip,
                      PadSpin,
                      ReorderList, SearchableList, Spin, button as _button,
                      label as _label)


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
    f.setObjectName("hline")
    return f


class _ListRow(QWidget):
    """A row in a master list: dot, name, secondary line, chip, chevron."""

    def __init__(self, name: str, secondary: str, category: str = None,
                 tag: str = "", tag_category: str = "running"):
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
        if tag:
            lay.addWidget(Chip(tag, tag_category))
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
        lay.setContentsMargins(*theme.PAGE_PAD)
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
    """A page: a heading, then whatever goes in `self.root`.

    scroll=True puts the body in a scroll area, leaving the heading fixed. Pages
    made of stacked fields need it — they grow as settings are added, and content
    that is merely clipped is content nobody knows is there. Pages built around a
    table or a list don't: those already scroll on their own, and nesting one
    scrolling thing inside another is horrible to use.
    """

    def __init__(self, title: str, desc: str, scroll: bool = False):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*theme.PANEL_PAD)
        outer.setSpacing(0)
        self.head = QVBoxLayout()
        self.head.setSpacing(4)
        self.head.addWidget(_label(title, "h2"))
        if desc:
            self.head.addWidget(_label(desc, "hint", wrap=True))
        outer.addLayout(self.head)
        outer.addSpacing(18)

        if not scroll:
            self.root = outer
            return
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.root = QVBoxLayout(holder)
        self.root.setContentsMargins(0, 0, 12, 8)      # room for the scrollbar
        self.root.setSpacing(0)
        area.setWidget(holder)
        outer.addWidget(area, 1)


class ServicesPage(QWidget):
    """List of services; opens a detail page for the selected one."""

    changed = Signal()

    def __init__(self, cfg_ref, store=None):
        super().__init__()
        self.cfg = cfg_ref
        self.stack = QStackedWidget()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)

        self.list_page = _Page("Services",
                               "Grouped by category, in the order the tray flyout "
                               "and the dashboard show them. Drag a service to "
                               "move it, or onto another heading to file it there. "
                               "Open one to set how it should recover when it "
                               "stops on its own.")
        self.list = ReorderList()
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda _i: self._open_selected())
        self.list.dropped.connect(self._dropped)
        self.list_page.root.addWidget(self.list, 1)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(_button("Add services…", "primary", self._add))
        bar.addWidget(_button("Open", None, self._open_selected))
        # Filing services is something you do to several at once, so it belongs
        # on the list and not only inside one service's page.
        bar.addWidget(_button("Category…", None, self._set_category))
        bar.addWidget(_button("Remove", "danger", self._remove))
        bar.addStretch(1)
        self.list_page.root.addSpacing(14)
        self.list_page.root.addLayout(bar)

        self.detail = ServiceDetail(store)
        self.detail.back.connect(self._show_list)
        self.detail.changed.connect(self._refresh_and_signal)

        self.stack.addWidget(self.list_page)
        self.stack.addWidget(self.detail)
        self.refresh()

    # -- list --------------------------------------------------------------
    def refresh(self):
        """Grouped by category, with every category shown — an empty one is
        somewhere to drag a service to, which is the point of it being here."""
        keep = self.list.currentRow()
        self.list.clear()
        #: one entry per list row: ("group", category) or ("service", Service)
        self._entries = []
        cfg = self.cfg()
        for name, title, members in cfg.grouped_services(include_empty=True):
            self._add_group(name, title, len(members))
            for svc in members:
                self._add_service(svc)
        if 0 <= keep < self.list.count():
            self.list.setCurrentRow(keep)

    def _add_group(self, name: str, title: str, count: int):
        item = QListWidgetItem()
        # Enabled so it can be a drop target, but neither selectable nor
        # draggable: a heading isn't a thing you move or act on. Dragging the
        # heading itself would also be ambiguous — does it carry its services? —
        # so the group's own order is changed with the two arrows instead.
        item.setFlags(Qt.ItemIsEnabled)
        widget = QWidget()
        widget.setObjectName("sectionBar")
        widget.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(widget)
        row.setContentsMargins(theme.SP_10, theme.SP_4, theme.SP_8, theme.SP_4)
        row.setSpacing(4)
        row.addWidget(_label(title.upper(), "section"), 1)
        row.addWidget(_label("empty — drag a service here" if not count
                             else f"{count}", "hint"))

        cats = [c.name for c in self.cfg().categories]
        if name in cats:
            index = cats.index(name)
            for glyph, delta, tip in (("▲", -1, "Move this category up"),
                                      ("▼", 1, "Move this category down")):
                b = _button(glyph, "quiet")
                b.setFixedSize(22, 20)
                b.setToolTip(tip)
                b.setEnabled(0 <= index + delta < len(cats))
                b.clicked.connect(lambda _=False, i=index, d=delta:
                                  self._move_category(i, d))
                row.addWidget(b)
        else:
            # "No category" has no position of its own — it follows the rest.
            row.addSpacing(52)

        item.setSizeHint(widget.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, widget)
        self._entries.append(("group", name))

    def _move_category(self, index: int, delta: int):
        cats = self.cfg().categories
        target = index + delta
        if not (0 <= index < len(cats) and 0 <= target < len(cats)):
            return
        cats.insert(target, cats.pop(index))
        self._refresh_and_signal()

    def _add_service(self, svc):
        rec = svc.recovery
        if not rec.enabled:
            note = "no automatic recovery"
        elif rec.max_attempts:
            note = f"recovers automatically, up to {rec.max_attempts} attempts"
        else:
            note = "recovers automatically, unlimited attempts"
        # The machine is the service's source, so it goes on the right as a chip
        # rather than buried in the middle of the secondary line. The category is
        # the heading above it now, so it isn't repeated here.
        machine = self.cfg().machine(svc.machine)
        where = (control.host_name() or "This PC") if (
            machine and machine.is_local) else (svc.machine or "?")
        item = QListWidgetItem()
        widget = _ListRow(svc.display(), f"{svc.name}  ·  {note}", "none",
                          tag=where, tag_category="none")
        item.setSizeHint(widget.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, widget)
        self._entries.append(("service", svc))

    def _refresh_and_signal(self):
        self.refresh()
        self.changed.emit()

    def _selected_services(self) -> list:
        """The chosen services, in the order shown. Headings can't be selected,
        so anything selected is a service."""
        return [self._entries[i.row()][1] for i in
                sorted(self.list.selectedIndexes(), key=lambda i: i.row())
                if 0 <= i.row() < len(self._entries)
                and self._entries[i.row()][0] == "service"]

    def _group_at(self, insert_at: int) -> str:
        """Which category an insertion point falls into — the nearest heading
        above it."""
        for i in range(min(insert_at, len(self._entries)) - 1, -1, -1):
            if self._entries[i][0] == "group":
                return self._entries[i][1]
        return cfg_mod.NO_CATEGORY

    def _dropped(self, source_row: int, insert_at: int):
        """A service dragged onto a group joins it, at the position it was let go.

        The visual order becomes the stored order outright, so what the tray
        panel and the dashboard show is what was arranged here.
        """
        if not (0 <= source_row < len(self._entries)):
            return
        kind, svc = self._entries[source_row]
        if kind != "service":
            return
        category = self._group_at(insert_at)
        services = [e[1] for e in self._entries if e[0] == "service"]
        before = sum(1 for i, e in enumerate(self._entries)
                     if i < insert_at and e[0] == "service" and e[1] is not svc)
        services.remove(svc)
        svc.category = category
        services.insert(before, svc)
        self.cfg().services[:] = services
        self._refresh_and_signal()

    def _add(self):
        cfg = self.cfg()
        machine = cfg_mod.LOCAL_MACHINE
        if len(cfg.machines) > 1:
            # More than one machine exists, so which one has to be asked.
            dlg = SearchableList("Add services", "On which machine?",
                                [(m.display(), m.name) for m in cfg.machines], self)
            if dlg.exec() != QDialog.Accepted:
                return
            machine = dlg.picked[0]

        taken = {s.name for s in cfg.services if (s.machine or "") == machine}
        picker = ServicePicker(taken, self, machine=machine)
        if picker.exec() != QDialog.Accepted:
            return
        for s in picker.picked:
            cfg.services.append(cfg_mod.Service(name=s["name"], label=s["display"],
                                                machine=machine))
        self._refresh_and_signal()

    def _remove(self):
        chosen = self._selected_services()
        if not chosen:
            QMessageBox.information(self, "Service Officer",
                                    "Select a service in the list first.")
            return
        names = [s.display() for s in chosen]
        msg = (f'Stop monitoring "{names[0]}"?' if len(names) == 1
               else f"Stop monitoring these {len(names)} services?")
        if QMessageBox.question(self, "Remove service", msg) != QMessageBox.Yes:
            return
        keep = [s for s in self.cfg().services if s not in chosen]
        self.cfg().services[:] = keep
        self._refresh_and_signal()

    def _set_category(self):
        """File the selected services under a heading, creating it if needed."""
        from PySide6.QtWidgets import QInputDialog
        chosen = self._selected_services()
        if not chosen:
            QMessageBox.information(self, "Service Officer",
                                    "Select one or more services in the list "
                                    "first.")
            return
        cfg = self.cfg()
        new_label = "New category…"
        options = ([cfg_mod.NO_CATEGORY_TITLE]
                   + [c.name for c in cfg.categories] + [new_label])
        # Preselect what they already share, so re-filing one service doesn't
        # start from the top of the list.
        current = {s.category or cfg_mod.NO_CATEGORY for s in chosen}
        start = 0
        if len(current) == 1:
            only = current.pop()
            start = options.index(only) if only in options else 0

        heading = ("Put this service under:" if len(chosen) == 1
                   else f"Put these {len(chosen)} services under:")
        pick, ok = QInputDialog.getItem(self, "Category", heading, options,
                                        start, False)
        if not ok or not pick:
            return
        if pick == new_label:
            name, ok = QInputDialog.getText(self, "New category",
                                            "Category name:")
            name = (name or "").strip()
            if not ok or not name:
                return
            if not cfg.category(name):
                cfg.categories.append(cfg_mod.Category(name=name))
            pick = name
        elif pick == cfg_mod.NO_CATEGORY_TITLE:
            pick = cfg_mod.NO_CATEGORY

        for svc in chosen:
            svc.category = pick
        self._refresh_and_signal()

    def _open_selected(self):
        chosen = self._selected_services()
        if len(chosen) != 1:
            QMessageBox.information(self, "Service Officer",
                                    "Select one service to open.")
            return
        self.detail.load(chosen[0], self.cfg().categories)
        self.stack.setCurrentWidget(self.detail)

    def _show_list(self):
        self.refresh()
        self.stack.setCurrentWidget(self.list_page)


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
        for text, kind in (("A port answers", "tcp"),
                           ("A URL answers", "http"),
                           ("It has a process", "process"),
                           ("A file is being written", "file"),
                           ("A command succeeds", "command")):
            self.add_menu.addAction(text, lambda k=kind: self._add_check(k))
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
        for index, check in enumerate(checks):
            self.checks_lay.addWidget(self._check_row(index, check))
        self.health_rules.setVisible(bool(checks))
        self._sync_health_enabled()

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


class CategoriesPage(_Page):
    """Headings the service lists are grouped under.

    Grouping only — nothing acts on a category — so this page is a list of names
    and their order, which is the order the groups appear in.
    """

    changed = Signal()

    def __init__(self, cfg_ref):
        super().__init__("Categories",
                         "Group your services under headings — SAP, SQL, "
                         "printing — so the dashboard and the tray panel can "
                         "fold away the ones you aren't looking at. Drag to "
                         "change the order the groups appear in.")
        self.cfg = cfg_ref

        self.list = ReorderList()
        self.list.reordered.connect(self._reorder)
        self.list.itemDoubleClicked.connect(lambda _i: self._rename())
        self.root.addWidget(self.list, 1)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(_button("Add category…", "primary", self._add))
        bar.addWidget(_button("Rename…", None, self._rename))
        bar.addWidget(_button("Remove", "danger", self._remove))
        bar.addStretch(1)
        self.root.addSpacing(14)
        self.root.addLayout(bar)
        self.root.addSpacing(10)
        self.root.addWidget(_label(
            "Services you haven't filed anywhere are listed together under "
            f"“{cfg_mod.NO_CATEGORY_TITLE}”, so nothing goes missing.",
            "hint", wrap=True))
        self.refresh()

    def refresh(self):
        keep = self.list.currentRow()
        self.list.clear()
        cfg = self.cfg()
        for cat in cfg.categories:
            members = [s for s in cfg.services if (s.category or "") == cat.name]
            names = ", ".join(s.display() for s in members[:4])
            if len(members) > 4:
                names += f", and {len(members) - 4} more"
            item = QListWidgetItem()
            widget = _ListRow(cat.name,
                              names or "nothing filed here yet",
                              tag=f"{len(members)}", tag_category="none")
            item.setSizeHint(widget.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, widget)
        if 0 <= keep < self.list.count():
            self.list.setCurrentRow(keep)

    def _ask(self, title: str, initial: str = "") -> str:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, title, "Category name:",
                                        text=initial)
        return (name or "").strip() if ok else ""

    def _add(self):
        name = self._ask("Add category")
        if not name:
            return
        if self.cfg().category(name):
            QMessageBox.information(self, "Service Officer",
                                    "That category already exists.")
            return
        self.cfg().categories.append(cfg_mod.Category(name=name))
        self.refresh()
        self.changed.emit()

    def _rename(self):
        row = self.list.currentRow()
        if row < 0:
            return
        cat = self.cfg().categories[row]
        name = self._ask("Rename category", cat.name)
        if not name or name == cat.name:
            return
        if self.cfg().category(name):
            QMessageBox.information(self, "Service Officer",
                                    "That category already exists.")
            return
        # Services point at the category by name, so they have to come along.
        for svc in self.cfg().services:
            if (svc.category or "") == cat.name:
                svc.category = name
        cat.name = name
        self.refresh()
        self.changed.emit()

    def _remove(self):
        row = self.list.currentRow()
        if row < 0:
            return
        cfg = self.cfg()
        cat = cfg.categories[row]
        members = [s for s in cfg.services if (s.category or "") == cat.name]
        message = f'Remove the category "{cat.name}"?'
        if members:
            message += (f"\n\nIts {len(members)} service(s) stay, listed under "
                        f"“{cfg_mod.NO_CATEGORY_TITLE}”.")
        if QMessageBox.question(self, "Remove category",
                                message) != QMessageBox.Yes:
            return
        for svc in members:
            svc.category = cfg_mod.NO_CATEGORY
        del cfg.categories[row]
        self.refresh()
        self.changed.emit()

    def _reorder(self, source, target):
        cats = self.cfg().categories
        if not (0 <= source < len(cats) and 0 <= target < len(cats)):
            return
        cats.insert(target, cats.pop(source))
        self.refresh()
        self.changed.emit()
        self.list.setCurrentRow(target)


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
        bar.addWidget(_button("Test run ▸", None,
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


class MachinesPage(_Page):
    """Every service belongs to a machine; this computer is always one of them."""

    changed = Signal()
    #: an address resolved on a worker thread; redraw on the GUI thread
    address_found = Signal()

    def __init__(self, cfg_ref):
        super().__init__("Machines",
                         "Where your services live, by name and address. This "
                         "computer is always here; adding another lets its "
                         "services appear in the same panel.")
        self.cfg = cfg_ref
        self.address_found.connect(self.refresh)

        self.list = QListWidget()
        self.root.addWidget(self.list, 1)
        bar = QHBoxLayout()
        bar.addWidget(_button("Add machine…", "primary", self._add))
        bar.addWidget(_button("Remove", "danger", self._remove))
        bar.addStretch(1)
        self.root.addSpacing(14)
        self.root.addLayout(bar)
        self.root.addSpacing(10)
        self.root.addWidget(_label(
            "Managing another machine needs administrator rights on it and the "
            "usual Windows service ports reachable. Its services are added on "
            "the Services page, where each one names the machine it belongs to.",
            "hint", wrap=True))
        self.refresh()

    def refresh(self):
        self.list.clear()
        cfg = self.cfg()
        for machine in cfg.machines:
            count = sum(1 for s in cfg.services if (s.machine or "") == machine.name)
            item = QListWidgetItem()
            widget = _ListRow(self._title(machine),
                              f"{count} service{'s' if count != 1 else ''}",
                              tag="This PC" if machine.is_local else "",
                              tag_category="running")
            item.setSizeHint(widget.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, widget)
            # DNS costs seconds on a name that doesn't resolve, so the address
            # arrives late and the row is redrawn then. Only ask when we don't
            # already know it — otherwise the redraw would ask again forever.
            if control.cached_address(machine.name) is None:
                control.resolve_address(machine.name,
                                        lambda *_a: self.address_found.emit())

    def _title(self, machine) -> str:
        """CTL052 (10.77.3.50) — the name alone isn't enough when someone has to
        RDP to the box, and an IP alone isn't enough to know which box it is."""
        name = control.host_name() if machine.is_local else machine.name
        name = name or machine.display()
        address = control.cached_address(machine.name)
        if address:
            return f"{name}  ({address})"
        if machine.label and machine.label != name:
            return f"{name}  ·  {machine.label}"
        return name

    def _add(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Add machine",
                                        "Computer name (as Windows knows it):")
        name = (name or "").strip().lstrip("\\")
        if not ok or not name:
            return
        if self.cfg().machine(name):
            QMessageBox.information(self, "Service Officer",
                                    "That machine is already listed.")
            return
        if not control.reachable(name):
            if QMessageBox.question(
                    self, "Service Officer",
                    f"Could not reach {name} — its service manager did not "
                    "answer.\n\nAdd it anyway?") != QMessageBox.Yes:
                return
        self.cfg().machines.append(cfg_mod.Machine(name=name, label=name))
        self.refresh()
        self.changed.emit()

    def _remove(self):
        row = self.list.currentRow()
        if row < 0:
            return
        machine = self.cfg().machines[row]
        if machine.is_local:
            QMessageBox.information(self, "Service Officer",
                                    "This computer can't be removed.")
            return
        using = [s.display() for s in self.cfg().services
                 if (s.machine or "") == machine.name]
        if using:
            QMessageBox.information(
                self, "Service Officer",
                f"{machine.display()} still has {len(using)} service(s) here. "
                "Remove them first:\n\n" + "\n".join(using[:8]))
            return
        del self.cfg().machines[row]
        self.refresh()
        self.changed.emit()


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


class HistoryPage(_Page):
    changed = Signal()

    COLUMNS = ("Time", "Service", "Event", "Detail", "Source")
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

        self.full_detail = QCheckBox("Full detail")
        self.full_detail.setToolTip(
            "Every state the SCM reported, including the halfway ones. Off, a "
            "restart reads as “restart requested” then “Running” instead of four "
            "rows saying the same thing. Nothing is left out of the file either "
            "way.")
        self.full_detail.toggled.connect(self.reload)
        filt.addWidget(self.full_detail)

        self.include_windows = QCheckBox("Windows event log")
        self.include_windows.setToolTip(
            "Merge what Windows recorded about these services — the SCM's "
            "\"terminated unexpectedly\", and errors the service itself logged. "
            "This is usually where the reason is.")
        self.include_windows.toggled.connect(self.reload)
        filt.addWidget(self.include_windows)

        # Only offered once something is actually filtered — a permanently
        # visible "clear" invites the question of what it would clear.
        self.clear_filters = _button("Clear filters ✕", "quiet", self._clear_filters)
        self.clear_filters.setToolTip("Back to all services, the last 24 hours, "
                                      "any trigger.")
        self.clear_filters.setVisible(False)
        filt.addWidget(self.clear_filters)

        filt.addStretch(1)
        filt.addWidget(_button("Refresh", "quiet", self.reload))
        filt.addWidget(_button("Export CSV…", "quiet", self._export))
        self.root.addLayout(filt)
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
                full=self.full_detail.isChecked())
        except Exception:
            return []
        wanted = self.source_filter.currentData()
        if wanted:
            rows = [r for r in rows if r.get("source", "").startswith(wanted)]
        return rows

    def reload(self):
        rows = self._current_rows()
        self._rows_cache = rows
        self.clear_filters.setVisible(self._filtered())
        self._show_path()

        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for r in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            ts = str(r.get("ts", ""))
            day, _, clock = ts.partition("T")
            cells = [f"{day}  {clock[:8]}", r.get("label") or r.get("service", ""),
                     r.get("event", ""), r.get("detail", ""), r.get("source", "")]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
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


class GeneralPage(_Page):
    changed = Signal()
    theme_changed = Signal(str)      # applied live, so you can see the choice

    def __init__(self, cfg_ref):
        # Scrolls: appearance, startup, notifications and the about block leave
        # only fifty pixels spare, and every setting added eats into that.
        super().__init__("General", "How the app itself behaves.", scroll=True)
        self.cfg = cfg_ref

        self.root.addWidget(_label("APPEARANCE", "section"))
        self.root.addSpacing(9)
        self.theme = QComboBox()
        self.theme.addItems(["System", "Dark", "Light"])
        self.theme.setFixedWidth(150)
        self.theme.currentIndexChanged.connect(self._set_theme)
        self.root.addWidget(_sentence("Theme", self.theme))
        self.root.addWidget(_label(
            "System follows the Windows setting and switches with it.",
            "hint", wrap=True))
        self.root.addSpacing(24)

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
        self.root.addWidget(_label("ABOUT", "section"))
        self.root.addSpacing(9)
        # Which build, and where it lives. "Version 2.0.0" alone doesn't answer
        # the question people actually ask on someone else's server — is this the
        # one I installed — so the commit and build time are here too.
        build = _label(version.full(), "hint", wrap=True)
        build.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.root.addWidget(build)
        self.root.addSpacing(4)
        where = _label(f"Installed in  {version.install_dir()}\n"
                       f"Settings and history in  {cfg_mod.APP_DIR}",
                       "hint", wrap=True)
        where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.root.addWidget(where)
        self.root.addSpacing(8)
        row = QHBoxLayout()
        row.addWidget(_button("Copy build details", "quiet", self._copy_build))
        row.addStretch(1)
        self.root.addLayout(row)
        self.root.addStretch(1)

    def _copy_build(self):
        """One click to paste into a ticket."""
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(
            f"Service Officer {version.full()}\n"
            f"Installed in: {version.install_dir()}\n"
            f"Data in: {cfg_mod.APP_DIR}\n"
            f"Machine: {control.host_name()} ({control.cached_address('') or '?'})")

    _THEMES = ("system", "dark", "light")

    def _set_theme(self, index):
        value = self._THEMES[index]
        self.cfg().theme = value
        self.changed.emit()
        self.theme_changed.emit(value)

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
        self.theme.blockSignals(True)
        self.theme.setCurrentIndex(self._THEMES.index(
            cfg.theme if cfg.theme in self._THEMES else "system"))
        self.theme.blockSignals(False)


# ── the window ─────────────────────────────────────────────────────────────
class MainPanel(QDialog):
    """The window the tray icon opens on a double-click."""

    saved = Signal(object)               # the new Config
    test_run = Signal(object, str)       # the stack being edited, action
    run_trigger = Signal(object)         # a trigger, run on demand from its page
    theme_changed = Signal(str)          # applied immediately, saved with the rest
    # Dashboard controls act on live services, so the app does the work.
    action_requested = Signal(str, str, str)     # action, service, machine
    bulk_requested = Signal(str, list)
    run_stack = Signal(str)
    refresh_requested = Signal()
    open_services_mmc = Signal()

    def __init__(self, cfg, parent=None, store=None, live_config=None):
        super().__init__(parent)
        # Without a running app behind it (tests, a screenshot) the dashboard
        # falls back to what it was handed, and to the global store.
        self._store = store if store is not None else st.store
        self._live = live_config or (lambda: self._cfg)
        # No version here: the title bar is read every time the window opens, and
        # a build number is something you go and look up once. It lives in
        # General → About, with the commit and a copy button.
        self.setWindowTitle("Service Officer — Service Management Panel")
        self.setWindowIcon(icons.base_icon("green"))
        # A dialog by class, a window by behaviour: this is where the work
        # happens now, so it gets minimise and maximise like any other window.
        self.setWindowFlags(Qt.Window | Qt.WindowMinimizeButtonHint |
                            Qt.WindowMaximizeButtonHint |
                            Qt.WindowCloseButtonHint)
        self.resize(980, 660)
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
        nav.setObjectName("navPanel")
        nav.setAttribute(Qt.WA_StyledBackground, True)
        nav.setFixedWidth(186)
        nl = QVBoxLayout(nav)
        nl.setContentsMargins(0, 14, 0, 14)
        nl.setSpacing(0)

        self.pages = QStackedWidget()
        get = lambda: self._cfg

        # The dashboard acts on real services, so it reads the saved config and
        # the live store — not the copy being edited on the other pages.
        self.dashboard = DashboardPage(self._live, self._store)
        self.dashboard.action_requested.connect(self.action_requested)
        self.dashboard.bulk_requested.connect(self.bulk_requested)
        self.dashboard.run_stack.connect(self.run_stack)
        self.dashboard.refresh_requested.connect(self.refresh_requested)
        self.dashboard.open_services_mmc.connect(self.open_services_mmc)

        self.services_page = ServicesPage(get, self._store)
        self.categories_page = CategoriesPage(get)
        self.stacks_page = StacksPage(get)
        self.schedule_page = SchedulePage(get)
        self.machines_page = MachinesPage(get)
        self.history_page = HistoryPage(get)
        self.general_page = GeneralPage(get)
        self.services_page.changed.connect(self.stacks_page.refresh)
        self.services_page.changed.connect(self.schedule_page.refresh)
        self.stacks_page.changed.connect(self.schedule_page.refresh)
        self.stacks_page.test_run.connect(self.test_run)
        self.schedule_page.run_now.connect(self.run_trigger)
        self.general_page.theme_changed.connect(self.theme_changed)
        self.general_page.theme_changed.connect(lambda _m: self.restyle())
        self.machines_page.changed.connect(self.services_page.refresh)
        # Renaming or removing a category changes what the Services rows say,
        # and filing a service can create a category the other page must list.
        self.categories_page.changed.connect(self.services_page.refresh)
        self.services_page.changed.connect(self.categories_page.refresh)
        for page in (self.services_page, self.categories_page, self.stacks_page,
                     self.schedule_page, self.machines_page, self.history_page,
                     self.general_page):
            page.changed.connect(self._refresh_save_state)

        self._nav_buttons = []
        self._by_name = {}
        self._buttons_by_name = {}
        # Settings are one section here, not the name of the window.
        sections = [("Overview", None, None),
                    ("Dashboard", "dashboard", self.dashboard),
                    ("Manage", None, None),
                    ("Services", "services", self.services_page),
                    ("Categories", "categories", self.categories_page),
                    ("Stacks", "stacks", self.stacks_page),
                    ("Schedule", "schedule", self.schedule_page),
                    ("History", "history", self.history_page),
                    ("Infrastructure", None, None),
                    ("Machines", "machines", self.machines_page),
                    ("Settings", None, None),
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
            self._by_name[kind] = page
            self._buttons_by_name[kind] = b
        nl.addStretch(1)

        body.addWidget(nav)
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)

        foot = QWidget()
        foot.setObjectName("footerBar")
        foot.setAttribute(Qt.WA_StyledBackground, True)
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(16, 11, 16, 11)
        # Next to the buttons, not stranded in the far corner.
        fl.addStretch(1)
        self.status = _label("", "hint")
        fl.addWidget(self.status)
        fl.addSpacing(8)
        fl.addWidget(_button("Close", None, self.reject))
        self.save_button = _button("Save", "primary", self._save)
        fl.addWidget(self.save_button)
        outer.addWidget(foot)

        self.history_page.load_from(self._cfg)
        self.general_page.load_from(self._cfg)
        self.go_to("dashboard")            # what you want on opening: the status
        self._refresh_save_state()

    def _select(self, page, button):
        self.pages.setCurrentWidget(page)
        for b in self._nav_buttons:
            b.setChecked(b is button)

    def go_to(self, name: str) -> bool:
        """Open a named section — the tray menu offers them directly."""
        page = self._by_name.get(name)
        if page is None:
            return False
        self._select(page, self._buttons_by_name.get(name))
        return True

    def config(self):
        return self._cfg

    def restyle(self) -> None:
        """Repaint what the global stylesheet can't reach after a mode change.

        That is now only the drawn icons — every colour is in the sheet, so the
        per-widget FlatEdit.restyle() hook is gone. The list rebuilds stay because
        their rows carry drawn status dots.
        """
        for kind, btn in self._buttons_by_name.items():
            btn.setIcon(icons.nav_icon(kind, 19))
        self.services_page.refresh()
        self.categories_page.refresh()
        self.stacks_page.refresh()
        self.dashboard.rebuild()
        if self.stacks_page.detail.stack is not None:
            self.stacks_page.detail._rebuild()

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
