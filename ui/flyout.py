"""The left-click panel: every configured service with inline actions.

Two things that were fought for under tkinter come free here. A frameless
non-activating window is `Qt.Tool | Qt.FramelessWindowHint` plus
`WA_ShowWithoutActivating`, not a `ShowWindow` workaround; and state updates
arrive on the GUI thread through a signal instead of a hand-built queue.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from core import state as st
from . import icons, theme

WIDTH = 466
ROW_MIN = 3          # keep a comfortable minimum even with one service
MARGIN = 12


class _Row(QWidget):
    """One service: name, short name, status chip, Start/Stop/Restart."""

    act = Signal(str, str, str)      # action, service, machine

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.status = st.UNKNOWN
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("row")
        self.setStyleSheet(f"#row:hover {{ background: {theme.BG_HOVER}; }}")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(10)

        who = QVBoxLayout()
        who.setSpacing(1)
        self.name = QLabel(service.display())
        self.name.setProperty("role", "strong")
        self.short = QLabel(service.name)
        self.short.setProperty("role", "mono")
        who.addWidget(self.name)
        who.addWidget(self.short)
        lay.addLayout(who, 1)

        self.chip = QLabel("…")
        self.chip.setAlignment(Qt.AlignCenter)
        self.chip.setMinimumWidth(74)
        self.chip.setStyleSheet(theme.chip_style("none"))
        lay.addWidget(self.chip)

        self.buttons = {}
        for action, glyph, tip in (("start", "▶", "Start"),
                                   ("stop", "■", "Stop"),
                                   ("restart", "↻", "Restart")):
            b = QPushButton(glyph)
            b.setProperty("kind", "action")
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.setEnabled(False)
            b.clicked.connect(lambda _=False, a=action: self.act.emit(
                a, self.service.name, self.service.machine))
            self.buttons[action] = b
            lay.addWidget(b)

        # Last resort, kept visually apart and in red: when a service wedges,
        # Stop does nothing and the SCM reports "Stopping" for ever.
        kill = QPushButton("✕")
        kill.setProperty("kind", "kill")
        kill.setToolTip("Kill the process — for when Stop doesn't work")
        kill.setCursor(Qt.PointingHandCursor)
        kill.setEnabled(False)
        kill.clicked.connect(lambda: self.act.emit(
            "kill", self.service.name, self.service.machine))
        self.buttons["kill"] = kill
        lay.addSpacing(6)
        lay.addWidget(kill)

    def set_status(self, status: str, busy_label: str = "") -> None:
        self.status = status
        cat = st.category(status)
        self.chip.setText(busy_label or status)
        self.chip.setStyleSheet(theme.chip_style("pending" if busy_label else cat))
        # Kill stays available while anything is running or stuck mid-transition —
        # that stuck case is exactly what it is for — but never for a remote
        # service, where terminating a process isn't something we can do.
        local = not self.service.machine
        allowed = {
            "running": {"start": False, "stop": True, "restart": True, "kill": local},
            "stopped": {"start": True, "stop": False, "restart": True, "kill": False},
            "paused":  {"start": False, "stop": True, "restart": True, "kill": local},
            "pending": {"start": False, "stop": False, "restart": False, "kill": local},
        }.get(cat, {"start": False, "stop": False, "restart": False, "kill": False})
        for action, b in self.buttons.items():
            enabled = bool(allowed.get(action))
            if action != "kill":
                enabled = enabled and not busy_label
            b.setEnabled(enabled)


class _StackRow(QWidget):
    """One stack: name, what it will do, and the button that runs it."""

    run = Signal(str)

    def __init__(self, stack, services, parent=None):
        super().__init__(parent)
        self.setObjectName("row")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(10)

        col = QVBoxLayout()
        col.setSpacing(1)
        name = QLabel(stack.name)
        name.setProperty("role", "strong")
        steps = QLabel(stack.summary(services) or "no steps yet")
        steps.setProperty("role", "hint")
        col.addWidget(name)
        col.addWidget(steps)
        lay.addLayout(col, 1)

        trigger = QPushButton("▶  Run")
        trigger.setProperty("kind", "primary")
        trigger.setCursor(Qt.PointingHandCursor)
        trigger.setEnabled(bool(stack.steps))
        trigger.setToolTip(stack.describe(services) or "Add steps in Settings")
        trigger.clicked.connect(lambda: self.run.emit(stack.name))
        lay.addWidget(trigger)


class Flyout(QWidget):
    """Anchored above the tray icon, closes when the user clicks elsewhere."""

    action_requested = Signal(str, str, str)   # action, service, machine
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
        close = QPushButton("✕")
        close.setProperty("kind", "quiet")
        close.setFixedSize(26, 24)
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

        # footer
        foot = QWidget()
        foot.setObjectName("footerBar")
        foot.setAttribute(Qt.WA_StyledBackground, True)
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(10, 9, 10, 9)
        fl.setSpacing(6)
        for text, slot in (("↻  Refresh", self.refresh),
                           ("▤  Services", self.open_services_mmc.emit),
                           ("⚙  Settings", self._settings)):
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
        self.hide()
        self.open_settings.emit()

    # -- content -----------------------------------------------------------
    def rebuild(self) -> None:
        """(Re)create the rows: services on top, stacks underneath."""
        cfg = self._config()
        signature = ([(s.machine, s.name, s.display()) for s in cfg.services],
                     [(s.name, s.summary(cfg.services)) for s in cfg.stacks])
        if signature == self._signature:
            return
        self._signature = signature

        for row in self._rows.values():
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        for w in self._stack_widgets:
            w.setParent(None)
            w.deleteLater()
        self._stack_widgets.clear()

        def add(widget):
            self.list_lay.insertWidget(self.list_lay.count() - 1, widget)

        for svc in cfg.services:
            row = _Row(svc)
            row.act.connect(self.action_requested)
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
        if cfg.stacks:
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

            for stack in cfg.stacks:
                row = _StackRow(stack, cfg.services)
                row.run.connect(self.run_stack)
                self._stack_widgets.append(row)
                add(row)

        self._resize_to_content()
        self.apply_states()

    def apply_states(self) -> None:
        cfg = self._config()
        running = 0
        for svc in cfg.services:
            row = self._rows.get(svc.key)
            if not isinstance(row, _Row):
                continue
            status = self._store.status_of(svc.name, svc.machine)
            row.set_status(status)
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

    def mark_busy(self, name: str, machine: str, label: str) -> None:
        row = self._rows.get((machine or "", name))
        if isinstance(row, _Row):
            row.set_status(row.status, busy_label=label)

    def _filter(self, text: str) -> None:
        q = (text or "").strip().lower()
        for key, row in self._rows.items():
            if isinstance(row, _Row):
                hit = q in row.service.display().lower() or q in row.service.name.lower()
                row.setVisible(hit)
        self._resize_to_content()

    def _resize_to_content(self, settled: bool = False) -> None:
        """Grow to fit the rows, up to what the screen allows.

        Measure the list's own layout rather than multiplying a row's sizeHint:
        the hint is smaller than a row actually renders, so the multiplication
        clipped the last row and forced a scrollbar with only a few services.

        The first measurement happens before Qt has laid the rows out, so it
        comes up a few pixels short; one follow-up pass on the next event-loop
        turn gets the final number.
        """
        rows = [r for r in self._rows.values() if isinstance(r, _Row) and r.isVisible()]
        self.list.adjustSize()
        content = self.list_lay.sizeHint().height()

        row_h = rows[0].sizeHint().height() if rows else 46
        floor = ROW_MIN * row_h                      # comfortable minimum
        screen = self.screen().availableGeometry()
        ceiling = max(floor, screen.height() - 260)  # leave room for the chrome

        self.scroll.setFixedHeight(int(max(floor, min(content, ceiling))))
        self.adjustSize()
        if not settled:
            QTimer.singleShot(0, lambda: self._resize_to_content(True))

    def refresh(self) -> None:
        self.rebuild()
        self.apply_states()

    # -- showing / hiding --------------------------------------------------
    def popup(self, icon_rect=None) -> None:
        self.rebuild()
        self.apply_states()
        self.search.clear()
        self.adjustSize()
        self.move(self._anchor(icon_rect))
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
        if ev.type() == QEvent.WindowDeactivate and self.isVisible():
            if not self.search.text().strip():
                QTimer.singleShot(0, self.hide)
        return super().event(ev)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(ev)
