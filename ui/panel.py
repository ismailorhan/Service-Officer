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
import threading

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QMessageBox, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from core.i18n import t
from core import config as cfg_mod
from core import state as st

from . import icons
from .widgets import button as _button, label as _label
from .dashboard import DashboardPage
from .pages import (CategoriesPage, ClientsPage, GeneralPage, HistoryPage,
                    HubPage, MachinesPage, SchedulePage, ServicesPage,
                    StacksPage)


# ── the window ─────────────────────────────────────────────────────────────
class MainPanel(QDialog):
    """The window the tray icon opens on a double-click."""

    saved = Signal(object)               # the new Config
    #: What the sidebar's foot should say about updates, or "" for nothing. A signal because
    #: the answer arrives on a socket thread and a worker thread has no Qt thread — the same
    #: reason every other cross-thread hop in this product is one.
    update_found = Signal(str)
    test_run = Signal(object, str)       # the stack being edited, action
    run_trigger = Signal(object)         # a trigger, run on demand from its page
    theme_changed = Signal(str)          # applied immediately, stored immediately
    #: A display choice was stored on this computer. The application re-reads it: the tray
    #: menu and the flyout are worded at build time, so they have to be built again.
    mine_changed = Signal()
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

    def __init__(self, cfg, parent=None, store=None, live_config=None,
                 hub=None):
        super().__init__(parent)
        # Without a running app behind it (tests, a screenshot) the dashboard
        # falls back to what it was handed, and to the global store.
        self._store = store if store is not None else st.store
        self._live = live_config or (lambda: self._cfg)
        #: The hub this panel reads, or None when it runs its own engine. A callable so the
        #: Clients page can ask again rather than hold a reference to something that was
        #: None when the window was built.
        self._hub = hub if callable(hub) else (lambda: hub)
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
        self.dashboard = DashboardPage(self._live, self._store, hub=self._hub)
        self.dashboard.action_requested.connect(self.action_requested)
        self.dashboard.bulk_requested.connect(self.bulk_requested)
        self.dashboard.run_stack.connect(self.run_stack)
        self.dashboard.refresh_requested.connect(self.refresh_requested)
        self.dashboard.open_services_mmc.connect(self.open_services_mmc)

        self.services_page = ServicesPage(get, self._store, self._hub)
        self.categories_page = CategoriesPage(get)
        self.stacks_page = StacksPage(get)
        self.schedule_page = SchedulePage(get)
        # The store, because a machine's row says whether it is answering — which is
        # live, not a setting.
        self.machines_page = MachinesPage(get, self._store, self._hub)
        self.history_page = HistoryPage(get, self._hub)
        self.clients_page = ClientsPage(self._hub)
        self.hub_page = HubPage(get, self._hub)
        self.general_page = GeneralPage(get)
        self.services_page.changed.connect(self.stacks_page.refresh)
        self.services_page.changed.connect(self.schedule_page.refresh)
        self.stacks_page.changed.connect(self.schedule_page.refresh)
        self.stacks_page.test_run.connect(self.test_run)
        self.schedule_page.run_now.connect(self.run_trigger)
        self.general_page.theme_changed.connect(self.theme_changed)
        self.general_page.mine_changed.connect(self.mine_changed)
        self.hub_page.hub_changed.connect(self.hub_changed)
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
                    ("Hub", "hub", self.hub_page),
                    ("Machines", "machines", self.machines_page),
                    ("Clients", "clients", self.clients_page),
                    ("Settings", None, None),
                    ("General", "general", self.general_page)]
        for text, kind, page in sections:
            if page is None:
                cap = _label(t(text).upper(), "section")
                cap.setContentsMargins(16, 14, 16, 6)
                nl.addWidget(cap)
                continue
            self.pages.addWidget(page)
            # `text` is translated; `kind` is the key everything else uses, and stays.
            b = QPushButton("  " + t(text))
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
        # An update, at the foot of the sidebar, and only when there is one.
        #
        # It was only on the Hub page, which is three clicks away and a page nobody opens
        # twice — on an ERP server that is weeks of not knowing. Here it is in the one place
        # that is on screen whenever the panel is, and it goes away entirely when there is
        # nothing to say: a permanent row reading "up to date" is a row people stop seeing,
        # and then they stop seeing it when it changes.
        self.update_hint = QPushButton("")
        self.update_hint.setProperty("kind", "nav")
        self.update_hint.setProperty("nudge", "true")
        self.update_hint.setCursor(Qt.PointingHandCursor)
        self.update_hint.setIcon(icons.nav_icon("hub", 19))
        self.update_hint.setIconSize(QSize(19, 19))
        # Straight to where the button that acts on it lives, rather than explaining itself
        # in a tooltip nobody hovers.
        self.update_hint.clicked.connect(lambda: self.go_to("hub"))
        self.update_hint.setVisible(False)
        #: Started on the first check, so a window nobody asked about updates on has no timer.
        self._hint_timer = None
        nl.addWidget(self.update_hint)
        self.update_found.connect(self._show_update_hint)
        # No hub, nobody to pair: the page and its button are not there rather than
        # there and empty. A panel that runs its own engine has no clients by
        # definition — it is the only thing reading itself.
        if self._hub() is None:
            self._buttons_by_name["clients"].setVisible(False)

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
        # No Close button: the title bar already has one, and a second way to do the same
        # thing sits next to Save competing for the same glance.
        self.save_button = _button("Save", "primary", self._save)
        fl.addWidget(self.save_button)
        outer.addWidget(foot)

        self.history_page.load_from(self._cfg)
        self.general_page.load_from(self._cfg)
        self.hub_page.load_from(self._cfg)
        self.go_to("dashboard")            # what you want on opening: the status
        self._refresh_save_state()

    def _select(self, page, button):
        self.pages.setCurrentWidget(page)
        for b in self._nav_buttons:
            b.setChecked(b is button)
        # Asked for when it is looked at, not when the window opens: this one is a request
        # over a network, and most times the panel is opened nobody goes near it.
        if page is self.clients_page or page is self.hub_page:
            page.refresh()

    #: How often the sidebar's foot is refreshed while this window is open.
    #:
    #: It was filled in once, when the panel opened. So a panel that was already open when the
    #: hub learned about a release never said so — which is how it was found: the Hub page read
    #: "2.2.9 is available" and the corner underneath it was empty. An indicator that only
    #: updates when you close and reopen the window is not much of an indicator.
    #:
    #: A minute, and it costs a read of the hub's memory — no network, no disk. The hub itself
    #: only asks GitHub once a day; this is just how quickly the answer reaches the corner.
    UPDATE_HINT_SECONDS = 60

    def check_for_update(self) -> None:
        """Ask the hub what it knows and put it at the foot of the sidebar, or take it away.

        One read, off the drawing thread: the hub answers from memory, but "the hub answers
        from memory" is a promise about the hub and this is a socket. A panel that freezes
        while opening is a panel people close.
        """
        hub = self._hub()
        if hub is None:
            return
        threading.Thread(target=self._asked_about_update, args=(hub,), daemon=True,
                         name="panel-update-hint").start()
        if self._hint_timer is None:
            self._hint_timer = QTimer(self)
            self._hint_timer.timeout.connect(self.check_for_update)
            self._hint_timer.start(self.UPDATE_HINT_SECONDS * 1000)

    def _asked_about_update(self, hub) -> None:
        try:
            said = hub.update_state()
            said = self._freshened(hub, said)
        except Exception:
            # Quietly. The hub being unreachable is already said in three louder places, and a
            # third voice saying it in the sidebar is noise.
            return
        from core import version as version_mod
        offered = said.get("available") or ""
        running = said.get("running") or ""
        if offered:
            # The hub has a newer release to install. Its own business, but worth surfacing
            # wherever somebody is looking.
            self.update_found.emit(t("{version} is available", version=offered))
        elif running and not version_mod.compatible(running):
            # This panel is the one that is behind. Different sentence, same row: what somebody
            # needs to know is that a version is in the way, and where to go about it.
            self.update_found.emit(t("Update this computer to {version}", version=running))
        else:
            self.update_found.emit("")

    @staticmethod
    def _freshened(hub, said: dict) -> dict:
        """Make the hub look again if its answer is old, and return the newer one.

        Opening a window is a person wanting to know now. Without this the panel asked the hub
        every minute and the hub had nothing new to say for up to a day — a fast question about
        a stale answer, and a release published an hour after the hub's last look went
        unmentioned until the next one.

        Bounded by `updates.STALE_SECONDS`, and self-limiting: a check stamps the hub's clock
        whether or not it reached GitHub, so a hub with no way out is asked once an hour rather
        than once a minute.
        """
        from core import updates
        ago = said.get("checked_ago")
        if ago is None:
            # An older hub, before it reported this. Its own daily check still runs; there is
            # simply nothing here to decide with, and guessing would mean asking every minute.
            return said
        if 0 <= float(ago) < updates.STALE_SECONDS:
            return said
        try:
            hub.check_for_update()
            return hub.update_state()
        except Exception:
            # The hub not answering is said elsewhere. What was already known is better than
            # nothing, so the stale answer stands.
            return said

    def _show_update_hint(self, text: str) -> None:
        self.update_hint.setText("  " + text if text else "")
        self.update_hint.setVisible(bool(text))

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
