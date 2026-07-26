"""The left-click panel: every configured service with inline actions.

Two things that were fought for under tkinter come free here. A frameless
non-activating window is `Qt.Tool | Qt.FramelessWindowHint` plus
`WA_ShowWithoutActivating`, not a `ShowWindow` workaround; and state updates
arrive on the GUI thread through a signal instead of a hand-built queue.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from core import state as st
from . import icons, theme
from .rows import BulkBar, ServiceRow
from .servicelist import ServiceListMixin
from .widgets import Chip

WIDTH = theme.FLYOUT_WIDTH          # kept as a name others import
ROW_MIN = 3          # keep a comfortable minimum even with one service
MARGIN = 12



class Flyout(ServiceListMixin, QWidget):
    """Anchored above the tray icon, closes when the user clicks elsewhere.

    The rows, the grouping and what a tick means come from ServiceListMixin,
    shared with the dashboard. What is specific here is the geometry: it grows
    upwards from a fixed bottom edge, so nothing shifts under the pointer.
    """

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
        self.badge = Chip("", "running")
        head.addWidget(self.badge)

        # Pinned, the panel stops closing when you click elsewhere — for
        # watching a stack come up while working in another window.
        self.pin = QPushButton()
        self.pin.setProperty("kind", "quiet")
        self.pin.setCheckable(True)
        self.pin.setFixedSize(*theme.ACTION_BTN)
        self.pin.setIconSize(QSize(13, 13))
        self.pin.setCursor(Qt.PointingHandCursor)
        self.pin.toggled.connect(self._pin_toggled)
        head.addWidget(self.pin)
        self._paint_pin()

        # Drawn, not typed: the ✕ glyph came out blank in both themes because
        # the button's font had no such character.
        close = QPushButton()
        close.setProperty("kind", "quiet")
        close.setFixedSize(*theme.ACTION_BTN)
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
                                   ("STATUS", theme.COL_STATUS_W, Qt.AlignCenter),
                                   ("ACTIONS", theme.COL_ACTIONS_W, Qt.AlignRight)):
            lb = QLabel(text)
            lb.setProperty("role", "section")
            lb.setAlignment(align | Qt.AlignVCenter)
            if width:
                lb.setFixedWidth(width)
                cl.addWidget(lb)
            else:
                cl.addWidget(lb, 1)
        root.addWidget(cols)

        # Bulk actions appear directly under the header they belong to — the
        # tick box that selects everything is right above them. Its buttons are
        # laid out like the footer's, and its own background says it acts on a
        # selection rather than on everything.
        #
        # Appearing must not shove the rows down: the window is anchored to its
        # bottom edge (see _keep_bottom), so it grows upwards and every row stays
        # exactly where the pointer left it.
        self.bulk = BulkBar()
        self.bulk.chosen.connect(self._bulk)
        self.bulk.cleared.connect(lambda: self._set_all(False))
        root.addWidget(self.bulk)

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

        # footer
        foot = QWidget()
        foot.setObjectName("footerBar")
        foot.setAttribute(Qt.WA_StyledBackground, True)
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(*theme.FOOT_PAD)
        fl.setSpacing(6)
        for text, slot in ((f"{theme.GLYPH_REFRESH}  Refresh", self.refresh),
                           (f"{theme.GLYPH_SERVICES}  Services",
                            self.open_services_mmc.emit),
                           (f"{theme.GLYPH_SETTINGS}  Manage", self._settings)):
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

    def _paint_pin(self):
        on = self.pin.isChecked()
        self.pin.setIcon(icons.nav_icon("pin" if on else "unpin", 13,
                                        theme.RUN if on else theme.FG3))
        self.pin.setToolTip("Pinned — stays open until you close it.\n"
                            "Click to unpin." if on else
                            "Pin the panel open, so clicking elsewhere doesn't "
                            "close it.")

    def _pin_toggled(self, on: bool):
        self._paint_pin()
        if on:
            self.raise_()

    @property
    def pinned(self) -> bool:
        return self.pin.isChecked()

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

        self._sections = self._add_service_groups(cfg, add)

        if not cfg.services:
            empty = QLabel("No services configured.\nAdd some from Settings.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setProperty("role", "hint")
            empty.setContentsMargins(0, 26, 0, 26)
            self._rows[("", "__empty__")] = empty
            add(empty)

        self._stack_widgets = self._add_stack_section(shown_stacks, cfg.services,
                                                     add)

        self._apply_visibility()
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
                           disabled=self._store.is_disabled(svc.name, svc.machine),
                           health=self._store.health_of(svc.name, svc.machine),
                           health_detail=self._store.health_detail(svc.name,
                                                                   svc.machine))
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

    def _selection_settled(self, settled: bool) -> None:
        """The window is only as tall as its rows, so any change to which rows
        exist changes its height.

        `settled` is false when rows appeared or vanished: Qt hasn't laid them out
        yet, so the height has to be measured a second time once it has. Ticking a
        box doesn't change which rows exist, so that pass has nothing to find and
        is skipped rather than resizing twice — two window operations in one click
        read as a blink.
        """
        self._resize_to_content(settled)

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
        self._apply_geometry()
        if not settled:
            QTimer.singleShot(0, lambda: self._resize_to_content(True))

    def _apply_geometry(self) -> None:
        """Resize and reposition in one move, growing upwards.

        The panel is anchored to the tray icon at the bottom of the screen, so
        holding the top-left corner — which is what adjustSize() does — pushed
        the footer down under the taskbar whenever a row appeared.

        Both edges have to change in a single setGeometry. Resizing and then
        moving is two window changes, and Windows presents a frame between them:
        the panel appeared 49px taller at its old position, then jumped up. That
        was the blink when a tick box was clicked — measured as two distinct
        geometries per click, now one.
        """
        self.layout().activate()                 # so sizeHint is the final one
        height = self.sizeHint().height()
        if self._bottom is None or not self.isVisible():
            self.adjustSize()
            return
        screen = self.screen().availableGeometry()
        y = max(screen.top() + 4, self._bottom - height)
        target = QRect(self.x(), int(y), self.width(), int(height))
        if target != self.geometry():
            self.setGeometry(target)

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

    def resizeEvent(self, ev):
        """Last line of defence for growing upwards.

        Our own resizing goes through _apply_geometry, but Qt resizes the window
        itself too — when a layout's minimum grows, for instance after a group is
        expanded — and that holds the top-left corner, which pushed the footer
        down under the taskbar. Re-anchoring here catches every case, whoever
        caused it. Moving does not raise another resize, so this can't loop.

        The height comes from the event, not from self.height(): for a top-level
        window the widget still reports the old height while this runs, so using
        it re-anchored to the size we were leaving and nothing appeared to move.
        """
        super().resizeEvent(ev)
        if self._bottom is None or not self.isVisible():
            return
        screen = self.screen().availableGeometry()
        y = max(screen.top() + 4, self._bottom - ev.size().height())
        if y != self.y():
            self.move(self.x(), int(y))

    def event(self, ev):
        # Clicking away closes it — Qt tells us directly, no foreground polling.
        # But one of our own dialogs (the kill confirmation, an error) also
        # deactivates this window, and closing the panel underneath it is
        # disorienting, so hold while a modal of ours is up.
        if ev.type() == QEvent.WindowDeactivate and self.isVisible():
            from PySide6.QtWidgets import QApplication
            if (not self.pinned and not self.search.text().strip()
                    and QApplication.activeModalWidget() is None):
                QTimer.singleShot(0, self._hide_unless_modal)
        return super().event(ev)

    def _hide_unless_modal(self):
        from PySide6.QtWidgets import QApplication
        if self.pinned:
            return
        if QApplication.activeModalWidget() is None:
            self.hide()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(ev)
