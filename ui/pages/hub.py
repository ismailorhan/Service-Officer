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

import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QHBoxLayout, QLineEdit, QMessageBox, QVBoxLayout,
                               QWidget)

from core import local as local_mod
from core.i18n import t
from core import version

from .. import theme
from ..widgets import button as _button, label as _label
from .base import LABEL_WIDTH, _Page, _fields


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
        #
        # A row each, in the shared grid rather than side by side in one sentence. Side by side
        # they put Port's label in the middle of the line, so nothing on this page lined up with
        # anything else on it — and the one note had to explain both fields at once. Now every
        # label on the page starts at the same place and every dot explains one thing.
        self.hub_url = QLineEdit()
        self.hub_url.setPlaceholderText("empty — this computer does the work itself")
        self.hub_url.setMinimumWidth(220)
        self.hub_port = QLineEdit()
        self.hub_port.setFixedWidth(64)
        self.hub_port.setPlaceholderText("8797")
        self.root.addWidget(_fields(
            (t("Host"), self.hub_url,
             t("A host name or IP. Leave it empty to watch this computer's own services "
               "instead — that is what a single-machine install is.")),
            (t("Port"), self.hub_port,
             t("Empty means 8797.\n\n"
               "This is not the port a hub on this computer listens on — that one is under "
               "SERVING below.")),
            fill=True))
        self.root.addSpacing(20)

        self.root.addWidget(_label("TOKEN", "section"))
        self.root.addSpacing(9)
        self.hub_token = QLineEdit()
        self.hub_token.setEchoMode(QLineEdit.Password)
        self.hub_token.setMinimumWidth(260)
        self.root.addWidget(_fields(
            (t("Token"), self.hub_token,
             t("What proves this computer may read that hub. Shown once, when the hub "
               "issues it, and stored on this computer afterwards.")),
            fill=True))
        self.hub_token_hint = _label("", "hint", wrap=True)
        # Indented to the value column: it is about the field above it, not about the section.
        self.hub_token_hint.setContentsMargins(LABEL_WIDTH + theme.SP_12, 4, 0, 0)
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
        # "This computer serves clients on port" as a label is wider than the label column, so
        # it would have pushed its own field out of line with every other field on the page —
        # the thing this change is for. Under a heading that already says SERVING, "Listens on"
        # says it, and the dot carries the rest.
        serving.addWidget(_fields(
            (t("Listens on"), self.listen_port,
             t("The port the Hub service listens on — not the one above, which is where "
               "this panel reads from. Applying it moves the socket and the firewall "
               "rule, and every client is told the new number first so they follow "
               "rather than losing the hub.\n"
               "ServiceOfficerHub.exe port <n> does the same from a command line, which "
               "is the way in when the hub cannot be reached at all.")),
            # Filling like the rows above it, even though a port box is narrow: measured
            # without it, this one dot sat at x=240 while the other three were at 884. Two
            # rules on one page is the raggedness again, one row of it.
            fill=True))
        serving.addSpacing(10)
        moving = QHBoxLayout()
        moving.addWidget(_button(t("Move to this port"), "primary", self._move_port))
        moving.addStretch(1)
        serving.addLayout(moving)
        self.serve_result = _label("", "hint", wrap=True)
        serving.addWidget(self.serve_result)

        # -- the release this hub is on ----------------------------------------
        # Inside `serving`, so it appears exactly where the port does: on the hub's own
        # machine. A workstation cannot install a hub's update — the hub is the elevated,
        # always-running piece and the client is deliberately not — so offering the button
        # there would be offering something that cannot work.
        serving.addSpacing(24)
        serving.addWidget(_label(t("UPDATE"), "section"))
        serving.addSpacing(9)
        self.update_state = _label("", "hint", wrap=True)
        serving.addWidget(self.update_state)
        serving.addSpacing(10)
        updating = QHBoxLayout()
        self.update_button = _button(t("Install it"), "primary", self._install_update)
        updating.addWidget(self.update_button)
        updating.addWidget(_button(t("Check now"), "quiet", self._check_update))
        updating.addStretch(1)
        serving.addLayout(updating)
        self.update_result = _label("", "hint", wrap=True)
        serving.addWidget(self.update_result)

        self.root.addWidget(self.serving)

        # -- catching this computer up -----------------------------------------
        # The other side of the same coin, and it appears where SERVING does not: on a
        # workstation. The hub updates first — that is the forced order, because a new client
        # against an old hub is the mismatch the wrong way round — and then this computer is
        # the one that is behind.
        self.catching_up = QWidget()
        catch = QVBoxLayout(self.catching_up)
        catch.setContentsMargins(0, 22, 0, 0)
        catch.setSpacing(0)
        catch.addWidget(_label(t("UPDATE"), "section"))
        catch.addSpacing(9)
        self.catch_state = _label("", "hint", wrap=True)
        catch.addWidget(self.catch_state)
        catch.addSpacing(10)
        catching = QHBoxLayout()
        self.catch_button = _button(t("Update this computer"), "primary",
                                    self._catch_up)
        catching.addWidget(self.catch_button)
        catching.addStretch(1)
        catch.addLayout(catching)
        self.catch_result = _label("", "hint", wrap=True)
        catch.addWidget(self.catch_result)
        self.root.addWidget(self.catching_up)
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

    # -- the release this hub is on ----------------------------------------
    def _describe_update(self) -> None:
        """What the hub's own daily check found. One read, answered from its memory."""
        hub = self._hub()
        self.update_button.setEnabled(False)
        if hub is None:
            self.update_state.setText(t("This computer watches its own services."))
            return
        try:
            said = hub.update_state()
        except Exception as exc:
            self.update_state.setText(t("Could not ask the hub: {why}", why=exc))
            return
        running, offered = said.get("running", "?"), said.get("available") or ""
        if not offered:
            trouble = said.get("trouble") or ""
            self.update_state.setText(
                t("Running {version} — the newest there is.", version=running) if not trouble
                # "Nothing new" and "could not ask" are different facts, and a hub that has
                # not reached the feed for a week should not read as up to date.
                else t("Running {version}. The last check did not get through: {why}",
                       version=running, why=trouble))
            return
        busy = said.get("busy") or ""
        notes = (said.get("notes") or "").strip()
        text = t("{offered} is available — this hub is on {running}.",
                 offered=offered, running=running)
        if notes:
            text += "\n" + notes
        if busy:
            # Named, not merely disabled: a button that is off for no visible reason reads as
            # a bug in the button.
            text += "\n" + t("Not now — {why}. It will wait.", why=busy)
        self.update_state.setText(text)
        self.update_button.setEnabled(not busy)

    def _check_update(self) -> None:
        """Ask the feed now rather than waiting for the daily check.

        On the hub's own thread, not this one: the panel must not sit on a network call, and
        the hub is where the answer is kept anyway.
        """
        hub = self._hub()
        if hub is None:
            return
        self.update_result.setText(t("Asking…"))
        self.update_result.repaint()
        engine = getattr(hub, "engine", None)
        watcher = getattr(hub, "updates", None) or (
            getattr(engine, "updates", None) if engine is not None else None)
        if watcher is not None:                    # the hub is in this process
            threading.Thread(target=self._checked, args=(watcher,), daemon=True,
                             name="update-check-now").start()
            return
        # A client of a hub in another process: it checks daily and this panel reads what it
        # found. Asking again from here would be asking the wrong computer.
        self.update_result.setText("")
        self._describe_update()

    def _checked(self, watcher) -> None:
        watcher.check_now()
        self.update_result.setText("")
        self._describe_update()

    def _install_update(self) -> None:
        """Hand it to the hub. The hub verifies the download and stops itself."""
        hub = self._hub()
        if hub is None:
            return
        self.update_result.setText(t("Downloading and checking it…"))
        self.update_result.repaint()
        try:
            said = hub.install_update()
        except Exception as exc:
            self.update_result.setText(t("It was not installed: {why}", why=exc))
            return
        self.update_result.setText(t(
            "Installing {version}. This hub stops for a moment, so every panel shows "
            "disconnected until it is back — including this one.",
            version=said.get("version", "")))

    # -- catching this computer up -----------------------------------------
    def _describe_catch_up(self) -> None:
        """Whether this computer is behind its hub, and whether there is a way to fix it here.

        Asked of the hub even when this client is refusing to connect: the mismatch is exactly
        the state that needs a way out, and `_ask` deliberately has no version guard on it.
        """
        hub = self._hub()
        self.catch_button.setEnabled(False)
        if hub is None:
            self.catching_up.setVisible(False)
            return
        try:
            said = hub.update_state()
        except Exception as exc:
            self.catching_up.setVisible(True)
            self.catch_state.setText(t("Could not ask the hub: {why}", why=exc))
            return
        theirs = said.get("running") or ""
        if version.compatible(theirs):
            # Nothing to catch up on. The section goes away rather than saying "you are fine":
            # a page that reports non-problems is a page nobody reads.
            self.catching_up.setVisible(False)
            return
        self.catching_up.setVisible(True)
        offered = said.get("installer") or None
        text = t("This computer is running {mine} and the hub is running {theirs}. A client "
                 "and its hub have to be the same release.",
                 mine=version.short(), theirs=theirs)
        if offered:
            text += "\n" + t("The hub can hand this computer the installer, so no internet "
                             "is needed here.")
            self.catch_button.setEnabled(True)
        else:
            # Honest about the dead end rather than offering a button that cannot work.
            text += "\n" + t("The hub does not have the installer for its own release, so it "
                             "has to be fetched from the release page and run here.")
        self.catch_state.setText(text)

    def _catch_up(self) -> None:
        """Fetch the hub's installer, prove it, and hand it to Windows.

        Windows, not this process: a client panel is deliberately not elevated, and starting an
        installer that requires administrator from an unelevated process fails outright rather
        than prompting. ShellExecute is what puts the consent prompt on screen.
        """
        hub = self._hub()
        if hub is None:
            return
        self.catch_button.setEnabled(False)
        self.catch_result.setText(t("Downloading from the hub and checking it…"))
        self.catch_result.repaint()
        threading.Thread(target=self._catch_up_now, args=(hub,), daemon=True,
                         name="client-update").start()

    def _catch_up_now(self, hub) -> None:
        """Off the drawing thread: this is 37 MB over the customer's network."""
        from core import updates
        try:
            said = hub.update_state()
            offered = said.get("installer") or {}
            path = hub.fetch_installer(offered.get("sha256", ""))
        except Exception as exc:
            self.catch_result.setText(t("It was not installed: {why}", why=exc))
            self.catch_button.setEnabled(True)
            return
        try:
            updates.install(path, how=updates.ASK_THE_PERSON)
        except Exception as exc:
            self.catch_result.setText(t("It was not installed: {why}", why=exc))
            self.catch_button.setEnabled(True)
            return
        self.catch_result.setText(t(
            "Installing. This app closes and reopens on the new release."))

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
        note = (f"{answer.get('name', '?')} answered — version {theirs}")
        # The same rule the client's own handshake uses, from the same function. Spelled out
        # here it was free to drift from the one that decides whether the panel connects at
        # all, and a Test that says "these match" beside a panel that refuses to is worse
        # than either answer on its own.
        if not version.compatible(theirs):
            note += (f", which is not this panel's {version.short()}. A client and its hub "
                     "have to be the same release; update one of them.")
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
        if shown:
            self._describe_update()
        else:
            # The two are exclusive by construction: on the hub's own machine the question is
            # "is there a newer release", and on a workstation it is "am I behind my hub".
            self._describe_catch_up()
        if shown and _cfg is not None:
            self.listen_port.setText(str(getattr(getattr(_cfg, "hub", None), "port", "")))
