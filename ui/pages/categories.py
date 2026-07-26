"""Categories: the headings the flyout and dashboard group services under."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QListWidgetItem, QMessageBox

from core import config as cfg_mod

from ..widgets import ReorderList, button as _button, label as _label
from .base import _ListRow, _Page


class CategoriesPage(_Page):
    """Headings the service lists are grouped under.

    Grouping only — nothing acts on a category — so this page is a list of names
    and their order, which is the order the groups appear in.
    """

    changed = Signal()

    def __init__(self, cfg_ref):
        super().__init__("Categories",
                         "Group your services under headings — SAP, SQL, "
                         "printing — so the dashboard and the tray panel can "
                         "fold away the ones you aren't looking at. Drag to "
                         "change the order the groups appear in.")
        self.cfg = cfg_ref

        self.list = ReorderList()
        self.list.reordered.connect(self._reorder)
        self.list.itemDoubleClicked.connect(lambda _i: self._rename())
        self.root.addWidget(self.list, 1)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(_button("Add category…", "primary", self._add))
        bar.addWidget(_button("Rename…", None, self._rename))
        bar.addWidget(_button("Remove", "danger", self._remove))
        bar.addStretch(1)
        self.root.addSpacing(14)
        self.root.addLayout(bar)
        self.root.addSpacing(10)
        self.root.addWidget(_label(
            "Services you haven't filed anywhere are listed together under "
            f"“{cfg_mod.NO_CATEGORY_TITLE}”, so nothing goes missing.",
            "hint", wrap=True))
        self.refresh()

    def refresh(self):
        keep = self.list.currentRow()
        self.list.clear()
        cfg = self.cfg()
        for cat in cfg.categories:
            members = [s for s in cfg.services if (s.category or "") == cat.name]
            names = ", ".join(s.display() for s in members[:4])
            if len(members) > 4:
                names += f", and {len(members) - 4} more"
            item = QListWidgetItem()
            widget = _ListRow(cat.name,
                              names or "nothing filed here yet",
                              tag=f"{len(members)}", tag_category="none")
            item.setSizeHint(widget.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, widget)
        if 0 <= keep < self.list.count():
            self.list.setCurrentRow(keep)

    def _ask(self, title: str, initial: str = "") -> str:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, title, "Category name:",
                                        text=initial)
        return (name or "").strip() if ok else ""

    def _add(self):
        name = self._ask("Add category")
        if not name:
            return
        if self.cfg().category(name):
            QMessageBox.information(self, "Service Officer",
                                    "That category already exists.")
            return
        self.cfg().categories.append(cfg_mod.Category(name=name))
        self.refresh()
        self.changed.emit()

    def _rename(self):
        row = self.list.currentRow()
        if row < 0:
            return
        cat = self.cfg().categories[row]
        name = self._ask("Rename category", cat.name)
        if not name or name == cat.name:
            return
        if self.cfg().category(name):
            QMessageBox.information(self, "Service Officer",
                                    "That category already exists.")
            return
        # Services point at the category by name, so they have to come along.
        for svc in self.cfg().services:
            if (svc.category or "") == cat.name:
                svc.category = name
        cat.name = name
        self.refresh()
        self.changed.emit()

    def _remove(self):
        row = self.list.currentRow()
        if row < 0:
            return
        cfg = self.cfg()
        cat = cfg.categories[row]
        members = [s for s in cfg.services if (s.category or "") == cat.name]
        message = f'Remove the category "{cat.name}"?'
        if members:
            message += (f"\n\nIts {len(members)} service(s) stay, listed under "
                        f"“{cfg_mod.NO_CATEGORY_TITLE}”.")
        if QMessageBox.question(self, "Remove category",
                                message) != QMessageBox.Yes:
            return
        for svc in members:
            svc.category = cfg_mod.NO_CATEGORY
        del cfg.categories[row]
        self.refresh()
        self.changed.emit()

    def _reorder(self, source, target):
        cats = self.cfg().categories
        if not (0 <= source < len(cats) and 0 <= target < len(cats)):
            return
        cats.insert(target, cats.pop(source))
        self.refresh()
        self.changed.emit()
        self.list.setCurrentRow(target)
