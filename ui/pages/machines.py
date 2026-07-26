"""Machines: the servers whose services this panel can reach."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QHBoxLayout, QListWidget, QListWidgetItem,
                               QMessageBox)

from core import config as cfg_mod
from core import control

from ..widgets import button as _button, label as _label
from .base import _ListRow, _Page


class MachinesPage(_Page):
    """Every service belongs to a machine; this computer is always one of them."""

    changed = Signal()
    #: an address resolved on a worker thread; redraw on the GUI thread
    address_found = Signal()

    def __init__(self, cfg_ref):
        super().__init__("Machines",
                         "Where your services live, by name and address. This "
                         "computer is always here; adding another lets its "
                         "services appear in the same panel.")
        self.cfg = cfg_ref
        self.address_found.connect(self.refresh)

        self.list = QListWidget()
        self.root.addWidget(self.list, 1)
        bar = QHBoxLayout()
        bar.addWidget(_button("Add machine…", "primary", self._add))
        bar.addWidget(_button("Remove", "danger", self._remove))
        bar.addStretch(1)
        self.root.addSpacing(14)
        self.root.addLayout(bar)
        self.root.addSpacing(10)
        self.root.addWidget(_label(
            "Managing another machine needs administrator rights on it and the "
            "usual Windows service ports reachable. Its services are added on "
            "the Services page, where each one names the machine it belongs to.",
            "hint", wrap=True))
        self.refresh()

    def refresh(self):
        self.list.clear()
        cfg = self.cfg()
        for machine in cfg.machines:
            count = sum(1 for s in cfg.services if (s.machine or "") == machine.name)
            item = QListWidgetItem()
            widget = _ListRow(self._title(machine),
                              f"{count} service{'s' if count != 1 else ''}",
                              tag="This PC" if machine.is_local else "",
                              tag_category="running")
            item.setSizeHint(widget.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, widget)
            # DNS costs seconds on a name that doesn't resolve, so the address
            # arrives late and the row is redrawn then. Only ask when we don't
            # already know it — otherwise the redraw would ask again forever.
            if control.cached_address(machine.name) is None:
                control.resolve_address(machine.name,
                                        lambda *_a: self.address_found.emit())

    def _title(self, machine) -> str:
        """CTL052 (10.77.3.50) — the name alone isn't enough when someone has to
        RDP to the box, and an IP alone isn't enough to know which box it is."""
        name = control.host_name() if machine.is_local else machine.name
        name = name or machine.display()
        address = control.cached_address(machine.name)
        if address:
            return f"{name}  ({address})"
        if machine.label and machine.label != name:
            return f"{name}  ·  {machine.label}"
        return name

    def _add(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Add machine",
                                        "Computer name (as Windows knows it):")
        name = (name or "").strip().lstrip("\\")
        if not ok or not name:
            return
        if self.cfg().machine(name):
            QMessageBox.information(self, "Service Officer",
                                    "That machine is already listed.")
            return
        if not control.reachable(name):
            if QMessageBox.question(
                    self, "Service Officer",
                    f"Could not reach {name} — its service manager did not "
                    "answer.\n\nAdd it anyway?") != QMessageBox.Yes:
                return
        self.cfg().machines.append(cfg_mod.Machine(name=name, label=name))
        self.refresh()
        self.changed.emit()

    def _remove(self):
        row = self.list.currentRow()
        if row < 0:
            return
        machine = self.cfg().machines[row]
        if machine.is_local:
            QMessageBox.information(self, "Service Officer",
                                    "This computer can't be removed.")
            return
        using = [s.display() for s in self.cfg().services
                 if (s.machine or "") == machine.name]
        if using:
            QMessageBox.information(
                self, "Service Officer",
                f"{machine.display()} still has {len(using)} service(s) here. "
                "Remove them first:\n\n" + "\n".join(using[:8]))
            return
        del self.cfg().machines[row]
        self.refresh()
        self.changed.emit()
