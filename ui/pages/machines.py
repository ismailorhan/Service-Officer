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
from core import connectors, control

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
                control.resolve_address(machine.name,
                                        lambda *_a: self.address_found.emit())
        if 0 <= keep < self.list.count():
            self.list.setCurrentRow(keep)

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
        crumb.addWidget(_button("Machines", "quiet", self.back.emit))
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
        field("fingerprint", "Host key", self.fingerprint,
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
        self.setup_button = _button("Copy the setup commands", None, self._setup)
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
        for key in ("username", "auth", "key_path", "fingerprint"):
            self._hide_row(key, linux)
        self._hide_row("poll", not local)
        self.key_path.setEnabled(not linux or machine.auth == "key")
        self.setup_button.setVisible(linux)
        if local:
            self.result.setText("This computer is reached by being it — the "
                                "service manager is right here.")
            self.sudo_note.setText("")
        elif linux and machine.auth == "password":
            # Say so rather than failing later: the model has the field, but the
            # place to keep a password safely is not built yet, so a password
            # target cannot actually connect.
            self.sudo_note.setText(
                "Password sign-in is not wired up yet — there is nowhere to keep "
                "the password safely, so it is not kept. Use a key file for now.")
        elif linux:
            self.sudo_note.setText(
                "Reading a status needs no privilege. Starting and stopping needs "
                "sudo without a password, and instant updates need the account in "
                "the systemd-journal group — the button below writes out exactly "
                "what to run for the services you have chosen.")
        else:
            self.sudo_note.setText("")

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

    def _setup(self):
        """The exact commands this machine needs, for the services chosen.

        No unit name is hard-coded anywhere in the app: they come from what the
        user picked. This turns "which privilege do I grant?" from a support
        question into a copyable block.
        """
        machine = self.machine
        if machine is None:
            return
        page = self.parent()
        services = []
        while page is not None and not hasattr(page, "cfg"):
            page = page.parent()
        if page is not None:
            services = sorted(s.name for s in page.cfg().services
                              if (s.machine or "") == machine.name)
        account = machine.username or "svcofficer"
        verbs = ("start", "stop", "restart")
        if services:
            allowed = ", \\\n    ".join(
                f"/usr/bin/systemctl {verb} {unit}"
                for unit in services for verb in verbs)
        else:
            allowed = ("# add services for this machine on the Services page "
                       "first,\n    # and this list will name them")
        text = (
            f"# On {machine.where()}, as root:\n"
            f"usermod -aG systemd-journal {account}\n\n"
            f"# /etc/sudoers.d/service-officer — only these units, only these "
            f"verbs:\n"
            f"{account} ALL=(root) NOPASSWD: {allowed}\n")
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)
        self.result.setText(f"Copied. {len(services)} service(s) covered — paste "
                            f"it into a root shell on {machine.where()}.")
