"""Small shared widgets for the settings window."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QSizePolicy, QSpinBox, QVBoxLayout,
                               QWidget)

from . import theme


def label(text, role=None, wrap=False) -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    return lb


def button(text, kind=None, slot=None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if slot:
        b.clicked.connect(slot)
    b.setCursor(Qt.PointingHandCursor)
    return b


class Spin(QSpinBox):
    """A number box that selects its contents when you tab or click into it, so
    typing replaces the value instead of appending to it."""

    def __init__(self, value=0, lo=0, hi=9999, width=62):
        super().__init__()
        self.setRange(lo, hi)
        self.setValue(int(value))
        self.setFixedWidth(width)
        self.setAlignment(Qt.AlignCenter)
        self.setButtonSymbols(QSpinBox.NoButtons)

    def focusInEvent(self, ev):
        super().focusInEvent(ev)
        # After Qt has placed its own cursor, take the whole value.
        QTimer.singleShot(0, self.selectAll)

    def mousePressEvent(self, ev):
        had_focus = self.hasFocus()
        super().mousePressEvent(ev)
        if not had_focus:
            QTimer.singleShot(0, self.selectAll)


class Duration(QWidget):
    """A number plus a unit, stored as seconds.

    Reads as plain text ("30 s") until you point at it, then turns into editable
    controls — a settings page full of boxes is heavy to look at, and most of the
    time you are reading these values rather than changing them. The unit is
    short when idle and spelled out in the dropdown.

    Changing the unit reinterprets the number rather than converting it: typing
    30 and picking "minutes" means thirty minutes, which is what people expect.
    """

    changed = Signal()
    UNITS = (("seconds", "s", 1), ("minutes", "min", 60), ("hours", "h", 3600))

    def __init__(self, seconds: int = 0, minimum: int = 0, parent=None):
        super().__init__(parent)
        self._min = minimum
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)

        unit_index, amount = self._split(int(seconds))

        self.flat = QLabel()
        self.flat.setStyleSheet(f"color:{theme.FG}; padding:4px 2px;")
        lay.addWidget(self.flat)

        self.spin = Spin(amount, minimum, 9999, 62)
        self.unit = QComboBox()
        self.unit.addItems([long for long, _short, _f in self.UNITS])
        self.unit.setCurrentIndex(unit_index)
        self.unit.setFixedWidth(88)
        lay.addWidget(self.spin)
        lay.addWidget(self.unit)

        self.spin.valueChanged.connect(self._on_edit)
        self.unit.currentIndexChanged.connect(self._on_edit)
        self.spin.editingFinished.connect(self._maybe_flatten)
        for w in (self.spin, self.unit):
            w.installEventFilter(self)
        self._editing = True              # so the first call actually collapses
        self._set_editing(False)

    # -- flat / editing ----------------------------------------------------
    #  flat            plain text            "30 s"
    #  hover           underlined text       hints that it can be edited
    #  click / focus   spin + unit boxes     the only state that shows chrome
    _FLAT = "color:{fg}; padding:4px 2px;"
    _HOVER = "color:{fg}; padding:4px 2px; text-decoration:underline;"

    def _set_editing(self, editing: bool) -> None:
        self._editing = editing
        self.flat.setVisible(not editing)
        self.spin.setVisible(editing)
        self.unit.setVisible(editing)
        if not editing:
            short = self.UNITS[self.unit.currentIndex()][1]
            self.flat.setText(f"{self.spin.value()} {short}")
            self.flat.setStyleSheet(self._FLAT.format(fg=theme.FG))

    def _busy(self) -> bool:
        """Editing continues while a control has focus or its list is open — the
        pointer being elsewhere doesn't mean you're finished typing."""
        return (self.spin.hasFocus() or self.unit.hasFocus()
                or self.unit.view().isVisible())

    def enterEvent(self, ev):
        if not self._editing:
            self.flat.setStyleSheet(self._HOVER.format(fg=theme.FG))
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        if not self._editing:
            self.flat.setStyleSheet(self._FLAT.format(fg=theme.FG))
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        if not self._editing:
            self._set_editing(True)
            self.spin.setFocus()
            QTimer.singleShot(0, self.spin.selectAll)
        super().mousePressEvent(ev)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.FocusOut:
            QTimer.singleShot(0, self._maybe_flatten)
        return False

    def _maybe_flatten(self):
        if self._editing and not self._busy():
            self._set_editing(False)

    def _on_edit(self, *_):
        self.changed.emit()
        if not self._editing:
            self._set_editing(False)      # refresh the flat text

    @classmethod
    def _split(cls, seconds: int):
        """Largest unit that divides evenly — 90 stays 90 s, 120 shows 2 min."""
        for idx in (2, 1):
            factor = cls.UNITS[idx][2]
            if seconds and seconds % factor == 0:
                return idx, seconds // factor
        return 0, seconds

    def seconds(self) -> int:
        factor = self.UNITS[self.unit.currentIndex()][2]
        return max(self._min, self.spin.value() * factor)

    def set_seconds(self, seconds: int) -> None:
        idx, amount = self._split(int(seconds))
        self.unit.setCurrentIndex(idx)
        self.spin.setValue(amount)
        if not self._editing:
            self._set_editing(False)


class SearchableList(QDialog):
    """Pick one item from a searchable list of (label, payload) pairs."""

    def __init__(self, title, prompt, items, parent=None, multi=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(460, 480)
        self.picked = []
        self._items = list(items)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(9)
        lay.addWidget(label(prompt, "h2"))

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.textChanged.connect(self._populate)
        lay.addWidget(self.search)

        self.list = QListWidget()
        if multi:
            self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        lay.addWidget(self.list, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button("Cancel", None, self.reject))
        row.addWidget(button("Add", "primary", self.accept))
        lay.addLayout(row)

        self._populate()
        self.search.setFocus()

    def _populate(self):
        q = self.search.text().strip().lower()
        self.list.clear()
        for text, payload in self._items:
            if q and q not in text.lower():
                continue
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, payload)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def accept(self):
        self.picked = [i.data(Qt.UserRole) for i in self.list.selectedItems()]
        if not self.picked:
            return
        super().accept()
