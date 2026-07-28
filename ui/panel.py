"""The Service Management Panel — the app's main window.

This began as a settings dialog and outgrew it: the pages that matter are
Services, Stacks, Schedule and History, which are operational rather than
configuration. So the window is the panel now, and the settings live in it as one
section of its own menu instead of naming the whole thing.

What is left here is the window itself: the sidebar, which page is showing, and
Save. The pages are in ui/pages/, one module per section — this file was 2,400
lines when they all lived in it.

Numbers live inside sentences ("Try up to 3 times, waiting 10 seconds first…")
because six labelled rows for one rule read as clutter.

Edits are made on a copy of the config; nothing reaches disk until Save.
"""

from __future__ import annotations

import copy

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QMessageBox, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from core import config as cfg_mod
from core import state as st

from . import icons
from .widgets import button as _button, label as _label
from .dashboard import DashboardPage
from .pages import (CategoriesPage, GeneralPage, HistoryPage, MachinesPage,
                    SchedulePage, ServicesPage, StacksPage)


# ── the window ─────────────────────────────────────────────────────────────
class MainPanel(QDialog):
    """The window the tray icon opens on a double-click."""

    saved = Signal(object)               # the new Config
    test_run = Signal(object, str)       # the stack being edited, action
    run_trigger = Signal(object)         # a trigger, run on demand from its page
    theme_changed = Signal(str)          # applied immediately, saved with the rest
    #: The hub this client reads was changed on the General page. Passed up because the
    #: answer to it is a restart — whether this process runs an engine is settled when it
    #: starts — and only the application can do that.
    hub_changed = Signal(str)
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
        # The store, because a machine's row says whether it is answering — which is
        # live, not a setting.
        self.machines_page = MachinesPage(get, self._store)
        self.history_page = HistoryPage(get)
        self.general_page = GeneralPage(get)
        self.services_page.changed.connect(self.stacks_page.refresh)
        self.services_page.changed.connect(self.schedule_page.refresh)
        self.stacks_page.changed.connect(self.schedule_page.refresh)
        self.stacks_page.test_run.connect(self.test_run)
        self.schedule_page.run_now.connect(self.run_trigger)
        self.general_page.theme_changed.connect(self.theme_changed)
        self.general_page.hub_changed.connect(self.hub_changed)
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
