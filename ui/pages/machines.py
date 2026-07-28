"""Machines: the servers whose services this panel can reach.

A machine now carries *how* it is reached — the SCM for a Windows box, systemd
over SSH for a Linux one — so it has enough settings to need a page of its own
rather than a name in a list. Same shape as Services: a list, and a detail behind
each row.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout,
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

    def __init__(self, cfg_ref, store=None, hub=None):
        super().__init__()
        self.cfg = cfg_ref
        self.store = store
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

        self.detail = MachineDetail(hub)
        self.detail.back.connect(self._show_list)
        self.detail.changed.connect(self._refresh_and_signal)

        self.stack.addWidget(self.list_page)
        self.stack.addWidget(self.detail)

        # "answered 3s ago" has to keep being true, so the list re-reads the store
        # while it is on screen. Four rows, so this is cheap; stopped when it is not
        # visible, so it is not paid at all the rest of the time.
        self._tick = QTimer(self)
        self._tick.setInterval(3000)
        self._tick.timeout.connect(self._retick)
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self._tick.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._tick.stop()

    def _retick(self):
        if self.stack.currentWidget() is self.list_page and self.isVisible():
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
            reach, tag, tag_kind, why = self._reachability(machine)
            widget = _ListRow(self._title(machine),
                              self._summary(machine, count, reach),
                              tag=tag, tag_category=tag_kind)
            if why:
                widget.setToolTip(why)
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
        RDP to the box, and an IP alone isn't enough to know which box it is.

        What is shown is what the user called it. This used to show `machine.name`,
        which is not a name anybody chose to see: it is the key the services point
        at, fixed when the machine was added. So a machine added as "sd" and then
        called "hanadev" went on reading "sd (hanadev)", and the Called field looked
        like it did nothing.
        """
        name = (control.host_name() if machine.is_local else "") or machine.display()
        address = machine.address or control.cached_address(machine.name)
        if address and address != name:
            return f"{name}  ({address})"
        return name

    def _reachability(self, machine) -> tuple:
        """(what to add to the summary, chip text, chip category, the full reason).

        The reason is kept out of the summary on purpose. Put there, a transport's own
        words — "pywintypes.error: (1722, 'OpenSCManager', 'The RPC server is
        unavailable.')" — made the row wider than the window, pushed every chip out of
        sight and grew a horizontal scrollbar. It goes in the row's tooltip, where it
        is one hover away and costs the layout nothing.

        This is here because it was missing and cost an evening: every service on the
        SUSE machine read "Unknown" and there was no way to see that the machine had
        never been asked at all — the transport was claiming it would push changes,
        so the poller left it alone. "Unknown" on a service has several explanations;
        "never asked" on its machine has one.
        """
        if machine.is_local:
            return "", "This PC", "running", ""
        known = self.store.machine_state(machine.name) if self.store else {}
        if not known:
            return ("not asked yet", "waiting", "none",
                    "Nothing has asked this machine anything yet. It is asked "
                    f"every {machine.poll_seconds}s once the app is watching a "
                    "service on it.")
        ago = max(0.0, time.monotonic() - known.get("at", 0.0))
        when = ("just now" if ago < 2 else
                f"{ago:.0f}s ago" if ago < 60 else
                f"{ago / 60:.0f} min ago" if ago < 3600 else
                f"{ago / 3600:.1f} h ago")
        why = (known.get("detail") or "").strip()
        if known.get("reachable"):
            return f"answered {when}", "connected", "running", ""
        return (f"no answer, last tried {when}", "no answer", "stopped",
                why or "It did not answer, and said nothing about why.")

    def _summary(self, machine, count: int, reach: str = "") -> str:
        """What it is and how it is reached, in one line."""
        parts = [f"{count} service{'s' if count != 1 else ''}"]
        if machine.is_local:
            parts.append("Windows service manager")
        elif machine.is_linux:
            who = machine.username or "no account set"
            parts.append(f"systemd over SSH · {who}")
            if not machine.host_fingerprint:
                parts.append("host key not confirmed")
        elif machine.auth == "password":
            parts.append("Windows service manager · "
                         f"{machine.username or 'no account set'}")
        else:
            parts.append("Windows service manager · this computer's account")
        if reach:
            parts.append(reach)
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
        # What is typed here becomes the machine's id as well as its first
        # description and, until a Host is set, the name we connect to — which is
        # why it asks for the host name. The id itself is never shown again: it is
        # plumbing, and the one field on a machine that cannot be edited later.
        name, ok = QInputDialog.getText(
            self, "Add machine",
            "Host name or IP of the machine.\n"
            "Its description, account and the rest are on the next page.")
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
    #: carries a test's outcome from its worker thread to the label
    tested = Signal(str)
    #: Test connection worked out whether WinRM is usable, on its own thread. The box and
    #: the save belong on Qt's.
    winrm_found = Signal()

    KINDS = (("Windows — service manager", "windows"),
             ("Linux — systemd over SSH", "linux"))
    #: How to sign in, per target type. Different lists because they share nothing:
    #: a Windows machine can be reached as whoever is signed in here, which needs
    #: no account and no secret, and SSH has no equivalent of that.
    AUTHS_LINUX = (("Private key file", "key"),
                   ("Password (stored on this machine)", "password"))
    AUTHS_WINDOWS = (("This computer's signed-in account", "current_user"),
                     ("User name and password", "password"))
    AUTHS = AUTHS_LINUX          # kept as a name; the list in use follows the kind

    def __init__(self, hub=None):
        super().__init__("", "", scroll=True)
        self.machine = None
        #: Set when this panel talks to a hub rather than watching by itself. Only used to
        #: say whose reach a connection test proved — see _run_test.
        self._hub = hub

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
        #: the note beside a field, kept so it can be rewritten: what "User" means
        #: on a Windows machine and on a Linux one are different sentences.
        self._hints: dict = {}

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
                self._hints[key] = hint
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
        self.label.setPlaceholderText("what this machine is")
        self.label.editingFinished.connect(self._save)
        field("label", "Description", self.label,
              "Shown everywhere this machine appears.")

        self.kind = QComboBox()
        for text, _value in self.KINDS:
            self.kind.addItem(text)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        field("kind", "Target type", self.kind,
              "Everything else here follows from this.")

        self.address = QLineEdit()
        self.address.setPlaceholderText("host name or IP address")
        self.address.editingFinished.connect(self._save)
        where = QHBoxLayout()
        where.setSpacing(theme.SP_8)
        where.addWidget(self.address, 1)
        where.addWidget(_label("port", "hint"))
        self.port = _spin(0, 0, 65535, width=72)
        self.port.valueChanged.connect(lambda _v: self._save())
        where.addWidget(self.port)
        field("address", "Host", where,
              "Port 0 means the usual one — 22 for SSH.")

        # The method first, then what it asks for. The other way round put "User"
        # above the choice that decides whether a user is wanted at all.
        self.auth = QComboBox()
        self._auths = ()
        self._set_auth_choices("linux")
        self.auth.currentIndexChanged.connect(lambda _i: self._save())
        field("auth", "Sign in method", self.auth,
              "A key is preferred: no secret to keep.")

        self.username = QLineEdit()
        self.username.setPlaceholderText("account on that machine")
        self.username.editingFinished.connect(self._save)
        field("username", "User", self.username,
              "Who we log in as. Reading needs no privilege;\n"
              "acting needs sudo without a password.")

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

        self.winrm = QCheckBox("Use WinRM on this machine")
        self.winrm.toggled.connect(self._set_winrm)
        field("winrm", "Extras", self.winrm,
              "Lets this machine's process be terminated, its event log read and a\n"
              "command health check run on it. Without it those three are not offered.\n"
              "Every WinRM call signs in, so it leaves a logon record in that\n"
              "machine's Security log. Test connection sets this for you.")

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
        self.tested.connect(self._say_result)
        self.winrm_found.connect(self._winrm_decided)

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
        self._set_auth_choices(machine.kind)
        self.auth.setCurrentIndex(
            next((i for i, (_t, v) in enumerate(self._auths)
                  if v == machine.auth), 0))
        self.key_path.setText(machine.key_path)
        self.fingerprint.setText(machine.host_fingerprint)
        for field in (self.label, self.address, self.username, self.key_path,
                      self.fingerprint):
            field.setCursorPosition(0)
        self.poll.setValue(machine.poll_seconds)
        self._load_winrm(machine)
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
        if machine.is_local:
            machine.auth = "current_user"       # nothing to sign in to
        else:
            index = min(self.auth.currentIndex(), len(self._auths) - 1)
            machine.auth = self._auths[max(index, 0)][1]
        machine.username = self.username.text().strip()
        machine.key_path = self.key_path.text().strip()
        machine.host_fingerprint = self.fingerprint.text().strip()
        machine.poll_seconds = int(self.poll.value())
        self.crumb_name.setText(machine.display())
        self._apply_visibility()
        self.changed.emit()

    def _set_auth_choices(self, kind: str) -> None:
        """The two lists share nothing, so switching kind rebuilds the box.

        Rebuilt without emitting: repopulating a combo fires currentIndexChanged,
        which is wired to _save, which would write the first entry of the new list
        over the setting before load() had a chance to select the right one.
        """
        wanted = self.AUTHS_LINUX if kind == "linux" else self.AUTHS_WINDOWS
        if wanted == self._auths:
            return
        self._auths = wanted
        blocked = self.auth.blockSignals(True)
        self.auth.clear()
        for text, _value in wanted:
            self.auth.addItem(text)
        self.auth.blockSignals(blocked)

    def _set_winrm(self, on: bool) -> None:
        if self.machine is None:
            return
        self.machine.winrm = bool(on)
        self._save()

    def _kind_changed(self, _index):
        kind = self.KINDS[self.kind.currentIndex()][1]
        self._set_auth_choices(kind)
        if self.machine is not None:
            # Carry the intent across: both lists have "password", so a machine set
            # up with a password keeps it when the kind changes; anything else falls
            # back to that kind's first option.
            wanted = self.machine.auth
            index = next((i for i, (_t, v) in enumerate(self._auths)
                          if v == wanted), 0)
            self.auth.setCurrentIndex(index)
        self._save()

    def _load_winrm(self, machine) -> None:
        blocked = self.winrm.blockSignals(True)
        self.winrm.setChecked(bool(getattr(machine, "winrm", False)))
        self.winrm.blockSignals(blocked)

    def _apply_visibility(self):
        """This computer has nothing to configure — it is reached by being it."""
        machine = self.machine
        local = machine is None or machine.is_local
        linux = bool(machine and machine.is_linux)
        remote_windows = bool(machine and not local and not linux)
        self.kind.setEnabled(not local)
        for key in ("address",):
            self._hide_row(key, not local)
        # A remote Windows machine can also be reached as a named account, so it has
        # the same two rows. Only the ones that are genuinely SSH — the host key and
        # the key file — stay Linux-only.
        for key in ("auth",):
            self._hide_row(key, linux or remote_windows)
        self._hide_row("fingerprint", linux)
        by_password = bool(machine) and not local and machine.auth == "password"
        self._hide_row("username", linux or (remote_windows and by_password))
        self._hide_row("password", by_password)
        self._hide_row("key_path", linux and not by_password)
        self._hide_row("poll", not local)
        # Windows, and not this computer: locally these three already work, and on Linux
        # SSH runs commands and reads the journal already.
        #
        # `_hide_row`'s second argument is *shown*, not hidden — the name reads the other
        # way round and this was written backwards once because of it.
        self._hide_row("winrm", not (linux or local))
        # Nothing to set up as root, so the button is not offered — a button whose
        # only answer is "nothing to do" is a question the user has to ask first.
        self.setup_button.setVisible(linux and machine.username != "root")
        self._describe_fields(linux, remote_windows)
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
        elif remote_windows and by_password:
            self.sudo_note.setText(
                "Windows allows one account per machine at a time: if this computer "
                "already has a connection to it — a mapped drive, an open Explorer "
                "window — as somebody else, that account is used and this one is "
                "refused. The account must administer services on that machine, "
                "which normally means its Administrators group.")
        elif remote_windows:
            self.sudo_note.setText(
                "Reached as whoever is signed in to this computer. Nothing to set "
                "up, as long as that account administers services on the other "
                "machine — otherwise choose a user name and password above.")
        else:
            self.sudo_note.setText("")

    def _describe_fields(self, linux: bool, remote_windows: bool) -> None:
        """The same three rows mean different things on the two kinds of machine,
        and a note about sudo beside a Windows account would be nonsense."""
        if remote_windows:
            notes = {
                "username": "DOMAIN\\account, or .\\account for one local to that "
                            "machine.",
                "auth": "The signed-in account needs nothing set up. A user name "
                        "and password is for when it is not an administrator "
                        "there.",
                "password": "Kept encrypted on this computer, not in\n"
                            "services.json. Any administrator here can read it.",
            }
        else:
            notes = {
                "username": "Who we log in as. Reading needs no privilege;\n"
                            "acting needs sudo without a password.",
                "auth": "A key is preferred: no secret to keep.",
                "password": "Kept encrypted on this computer, not in\n"
                            "services.json. Any administrator here can read it.",
            }
        for key, text in notes.items():
            hint = self._hints.get(key)
            if hint is not None:
                hint.setText(text)

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
        # The config cannot show that this changed — it holds the name of the entry,
        # which is the same name as before — so nothing at save time would know to
        # let go of a connection made with the old password. Say so here.
        connectors.forget(machine.name)
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
        connectors.forget(machine.name)
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

        On a worker thread, because the answer can take a long time to arrive: a
        machine with RPC's dynamic ports firewalled took 42 seconds to refuse, and
        every one of those seconds was a frozen window with no cursor and no
        explanation. Measured, not imagined — 10.77.3.110 did exactly that.
        """
        machine = self.machine
        if machine is None:
            return
        if machine.is_local:
            self.result.setText("This computer answers — it is the one running "
                                "Service Officer.")
            return
        self.result.setText(f"Asking {machine.where()}…")
        threading.Thread(target=self._run_test, args=(machine,),
                         daemon=True).start()

    def _run_test(self, machine):
        """The test itself, off the UI thread. Reports through a signal."""
        connectors.forget(machine.name)
        conn = connectors.for_machine(machine.name, record=machine)
        try:
            # Signing in explicitly, because reachable() cannot report why it
            # failed — it answers a yes/no question for the poller. "The user name
            # or password is wrong" and "the machine could not be found" need
            # different things done about them.
            sign_in = getattr(conn, "_sign_in", None)
            if callable(sign_in):
                sign_in()
            if not conn.reachable():
                self.tested.emit(self._why_unreachable(machine))
                return
            winrm_said = self._test_winrm(machine)
            can = conn.abilities()
        except RuntimeError as exc:
            # Ours, and already a sentence — the transport's own words, not a
            # class name in front of them.
            self.tested.emit(f"{machine.where()}: {exc}")
            return
        except Exception as exc:
            self.tested.emit(f"{type(exc).__name__}: {exc}")
            return
        # Whose reach was just proved. With a hub, the chip on the machine shows what the
        # *hub* found, and this test ran here, as whoever is signed in — two different
        # subjects, and saying only "answered" invites the reading that they are one. Seen
        # on 2026-07-28: sc-sql answered a test while its chip said `waiting`.
        whose = " (from this computer)" if self._hub is not None else ""
        said = [f"{machine.where()} answered{whose}."]
        if machine.auth == "password" and not machine.is_linux:
            said.append(f"Signed in as {machine.username}.")
        said.append("Services can be started and stopped." if can.control
                    else "Watching only — no control.")
        said.append("Changes arrive as they happen." if can.push
                    else f"Status is asked for every {machine.poll_seconds}s.")
        if winrm_said:
            said.append(winrm_said)
        if can.why:
            said.append(can.why)
        self.tested.emit("  ".join(said))

    def _test_winrm(self, machine) -> str:
        """Try WinRM and set the switch from the answer. Returns what to say about it.

        This is where the decision gets made for somebody who should not have to know what
        WinRM is: press Test connection, and the switch ends up right. It is still a switch
        they can override afterwards — a machine that answers today may be one they would
        rather not have logon records on.

        Windows only, and never this computer.
        """
        if machine.is_local or machine.is_linux:
            return ""
        from core import secrets, winrm_windows

        user = machine.username if machine.auth == "password" else ""
        password = secrets.get(secrets.ref_for_machine(machine.name)) if user else ""
        winrm_windows.forget(machine.address or machine.name)
        answer = winrm_windows.probe(machine.address or machine.name, user, password)

        wanted = bool(answer.get("ok"))
        if wanted != bool(getattr(machine, "winrm", False)):
            machine.winrm = wanted
            self.winrm_found.emit()
        if wanted:
            return ("WinRM answers, so it is switched on: its process can be terminated, "
                    "its event log read, and a command run on it.")
        why = answer.get("why", "")
        return ("WinRM is not usable, so it is switched off." + (f"  {why}" if why else ""))

    def _winrm_decided(self) -> None:
        """Test connection set the machine's switch; show it and save it."""
        if self.machine is None:
            return
        self._load_winrm(self.machine)
        self.changed.emit()

    def _why_unreachable(self, machine) -> str:
        """A remote machine that did not answer: say what to open, not just that it
        did not. A Linux machine's reasons come from SSH; a Windows one's from the
        firewall diagnosis."""
        if machine.is_linux:
            return (f"{machine.where()} did not answer over SSH. Check that the "
                    f"machine is on, that port {machine.port or 22} is open, and that "
                    f"the account, key or password and the host key all match.")
        from core import scm_windows
        host = machine.address or machine.name
        told = scm_windows.diagnose(host)
        return told or f"{machine.where()} did not answer."

    def _say_result(self, text: str) -> None:
        """On the UI thread, and only if the page is still there — closing the panel
        while a test is in flight used to raise "Signal source has been deleted"."""
        try:
            self.result.setText(text)
        except RuntimeError:
            pass

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
