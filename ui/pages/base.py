"""Pieces every page is built from.

These are package-internal: a page shell with a title and description, the
row used in every list, the service chooser, and the small helpers that make
a number sit inside a sentence.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QDoubleSpinBox, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QScrollArea,
                               QVBoxLayout, QWidget)

from core import control

from .. import icons, theme
from ..widgets import Chip, Spin, button as _button, label as _label


def _spin(value, lo, hi, width=64, step=1):
    s = Spin(value, lo, hi, width)
    s.setSingleStep(step)
    return s


def _dspin(value, lo, hi, width=64):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(1)
    s.setSingleStep(0.5)
    s.setValue(float(value))
    s.setFixedWidth(width)
    s.setAlignment(Qt.AlignCenter)
    s.setButtonSymbols(QDoubleSpinBox.NoButtons)
    return s


def _sentence(*parts) -> QWidget:
    """Lay widgets and text fragments out as one line of prose."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    for p in parts:
        lay.addWidget(_label(p) if isinstance(p, str) else p)
    lay.addStretch(1)
    return w


def _hline():
    f = QFrame()
    f.setFixedHeight(1)
    f.setObjectName("hline")
    return f


class _ListRow(QWidget):
    """A row in a master list: dot, name, secondary line, chip, chevron."""

    def __init__(self, name: str, secondary: str, category: str = None,
                 tag: str = "", tag_category: str = "running", tags=()):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 9, 4, 9)
        lay.setSpacing(11)
        if category:
            dot = QLabel()
            dot.setPixmap(icons.status_dot(category, 8))
            lay.addWidget(dot)
        col = QVBoxLayout()
        col.setSpacing(2)
        n = _label(name, "strong")
        s = _label(secondary, "hint")
        col.addWidget(n)
        col.addWidget(s)
        lay.addLayout(col, 1)
        # More than one, because a row can be more than one thing: the computer somebody
        # is sitting at *and* the one running the engine are two separate facts, and a
        # machine can be both.
        for text, kind in ((tags or ()) if tags else ([(tag, tag_category)] if tag else [])):
            lay.addWidget(Chip(text, kind))
        lay.addWidget(_label(theme.GLYPH_CRUMB, "hint"))


