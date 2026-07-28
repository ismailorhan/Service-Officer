"""Settings: the handful of choices that belong to the app, not a service."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout

from core import config as cfg_mod
from core import control
from core import version

from ..widgets import button as _button, label as _label
from .base import _Page, _sentence


class GeneralPage(_Page):
    changed = Signal()
    theme_changed = Signal(str)      # applied live, so you can see the choice
    def __init__(self, cfg_ref):
        # Scrolls: appearance, startup, notifications and the about block leave
        # only fifty pixels spare, and every setting added eats into that.
        super().__init__("General", "How the app itself behaves.", scroll=True)
        self.cfg = cfg_ref

        self.root.addWidget(_label("APPEARANCE", "section"))
        self.root.addSpacing(9)
        self.theme = QComboBox()
        self.theme.addItems(["System", "Dark", "Light"])
        self.theme.setFixedWidth(150)
        self.theme.currentIndexChanged.connect(self._set_theme)
        self.root.addWidget(_sentence("Theme", self.theme))
        self.root.addWidget(_label(
            "System follows the Windows setting and switches with it.",
            "hint", wrap=True))
        self.root.addSpacing(24)

        self.root.addWidget(_label("STARTUP", "section"))
        self.root.addSpacing(9)
        self.auto = QCheckBox("Start automatically when Windows starts")
        self.auto.toggled.connect(self._set_auto)
        self.root.addWidget(self.auto)
        self.root.addSpacing(24)

        self.root.addWidget(_label("NOTIFICATIONS", "section"))
        self.root.addSpacing(9)
        self.on_crash = QCheckBox("A service stopped unexpectedly")
        self.on_recovery = QCheckBox("Recovery succeeded")
        self.on_give_up = QCheckBox("Recovery gave up")
        for box, attr in ((self.on_crash, "on_crash"),
                          (self.on_recovery, "on_recovery"),
                          (self.on_give_up, "on_give_up")):
            box.toggled.connect(lambda on, a=attr: self._set_note(a, on))
            self.root.addWidget(box)
            self.root.addSpacing(4)

        self.root.addSpacing(22)
        self.root.addWidget(_label("ABOUT", "section"))
        self.root.addSpacing(9)
        # Which build, and where it lives. "Version 2.0.0" alone doesn't answer
        # the question people actually ask on someone else's server — is this the
        # one I installed — so the commit and build time are here too.
        build = _label(version.full(), "hint", wrap=True)
        build.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.root.addWidget(build)
        self.root.addSpacing(4)
        where = _label(f"Installed in  {version.install_dir()}\n"
                       f"Settings and history in  {cfg_mod.APP_DIR}",
                       "hint", wrap=True)
        where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.root.addWidget(where)
        self.root.addSpacing(8)
        row = QHBoxLayout()
        row.addWidget(_button("Copy build details", "quiet", self._copy_build))
        row.addStretch(1)
        self.root.addLayout(row)
        self.root.addStretch(1)

    def _copy_build(self):
        """One click to paste into a ticket."""
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(
            f"Service Officer {version.full()}\n"
            f"Installed in: {version.install_dir()}\n"
            f"Data in: {cfg_mod.APP_DIR}\n"
            f"Machine: {control.host_name()} ({control.cached_address('') or '?'})")

    _THEMES = ("system", "dark", "light")

    def _set_theme(self, index):
        value = self._THEMES[index]
        self.cfg().theme = value
        self.changed.emit()
        self.theme_changed.emit(value)

    def _set_auto(self, on):
        self.cfg().auto_start = on
        self.changed.emit()

    def _set_note(self, attr, on):
        setattr(self.cfg().notifications, attr, on)
        self.changed.emit()

    def load_from(self, cfg):
        for box, value in ((self.auto, cfg.auto_start),
                           (self.on_crash, cfg.notifications.on_crash),
                           (self.on_recovery, cfg.notifications.on_recovery),
                           (self.on_give_up, cfg.notifications.on_give_up)):
            box.blockSignals(True)
            box.setChecked(value)
            box.blockSignals(False)
        self.theme.blockSignals(True)
        self.theme.setCurrentIndex(self._THEMES.index(
            cfg.theme if cfg.theme in self._THEMES else "system"))
        self.theme.blockSignals(False)
