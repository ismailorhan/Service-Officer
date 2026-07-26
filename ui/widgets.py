"""Small shared widgets for the settings window."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog,
                               QDoubleSpinBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

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


class Chip(QLabel):
    """A status pill: a word, on a fill that says what kind of state it is.

    A word rather than a coloured dot, deliberately — colour alone fails for
    colour-blind readers and photographs badly into a ticket.

    The colours live in theme.sheet() under a `cat` property, so a theme change
    repaints every chip in one pass. Before this there were seven bare QLabels
    with inline stylesheets, each of which had to be found and rebuilt by hand.
    """

    def __init__(self, text: str = "", category: str = "none",
                 min_width: int = None, parent=None):
        super().__init__(text, parent)
        self.setProperty("chip", "true")
        self.setAlignment(Qt.AlignCenter)
        if min_width is not None:
            self.setMinimumWidth(min_width)
        self.set_state(text, category)

    def set_state(self, text: str, category: str) -> None:
        self.setText(text)
        if self.property("cat") != category:
            self.setProperty("cat", category)
            # Qt only re-evaluates property selectors when told to.
            self.style().unpolish(self)
            self.style().polish(self)


class Elide(QLabel):
    """A label that shortens its text instead of widening its row.

    A QLabel's minimum width is its text, so one long display name pushed the
    tray panel's rows past the window and clipped the Kill button off the right
    edge — measured: 469px of content in a 466px panel, less 9px once a
    scrollbar appeared. The full text stays in the tooltip.
    """

    def __init__(self, text: str = "", role: str = None, parent=None):
        super().__init__(parent)
        self._full = text
        if role:
            self.setProperty("role", role)
        self.setMinimumWidth(40)
        self.setText(text)

    def setText(self, text: str) -> None:          # noqa: N802 - Qt naming
        self._full = text
        self.setToolTip("")
        super().setText(text)
        self._reflow()

    def _reflow(self):
        room = self.width()
        if room <= 0 or not self._full:
            return
        metrics = self.fontMetrics()
        if metrics.horizontalAdvance(self._full) <= room:
            if super().text() != self._full:
                super().setText(self._full)
            self.setToolTip("")
            return
        super().setText(metrics.elidedText(self._full, Qt.ElideRight, room))
        self.setToolTip(self._full)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()


class ReorderList(QListWidget):
    """A list whose rows are dragged into their order.

    Rows carry item widgets, and Qt's own InternalMove takes the item away and
    puts a bare one back — the widget goes with it. So the drop is intercepted,
    the intended move reported, and the owner rebuilds the list from reordered
    data. Qt still draws the insertion line for us.
    """

    reordered = Signal(int, int)      # from row, to row
    #: from row, and where the insertion line sat in the *current* list (0..count).
    #: A grouped list needs that raw position to tell which group was dropped
    #: into; a flat one only cares where the row ends up.
    dropped = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QListWidget.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDropIndicatorShown(True)

    def insertion_at(self, point) -> int:
        """Where the drop indicator sits, as an index between 0 and count."""
        row = self.indexAt(point).row()
        if row < 0:
            return self.count()                     # dropped past the last row
        where = self.dropIndicatorPosition()
        if where == QAbstractItemView.BelowItem:
            return row + 1
        if where == QAbstractItemView.OnItem:
            # Landed on a row rather than between two: use its own midpoint, so
            # the result matches where the line was drawn.
            rect = self.visualItemRect(self.item(row))
            return row + (1 if point.y() > rect.center().y() else 0)
        return row

    def dropEvent(self, event):
        source = self.currentRow()
        insert_at = self.insertion_at(event.position().toPoint())
        event.setDropAction(Qt.IgnoreAction)        # we move the data, not the item
        event.accept()
        if source < 0:
            return
        self.dropped.emit(source, insert_at)

        target = insert_at - (1 if source < insert_at else 0)
        target = max(0, min(target, self.count() - 1))
        if source != target:
            self.reordered.emit(source, target)


class Grip(QLabel):
    """Drag handle for rows that aren't in a list view.

    The stack's step rows hold their own editors, so the whole row can't be the
    drag source — dragging inside a number box has to keep editing the number.
    The handle carries its row index and reports where it was let go; the owner
    reorders and rebuilds.
    """

    dragging = Signal(int)            # row under the cursor, -1 while idle
    moved = Signal(int, int)          # from index, to index
    THRESHOLD = 4                     # px before a click counts as a drag

    def __init__(self, index: int, rows, parent=None):
        super().__init__(theme.GLYPH_GRIP, parent)     # tricolon: a grip, and BMP-safe
        self.index = index
        self._rows = rows                      # callable returning the row widgets
        self.setFixedWidth(14)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("Drag to reorder")
        self.setProperty("role", "grip")
        self._press = None
        self._live = False

    def _row_at(self, global_y: int) -> int:
        rows = [r for r in self._rows() if r.isVisible()]
        if not rows:
            return self.index
        for i, row in enumerate(rows):
            top = row.mapToGlobal(QPoint(0, 0)).y()
            if top <= global_y <= top + row.height():
                return i
        first = rows[0].mapToGlobal(QPoint(0, 0)).y()
        return 0 if global_y < first else len(rows) - 1

    def mousePressEvent(self, event):
        self._press = event.globalPosition().toPoint()
        self._live = False
        event.accept()

    def mouseMoveEvent(self, event):
        if self._press is None:
            return
        here = event.globalPosition().toPoint()
        if not self._live and abs(here.y() - self._press.y()) < self.THRESHOLD:
            return
        self._live = True
        self.setCursor(Qt.ClosedHandCursor)
        self.dragging.emit(self._row_at(here.y()))
        event.accept()

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.OpenHandCursor)
        self.dragging.emit(-1)
        if self._press is None or not self._live:
            self._press = None
            event.accept()
            return
        self._press = None
        target = self._row_at(event.globalPosition().toPoint().y())
        if target != self.index:
            self.moved.emit(self.index, target)
        event.accept()


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

    # No colour here: QLabel[role="flatValue"] in the sheet owns that, so a theme
    # change repaints it without anyone re-applying anything. Only the underline
    # stays inline — QSS cannot hover-underline a QLabel in a way I can verify
    # without a real pointer, and guessing would change behaviour blind.
    _IDLE = "padding:4px 2px;"
    _HOVER = "padding:4px 2px; text-decoration:underline;"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(5)
        self.flat = QLabel()
        self.flat.setProperty("role", "flatValue")
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
            self.flat.setStyleSheet(self._IDLE)

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
            self.flat.setStyleSheet(self._HOVER)
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        if not self._editing:
            self.flat.setStyleSheet(self._IDLE)
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
        self._min = minimum                  # a floor in seconds, always
        unit_index, amount = self._split(int(seconds))
        self.spin = Spin(amount, 0, 9999, 62)
        self.unit = QComboBox()
        self.unit.addItems([long for long, _short, _f in self.UNITS])
        self.unit.setCurrentIndex(unit_index)
        self.unit.setFixedWidth(88)
        self._apply_minimum()
        self.spin.valueChanged.connect(self._on_edit)
        self.unit.currentIndexChanged.connect(self._unit_changed)
        self.spin.editingFinished.connect(self._maybe_flatten)
        self._wire()

    def _apply_minimum(self) -> None:
        """Express the floor in whatever unit is showing.

        The floor is in seconds, and it used to be handed to the spin box as-is:
        with "minutes" selected, a five-*second* minimum stopped you typing
        anything under five *minutes*. Typing 1 left the box holding an invalid
        value, so Qt put the previous number back — which looked like the field
        refusing to change.
        """
        factor = self.UNITS[self.unit.currentIndex()][2]
        self.spin.setMinimum(-(-self._min // factor))    # ceil, in whole units

    def _unit_changed(self, _index) -> None:
        self._apply_minimum()
        self._on_edit()

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
        self.unit.blockSignals(True)
        self.unit.setCurrentIndex(idx)
        self.unit.blockSignals(False)
        self._apply_minimum()            # before setValue, or it clamps to the old
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
