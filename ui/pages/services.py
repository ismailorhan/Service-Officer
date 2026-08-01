"""The Services page: what is being watched, grouped by category.

The list only ever shows and orders services; everything about one service lives
in ServiceDetail, next door.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QListWidget,
                               QListWidgetItem, QMessageBox, QStackedWidget,
                               QVBoxLayout, QWidget)

from core import config as cfg_mod
from core import control

from .. import theme
from ..widgets import (ReorderList, SearchableList, button as _button,
                       label as _label)
from .base import ServicePicker, _ListRow, _Page
from .service_detail import ServiceDetail


class ServicesPage(QWidget):
    """List of services; opens a detail page for the selected one."""

    changed = Signal()

    def __init__(self, cfg_ref, store=None, hub=None):
        #: Whether this panel reads a hub. Only used so the service picker asks the hub
        #: what exists rather than this computer — see ServicePicker. A getter or the client
        #: itself; both accepted, see MachinesPage.
        self._hub = hub if callable(hub) else (lambda: hub)
        super().__init__()
        self.cfg = cfg_ref
        self.stack = QStackedWidget()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)

        self.list_page = _Page("Services",
                               "Grouped by category, in the order the tray flyout "
                               "and the dashboard show them. Drag a service to "
                               "move it, or onto another heading to file it there. "
                               "Open one to set how it should recover when it "
                               "stops on its own.")
        self.list = ReorderList()
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda _i: self._open_selected())
        self.list.dropped.connect(self._dropped)
        self.list_page.root.addWidget(self.list, 1)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(_button("Add services…", "primary", self._add))
        bar.addWidget(_button("Open", None, self._open_selected))
        # Filing services is something you do to several at once, so it belongs
        # on the list and not only inside one service's page.
        bar.addWidget(_button("Category…", None, self._set_category))
        bar.addWidget(_button("Remove", "danger", self._remove))
        bar.addStretch(1)
        self.list_page.root.addSpacing(14)
        self.list_page.root.addLayout(bar)

        self.detail = ServiceDetail(store)
        self.detail.back.connect(self._show_list)
        self.detail.changed.connect(self._refresh_and_signal)

        self.stack.addWidget(self.list_page)
        self.stack.addWidget(self.detail)
        self.refresh()

    # -- list --------------------------------------------------------------
    def refresh(self):
        """Grouped by category, with every category shown — an empty one is
        somewhere to drag a service to, which is the point of it being here."""
        keep = self.list.currentRow()
        self.list.clear()
        #: one entry per list row: ("group", category) or ("service", Service)
        self._entries = []
        cfg = self.cfg()
        for name, title, members in cfg.grouped_services(include_empty=True):
            self._add_group(name, title, len(members))
            for svc in members:
                self._add_service(svc)
        if 0 <= keep < self.list.count():
            self.list.setCurrentRow(keep)

    def _add_group(self, name: str, title: str, count: int):
        item = QListWidgetItem()
        # Enabled so it can be a drop target, but neither selectable nor
        # draggable: a heading isn't a thing you move or act on. Dragging the
        # heading itself would also be ambiguous — does it carry its services? —
        # so the group's own order is changed with the two arrows instead.
        item.setFlags(Qt.ItemIsEnabled)
        widget = QWidget()
        widget.setObjectName("sectionBar")
        widget.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(widget)
        row.setContentsMargins(theme.SP_10, theme.SP_4, theme.SP_8, theme.SP_4)
        row.setSpacing(4)
        row.addWidget(_label(title.upper(), "section"), 1)
        row.addWidget(_label("empty — drag a service here" if not count
                             else f"{count}", "hint"))

        cats = [c.name for c in self.cfg().categories]
        if name in cats:
            index = cats.index(name)
            for glyph, delta, tip in ((theme.GLYPH_UP, -1, "Move this category up"),
                                      (theme.GLYPH_DOWN, 1, "Move this category down")):
                b = _button(glyph, "step")
                b.setToolTip(tip)
                b.setEnabled(0 <= index + delta < len(cats))
                b.clicked.connect(lambda _=False, i=index, d=delta:
                                  self._move_category(i, d))
                row.addWidget(b)
        else:
            # "No category" has no position of its own — it follows the rest.
            row.addSpacing(52)

        item.setSizeHint(widget.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, widget)
        self._entries.append(("group", name))

    def _move_category(self, index: int, delta: int):
        cats = self.cfg().categories
        target = index + delta
        if not (0 <= index < len(cats) and 0 <= target < len(cats)):
            return
        cats.insert(target, cats.pop(index))
        self._refresh_and_signal()

    def _add_service(self, svc):
        rec = svc.recovery
        if not rec.enabled:
            note = "no automatic recovery"
        elif rec.max_attempts:
            note = f"recovers automatically, up to {rec.max_attempts} attempts"
        else:
            note = "recovers automatically, unlimited attempts"
        # The machine is the service's source, so it goes on the right as a chip
        # rather than buried in the middle of the secondary line. The category is
        # the heading above it now, so it isn't repeated here.
        machine = self.cfg().machine(svc.machine)
        where = (control.host_name() or "This PC") if (
            machine and machine.is_local) else (svc.machine or "?")
        item = QListWidgetItem()
        widget = _ListRow(svc.display(), f"{svc.name}  ·  {note}", "none",
                          tag=where, tag_category="none")
        item.setSizeHint(widget.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, widget)
        self._entries.append(("service", svc))

    def _refresh_and_signal(self):
        self.refresh()
        self.changed.emit()

    def _selected_services(self) -> list:
        """The chosen services, in the order shown. Headings can't be selected,
        so anything selected is a service."""
        return [self._entries[i.row()][1] for i in
                sorted(self.list.selectedIndexes(), key=lambda i: i.row())
                if 0 <= i.row() < len(self._entries)
                and self._entries[i.row()][0] == "service"]

    def _group_at(self, insert_at: int) -> str:
        """Which category an insertion point falls into — the nearest heading
        above it."""
        for i in range(min(insert_at, len(self._entries)) - 1, -1, -1):
            if self._entries[i][0] == "group":
                return self._entries[i][1]
        return cfg_mod.NO_CATEGORY

    def _dropped(self, source_row: int, insert_at: int):
        """A service dragged onto a group joins it, at the position it was let go.

        The visual order becomes the stored order outright, so what the tray
        panel and the dashboard show is what was arranged here.
        """
        if not (0 <= source_row < len(self._entries)):
            return
        kind, svc = self._entries[source_row]
        if kind != "service":
            return
        category = self._group_at(insert_at)
        services = [e[1] for e in self._entries if e[0] == "service"]
        before = sum(1 for i, e in enumerate(self._entries)
                     if i < insert_at and e[0] == "service" and e[1] is not svc)
        services.remove(svc)
        svc.category = category
        services.insert(before, svc)
        self.cfg().services[:] = services
        self._refresh_and_signal()

    def _add(self):
        cfg = self.cfg()
        machine = cfg_mod.LOCAL_MACHINE
        if len(cfg.machines) > 1:
            # More than one machine exists, so which one has to be asked.
            dlg = SearchableList("Add services", "On which machine?",
                                [(m.display(), m.name) for m in cfg.machines], self)
            if dlg.exec() != QDialog.Accepted:
                return
            machine = dlg.picked[0]

        taken = {s.name for s in cfg.services if (s.machine or "") == machine}
        # The record travels with the name: this config is the panel's copy, and a
        # machine added here is not in what the transport registry can see yet.
        picker = ServicePicker(taken, self, machine=machine,
                               record=cfg.machine(machine), hub=self._hub())
        if picker.exec() != QDialog.Accepted:
            return
        for s in picker.picked:
            cfg.services.append(cfg_mod.Service(name=s["name"], label=s["display"],
                                                machine=machine))
        self._refresh_and_signal()

    def _remove(self):
        chosen = self._selected_services()
        if not chosen:
            QMessageBox.information(self, "Service Officer",
                                    "Select a service in the list first.")
            return
        names = [s.display() for s in chosen]
        msg = (f'Stop monitoring "{names[0]}"?' if len(names) == 1
               else f"Stop monitoring these {len(names)} services?")
        if QMessageBox.question(self, "Remove service", msg) != QMessageBox.Yes:
            return
        keep = [s for s in self.cfg().services if s not in chosen]
        self.cfg().services[:] = keep
        self._refresh_and_signal()

    def _set_category(self):
        """File the selected services under a heading, creating it if needed."""
        from PySide6.QtWidgets import QInputDialog
        chosen = self._selected_services()
        if not chosen:
            QMessageBox.information(self, "Service Officer",
                                    "Select one or more services in the list "
                                    "first.")
            return
        cfg = self.cfg()
        new_label = "New category…"
        options = ([cfg_mod.NO_CATEGORY_TITLE]
                   + [c.name for c in cfg.categories] + [new_label])
        # Preselect what they already share, so re-filing one service doesn't
        # start from the top of the list.
        current = {s.category or cfg_mod.NO_CATEGORY for s in chosen}
        start = 0
        if len(current) == 1:
            only = current.pop()
            start = options.index(only) if only in options else 0

        heading = ("Put this service under:" if len(chosen) == 1
                   else f"Put these {len(chosen)} services under:")
        pick, ok = QInputDialog.getItem(self, "Category", heading, options,
                                        start, False)
        if not ok or not pick:
            return
        if pick == new_label:
            name, ok = QInputDialog.getText(self, "New category",
                                            "Category name:")
            name = (name or "").strip()
            if not ok or not name:
                return
            if not cfg.category(name):
                cfg.categories.append(cfg_mod.Category(name=name))
            pick = name
        elif pick == cfg_mod.NO_CATEGORY_TITLE:
            pick = cfg_mod.NO_CATEGORY

        for svc in chosen:
            svc.category = pick
        self._refresh_and_signal()

    def _open_selected(self):
        chosen = self._selected_services()
        if len(chosen) != 1:
            QMessageBox.information(self, "Service Officer",
                                    "Select one service to open.")
            return
        self.detail.load(chosen[0], self.cfg().categories)
        self.stack.setCurrentWidget(self.detail)

    def _show_list(self):
        self.refresh()
        self.stack.setCurrentWidget(self.list_page)
