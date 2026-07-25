"""Service and stack rows, shared by the tray flyout and the dashboard.

These started inside the flyout. They live here because the rule for which
buttons may be pressed is subtle — Kill stays live while a service is wedged
mid-transition, a disabled service can take no action at all, a remote process
isn't ours to terminate — and two copies of that rule would drift apart.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from core import state as st
from . import theme
from .widgets import Elide

#: what each action's button looks like in a narrow row
GLYPHS = (("start", "▶", "Start"), ("stop", "■", "Stop"), ("restart", "↻", "Restart"))


class ServiceRow(QWidget):
    """One service: tick box, name, short name, status chip, then the actions."""

    act = Signal(str, str, str)      # action, service, machine
    picked = Signal()                # the tick box changed

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.status = st.UNKNOWN
        self.disabled = False
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("row")
        self.setStyleSheet(f"#row:hover {{ background: {theme.BG_HOVER}; }}")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(8)

        # Ticking rows is how a whole SAP stack gets stopped without clicking
        # five separate buttons and hoping the order held.
        self.tick = QCheckBox()
        self.tick.setToolTip("Include in a bulk action")
        self.tick.toggled.connect(lambda _on: self.picked.emit())
        lay.addWidget(self.tick)

        who = QVBoxLayout()
        who.setSpacing(1)
        # Elided, not fixed: one long display name must not push the actions off
        # the right edge of a 466px panel.
        self.name = Elide(service.display(), "strong")
        self.short = Elide(service.name, "mono")
        who.addWidget(self.name)
        who.addWidget(self.short)
        lay.addLayout(who, 1)

        self.chip = QLabel("…")
        self.chip.setAlignment(Qt.AlignCenter)
        self.chip.setMinimumWidth(70)
        self.chip.setStyleSheet(theme.chip_style("none"))
        lay.addWidget(self.chip)

        self.buttons = {}
        for action, glyph, tip in GLYPHS:
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

    def set_status(self, status: str, busy_label: str = "",
                   disabled: bool = False, health: str = "unknown",
                   health_detail: str = "") -> None:
        self.status = status
        self.disabled = disabled
        cat = st.category(status)
        # A disabled service can't be started at all, so say so instead of
        # showing "Stopped" next to a Start button that would only fail.
        if disabled and cat == "stopped":
            self.chip.setText(busy_label or "Disabled")
            self.chip.setStyleSheet(theme.chip_style("none"))
            for action, b in self.buttons.items():
                b.setEnabled(False)
            self.setToolTip("This service is disabled in Windows — enable it in "
                            "services.msc before it can start.")
            return
        # Running but not answering is the failure the service list cannot show.
        # It gets the chip, because "Running" next to a dead service is a lie —
        # and the reason goes in the tooltip, where the next question is answered.
        if health == "unhealthy" and not busy_label and cat == "running":
            self.chip.setText("Not responding")
            self.chip.setStyleSheet(theme.chip_style("stopped"))
            self.setToolTip("The service is running, but its health checks are "
                            "failing:\n" + (health_detail or "no detail"))
            self._set_buttons(cat, busy_label)
            return

        self.setToolTip(f"Health checks pass — {health_detail}"
                        if health == "healthy" and health_detail else "")
        self.chip.setText(busy_label or status)
        self.chip.setStyleSheet(theme.chip_style("pending" if busy_label else cat))
        self._set_buttons(cat, busy_label)

    def _set_buttons(self, cat: str, busy_label: str) -> None:
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


class StackRow(QWidget):
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
        col.addWidget(Elide(stack.name, "strong"))
        col.addWidget(Elide(stack.summary(services) or "no steps yet", "hint"))
        lay.addLayout(col, 1)

        trigger = QPushButton("▶  Run")
        trigger.setProperty("kind", "primary")
        trigger.setCursor(Qt.PointingHandCursor)
        trigger.setEnabled(bool(stack.steps))
        trigger.setToolTip(stack.describe(services) or "Add steps in Settings")
        trigger.clicked.connect(lambda: self.run.emit(stack.name))
        lay.addWidget(trigger)


#: Which category headings are folded shut, shared by every list that shows
#: them. View state, not configuration: collapsing a group in the flyout should
#: show it collapsed in the dashboard too, and it has no business in the file
#: that describes what to monitor.
collapsed: set = set()


def is_collapsed(category: str) -> bool:
    return (category or "") in collapsed


def set_collapsed(category: str, folded: bool) -> None:
    key = category or ""
    collapsed.add(key) if folded else collapsed.discard(key)


class SectionBar(QWidget):
    """A category heading that folds its services away.

    The whole bar is the hit target, not just the chevron: a heading you can
    click anywhere on is easier to hit than a 12px triangle.
    """

    toggled = Signal(str, bool)          # category, now collapsed

    def __init__(self, category: str, title: str, count: int, running: int,
                 parent=None):
        super().__init__(parent)
        self.category = category or ""
        self.setObjectName("sectionBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 5, 14, 5)
        lay.setSpacing(8)

        self.chevron = QLabel()
        self.chevron.setFixedWidth(12)
        lay.addWidget(self.chevron)
        head = Elide(title.upper(), "section")
        lay.addWidget(head, 1)
        self.tally = QLabel(f"{running}/{count}")
        self.tally.setProperty("role", "hint")
        self.tally.setToolTip(f"{running} of {count} running")
        lay.addWidget(self.tally)
        self._paint()

    def _paint(self):
        # ▸ / ▾ rather than a drawn icon: these are BMP glyphs, and an astral
        # one costs ~600 ms of colour-emoji font loading in this process.
        folded = is_collapsed(self.category)
        self.chevron.setText("▸" if folded else "▾")
        self.chevron.setStyleSheet(f"color:{theme.FG3};")
        self.setToolTip("Click to show these services" if folded
                        else "Click to fold this group away")

    def mousePressEvent(self, event):
        folded = not is_collapsed(self.category)
        set_collapsed(self.category, folded)
        self._paint()
        self.toggled.emit(self.category, folded)
        event.accept()


class BulkBar(QWidget):
    """What appears once services are ticked.

    Laid out like the footer — equal-width buttons, the same margins — so it
    reads as a row of controls rather than a toolbar squeezed into a list. Its
    own background is what marks it as not being one of the rows above it.
    """

    chosen = Signal(str)             # action to apply to the selection
    cleared = Signal()

    ACTIONS = (("Start", "start", None), ("Stop", "stop", None),
               ("Restart", "restart", None), ("Kill", "kill", "destructive"))
    #: buttons share the width like the footer's, but stop growing — stretched
    #: across a 1000px dashboard they looked like a mistake
    MAX_BUTTON = 150

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("bulkBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 9, 10, 9)
        lay.setSpacing(6)

        self.count = QLabel("")
        self.count.setProperty("role", "strong")
        self.count.setMinimumWidth(78)
        lay.addWidget(self.count)

        # Words, not glyphs: at 466px the glyph version pushed Kill off the
        # edge, and Kill is the one button that must not be ambiguous.
        for text, action, kind in self.ACTIONS:
            b = QPushButton(text)
            if kind:
                b.setProperty("kind", kind)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(f"{text} every selected service")
            b.setMaximumWidth(self.MAX_BUTTON)
            b.clicked.connect(lambda _=False, a=action: self.chosen.emit(a))
            lay.addWidget(b, 1)

        clear = QPushButton("Clear")
        clear.setProperty("kind", "quiet")
        clear.setCursor(Qt.PointingHandCursor)
        clear.setToolTip("Unselect everything")
        clear.setMaximumWidth(self.MAX_BUTTON)
        clear.clicked.connect(self.cleared.emit)
        lay.addWidget(clear, 1)
        lay.addStretch(0)          # leftover room goes to the end, not the gaps
        self.setVisible(False)

    def set_count(self, n: int) -> None:
        self.count.setText(f"{n} selected")
        self.setVisible(bool(n))
