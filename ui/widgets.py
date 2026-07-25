"""Small shared widgets for the settings window."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

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


class FlatEdit(QWidget):
    """A value that reads as text and only becomes a control when you click it.

    Three states:
      idle     plain text            "30 s", "3"
      hover    underlined text       hints that it is editable
      editing  real input widgets    the only state that shows any chrome

    A settings page is read far more often than it is edited, and a page full of
    input boxes is heavy to look at. Editing continues until focus leaves — the
    pointer moving away doesn't mean you have finished typing.
    """

    changed = Signal()

    _IDLE = "color:{fg}; padding:4px 2px;"
    _HOVER = "color:{fg}; padding:4px 2px; text-decoration:underline;"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(5)
        self.flat = QLabel()
        self._lay.addWidget(self.flat)
        self._editing = True                 # so the first collapse takes effect

    # -- subclass hooks ----------------------------------------------------
    def _editors(self) -> list:
        raise NotImplementedError

    def _flat_text(self) -> str:
        raise NotImplementedError

    def _focus_target(self):
        return self._editors()[0]

    def _wire(self) -> None:
        """Call once the editors exist."""
        for w in self._editors():
            self._lay.addWidget(w)
            w.installEventFilter(self)
        self._set_editing(False)

    # -- state -------------------------------------------------------------
    def _set_editing(self, editing: bool) -> None:
        self._editing = editing
        self.flat.setVisible(not editing)
        for w in self._editors():
            w.setVisible(editing)
        if not editing:
            self.flat.setText(self._flat_text())
            self.flat.setStyleSheet(self._IDLE.format(fg=theme.FG))

    def restyle(self) -> None:
        """Re-read the palette after a theme change."""
        if not self._editing:
            self.flat.setStyleSheet(self._IDLE.format(fg=theme.FG))

    def _busy(self) -> bool:
        for w in self._editors():
            if w.hasFocus():
                return True
            view = getattr(w, "view", None)
            if callable(view) and view().isVisible():
                return True
        return False

    def enterEvent(self, ev):
        if not self._editing:
            self.flat.setStyleSheet(self._HOVER.format(fg=theme.FG))
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        if not self._editing:
            self.flat.setStyleSheet(self._IDLE.format(fg=theme.FG))
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        if not self._editing:
            self._set_editing(True)
            target = self._focus_target()
            target.setFocus()
            if hasattr(target, "selectAll"):
                QTimer.singleShot(0, target.selectAll)
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
            self._set_editing(False)      # refresh the idle text


class FlatSpin(FlatEdit):
    """A whole number, shown as text until clicked."""

    def __init__(self, value=0, lo=0, hi=9999, suffix="", parent=None):
        super().__init__(parent)
        self._suffix = suffix
        self.spin = Spin(value, lo, hi, 62)
        self.spin.valueChanged.connect(self._on_edit)
        self.spin.editingFinished.connect(self._maybe_flatten)
        self._wire()

    def _editors(self):
        return [self.spin]

    def _flat_text(self):
        return f"{self.spin.value()}{self._suffix}"

    def value(self) -> int:
        return self.spin.value()

    def setValue(self, v) -> None:
        self.spin.setValue(int(v))
        if not self._editing:
            self._set_editing(False)


class FlatFactor(FlatEdit):
    """A multiplier such as the recovery backoff, shown as text until clicked."""

    def __init__(self, value=2.0, lo=1.0, hi=10.0, parent=None):
        super().__init__(parent)
        self.spin = QDoubleSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setDecimals(1)
        self.spin.setSingleStep(0.5)
        self.spin.setValue(float(value))
        self.spin.setFixedWidth(66)
        self.spin.setAlignment(Qt.AlignCenter)
        self.spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.spin.valueChanged.connect(self._on_edit)
        self.spin.editingFinished.connect(self._maybe_flatten)
        self._wire()

    def _editors(self):
        return [self.spin]

    def _flat_text(self):
        return f"×{self.spin.value():.1f}"

    def value(self) -> float:
        return self.spin.value()

    def setValue(self, v) -> None:
        self.spin.setValue(float(v))
        if not self._editing:
            self._set_editing(False)


class PadSpin(Spin):
    """Two-digit display, so 04 stays 04 instead of collapsing to 4."""

    def textFromValue(self, value):
        return f"{int(value):02d}"

    def valueFromText(self, text):
        try:
            return int(str(text).strip() or 0)
        except ValueError:
            return self.minimum()


class Duration(FlatEdit):
    """A number plus a unit, stored as seconds.

    Changing the unit reinterprets the number rather than converting it: typing
    30 and picking "minutes" means thirty minutes, which is what people expect.
    """

    UNITS = (("seconds", "s", 1), ("minutes", "min", 60), ("hours", "h", 3600))

    def __init__(self, seconds: int = 0, minimum: int = 0, parent=None):
        super().__init__(parent)
        self._min = minimum
        unit_index, amount = self._split(int(seconds))
        self.spin = Spin(amount, minimum, 9999, 62)
        self.unit = QComboBox()
        self.unit.addItems([long for long, _short, _f in self.UNITS])
        self.unit.setCurrentIndex(unit_index)
        self.unit.setFixedWidth(88)
        self.spin.valueChanged.connect(self._on_edit)
        self.unit.currentIndexChanged.connect(self._on_edit)
        self.spin.editingFinished.connect(self._maybe_flatten)
        self._wire()

    def _editors(self):
        return [self.spin, self.unit]

    def _flat_text(self):
        short = self.UNITS[self.unit.currentIndex()][1]
        return f"{self.spin.value()} {short}"

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
