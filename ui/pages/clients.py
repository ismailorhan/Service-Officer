"""Clients: who may read this hub, and what each one has done with that.

Only shown when there *is* a hub — on a single-machine install there is nobody to pair, and
a page listing nothing is a page to wonder about.

The shape of this page follows from one property of the store: a token is kept as a
SHA-256 and nothing else, so a copy of a hub's store is not a set of keys. That means a
token can be *shown* exactly once, at the moment it is issued, and never again. So:

* issue one, with a label and a note about why it exists;
* copy it — or the whole command to run on the machine it is for — while it is on screen;
* afterwards, the list says who was issued what, when, and when they last used it, with the
  host name the connection actually came from.

The hub does the work. This page asks it, because the client list lives in a store only
administrators can write and the panel does not run elevated.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QApplication, QDialog, QGridLayout, QHBoxLayout,
                               QHeaderView, QLineEdit, QMessageBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from core import clock

from .. import theme
from ..widgets import button as _button, label as _label
from .base import _Page


class _IssuedDialog(QDialog):
    """The one moment a token is readable.

    Deliberately a dialog and not a row in the table: it has to be dismissed on purpose,
    because closing it is the last chance anybody has to copy what is in it. It says so.
    """

    def __init__(self, made: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Client added")
        self.setModal(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        root.addWidget(_label(f"{made.get('name', '')} — token issued", "h2"))
        root.addWidget(_label(
            "This is the only time it is shown. The hub keeps a hash of it, not the "
            "token, so it cannot be looked up again — issue another if it is lost.",
            "hint", wrap=True))

        token = QLineEdit(made.get("token", ""))
        token.setReadOnly(True)
        token.setCursorPosition(0)
        token.setFont(QFont(theme.MONO, 9))
        root.addWidget(_label("Token", "hint"))
        root.addWidget(token)

        command = QLineEdit(made.get("command", ""))
        command.setReadOnly(True)
        command.setCursorPosition(0)
        command.setFont(QFont(theme.MONO, 9))
        root.addWidget(_label("Run this on that computer", "hint"))
        root.addWidget(command)

        if made.get("fingerprint"):
            root.addWidget(_label(
                f"It should see this certificate:  {made['fingerprint']}",
                "hint", wrap=True))
        if made.get("replaced"):
            root.addWidget(_label(
                "That name already had a token. The old one has stopped working.",
                "hint", wrap=True))

        row = QHBoxLayout()
        row.addWidget(_button("Copy token", "quiet",
                              lambda: QApplication.clipboard().setText(
                                  made.get("token", ""))))
        row.addWidget(_button("Copy command", "primary",
                              lambda: QApplication.clipboard().setText(
                                  made.get("command", ""))))
        row.addStretch(1)
        row.addWidget(_button("Done", "quiet", self.accept))
        root.addLayout(row)
        self.setMinimumWidth(560)


class _AddDialog(QDialog):
    """A name and a note. The name is what identifies it afterwards; the note is for
    whoever reads the list in six months."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add a client")
        self.setModal(True)
        form = QGridLayout(self)
        form.setContentsMargins(18, 16, 18, 14)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        form.addWidget(_label("Name", "hint"), 0, 0)
        self.name = QLineEdit()
        self.name.setPlaceholderText("ismail-laptop")
        self.name.setMinimumWidth(280)
        form.addWidget(self.name, 0, 1)

        form.addWidget(_label("Description", "hint"), 1, 0)
        self.description = QLineEdit()
        self.description.setPlaceholderText("whose it is, or why it exists")
        form.addWidget(self.description, 1, 1)

        form.addWidget(_label(
            "The name is how this client appears here and in the hub's log. It is not "
            "checked against anything — the machine reports its own host name when it "
            "connects, and that appears beside it.", "hint", wrap=True), 2, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(_button("Cancel", "quiet", self.reject))
        self.ok = _button("Issue token", "primary", self.accept)
        row.addWidget(self.ok)
        form.addLayout(row, 3, 1)
        self.name.textChanged.connect(
            lambda text: self.ok.setEnabled(bool(text.strip())))
        self.ok.setEnabled(False)


class ClientsPage(_Page):
    """Every client paired with the hub this panel is reading."""

    COLUMNS = ("Name", "Description", "Host", "Issued", "Last used")

    #: answers arrive on a worker thread; the table is Qt's
    listed = Signal(object)
    failed = Signal(str)

    def __init__(self, hub_ref):
        super().__init__("Clients",
                         "Who may read this hub. A token is shown once, when it is "
                         "issued.")
        #: A callable returning the hub client, or None. A callable because the panel is
        #: built before anybody knows whether this is a client of a hub.
        self.hub = hub_ref
        self.listed.connect(self._fill)
        self.failed.connect(self._complain)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column in range(2, len(self.COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.root.addWidget(self.table, 1)

        self.note = _label("", "hint", wrap=True)
        self.root.addSpacing(6)
        self.root.addWidget(self.note)

        row = QHBoxLayout()
        self.add_button = _button("Add client…", "primary", self._add)
        self.revoke_button = _button("Revoke", "quiet", self._revoke)
        row.addWidget(self.add_button)
        row.addWidget(self.revoke_button)
        row.addWidget(_button("Refresh", "quiet", self.refresh))
        row.addStretch(1)
        self.root.addSpacing(8)
        self.root.addLayout(row)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self._selection_changed()

    # -- reading -----------------------------------------------------------
    def refresh(self):
        """Ask the hub, on a thread: this is a request over a network, and the panel's
        thread is the one that paints."""
        hub = self.hub()
        if hub is None:
            self.note.setText("No hub: there is nobody to pair with this computer.")
            self.table.setRowCount(0)
            return
        self.note.setText("Asking the hub…")

        def work():
            try:
                self.listed.emit(hub.clients())
            except Exception as exc:
                self.failed.emit(str(exc))

        threading.Thread(target=work, daemon=True, name="hub-clients").start()

    def _complain(self, why: str):
        self.note.setText(f"Could not read the hub's client list: {why}")

    def _fill(self, answer: dict):
        clients = answer.get("clients") or []
        self.table.setRowCount(0)
        for client in clients:
            row = self.table.rowCount()
            self.table.insertRow(row)
            cells = (client.get("name", ""),
                     client.get("description", ""),
                     client.get("host", ""),
                     clock.local_text(client.get("added", "")),
                     clock.local_text(client.get("last_seen", ""))
                     or "never used")
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 4 and not client.get("last_seen"):
                    item.setForeground(QColor(theme.FG2))
                self.table.setItem(row, column, item)
        self.note.setText(
            f"{len(clients)} client{'' if len(clients) == 1 else 's'} may read "
            f"{answer.get('url', 'this hub')}."
            if clients else
            "Nothing is paired with this hub yet.")
        self._selection_changed()

    def _selected(self) -> str:
        rows = self.table.selectionModel().selectedRows() \
            if self.table.selectionModel() else []
        if not rows:
            return ""
        item = self.table.item(rows[0].row(), 0)
        return item.text() if item is not None else ""

    def _selection_changed(self):
        self.revoke_button.setEnabled(bool(self._selected()))

    # -- writing -----------------------------------------------------------
    def _add(self):
        hub = self.hub()
        if hub is None:
            return
        asked = _AddDialog(self)
        if asked.exec() != QDialog.Accepted:
            return
        name = asked.name.text().strip()
        try:
            made = hub.add_client(name, asked.description.text().strip())
        except Exception as exc:
            QMessageBox.warning(self, "Service Officer",
                                f"The hub would not issue a token: {exc}")
            return
        _IssuedDialog(made, self).exec()
        self.refresh()

    def _revoke(self):
        hub = self.hub()
        name = self._selected()
        if hub is None or not name:
            return
        if QMessageBox.question(
                self, "Service Officer",
                f"Stop {name} from reading this hub?\n\n"
                "It takes effect immediately. Issuing another token is the only way "
                "back.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            gone = hub.revoke_client(name)
        except Exception as exc:
            QMessageBox.warning(self, "Service Officer",
                                f"Could not revoke {name}: {exc}")
            return
        if not gone:
            QMessageBox.information(self, "Service Officer",
                                    f"{name} was not paired with this hub.")
        self.refresh()

    # -- the panel's own protocol ------------------------------------------
    def load_from(self, cfg):
        """The panel calls this for every page when it opens. Deliberately does nothing:
        the client list has nothing to do with the config, and asking the hub for it is a
        request over a network that most openings of this window do not need. It is
        refreshed when the page is actually selected — see MainPanel._select."""
