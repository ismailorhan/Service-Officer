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

    def set_status(self, status: str, busy_label: str = "") -> None:
        self.status = status
        cat = st.category(status)
        self.chip.setText(busy_label or status)
        self.chip.setStyleSheet(theme.chip_style("pending" if busy_label else cat))
        allowed = {
            "running": {"start": False, "stop": True, "restart": True},
            "stopped": {"start": True, "stop": False, "restart": True},
            "paused":  {"start": False, "stop": True, "restart": True},
        }.get(cat, {"start": False, "stop": False, "restart": False})
        for action, b in self.buttons.items():
            b.setEnabled(bool(allowed.get(action)) and not busy_label)


class Flyout(QWidget):
    """Anchored above the tray icon, closes when the user clicks elsewhere."""

    action_requested = Signal(str, str, str)   # action, service, machine
    open_settings = Signal()
    open_services_mmc = Signal()
    run_stack = Signal(str, str)               # stack name, action

    def __init__(self, config_getter, store: st.Store, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        self._config = config_getter
        self._store = store
        self._rows: dict = {}
        self._signature = None
        self.setFixedWidth(WIDTH)
        self.setStyleSheet(f"QWidget#shell {{ background:{theme.BG}; "
                           f"border:1px solid #3a3a3a; }}")
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
        title.setStyleSheet(f"color:{theme.FG}; font-size:11.5pt; font-weight:600;")
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
        cols.setStyleSheet(f"background:{theme.BG_RAISE};")
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
        foot.setStyleSheet(f"background:#1b1b1b; border-top:1px solid {theme.LINE};")
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
        line.setFrameShape(QFrame.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{theme.LINE}; border:none;")
        return line

    def _settings(self):
        self.hide()
        self.open_settings.emit()

    # -- content -----------------------------------------------------------
    def rebuild(self) -> None:
        """(Re)create rows for the configured services."""
        cfg = self._config()
        signature = [(s.machine, s.name, s.display()) for s in cfg.services]
        if signature == self._signature:
            return
        self._signature = signature

        for row in self._rows.values():
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()

        for svc in cfg.services:
            row = _Row(svc)
            row.act.connect(self.action_requested)
            self._rows[svc.key] = row
            self.list_lay.insertWidget(self.list_lay.count() - 1, row)

        if not cfg.services:
            empty = QLabel("No services configured.\nAdd some from Settings.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setProperty("role", "hint")
            empty.setContentsMargins(0, 26, 0, 26)
            self._rows[("", "__empty__")] = empty
            self.list_lay.insertWidget(self.list_lay.count() - 1, empty)

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

    def _resize_to_content(self) -> None:
        rows = [r for r in self._rows.values() if isinstance(r, _Row) and r.isVisible()]
        row_h = rows[0].sizeHint().height() if rows else 46
        visible = max(ROW_MIN, len(rows)) if rows else ROW_MIN
        screen = self.screen().availableGeometry()
        chrome = 250
        max_rows = max(ROW_MIN, (screen.height() - chrome - 50) // max(1, row_h))
        self.scroll.setFixedHeight(int(min(visible, max_rows) * row_h))
        self.adjustSize()

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
