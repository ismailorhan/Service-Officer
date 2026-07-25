"""The left-click panel: every configured service with inline actions.

Two things that were fought for under tkinter come free here. A frameless
non-activating window is `Qt.Tool | Qt.FramelessWindowHint` plus
`WA_ShowWithoutActivating`, not a `ShowWindow` workaround; and state updates
arrive on the GUI thread through a signal instead of a hand-built queue.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from core import state as st
from . import icons, theme
from .rows import BulkBar, SectionBar, ServiceRow, StackRow, is_collapsed

WIDTH = 466
ROW_MIN = 3          # keep a comfortable minimum even with one service
MARGIN = 12



class Flyout(QWidget):
    """Anchored above the tray icon, closes when the user clicks elsewhere."""

    action_requested = Signal(str, str, str)   # action, service, machine
    bulk_requested = Signal(str, list)         # action, [(service, machine), …]
    run_stack = Signal(str)
    open_settings = Signal()
    open_services_mmc = Signal()

    def __init__(self, config_getter, store: st.Store, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        self._config = config_getter
        self._store = store
        self._rows: dict = {}
        self._stack_widgets: list = []
        self._signature = None
        #: screen y of the bottom edge while shown, so the panel grows upwards
        self._bottom = None
        self.setFixedWidth(WIDTH)
        self._build()

    # -- construction ------------------------------------------------------
    def _build(self):
        shell = QWidget(self)
        shell.setObjectName("shell")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(shell)

        root = QVBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # header
        head = QHBoxLayout()
        head.setContentsMargins(14, 11, 10, 9)
        head.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(icons.base_pixmap("green", 20))
        head.addWidget(logo)
        title = QLabel("Service Officer")
        title.setObjectName("flyoutTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.badge = QLabel("")
        self.badge.setStyleSheet(theme.chip_style("running"))
        head.addWidget(self.badge)
        # Drawn, not typed: the ✕ glyph came out blank in both themes because
        # the button's font had no such character.
        close = QPushButton()
        close.setProperty("kind", "quiet")
        close.setFixedSize(26, 24)
        close.setIcon(icons.nav_icon("close", 12, theme.FG3))
        close.setIconSize(QSize(12, 12))
        close.setToolTip("Close")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.hide)
        head.addWidget(close)
        root.addLayout(head)
        root.addWidget(self._hline())

        self.summary = QLabel("")
        self.summary.setProperty("role", "hint")
        self.summary.setContentsMargins(14, 5, 14, 5)
        root.addWidget(self.summary)

        # search
        wrap = QWidget()
        sl = QHBoxLayout(wrap)
        sl.setContentsMargins(10, 0, 10, 6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search services…")
        self.search.textChanged.connect(self._filter)
        sl.addWidget(self.search)
        root.addWidget(wrap)

        # column header
        cols = QWidget()
        cols.setObjectName("columnHeader")
        cols.setAttribute(Qt.WA_StyledBackground, True)
        cl = QHBoxLayout(cols)
        cl.setContentsMargins(14, 4, 14, 4)
        cl.setSpacing(10)
        self.tick_all = QCheckBox()
        self.tick_all.setTristate(True)
        self.tick_all.setToolTip("Select every service shown")
        self.tick_all.clicked.connect(self._toggle_all)
        cl.addWidget(self.tick_all)
        for text, width, align in (("SERVICE", 0, Qt.AlignLeft),
                                   ("STATUS", 74, Qt.AlignCenter),
                                   ("ACTIONS", 96, Qt.AlignRight)):
            lb = QLabel(text)
            lb.setProperty("role", "section")
            lb.setAlignment(align | Qt.AlignVCenter)
            if width:
                lb.setFixedWidth(width)
                cl.addWidget(lb)
            else:
                cl.addWidget(lb, 1)
        root.addWidget(cols)

        # list
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list = QWidget()
        self.list_lay = QVBoxLayout(self.list)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(0)
        self.list_lay.addStretch(1)
        self.scroll.setWidget(self.list)
        root.addWidget(self.scroll, 1)

        # Bulk actions sit above the footer and match it — same margins, equal
        # widths — so they read as controls rather than a toolbar wedged into the
        # list. The row's own background is what says it isn't one of the rows.
        self.bulk = BulkBar()
        self.bulk.chosen.connect(self._bulk)
        self.bulk.cleared.connect(lambda: self._set_all(False))
        root.addWidget(self._hline())
        root.addWidget(self.bulk)

        # footer
        foot = QWidget()
        foot.setObjectName("footerBar")
        foot.setAttribute(Qt.WA_StyledBackground, True)
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(10, 9, 10, 9)
        fl.setSpacing(6)
        for text, slot in (("↻  Refresh", self.refresh),
                           ("▤  Services", self.open_services_mmc.emit),
                           ("⚙  Manage", self._settings)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            fl.addWidget(b, 1)
        root.addWidget(foot)

    @staticmethod
    def _hline() -> QFrame:
        line = QFrame()
        line.setObjectName("hline")
        line.setFrameShape(QFrame.HLine)
        line.setFixedHeight(1)
        return line

    def _settings(self):
        """Hands over to the management panel — this button used to say Settings,
        which is now one section inside it."""
        self.hide()
        self.open_settings.emit()

    # -- content -----------------------------------------------------------
    def rebuild(self) -> None:
        """(Re)create the rows: services on top, stacks underneath."""
        cfg = self._config()
        shown_stacks = [s for s in cfg.stacks if s.show_in_flyout]
        signature = ([(s.machine, s.name, s.display(), s.category)
                      for s in cfg.services],
                     [c.name for c in cfg.categories],
                     [(s.name, s.summary(cfg.services)) for s in shown_stacks])
        if signature == self._signature:
            return
        self._signature = signature

        for row in list(self._rows.values()) + getattr(self, "_sections", []):
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        for w in self._stack_widgets:
            w.setParent(None)
            w.deleteLater()
        self._stack_widgets.clear()

        def add(widget):
            self.list_lay.insertWidget(self.list_lay.count() - 1, widget)

        # Grouped by category, with a heading only when there is more than one
        # group: a single "No category" bar above every service says nothing.
        groups = cfg.grouped_services()
        self._sections = []
        show_headings = len(groups) > 1 or bool(cfg.categories)
        for name, title, members in groups:
            if show_headings:
                bar = SectionBar(name, title, len(members),
                                 sum(1 for s in members
                                     if self._store.status_of(s.name, s.machine)
                                     == st.RUNNING))
                bar.toggled.connect(self._section_toggled)
                self._sections.append(bar)
                add(bar)
            for svc in members:
                row = ServiceRow(svc)
                row.act.connect(self.action_requested)
                row.picked.connect(self._selection_changed)
                row.category = name
                self._rows[svc.key] = row
                add(row)

        if not cfg.services:
            empty = QLabel("No services configured.\nAdd some from Settings.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setProperty("role", "hint")
            empty.setContentsMargins(0, 26, 0, 26)
            self._rows[("", "__empty__")] = empty
            add(empty)

        # Stacks live in the same scrolling list, under their own heading, so a
        # whole sequence is one click away from where you read the statuses.
        if shown_stacks:
            bar = QWidget()
            bar.setObjectName("sectionBar")
            bar.setAttribute(Qt.WA_StyledBackground, True)
            bl = QHBoxLayout(bar)
            bl.setContentsMargins(14, 5, 14, 5)
            head = QLabel("STACKS")
            head.setProperty("role", "section")
            bl.addWidget(head)
            bl.addStretch(1)
            self._stack_widgets.append(bar)
            add(bar)

            for stack in shown_stacks:
                row = StackRow(stack, cfg.services)
                row.run.connect(self.run_stack)
                self._stack_widgets.append(row)
                add(row)

        self._apply_collapse()
        self._resize_to_content()
        self.apply_states()

    def apply_states(self) -> None:
        cfg = self._config()
        running = 0
        for svc in cfg.services:
            row = self._rows.get(svc.key)
            if not isinstance(row, ServiceRow):
                continue
            status = self._store.status_of(svc.name, svc.machine)
            row.set_status(status,
                           disabled=self._store.is_disabled(svc.name, svc.machine))
            if status == st.RUNNING:
                running += 1

        total = len(cfg.services)
        self.badge.setText(f"{running} of {total} running" if total else "no services")
        parts = [f"{total} service{'s' if total != 1 else ''}", f"{running} running"]
        stopped = sum(1 for s in cfg.services
                      if self._store.status_of(s.name, s.machine) == st.STOPPED)
        parts.append(f"{stopped} stopped")
        other = total - running - stopped
        if other > 0:
            parts.append(f"{other} other")
        self.summary.setText("  ·  ".join(parts))

    # -- grouping ----------------------------------------------------------
    def _section_toggled(self, _category: str, _folded: bool) -> None:
        self._apply_collapse()
        self._selection_changed()          # a folded tick is not a selection

    def _apply_collapse(self) -> None:
        """Hide the rows of folded groups. Search wins: a matched row shows even
        if its group is shut, otherwise searching looks broken."""
        query = (self.search.text() or "").strip().lower()
        for row in self._rows.values():
            if not isinstance(row, ServiceRow):
                continue
            matches = (query in row.service.display().lower()
                       or query in row.service.name.lower())
            folded = is_collapsed(getattr(row, "category", ""))
            row.setVisible(matches and (not folded or bool(query)))
            if row.isHidden() and row.tick.isChecked():
                row.tick.blockSignals(True)
                row.tick.setChecked(False)
                row.tick.blockSignals(False)

    # -- bulk actions ------------------------------------------------------
    def _service_rows(self, visible_only: bool = True) -> list:
        # isHidden(), not isVisible(): a row in a window that hasn't been shown
        # yet is invisible without having been filtered out, and asking the wrong
        # question there makes the selection silently empty.
        return [r for r in self._rows.values() if isinstance(r, ServiceRow)
                and (not r.isHidden() or not visible_only)]

    def selected(self) -> list:
        """The ticked rows, in the order they are shown."""
        return [r for r in self._service_rows() if r.tick.isChecked()]

    def _set_all(self, on: bool) -> None:
        for row in self._service_rows():
            row.tick.blockSignals(True)
            row.tick.setChecked(on)
            row.tick.blockSignals(False)
        self._selection_changed()

    def _toggle_all(self) -> None:
        # A tristate box clicks through to Partial, which as a *command* means
        # nothing — so treat any click as "select all unless all are selected".
        rows = self._service_rows()
        self._set_all(not (rows and all(r.tick.isChecked() for r in rows)))

    def _selection_changed(self) -> None:
        rows = self._service_rows()
        chosen = [r for r in rows if r.tick.isChecked()]
        self.bulk.set_count(len(chosen))
        self.tick_all.blockSignals(True)
        if not chosen:
            self.tick_all.setCheckState(Qt.Unchecked)
        elif len(chosen) == len(rows):
            self.tick_all.setCheckState(Qt.Checked)
        else:
            self.tick_all.setCheckState(Qt.PartiallyChecked)
        self.tick_all.blockSignals(False)
        self._resize_to_content()

    def _bulk(self, action: str) -> None:
        targets = [(r.service.name, r.service.machine) for r in self.selected()]
        if not targets:
            return
        self.bulk_requested.emit(action, targets)
        self._set_all(False)

    def mark_busy(self, name: str, machine: str, label: str) -> None:
        row = self._rows.get((machine or "", name))
        if isinstance(row, ServiceRow):
            row.set_status(row.status, busy_label=label)

    def _filter(self, _text: str = "") -> None:
        # Visibility has two inputs now — the search box and folded groups — so
        # one place decides it. A tick you can't see is a bulk action you didn't
        # mean, and _apply_collapse drops those too.
        self._apply_collapse()
        self._selection_changed()

    def _resize_to_content(self, settled: bool = False) -> None:
        """Grow to fit the rows, up to what the screen allows.

        Measure the list's own layout rather than multiplying a row's sizeHint:
        the hint is smaller than a row actually renders, so the multiplication
        clipped the last row and forced a scrollbar with only a few services.

        The first measurement happens before Qt has laid the rows out, so it
        comes up a few pixels short; one follow-up pass on the next event-loop
        turn gets the final number.
        """
        rows = self._service_rows()
        self.list.adjustSize()
        content = self.list_lay.sizeHint().height()

        row_h = rows[0].sizeHint().height() if rows else 46
        floor = ROW_MIN * row_h                      # comfortable minimum
        screen = self.screen().availableGeometry()
        ceiling = max(floor, screen.height() - 260)  # leave room for the chrome

        self.scroll.setFixedHeight(int(max(floor, min(content, ceiling))))
        self.adjustSize()
        self._keep_bottom()
        if not settled:
            QTimer.singleShot(0, lambda: self._resize_to_content(True))

    def _keep_bottom(self) -> None:
        """Grow upwards, not downwards.

        The panel is anchored to the tray icon at the bottom of the screen, so
        adjustSize() — which holds the top-left corner — pushed the footer down
        under the taskbar whenever a row appeared. Holding the bottom edge
        instead means the bulk bar and extra rows open into empty screen.
        """
        if self._bottom is None or not self.isVisible():
            return
        screen = self.screen().availableGeometry()
        y = max(screen.top() + 4, self._bottom - self.height())
        if y != self.y():
            self.move(self.x(), int(y))

    def refresh(self) -> None:
        self.rebuild()
        self.apply_states()

    # -- showing / hiding --------------------------------------------------
    def popup(self, icon_rect=None) -> None:
        self.rebuild()
        self.apply_states()
        self.search.clear()
        self._set_all(False)
        self.adjustSize()
        where = self._anchor(icon_rect)
        self.move(where)
        self._bottom = where.y() + self.height()      # the edge to grow away from
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.setFocus()

    def _anchor(self, icon_rect) -> QPoint:
        screen = self.screen().availableGeometry()
        w, h = self.width(), self.sizeHint().height()
        if icon_rect is not None and not icon_rect.isNull():
            x = icon_rect.center().x() - w // 2
            y = icon_rect.top() - h - 6
        else:
            pos = QCursor.pos()
            x, y = pos.x() - w // 2, screen.bottom() - h - MARGIN
        x = max(screen.left() + 4, min(x, screen.right() - w - 4))
        y = max(screen.top() + 4, min(y, screen.bottom() - h - MARGIN))
        return QPoint(int(x), int(y))

    def event(self, ev):
        # Clicking away closes it — Qt tells us directly, no foreground polling.
        # But one of our own dialogs (the kill confirmation, an error) also
        # deactivates this window, and closing the panel underneath it is
        # disorienting, so hold while a modal of ours is up.
        if ev.type() == QEvent.WindowDeactivate and self.isVisible():
            from PySide6.QtWidgets import QApplication
            if (not self.search.text().strip()
                    and QApplication.activeModalWidget() is None):
                QTimer.singleShot(0, self._hide_unless_modal)
        return super().event(ev)

    def _hide_unless_modal(self):
        from PySide6.QtWidgets import QApplication
        if QApplication.activeModalWidget() is None:
            self.hide()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(ev)
