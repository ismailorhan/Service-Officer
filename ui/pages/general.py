"""Settings: the handful of choices that belong to the app, not a service."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLineEdit,
                               QMessageBox)

from core import config as cfg_mod
from core import control
from core import local as local_mod
from core import version

from ..widgets import button as _button, label as _label
from .base import _Page, _sentence


class GeneralPage(_Page):
    changed = Signal()
    theme_changed = Signal(str)      # applied live, so you can see the choice
    #: The hub address was changed and stored. The application decides what to do about
    #: it — which is to restart, because whether an engine runs in this process is
    #: settled when it starts.
    hub_changed = Signal(str)

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

        self.root.addSpacing(24)
        self._build_hub()

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

    # -- the hub ----------------------------------------------------------
    def _build_hub(self):
        """Which hub this panel reads, or nothing at all.

        Empty means this computer does the work itself, which is what a single-machine
        install is — so this one field is the whole of "client or not". There is no mode
        to choose anywhere in this product; there is an address.
        """
        self.root.addWidget(_label("HUB", "section"))
        self.root.addSpacing(9)
        self.hub_state = _label("", "hint", wrap=True)
        self.root.addWidget(self.hub_state)
        self.root.addSpacing(8)

        # Two fields, laid out like the installer's: a host is a host and a port is a
        # port, and "ctl052:9100" asks somebody to know that a colon means something here.
        self.hub_url = QLineEdit()
        self.hub_url.setPlaceholderText("empty — this computer does the work itself")
        self.hub_url.setMinimumWidth(220)
        self.hub_port = QLineEdit()
        self.hub_port.setFixedWidth(64)
        self.hub_port.setPlaceholderText("8797")
        self.root.addWidget(_sentence("Host", self.hub_url, "Port", self.hub_port))
        self.root.addWidget(_label(
            "A host name or IP. Leave the host empty to watch this computer's own "
            "services instead; leave the port empty for 8797.", "hint", wrap=True))
        self.root.addSpacing(10)

        self.hub_token = QLineEdit()
        self.hub_token.setEchoMode(QLineEdit.Password)
        self.hub_token.setMinimumWidth(260)
        self.root.addWidget(_sentence("Token", self.hub_token))
        self.hub_token_hint = _label("", "hint", wrap=True)
        self.root.addWidget(self.hub_token_hint)
        self.root.addSpacing(10)

        row = QHBoxLayout()
        row.addWidget(_button("Apply and restart", "primary", self._apply_hub))
        row.addWidget(_button("Test", "quiet", self._test_hub))
        row.addStretch(1)
        self.root.addLayout(row)
        self.hub_result = _label("", "hint", wrap=True)
        self.root.addWidget(self.hub_result)

    #: What the hub listens on unless somebody changed it. Named here rather than
    #: repeated: it is also the installer's default and core/config's.
    DEFAULT_PORT = "8797"

    def _normalised(self, given: str = None, port: str = None) -> str:
        """The two fields as one URL, tolerating a whole address pasted into the host box.

        Somebody who copies "https://ctl052:9100" from a ticket and pastes it into Host
        should get what they meant, not a refusal — so a port found there wins over the
        port field, which is where it was typed.
        """
        host = (self.hub_url.text() if given is None else given).strip()
        if not host:
            return ""
        if "://" in host:
            host = host.split("://", 1)[1]
        host = host.split("/", 1)[0].strip()
        if not host:
            return ""
        pasted = ""
        bracketed = host.startswith("[")
        tail = host.split("]", 1)[1] if bracketed else host
        if ":" in tail:
            host, _, pasted = host.rpartition(":")
            pasted = pasted.strip()
        wanted = pasted or (self.hub_port.text() if port is None else port).strip()
        if not wanted.isdigit() or not 1 <= int(wanted) <= 65535:
            wanted = self.DEFAULT_PORT
        return f"https://{host}:{wanted}"

    @staticmethod
    def _split(url: str) -> tuple:
        """A stored URL back into the two fields it is shown in."""
        host = (url or "").strip()
        if not host:
            return "", ""
        if "://" in host:
            host = host.split("://", 1)[1]
        host = host.split("/", 1)[0]
        tail = host.split("]", 1)[1] if host.startswith("[") else host
        if ":" in tail:
            host, _, port = host.rpartition(":")
            return host, port
        return host, ""

    def _test_hub(self):
        """Ask before committing to it, because the answer names the machine and the
        version — the two things that make an address wrong in a way nobody notices."""
        url = self._normalised()
        if not url:
            self.hub_result.setText("No address: this computer will do the work itself.")
            return
        from core import hub_client
        self.hub_result.setText(f"Asking {url}…")
        self.hub_result.repaint()
        client = hub_client.HubClient(url, self.hub_token.text().strip())
        try:
            answer = client.ping()
        except Exception as exc:
            self.hub_result.setText(f"{url} did not answer: {exc}")
            return
        theirs = str(answer.get("version", "?"))
        mine = version.short()
        note = (f"{answer.get('name', '?')} answered — version {theirs}")
        if theirs.split(".")[:3] != mine.split(".")[:3]:
            note += (f", which is not this panel's {mine}. A client and its hub have to "
                     "match; upgrade one of them.")
        self.hub_result.setText(note)

    def _apply_hub(self):
        """Store it and restart.

        Restart rather than reconnect: whether this process runs an engine of its own is
        settled when it starts, and switching between the two live would mean tearing down
        a poller, a watchdog and a scheduler in place. A restart is honest and takes a
        second; pretending would be neither.
        """
        url = self._normalised()
        token = self.hub_token.text().strip()
        settings = local_mod.load()
        if url and url != settings.hub_url:
            settings.hub_fingerprint = ""      # a different hub, so the old pin is void
        settings.hub_url = url
        if not local_mod.save(settings):
            QMessageBox.warning(self, "Service Officer",
                                "Could not save this client's settings.")
            return
        if url and token:
            local_mod.set_token(url, token)
        self.hub_token.clear()
        self._describe_hub()
        self.hub_changed.emit(url)

    def _describe_hub(self):
        settings = local_mod.load()
        if settings.hub_url:
            pinned = settings.hub_fingerprint or "not pinned yet"
            self.hub_state.setText(
                f"Reading {settings.hub_url}\nCertificate  {pinned}")
            self.hub_token_hint.setText(
                "Stored already — type a new one only to replace it."
                if local_mod.token(settings.hub_url) else
                "Needed. On the hub: ServiceOfficerHub.exe client add <a name for this "
                "computer>")
        else:
            self.hub_state.setText(
                "No hub: this computer watches its own services, and stops watching when "
                "this app is closed.")
            self.hub_token_hint.setText("")

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
        settings = local_mod.load()
        host, port = self._split(settings.hub_url)
        for field, value in ((self.hub_url, host), (self.hub_port, port)):
            field.blockSignals(True)
            field.setText(value)
            field.blockSignals(False)
        self._describe_hub()
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