# ── service picker ─────────────────────────────────────────────────────────
class ServicePicker(QDialog):
    """Choose one or more installed services. Multi-select is the point: adding
    a SAP stack means adding five services, not repeating a dialog five times."""

    #: the listing, or an empty list and a reason, from the reading thread
    loaded = Signal(list, str)

    def __init__(self, taken, parent=None, machine="", record=None, hub=None):
        super().__init__(parent)
        self.setWindowTitle("Add services")
        self.resize(520, 560)
        #: Set when this panel reads a hub. Then the hub is asked what exists: it is the
        #: computer the services live on, and asking here would list whichever workstation
        #: the panel happens to be running on — for `machine == ""` that is not even the
        #: right machine.
        self._hub = hub
        self.picked = []
        self._all = []
        self._taken = set(taken)
        self._machine = machine
        # The machine's own record, so a Linux target is enumerated with systemctl
        # rather than through the Windows service manager. Without it a machine
        # added in the panel but not yet saved — or any machine at all in the
        # standalone panel — was asked the wrong way.
        self._record = record

        lay = QVBoxLayout(self)
        lay.setContentsMargins(*theme.PAGE_PAD)
        lay.setSpacing(9)
        lay.addWidget(_label("Pick the services to monitor", "h2"))
        lay.addWidget(_label("Search by display name or service name. "
                             "Ctrl/Shift-click to choose several.", "hint", wrap=True))

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.textChanged.connect(self._populate)
        lay.addWidget(self.search)

        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        lay.addWidget(self.list, 1)

        self.count = _label("", "hint")
        lay.addWidget(self.count)
        self.loaded.connect(self._arrived)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(_button("Cancel", None, self.reject))
        row.addWidget(_button("Add", "primary", self.accept))
        lay.addLayout(row)

        self._load()

    def _load(self):
        """Read the machine's services, off the UI thread when it is another machine.

        Enumerating this computer takes a few milliseconds. Enumerating a remote
        Windows box took **fifteen seconds** — during which the dialog was blank,
        unresponsive and titled "Not Responding", because the listing ran on the
        thread that draws it.
        """
        if not self._machine and self._hub is None:
            try:
                self._all = control.list_all_services(self._machine, self._record)
            except Exception as exc:
                QMessageBox.warning(self, "Service Officer", self._why(exc))
                self._all = []
            self._populate()
            return
        where = self._machine
        self.count.setText(f"Reading the services on {where}…")
        self.list.setEnabled(False)

        def work():
            try:
                self.loaded.emit(self._listing(where), "")
            except Exception as exc:
                self.loaded.emit([], self._why(exc))
        threading.Thread(target=work, daemon=True).start()

    def _listing(self, where: str) -> list:
        """What is installed on that machine, asked of whoever can see it."""
        if self._hub is not None:
            return self._hub.services_on(where)
        return control.list_all_services(where, self._record)

    def _arrived(self, found: list, problem: str) -> None:
        """Back on the UI thread. Guarded, because the dialog may be gone: fifteen
        seconds is long enough to press Cancel."""
        try:
            self.list.setEnabled(True)
            if problem:
                self.count.setText("")
                QMessageBox.warning(self, "Service Officer", problem)
            self._all = found
            self._populate()
        except RuntimeError:
            pass

    def _why(self, exc) -> str:
        """Say which machine and what to look at.

        "(1722, \'OpenSCManager\', \'The RPC server is unavailable.\')" is what the
        API said, not what happened. The raw text is kept at the end, because it is
        what a search engine needs, but it comes after the sentence a person can
        act on.
        """
        where = self._machine or "this computer"
        linux = bool(self._record is not None
                     and getattr(self._record, "is_linux", False))
        if linux:
            hint = (f"{where} did not answer over SSH. Check the account, the "
                    f"password or key, and that the host key matches.")
        elif self._machine:
            hint = (f"{where} did not answer. It may be switched off, or its "
                    f"firewall may be blocking Remote Service Management — the "
                    f"rule Windows needs for another computer to list services.")
        else:
            hint = "This computer's service manager did not answer."
        return f"Could not list the services on {where}.\n\n{hint}\n\n{exc}"

    def _populate(self):
        q = self.search.text().strip().lower()
        self.list.clear()
        shown = 0
        for s in self._all:
            if s["name"] in self._taken:
                continue
            if q and q not in s["display"].lower() and q not in s["name"].lower():
                continue
            item = QListWidgetItem(f"  {s['display']}   ·   {s['name']}   ({s['status']})")
            item.setData(Qt.UserRole, s)
            self.list.addItem(item)
            shown += 1
        self.count.setText(f"{shown} services")

    def accept(self):
        self.picked = [i.data(Qt.UserRole) for i in self.list.selectedItems()]
        if not self.picked:
            return
        super().accept()


# ── pages ──────────────────────────────────────────────────────────────────
class _Page(QWidget):
    """A page: a heading, then whatever goes in `self.root`.

    scroll=True puts the body in a scroll area, leaving the heading fixed. Pages
    made of stacked fields need it — they grow as settings are added, and content
    that is merely clipped is content nobody knows is there. Pages built around a
    table or a list don't: those already scroll on their own, and nesting one
    scrolling thing inside another is horrible to use.
    """

    def __init__(self, title: str, desc: str, scroll: bool = False):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*theme.PANEL_PAD)
        outer.setSpacing(0)
        self.head = QVBoxLayout()
        self.head.setSpacing(4)
        self.head.addWidget(_label(title, "h2"))
        if desc:
            self.head.addWidget(_label(desc, "hint", wrap=True))
        outer.addLayout(self.head)
        outer.addSpacing(18)

        if not scroll:
            self.root = outer
            return
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.root = QVBoxLayout(holder)
        self.root.setContentsMargins(0, 0, 12, 8)      # room for the scrollbar
        self.root.setSpacing(0)
        area.setWidget(holder)
        outer.addWidget(area, 1)
