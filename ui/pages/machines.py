"""Machines: the servers whose services this panel can reach.

A machine now carries *how* it is reached — the SCM for a Windows box, systemd
over SSH for a Linux one — so it has enough settings to need a page of its own
rather than a name in a list. Same shape as Services: a list, and a detail behind
each row.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QStackedWidget, QVBoxLayout,
                               QWidget)

from core import config as cfg_mod
from core import connectors, control, secrets

from .. import theme
from ..widgets import button as _button, label as _label
from .base import _ListRow, _Page, _spin


class MachinesPage(QWidget):
    """Every service belongs to a machine; this computer is always one of them."""

    changed = Signal()
    #: an address resolved on a worker thread; redraw on the GUI thread
    address_found = Signal()

    def __init__(self, cfg_ref):
        super().__init__()
        self.cfg = cfg_ref
        self.address_found.connect(self.refresh)

        self.stack = QStackedWidget()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)

        self.list_page = _Page(
            "Machines",
            "Where your services live. This computer is always here; add another "
            "and its services appear in the same panel. Open one to set how it is "
            "reached — Windows through its service manager, Linux over SSH.")
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self._open())
        self.list_page.root.addWidget(self.list, 1)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(_button("Add machine…", "primary", self._add))
        bar.addWidget(_button("Open", None, self._open))
        bar.addWidget(_button("Remove", "danger", self._remove))
        bar.addStretch(1)
        self.list_page.root.addSpacing(14)
        self.list_page.root.addLayout(bar)

        self.detail = MachineDetail()
        self.detail.back.connect(self._show_list)
        self.detail.changed.connect(self._refresh_and_signal)

        self.stack.addWidget(self.list_page)
        self.stack.addWidget(self.detail)
        self.refresh()

    # -- list --------------------------------------------------------------
    def refresh(self):
        keep = self.list.currentRow()
        self.list.clear()
        cfg = self.cfg()
        for machine in cfg.machines:
            count = sum(1 for s in cfg.services
                        if (s.machine or "") == machine.name)
            item = QListWidgetItem()
            widget = _ListRow(self._title(machine), self._summary(machine, count),
                              tag="This PC" if machine.is_local else "",
                              tag_category="running")
            item.setSizeHint(widget.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, widget)
            # DNS costs seconds on a name that doesn't resolve, so the address
            # arrives late and the row is redrawn then. Only ask when we don't
            # already know it — otherwise the redraw would ask again forever.
            if control.cached_address(machine.name) is None:
                control.resolve_address(machine.name, self._address_arrived)
        if 0 <= keep < self.list.count():
            self.list.setCurrentRow(keep)

    def _address_arrived(self, *_args):
        """A DNS lookup finished on a worker thread.

        Guarded because the page may be gone by then: closing the panel while a
        name is still resolving deleted the C++ object under this QObject, and the
        callback raised "Signal source has been deleted" in the worker — a
        traceback in the log for the ordinary act of closing a window.
        """
        try:
            self.address_found.emit()
        except RuntimeError:
            pass

    def _title(self, machine) -> str:
        """CTL052 (10.77.3.50) — the name alone isn't enough when someone has to
        RDP to the box, and an IP alone isn't enough to know which box it is."""
        name = control.host_name() if machine.is_local else machine.name
        name = name or machine.display()
        address = machine.address or control.cached_address(machine.name)
        if address and address != name:
            return f"{name}  ({address})"
        if machine.label and machine.label != name:
            return f"{name}  ·  {machine.label}"
        return name

    def _summary(self, machine, count: int) -> str:
        """What it is and how it is reached, in one line."""
        parts = [f"{count} service{'s' if count != 1 else ''}"]
        if machine.is_local:
            parts.append("Windows service manager")
        elif machine.is_linux:
            who = machine.username or "no account set"
            parts.append(f"systemd over SSH · {who}")
            if not machine.host_fingerprint:
                parts.append("host key not confirmed")
        else:
            parts.append("Windows service manager")
        return "  ·  ".join(parts)

    def _selected(self):
        row = self.list.currentRow()
        machines = self.cfg().machines
        return machines[row] if 0 <= row < len(machines) else None

    def _open(self):
        machine = self._selected()
        if machine is None:
            return
        self.detail.load(machine)
        self.stack.setCurrentWidget(self.detail)

    def _show_list(self):
        self.stack.setCurrentWidget(self.list_page)
        self.refresh()

    def _refresh_and_signal(self):
        # A machine's transport may have changed, so anything holding a connection
        # to it has to be let go of.
        connectors.forget()
        self.refresh()
        self.changed.emit()

    def _add(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Add machine",
                                        "Computer name or host name:")
        name = (name or "").strip().lstrip("\\")
        if not ok or not name:
            return
        if self.cfg().machine(name):
            QMessageBox.information(self, "Service Officer",
                                    "That machine is already listed.")
            return
        # No reachability check here any more: we do not yet know whether this is
        # a Windows box or a Linux one, and probing the wrong way would report a
        # perfectly good machine as unreachable. That is what the detail page's
        # Test button is for, once the transport is set.
        self.cfg().machines.append(cfg_mod.Machine(name=name, label=name))
        self.refresh()
        self.list.setCurrentRow(self.list.count() - 1)
        self.changed.emit()
        self._open()

    def _remove(self):
        machine = self._selected()
        if machine is None:
            return
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
        self.cfg().machines.remove(machine)
        connectors.forget(machine.name)
        self.refresh()
        self.changed.emit()


