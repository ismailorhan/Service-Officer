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
from .widgets import Chip, Elide

#: what each action's button looks like in a narrow row
GLYPHS = (("start", theme.GLYPH_START, "Start"),
          ("stop", theme.GLYPH_STOP, "Stop"),
          ("restart", theme.GLYPH_RESTART, "Restart"))


class ServiceRow(QWidget):
    """One service: tick box, name, short name, status chip, then the actions."""

    act = Signal(str, str, str)      # action, service, machine
    picked = Signal()                # the tick box changed

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.status = st.UNKNOWN
        self.disabled = False
        #: an action of ours in flight ("Restarting…"), until it reports back
        self._busy = ""
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("row")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(*theme.ROW_PAD)
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

        self.chip = Chip("…", "none", min_width=theme.CHIP_MIN_W)
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
        kill = QPushButton(theme.GLYPH_KILL)
        kill.setProperty("kind", "kill")
        kill.setToolTip("Kill the process — for when Stop doesn't work")
        kill.setCursor(Qt.PointingHandCursor)
        kill.setEnabled(False)
        kill.clicked.connect(lambda: self.act.emit(
            "kill", self.service.name, self.service.machine))
        #: Whether a process can be ended on this service's machine. True on this computer;
        #: on another it takes a transport that can, so whoever builds the row says. Set
        #: before the first set_status() — a row with nobody to tell it offers nothing,
        #: which is the safe way round.
        self.can_kill = not self.service.machine
        self.buttons["kill"] = kill
        lay.addSpacing(6)
        lay.addWidget(kill)

    def set_status(self, status: str, busy_label: str = "",
                   disabled: bool = False, health: str = "unknown",
                   health_detail: str = "") -> None:
        self.status = status
        self.disabled = disabled
        # "Restarting…" has to survive the next repaint. It did not: any status
        # arriving from anywhere — another service's poll, a start-type sweep —
        # called this without a busy label and wiped it, so pressing Restart on a
        # Linux service showed "Restarting…" for a moment and then "Running" again
        # while the restart was still going on.
        if busy_label:
            self._busy = busy_label
        busy_label = busy_label or self._busy
        cat = st.category(status)
        # A disabled service can't be started at all, so say so instead of
        # showing "Stopped" next to a Start button that would only fail.
        if disabled and cat == "stopped":
            self.chip.set_state(busy_label or "Disabled", "none")
            for action, b in self.buttons.items():
                b.setEnabled(False)
            self.setToolTip("This service is disabled in Windows — enable it in "
                            "services.msc before it can start.")
            return
        # What it effectively is: st.effective() owns that judgement, so the row,
        # the hover card and the tray icon cannot disagree about it.
        label, shown = st.effective(status, health)
        if busy_label:
            label, shown = busy_label, "pending"
        if shown == "stopped" and cat == "running":
            self.setToolTip("The service is running, but its health checks are "
                            "failing:\n" + (health_detail or "no detail"))
        elif shown == "pending" and cat == "running" and not busy_label:
            self.setToolTip(health_detail or "It has started; its health checks "
                                             "have not passed yet.")
        else:
            self.setToolTip(f"Health checks pass — {health_detail}"
                            if health == st.HEALTHY and health_detail else "")
        self.chip.set_state(label, shown)
        self._set_buttons(cat, busy_label)

    def _set_buttons(self, cat: str, busy_label: str) -> None:
        # Kill stays available while anything is running or stuck mid-transition —
        # that stuck case is exactly what it is for. On this computer always; on
        # another only where something can carry it, which is `can_kill`.
        able = self.can_kill
        allowed = {
            "running": {"start": False, "stop": True, "restart": True, "kill": able},
            "stopped": {"start": True, "stop": False, "restart": True, "kill": False},
            "paused":  {"start": False, "stop": True, "restart": True, "kill": able},
            "pending": {"start": False, "stop": False, "restart": False, "kill": able},
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
        lay.setContentsMargins(*theme.ROW_PAD)
        lay.setSpacing(10)

        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(Elide(stack.name, "strong"))
        col.addWidget(Elide(stack.summary(services) or "no steps yet", "hint"))
        lay.addLayout(col, 1)

        trigger = QPushButton(f"{theme.GLYPH_START}  Run")
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
        lay.setContentsMargins(*theme.BAR_PAD)
        lay.setSpacing(8)

        self.chevron = QLabel()
        self.chevron.setProperty("role", "chevron")
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
        self.chevron.setText(theme.GLYPH_FOLDED if folded else theme.GLYPH_FOLD)
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
        lay.setContentsMargins(*theme.FOOT_PAD)
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
