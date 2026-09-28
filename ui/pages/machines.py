"""Machines: the servers whose services this panel can reach.

A machine now carries *how* it is reached — the SCM for a Windows box, systemd
over SSH for a Linux one — so it has enough settings to need a page of its own
rather than a name in a list. Same shape as Services: a list, and a detail behind
each row.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from core import config as cfg_mod
from core import connectors, control, secrets

from .. import theme
from core.i18n import t

from ..widgets import button as _button, label as _label
from .base import _ListRow, _Page, _fields, _show_row, _spin


class MachinesPage(QWidget):
    """Every service belongs to a machine; this computer is always one of them."""

    changed = Signal()
    #: an address resolved on a worker thread; redraw on the GUI thread
    address_found = Signal()

    def __init__(self, cfg_ref, store=None, hub=None):
        super().__init__()
        self.cfg = cfg_ref
        self.store = store
        # Normalised the way panel.py does it: the panel is built before anybody knows
        # whether this is a client of a hub, so what arrives may be a getter or the thing
        # itself. Both are accepted and it is called from here on — a getter stored as if it
        # were the client is never None, so every "am I connected" test silently passed.
        self._hub = hub if callable(hub) else (lambda: hub)
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
        bar.addWidget(_button(t("Add machine…"), "primary", self._add))
        bar.addWidget(_button(t("Open"), None, self._open))
        bar.addWidget(_button(t("Remove"), "danger", self._remove))
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
                              tags=self._chips(machine, tag, tag_kind))
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
        name = (self._engine_host() if machine.is_local else "") or machine.display()
        address = machine.address or control.cached_address(machine.name)
        if address and address != name:
            return f"{name}  ({address})"
        return name

    def _engine_host(self) -> str:
        """The name of the computer the engine runs on.

        Not this one, when a hub is being read: the machine that owns the engine has an empty
        name and no address, so a client used to fill the gap with `control.host_name()` —
        its *own* name — for a machine whose services live somewhere else. The hub says which
        computer it is in every snapshot.
        """
        hub = self._hub()
        if hub is not None:
            return getattr(hub, "host", "") or ""
        return control.host_name() or ""

    def _chips(self, machine, reach_tag: str, reach_kind: str) -> list:
        """The chips for this row. Two are possible, because they are two facts.

        "This PC" is where somebody is sitting. "Hub" is where the engine runs. On a
        single-machine install they are the same computer and it gets both; on a workstation
        reading a hub they are different computers, and the hub's row used to claim to be
        this one.
        """
        if not machine.is_local:
            return [(reach_tag, reach_kind)] if reach_tag else []
        chips = []
        connected = self._hub() is not None
        here = control.host_name() or ""
        engine_on = self._engine_host()
        if not connected or (here and engine_on and here.lower() == engine_on.lower()):
            chips.append((t("This PC"), "running"))
        if connected:
            chips.append((t("Hub"), "running"))
        return chips

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
            return "", t("This PC"), "running", ""
        known = self.store.machine_state(machine.name) if self.store else {}
        if not known:
            return (t("not asked yet"), t("waiting"), "none",
                    t("Nothing has asked this machine anything yet. It is asked every "
                      "{seconds}s once the app is watching a service on it.",
                      seconds=machine.poll_seconds))
        ago = max(0.0, time.monotonic() - known.get("at", 0.0))
        # Each of these is a whole phrase with the number in it, not "s" glued onto a figure:
        # a fragment cannot be translated, because where the number goes is part of the
        # language. Same reason the Recovery rows were rewritten.
        when = (t("just now") if ago < 2 else
                t("{seconds}s ago", seconds=f"{ago:.0f}") if ago < 60 else
                t("{minutes} min ago", minutes=f"{ago / 60:.0f}") if ago < 3600 else
                t("{hours} h ago", hours=f"{ago / 3600:.1f}"))
        why = (known.get("detail") or "").strip()
        if known.get("reachable"):
            return (t("answered {when}", when=when), t("connected"), "running", "")
        return (t("no answer, last tried {when}", when=when), t("no answer"), "stopped",
                why or t("It did not answer, and said nothing about why."))

    def _summary(self, machine, count: int, reach: str = "") -> str:
        """What it is and how it is reached, in one line."""
        # One service and several are two sentences, not a bolted-on "s": a language that
        # does not form its plural that way cannot use the second half.
        parts = [t("1 service") if count == 1 else t("{count} services", count=count)]
        if machine.is_local:
            parts.append(t("Windows service manager"))
        elif machine.is_linux:
            who = machine.username or t("no account set")
            parts.append(t("systemd over SSH · {who}", who=who))
            if not machine.host_fingerprint:
                parts.append(t("host key not confirmed"))
        elif machine.auth == "password":
            parts.append(t("Windows service manager · {who}",
                           who=machine.username or t("no account set")))
        else:
            parts.append(t("Windows service manager · this computer's account"))
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
            self, t("Add machine"),
            t("Host name or IP of the machine.\n"
              "Its description, account and the rest are on the next page."))
        name = (name or "").strip().lstrip("\\")
        if not ok or not name:
            return
        if self.cfg().machine(name):
            QMessageBox.information(self, "Service Officer",
                                    t("That machine is already listed."))
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
                                    t("This computer can't be removed."))
            return
        using = [s.display() for s in self.cfg().services
                 if (s.machine or "") == machine.name]
        if using:
            QMessageBox.information(
                self, "Service Officer",
                t("{machine} still has {count} service(s) here. Remove them first:",
                  machine=machine.display(), count=len(using))
                + "\n\n" + "\n".join(using[:8]))
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
        #: Whether this panel talks to a hub rather than watching by itself. Only used to
        #: say whose reach a connection test proved — see _run_test. A getter or the client
        #: itself; both accepted, see MachinesPage.
        self._hub = hub if callable(hub) else (lambda: hub)

        crumb = QHBoxLayout()
        crumb.setSpacing(6)
        crumb.addWidget(_button(f"{theme.GLYPH_BACK}  Machines", "quiet", self.back.emit))
        crumb.addWidget(_label(theme.GLYPH_CRUMB, "hint"))
        self.crumb_name = _label("", "strong")
        crumb.addWidget(self.crumb_name)
        crumb.addStretch(1)
        self.root.insertLayout(0, crumb)
        self.root.insertSpacing(1, 10)

        # One grid, shared with every other settings page — see base._fields. This page had its
        # own copy: the same three columns, the same 108px label column, the same dot at the end,
        # built by a local `field()` closure. Two implementations of one layout, agreeing only
        # because a test compared them. The keys are what the shared one was missing: this page
        # hides rows and rewrites notes, because what "User" means on a Windows machine and on a
        # Linux one are different sentences.
        self.label = QLineEdit()
        self.label.setPlaceholderText(t("what this machine is"))
        self.label.editingFinished.connect(self._save)

        self.kind = QComboBox()
        for text, _value in self.KINDS:
            # Translated here, not in KINDS: a class constant is built at import, before
            # anybody has chosen a language, so a t() up there would freeze the first one.
            self.kind.addItem(t(text))
        self.kind.currentIndexChanged.connect(self._kind_changed)

        self.address = QLineEdit()
        self.address.setPlaceholderText(t("host name or IP address"))
        self.address.editingFinished.connect(self._save)
        self.port = _spin(0, 0, 65535, width=72)
        self.port.valueChanged.connect(lambda _v: self._save())

        # The method first, then what it asks for. The other way round put "User"
        # above the choice that decides whether a user is wanted at all.
        self.auth = QComboBox()
        self._auths = ()
        self._set_auth_choices("linux")
        self.auth.currentIndexChanged.connect(lambda _i: self._save())

        self.username = QLineEdit()
        self.username.setPlaceholderText(t("account on that machine"))
        self.username.editingFinished.connect(self._save)

        # A password is never read back out of the store to show it. The field
        # holds what you are typing now; once saved it says so and goes blank.
        self.password = _SecretEdit()
        self.password.setPlaceholderText(t("type to set a password"))
        self.password.editingFinished.connect(self._save_password)

        self.key_path = QLineEdit()
        self.key_path.setPlaceholderText(r"C:\Users\you\.ssh\id_ed25519")
        self.key_path.editingFinished.connect(self._save)

        # The fingerprint is the whole of SSH's security on a first connection, so
        # it is a field a person fills in deliberately — never something the app
        # accepts because it was offered.
        self.fingerprint = QLineEdit()
        self.fingerprint.setPlaceholderText(t("SHA256:… — confirm it on the machine"))
        self.fingerprint.editingFinished.connect(self._save)

        self.poll = _spin(5, 2, 300, width=64)
        self.poll.valueChanged.connect(lambda _v: self._save())

        self.winrm = QCheckBox(t("Use WinRM on this machine"))
        self.winrm.toggled.connect(self._set_winrm)

        form = _fields(
            (t("Description"), self.label,
             t("Shown everywhere this machine appears."), False, "label"),
            (t("Target type"), self.kind,
             t("Everything else here follows from this."), False, "kind"),
            (t("Host"), (self.address, t("port"), self.port),
             t("Port 0 means the usual one — 22 for SSH."), False, "address"),
            (t("Sign in method"), self.auth,
             t("A key is preferred: no secret to keep."), False, "auth"),
            (t("User"), self.username,
             t("Who we log in as. Reading needs no privilege; acting needs sudo without a "
               "password."), False, "username"),
            # The field takes the room up to Forget. It had a "saved on this computer" label
            # beside it, which squeezed the field to about six characters for a sentence the
            # field already tells you: dots mean stored, empty means not.
            (t("Password"), (self.password,
                             _button(t("Forget"), "quiet", self._forget_password)),
             t("Kept encrypted on this computer, not in services.json. Any administrator here "
               "can read it."), False, "password"),
            (t("Key file"), (self.key_path,
                             _button(t("Browse…"), "quiet", self._browse)),
             t("The private key here. Its public half goes in that account's "
               "~/.ssh/authorized_keys."), False, "key_path"),
            (t("Host key"), (self.fingerprint,
                             _button(t("Get it"), "quiet", self._fetch_fingerprint)),
             t("Proves the machine is the one you meant. A different key later is refused."),
             False, "fingerprint"),
            (t("Check every"), (self.poll, t("seconds")),
             t("A remote machine cannot tell us on its own. With journal access this is only a "
               "safety net."), False, "poll"),
            (t("Extras"), self.winrm,
             t("Lets this machine's process be terminated, its event log read and a command "
               "health check run on it. Without it those three are not offered.\n\n"
               "Every WinRM call signs in, so it leaves a logon record in that machine's "
               "Security log. Test connection sets this for you."), False, "winrm"),
            fill=True)
        self.form = form
        #: Rows by name, and the dot of each — both from the shared grid now.
        self._rows = form.by_key
        self._hints = form.dots
        self._hide_row = lambda key, shown: _show_row(form.by_key.get(key, ()), shown)

        self.root.addWidget(form)
        self.root.addSpacing(theme.SP_12)
        self.sudo_note = _label("", "hint", wrap=True)
        self.root.addWidget(self.sudo_note)
        self.root.addSpacing(theme.SP_12)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(_button(t("Test connection"), "primary", self._test))
        # Named for what it hands you, since it was asked what it meant: the
        # commands to run *on that machine* so this account may control services.
        self.setup_button = _button(t("Copy what to run on that machine"), None,
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
            self.auth.addItem(t(text))          # a class constant; see KINDS
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
        if local:
            self.result.setText(t("This computer is reached by being it — the service "
                                  "manager is right here."))
            self.sudo_note.setText("")
        elif by_password and machine.username == "root":
            # Root needs no sudo and no group: that is the entire reason sudo
            # exists. Worth saying, because the alternative is someone following
            # setup steps they do not need.
            self.sudo_note.setText(t(
                "Signing in as root needs nothing set up on that machine — no sudo rule, "
                "no group. The cost is that a root password is stored on this computer, "
                "where any administrator can read it."))
        elif linux:
            self.sudo_note.setText(t(
                "Reading a status needs no privilege. Starting and stopping needs sudo "
                "without a password, and instant updates need the account in the "
                "systemd-journal group — the button below writes out exactly what to run "
                "for the services you have chosen."))
        elif remote_windows and by_password:
            self.sudo_note.setText(t(
                "Windows allows one account per machine at a time: if this computer "
                "already has a connection to it — a mapped drive, an open Explorer window "
                "— as somebody else, that account is used and this one is refused. The "
                "account must administer services on that machine, which normally means "
                "its Administrators group."))
        elif remote_windows:
            self.sudo_note.setText(t(
                "Reached as whoever is signed in to this computer. Nothing to set up, as "
                "long as that account administers services on the other machine — "
                "otherwise choose a user name and password above."))
        else:
            self.sudo_note.setText("")

    def _describe_fields(self, linux: bool, remote_windows: bool) -> None:
        """The same three rows mean different things on the two kinds of machine,
        and a note about sudo beside a Windows account would be nonsense."""
        # The Password note is the one the form already carries, word for word: the same
        # sentence in two places is one entry in the catalogue and one thing to reword.
        kept = t("Kept encrypted on this computer, not in services.json. Any administrator "
                 "here can read it.")
        if remote_windows:
            notes = {
                "username": t("DOMAIN\\account, or .\\account for one local to that "
                              "machine."),
                "auth": t("The signed-in account needs nothing set up. A user name and "
                          "password is for when it is not an administrator there."),
                "password": kept,
            }
        else:
            notes = {
                "username": t("Who we log in as. Reading needs no privilege; acting needs "
                              "sudo without a password."),
                "auth": t("A key is preferred: no secret to keep."),
                "password": kept,
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
            self.result.setText("")
        else:
            self.result.setText(secrets.last_error()
                                or t("The password could not be stored. Is Service "
                                     "Officer running as administrator?"))
        self.changed.emit()

    def _forget_password(self):
        machine = self.machine
        if machine is None:
            return
        secrets.forget(machine.secret_ref or secrets.ref_for_machine(machine.name))
        connectors.forget(machine.name)
        self.password.show_stored(False)
        self.result.setText(t("Forgotten. That machine cannot be reached with a password "
                              "until a new one is set."))
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
            self.result.setText(t("{host} did not offer a key — {why}", host=host,
                                  why=f"{type(exc).__name__}: {exc}"))
            return
        if not found:
            self.result.setText(t("{host} did not offer a key.", host=host))
            return
        was = self.fingerprint.text().strip()
        self.fingerprint.setText(found)
        self.fingerprint.setCursorPosition(0)
        self._save()
        if was and was != found:
            # Worth shouting about: either the machine was rebuilt, or this is not
            # the same machine.
            self.result.setText(t(
                "The key changed. It was {was} and is now {now}. Either that machine was "
                "rebuilt, or it is not the same machine — check before you keep this.",
                was=was, now=found))
        else:
            self.result.setText(t(
                "{host} offers {key}. Read over the network, so it proves nothing on its "
                "own: confirm it on the machine with "
                "ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub if the network between "
                "here and there is not trusted.", host=host, key=found))

    def _browse(self):
        from PySide6.QtWidgets import QFileDialog
        found, _filter = QFileDialog.getOpenFileName(self, t("Private key file"))
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
            self.result.setText(t("This computer answers — it is the one running Service "
                                  "Officer."))
            return
        self.result.setText(t("Asking {machine}…", machine=machine.where()))
        threading.Thread(target=self._run_test, args=(machine,),
                         daemon=True).start()

    def _run_test(self, machine):
        """The test itself, off the UI thread. Reports through a signal.

        Asked of the **hub** when there is one. It used to be asked here, so on a workstation
        the answer was "this desk can reach that server" — a different question from the one
        the hub answers every five seconds, and not the one anybody pressing the button means.
        The result even said "(from this computer)": honest about the wrong answer rather than
        giving the right one.
        """
        hub = self._hub()
        try:
            if hub is not None:
                found = hub.test_machine(machine.name)
            else:
                # No hub: this computer *is* the engine, so it is the right computer to ask.
                from core import probe
                found = probe.test_machine(machine)
        except Exception as exc:
            self.tested.emit(f"{type(exc).__name__}: {exc}")
            return
        self.tested.emit(self._word_test(machine, found))

    def _word_test(self, machine, found: dict) -> str:
        """The facts as a sentence. Here, not in core: this is what a person reads, in their
        language, and `probe` returns booleans on purpose."""
        if not found.get("reachable"):
            # The diagnosis comes from wherever the probe ran — a firewall question is about
            # the path between two machines — so it arrives worded and is passed through.
            return found.get("why") or t("{machine} did not answer.",
                                        machine=machine.where())

        said = [t("{machine} answered.", machine=machine.where())]
        if machine.auth == "password" and not machine.is_linux and found.get("signed_in"):
            said.append(t("Signed in as {who}.", who=machine.username))
        said.append(t("Services can be started and stopped.") if found.get("control")
                    else t("Watching only — no control."))
        said.append(t("Changes arrive as they happen.") if found.get("push")
                    else t("Status is asked for every {seconds}s.",
                           seconds=machine.poll_seconds))
        said.append(self._word_winrm(machine, found.get("winrm")))
        if found.get("abilities_why"):
            said.append(found["abilities_why"])
        return "  ".join(part for part in said if part)

    def _word_winrm(self, machine, answer) -> str:
        """What WinRM said, and the switch set from it.

        This is where the decision gets made for somebody who should not have to know what
        WinRM is: press Test connection, and the switch ends up right. It is still a switch
        they can override afterwards — a machine that answers today may be one they would
        rather not have logon records on.

        None means the question does not apply: this computer, or Linux.
        """
        if answer is None:
            return ""
        wanted = bool(answer.get("ok"))
        if wanted != bool(getattr(machine, "winrm", False)):
            machine.winrm = wanted
            self.winrm_found.emit()
        if wanted:
            return t("WinRM answers, so it is switched on: its process can be terminated, "
                     "its event log read, and a command run on it.")
        why = answer.get("why", "")
        # The reason comes from the machine that ran the probe, already worded, so it is
        # passed through rather than translated — see _word_test.
        return t("WinRM is not usable, so it is switched off.") + (f"  {why}" if why else "")

    def _winrm_decided(self) -> None:
        """Test connection set the machine's switch; show it and save it."""
        if self.machine is None:
            return
        self._load_winrm(self.machine)
        self.changed.emit()

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
                t("# Nothing to set up on {machine}: root already has\n"
                  "# every privilege this app needs, and reads the journal.\n",
                  machine=machine.where()))
            self.result.setText(t("Signing in as root needs no setup on that machine — "
                                  "nothing to run."))
            return

        # The comments in this block are read by whoever pastes it, so they are prose like
        # anything else on the page. The commands are not: they are what the machine runs.
        services = self._services_here()
        lines = [t("# Run this on {machine} as root.", machine=machine.where()),
                 t("# If you are not root: sudo -i   (or su -)"),
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
                t("# Refuse a broken file rather than discovering sudo is gone:"),
                "visudo -cf /etc/sudoers.d/service-officer",
            ]
        else:
            lines += ["",
                      t("# Choose this machine's services on the Services page and\n"
                        "# copy this again — the sudo rule will then name them, and\n"
                        "# grant nothing beyond them.")]
        QApplication.clipboard().setText("\n".join(lines) + "\n")
        self.result.setText(
            t("Copied — paste the whole block into a root shell on {machine}. "
              "{count} service(s) covered.", machine=machine.where(),
              count=len(services))
            if services else
            t("Copied. It only adds the journal group so far: choose this machine's "
              "services first and copy again to include the sudo rule."))