class _SecretEdit(QLineEdit):
    """A password field that looks filled when a password is stored.

    Clearing it after saving was technically tidy and read as "it was lost", which
    is worse: nobody can tell a saved password from a forgotten one. So it shows a
    row of dots — placeholder characters, never the real value, which is not held
    anywhere in the UI — and empties itself the moment you start typing a new one.
    """

    STAND_IN = "**********"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEchoMode(QLineEdit.Password)
        self._standing_in = False

    def show_stored(self, stored: bool) -> None:
        self._standing_in = stored
        super().setText(self.STAND_IN if stored else "")

    def typed(self) -> str:
        """What the user actually entered, or "" if these are only the dots."""
        return "" if self._standing_in else self.text()

    def focusInEvent(self, event):
        # Editing must start from nothing, or a new password would be appended to
        # ten asterisks that were never a password.
        if self._standing_in:
            self._standing_in = False
            self.clear()
        super().focusInEvent(event)


class MachineDetail(_Page):
    """One machine: what it is called, and how to reach it."""

    back = Signal()
    changed = Signal()

    KINDS = (("Windows — service manager", "windows"),
             ("Linux — systemd over SSH", "linux"))
    AUTHS = (("Private key file", "key"),
             ("Password (stored on this machine)", "password"))

    def __init__(self):
        super().__init__("", "", scroll=True)
        self.machine = None

        crumb = QHBoxLayout()
        crumb.setSpacing(6)
        crumb.addWidget(_button(f"{theme.GLYPH_BACK}  Machines", "quiet", self.back.emit))
        crumb.addWidget(_label(theme.GLYPH_CRUMB, "hint"))
        self.crumb_name = _label("", "strong")
        crumb.addWidget(self.crumb_name)
        crumb.addStretch(1)
        self.root.insertLayout(0, crumb)
        self.root.insertSpacing(1, 10)

        # A form, not sentences: eight settings with a ragged left edge are
        # harder to scan than a column of labels, and this is the one page where
        # every row is a different kind of thing. Labels left, values right, and
        # the ones whose meaning is not obvious carry a note beside them.
        form = QGridLayout()
        form.setHorizontalSpacing(theme.SP_12)
        form.setVerticalSpacing(theme.SP_10)
        form.setColumnMinimumWidth(0, 108)
        form.setColumnStretch(1, 1)
        self.form = form
        self._rows: dict = {}

        def field(key: str, text: str, widget, note: str = ""):
            """One row: label, the thing, and why it is there."""
            row = form.rowCount()
            caption = _label(text, "hint")
            caption.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            form.addWidget(caption, row, 0)
            if isinstance(widget, QWidget):
                form.addWidget(widget, row, 1)
            else:
                form.addLayout(widget, row, 1)
            hint = _label(note, "hint", wrap=True) if note else None
            if hint is not None:
                form.addWidget(hint, row, 2)
            self._rows[key] = [w for w in (caption, widget, hint) if w is not None]
            return row

        def hide(key: str, shown: bool):
            for part in self._rows.get(key, ()):
                if isinstance(part, QWidget):
                    part.setVisible(shown)
                else:                          # a layout of widgets
                    for i in range(part.count()):
                        item = part.itemAt(i).widget()
                        if item is not None:
                            item.setVisible(shown)

        self._hide_row = hide

        self.label = QLineEdit()
        self.label.setPlaceholderText("What you call this machine")
        self.label.editingFinished.connect(self._save)
        field("label", "Called", self.label,
              "Your name for it. Shown instead of the host name.")

        self.kind = QComboBox()
        for text, _value in self.KINDS:
            self.kind.addItem(text)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        field("kind", "Reached as", self.kind,
              "Everything else here follows from this.")

        self.address = QLineEdit()
        self.address.setPlaceholderText("host name or IP — blank uses the name")
        self.address.editingFinished.connect(self._save)
        where = QHBoxLayout()
        where.setSpacing(theme.SP_8)
        where.addWidget(self.address, 1)
        where.addWidget(_label("port", "hint"))
        self.port = _spin(0, 0, 65535, width=72)
        self.port.valueChanged.connect(lambda _v: self._save())
        where.addWidget(self.port)
        field("address", "Address", where,
              "Port 0 means the usual one — 22 for SSH.")

        self.username = QLineEdit()
        self.username.setPlaceholderText("account on that machine")
        self.username.editingFinished.connect(self._save)
        field("username", "Account", self.username,
              "Who we log in as. Reading needs no privilege;\n"
              "acting needs sudo without a password.")

        self.auth = QComboBox()
        for text, _value in self.AUTHS:
            self.auth.addItem(text)
        self.auth.currentIndexChanged.connect(lambda _i: self._save())
        field("auth", "Sign in with", self.auth,
              "A key is preferred: no secret to keep.")

        # A password is never read back out of the store to show it. The field
        # holds what you are typing now; once saved it says so and goes blank.
        self.password = _SecretEdit()
        self.password.setPlaceholderText("type to set a password")
        self.password.editingFinished.connect(self._save_password)
        pw = QHBoxLayout()
        pw.setSpacing(theme.SP_8)
        pw.addWidget(self.password, 1)
        self.password_state = _label("", "hint")
        pw.addWidget(self.password_state)
        pw.addWidget(_button("Forget", "quiet", self._forget_password))
        field("password", "Password", pw,
              "Kept encrypted on this computer, not in\n"
              "services.json. Any administrator here can read it.")

        self.key_path = QLineEdit()
        self.key_path.setPlaceholderText(r"C:\Users\you\.ssh\id_ed25519")
        self.key_path.editingFinished.connect(self._save)
        keys = QHBoxLayout()
        keys.setSpacing(theme.SP_8)
        keys.addWidget(self.key_path, 1)
        keys.addWidget(_button("Browse…", "quiet", self._browse))
        field("key_path", "Key file", keys,
              "The private key here. Its public half goes\n"
              "in that account's ~/.ssh/authorized_keys.")

        # The fingerprint is the whole of SSH's security on a first connection, so
        # it is a field a person fills in deliberately — never something the app
        # accepts because it was offered.
        self.fingerprint = QLineEdit()
        self.fingerprint.setPlaceholderText("SHA256:… — confirm it on the machine")
        self.fingerprint.editingFinished.connect(self._save)
        keyrow = QHBoxLayout()
        keyrow.setSpacing(theme.SP_8)
        keyrow.addWidget(self.fingerprint, 1)
        keyrow.addWidget(_button("Get it", "quiet", self._fetch_fingerprint))
        field("fingerprint", "Host key", keyrow,
              "Proves the machine is the one you meant.\n"
              "A different key later is refused.")

        self.poll = _spin(5, 2, 300, width=64)
        self.poll.valueChanged.connect(lambda _v: self._save())
        seconds = QHBoxLayout()
        seconds.setSpacing(theme.SP_8)
        seconds.addWidget(self.poll)
        seconds.addWidget(_label("seconds", "hint"))
        seconds.addStretch(1)
        field("poll", "Check every", seconds,
              "A remote machine cannot tell us on its own.\n"
              "With journal access this is only a safety net.")

        self.root.addLayout(form)
        self.root.addSpacing(theme.SP_12)
        self.sudo_note = _label("", "hint", wrap=True)
        self.root.addWidget(self.sudo_note)
        self.root.addSpacing(theme.SP_12)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(_button("Test connection", "primary", self._test))
        # Named for what it hands you, since it was asked what it meant: the
        # commands to run *on that machine* so this account may control services.
        self.setup_button = _button("Copy what to run on that machine", None,
                                    self._setup)
        bar.addWidget(self.setup_button)
        bar.addStretch(1)
        self.root.addLayout(bar)
        self.root.addSpacing(10)

        self.result = _label("", "hint", wrap=True)
        self.root.addWidget(self.result)
        self.root.addStretch(1)

    # -- loading and saving ------------------------------------------------
    def load(self, machine):
        self.machine = None            # so setting the widgets doesn't save
        self.crumb_name.setText(machine.display())
        self.label.setText(machine.label)
        self.kind.setCurrentIndex(
            next((i for i, (_t, v) in enumerate(self.KINDS)
                  if v == machine.kind), 0))
        self.address.setText(machine.address)
        self.port.setValue(machine.port)
        self.username.setText(machine.username)
        self.auth.setCurrentIndex(
            next((i for i, (_t, v) in enumerate(self.AUTHS)
                  if v == machine.auth), 0))
        self.key_path.setText(machine.key_path)
        self.fingerprint.setText(machine.host_fingerprint)
        for field in (self.label, self.address, self.username, self.key_path,
                      self.fingerprint):
            field.setCursorPosition(0)
        self.poll.setValue(machine.poll_seconds)
        held = secrets.has(machine.secret_ref)
        self.password.show_stored(held)
        self.password_state.setText("saved on this computer" if held else "not set")
        self.result.setText("")
        self.machine = machine
        self._apply_visibility()

    def _save(self):
        machine = self.machine
        if machine is None:
            return
        machine.label = self.label.text().strip() or machine.name
        machine.kind = self.KINDS[self.kind.currentIndex()][1]
        machine.address = self.address.text().strip()
        machine.port = int(self.port.value())
        machine.auth = (self.AUTHS[self.auth.currentIndex()][1]
                        if machine.kind == "linux" else "current_user")
        machine.username = self.username.text().strip()
        machine.key_path = self.key_path.text().strip()
        machine.host_fingerprint = self.fingerprint.text().strip()
        machine.poll_seconds = int(self.poll.value())
        self.crumb_name.setText(machine.display())
        self._apply_visibility()
        self.changed.emit()

    def _kind_changed(self, _index):
        self._save()

    def _apply_visibility(self):
        """This computer has nothing to configure — it is reached by being it."""
        machine = self.machine
        local = machine is None or machine.is_local
        linux = bool(machine and machine.is_linux)
        self.kind.setEnabled(not local)
        for key in ("address",):
            self._hide_row(key, not local)
        for key in ("username", "auth", "fingerprint"):
            self._hide_row(key, linux)
        by_password = linux and machine.auth == "password"
        self._hide_row("password", by_password)
        self._hide_row("key_path", linux and not by_password)
        self._hide_row("poll", not local)
        # Nothing to set up as root, so the button is not offered — a button whose
        # only answer is "nothing to do" is a question the user has to ask first.
        self.setup_button.setVisible(linux and machine.username != "root")
        if by_password:
            held = secrets.has(machine.secret_ref)
            self.password.show_stored(held)
            self.password_state.setText(
                "saved on this computer" if held else "not set")
        if local:
            self.result.setText("This computer is reached by being it — the "
                                "service manager is right here.")
            self.sudo_note.setText("")
        elif by_password and machine.username == "root":
            # Root needs no sudo and no group: that is the entire reason sudo
            # exists. Worth saying, because the alternative is someone following
            # setup steps they do not need.
            self.sudo_note.setText(
                "Signing in as root needs nothing set up on that machine — no "
                "sudo rule, no group. The cost is that a root password is stored "
                "on this computer, where any administrator can read it.")
        elif linux:
            self.sudo_note.setText(
                "Reading a status needs no privilege. Starting and stopping needs "
                "sudo without a password, and instant updates need the account in "
                "the systemd-journal group — the button below writes out exactly "
                "what to run for the services you have chosen.")
        else:
            self.sudo_note.setText("")

    def _save_password(self):
        """Store what was typed, then clear the field.

        The value is written to the secret store and the config only ever holds
        the *name* of the entry. Leaving the typed password in a widget would put
        it in a screenshot, a crash dump, and anything that walks the widget tree.
        """
        machine = self.machine
        typed = self.password.typed()
        if machine is None or not typed:
            return
        machine.secret_ref = secrets.ref_for_machine(machine.name)
        stored = secrets.put(machine.secret_ref, typed)
        # Dots, not the value: it is set and it looks set.
        self.password.show_stored(stored)
        if stored:
            self.password_state.setText("saved on this computer")
            self.result.setText("")
        else:
            self.password_state.setText("not saved")
            self.result.setText(secrets.last_error()
                                or "The password could not be stored. Is Service "
                                   "Officer running as administrator?")
        self.changed.emit()

    def _forget_password(self):
        machine = self.machine
        if machine is None:
            return
        secrets.forget(machine.secret_ref or secrets.ref_for_machine(machine.name))
        self.password.show_stored(False)
        self.password_state.setText("not set")
        self.result.setText("Forgotten. That machine cannot be reached with a "
                            "password until a new one is set.")
        self.changed.emit()

    def _fetch_fingerprint(self):
        """Ask the machine for its host key and fill the field in.

        The same thing `ssh` does on a first connection, and with the same caveat,
        which is said out loud rather than glossed over: a key read over the
        network is what an attacker in the middle would also hand us. It saves
        running ssh-keygen on the box; it does not replace checking it there when
        the network between you and the machine is not trusted.

        No credentials are used — a server offers its key before authentication.
        """
        machine = self.machine
        if machine is None or not machine.is_linux:
            return
        host = machine.address or machine.name
        try:
            from core import ssh_linux
            found = ssh_linux.fingerprint_of(host, machine.port or 22)
        except Exception as exc:
            self.result.setText(f"{host} did not offer a key — "
                                f"{type(exc).__name__}: {exc}")
            return
        if not found:
            self.result.setText(f"{host} did not offer a key.")
            return
        was = self.fingerprint.text().strip()
        self.fingerprint.setText(found)
        self.fingerprint.setCursorPosition(0)
        self._save()
        if was and was != found:
            # Worth shouting about: either the machine was rebuilt, or this is not
            # the same machine.
            self.result.setText(
                f"The key changed. It was {was} and is now {found}. Either that "
                f"machine was rebuilt, or it is not the same machine — check "
                f"before you keep this.")
        else:
            self.result.setText(
                f"{host} offers {found}. Read over the network, so it proves "
                f"nothing on its own: confirm it on the machine with "
                f"ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub if the network "
                f"between here and there is not trusted.")

    def _browse(self):
        from PySide6.QtWidgets import QFileDialog
        found, _filter = QFileDialog.getOpenFileName(self, "Private key file")
        if found:
            self.key_path.setText(found)
            self._save()

    # -- helping the user get it working -----------------------------------
    def _test(self):
        """Say what happened, in the words the transport used.

        A test that only says "failed" moves the problem rather than solving it:
        "no authentication methods available" and "host key not confirmed" need
        entirely different fixes.
        """
        machine = self.machine
        if machine is None:
            return
        if machine.is_local:
            self.result.setText("This computer answers — it is the one running "
                                "Service Officer.")
            return
        connectors.forget(machine.name)
        conn = connectors.for_machine(machine.name, record=machine)
        try:
            if not conn.reachable():
                self.result.setText(f"{machine.where()} did not answer.")
                return
            can = conn.abilities()
        except Exception as exc:
            self.result.setText(f"{type(exc).__name__}: {exc}")
            return
        said = [f"{machine.where()} answered."]
        said.append("Services can be started and stopped." if can.control
                    else "Watching only — no control.")
        said.append("Changes arrive as they happen." if can.push
                    else f"Status is asked for every {machine.poll_seconds}s.")
        if can.why:
            said.append(can.why)
        self.result.setText("  ".join(said))

    def _services_here(self) -> list:
        """The units chosen for this machine. Walked up to the page that holds the
        config, because the detail deliberately knows only its own machine."""
        page = self.parent()
        while page is not None and not hasattr(page, "cfg"):
            page = page.parent()
        if page is None:
            return []
        return sorted(s.name for s in page.cfg().services
                      if (s.machine or "") == self.machine.name)

    def _setup(self):
        """One block that can be pasted and will run. That is the whole
        requirement, and the first version failed it.

        It mixed commands to type with the *contents of a file*, so pasting it put
        a sudoers line into a shell and got "syntax error near unexpected token".
        A block that looks like a script has to be a script: the file is written
        with a heredoc, `usermod` is called by full path because it lives in
        /usr/sbin and is not on a normal PATH, and the result is checked with
        `visudo -c` — a malformed sudoers file can take sudo away from everyone on
        the machine, so it must never be left unverified.

        No unit name is hard-coded in the app: they come from what the user picked.
        """
        machine = self.machine
        if machine is None:
            return
        from PySide6.QtWidgets import QApplication

        account = machine.username or "svcofficer"
        if account == "root":
            # Nothing to grant. Saying so is the useful answer; producing a
            # sudoers file for root would be theatre.
            QApplication.clipboard().setText(
                f"# Nothing to set up on {machine.where()}: root already has\n"
                f"# every privilege this app needs, and reads the journal.\n")
            self.result.setText("Signing in as root needs no setup on that "
                                "machine — nothing to run.")
            return

        services = self._services_here()
        lines = [f"# Run this on {machine.where()} as root.",
                 f"# If you are not root: sudo -i   (or su -)",
                 "",
                 f"/usr/sbin/usermod -aG systemd-journal {account}"]
        if services:
            verbs = ("start", "stop", "restart")
            allowed = ", \\\n    ".join(f"/usr/bin/systemctl {verb} {unit}"
                                        for unit in services for verb in verbs)
            lines += [
                "",
                "cat > /etc/sudoers.d/service-officer <<'EOF'",
                f"{account} ALL=(root) NOPASSWD: {allowed}",
                "EOF",
                "chmod 0440 /etc/sudoers.d/service-officer",
                "# Refuse a broken file rather than discovering sudo is gone:",
                "visudo -cf /etc/sudoers.d/service-officer",
            ]
        else:
            lines += ["",
                      "# Choose this machine's services on the Services page and",
                      "# copy this again — the sudo rule will then name them, and",
                      "# grant nothing beyond them."]
        QApplication.clipboard().setText("\n".join(lines) + "\n")
        self.result.setText(
            f"Copied — paste the whole block into a root shell on "
            f"{machine.where()}. {len(services)} service(s) covered."
            if services else
            "Copied. It only adds the journal group so far: choose this machine's "
            "services first and copy again to include the sudo rule.")
