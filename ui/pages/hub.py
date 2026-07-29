"""Which hub this panel reads.

It lived at the bottom of General, under appearance, startup and notifications — four
scrolls past the things it outranks. A hub decides where every service in this window
comes from and whether this computer does the watching at all; that is infrastructure,
next to Machines and Clients, not a preference like a colour scheme.

Empty means this computer does the work itself, which is what a single-machine install is
— so this one field is the whole of "client or not". There is no mode to choose anywhere
in this product; there is an address.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QHBoxLayout, QLineEdit, QMessageBox, QVBoxLayout,
                               QWidget)

from core import local as local_mod
from core.i18n import t
from core import version

from ..widgets import button as _button, label as _label
from .base import _Page, _sentence


class HubPage(_Page):
    #: The hub address was changed and stored. The application decides what to do about
    #: it — which is to restart, because whether an engine runs in this process is
    #: settled when it starts.
    hub_changed = Signal(str)

    #: What the hub listens on unless somebody changed it. Named here rather than
    #: repeated: it is also the installer's default and core/config's.
    DEFAULT_PORT = "8797"

    def __init__(self, cfg_ref=None, hub=None):
        super().__init__("Hub", "Where this panel gets its services from. A hub watches "
                                "and controls; this window is a view of one.", scroll=True)

        #: The config, for the hub's *own* port — the one it listens on, which is a
        #: different thing from the address above. A getter or None.
        self.cfg = cfg_ref
        self._hub = hub if callable(hub) else (lambda: hub)

        self.hub_state = _label("", "hint", wrap=True)
        self.root.addWidget(self.hub_state)
        self.root.addSpacing(14)

        self.root.addWidget(_label("ADDRESS", "section"))
        self.root.addSpacing(9)
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
            "A host name or IP; leave the host empty to watch this computer's own services "
            "instead, and the port empty for 8797.\n"
            "This is not the port a hub on this computer listens on — that is the hub's "
            "own setting, stored by every client of it: "
            "ServiceOfficerHub.exe port <n>, then restart the service.",
            "hint", wrap=True))
        self.root.addSpacing(20)

        self.root.addWidget(_label("TOKEN", "section"))
        self.root.addSpacing(9)
        self.hub_token = QLineEdit()
        self.hub_token.setEchoMode(QLineEdit.Password)
        self.hub_token.setMinimumWidth(260)
        self.root.addWidget(_sentence("Token", self.hub_token))
        self.hub_token_hint = _label("", "hint", wrap=True)
        self.root.addWidget(self.hub_token_hint)
        self.root.addSpacing(20)

        row = QHBoxLayout()
        row.addWidget(_button("Apply and restart", "primary", self._apply_hub))
        row.addWidget(_button("Test", "quiet", self._test_hub))
        row.addStretch(1)
        self.root.addLayout(row)
        self.hub_result = _label("", "hint", wrap=True)
        self.root.addWidget(self.hub_result)

        # -- and the other port, which is a different thing entirely -----------
        self.serving = QWidget()
        serving = QVBoxLayout(self.serving)
        serving.setContentsMargins(0, 22, 0, 0)
        serving.setSpacing(0)
        serving.addWidget(_label(t("SERVING"), "section"))
        serving.addSpacing(9)
        self.listen_port = QLineEdit()
        self.listen_port.setFixedWidth(80)
        serving.addWidget(_sentence(t("This computer serves clients on port"),
                                    self.listen_port))
        serving.addWidget(_label(
            t("The port the Hub service listens on — not the one above, which is where this "
            "panel reads from. Applying it moves the socket and the firewall rule, and every "
            "client is told the new number first so they follow rather than losing the hub.\n"
            "ServiceOfficerHub.exe port <n> does the same from a command line, which is the "
            "way in when the hub cannot be reached at all."),
            "hint", wrap=True))
        serving.addSpacing(10)
        moving = QHBoxLayout()
        moving.addWidget(_button(t("Move to this port"), "primary", self._move_port))
        moving.addStretch(1)
        serving.addLayout(moving)
        self.serve_result = _label("", "hint", wrap=True)
        serving.addWidget(self.serve_result)
        self.root.addWidget(self.serving)
        # Without this the layout hands the spare height to the gaps between sections, and
        # a page with six widgets on it drifts into a page with six widgets *spread* over
        # it — 50 pixels between a heading and its field where General has 10. Every other
        # page here ends with one; measured against General's screenshot.
        self.root.addStretch(1)

    def _serves_here(self) -> bool:
        """Whether the hub being read is *this* computer's.

        Only then is its listening port something to offer. On a workstation it belongs to
        somebody else's machine, and a field that moves a server's socket sitting under a
        field about this panel's own address is two subjects in one place.
        """
        hub = self._hub()
        if hub is None:
            return False
        from core import control
        here = (control.host_name() or "").lower()
        return bool(here) and here == (getattr(hub, "host", "") or "").lower()

    def _move_port(self):
        """Ask the hub to listen somewhere else."""
        hub = self._hub()
        if hub is None:
            return
        text = self.listen_port.text().strip()
        if not text.isdigit() or not 1 <= int(text) <= 65535:
            self.serve_result.setText(t("A port is a number between 1 and 65535."))
            return
        wanted = int(text)
        self.serve_result.setText(t("Moving to {port}…", port=wanted))
        self.serve_result.repaint()
        try:
            said = hub.set_hub_port(wanted)
        except Exception as exc:
            self.serve_result.setText(t("The hub refused that: {why}", why=exc))
            return
        # It answers before it moves — the reply would otherwise go down with the socket it is
        # being written to — so this says asked and accepted, not done.
        self.serve_result.setText(t(
            "The hub is moving from {was} to {port}. This panel follows it, and the other "
            "clients were told first.",
            was=said.get("was", "?"), port=said.get("port", wanted)))

    # -- the two fields as one address -------------------------------------
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

    # -- asking, and committing -------------------------------------------
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

    # -- the panel's contract ---------------------------------------------
    def refresh(self):
        self.load_from(self.cfg() if callable(self.cfg) else None)

    def load_from(self, _cfg=None):
        host, port = self._split(local_mod.load().hub_url)
        for field, value in ((self.hub_url, host), (self.hub_port, port)):
            field.blockSignals(True)
            field.setText(value)
            field.blockSignals(False)
        self._describe_hub()
        shown = self._serves_here()
        self.serving.setVisible(shown)
        if shown and _cfg is not None:
            self.listen_port.setText(str(getattr(getattr(_cfg, "hub", None), "port", "")))
