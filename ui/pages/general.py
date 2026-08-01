"""Settings: the handful of choices that belong to the app, not a service."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QMessageBox

from core import config as cfg_mod
from core import control
from core import i18n
from core.i18n import t
from core import local as local_mod
from core import version

from ..widgets import button as _button, label as _label
from .base import _Page, _fields


class GeneralPage(_Page):
    #: Something in the *config* changed — the notification switches, which are still there
    #: because the watchdog reads them too.
    changed = Signal()
    theme_changed = Signal(str)      # applied live, so you can see the choice
    #: A display choice changed and has been written to this computer's own file. Separate
    #: from `changed` because it is not part of the landscape and must never travel to a hub:
    #: a client that saved its theme there had it reverted on the next launch, and wrote one
    #: person's eyesight into a file every other client reads.
    mine_changed = Signal()
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
        self.language = QComboBox()
        for _code, name in i18n.LANGUAGES:
            self.language.addItem(name)
        self.language.setFixedWidth(150)
        self.language.currentIndexChanged.connect(self._set_language)
        # One grid. As two sentences the two combos started at different places, because
        # "Theme" and "Language" are different widths — six characters of difference deciding
        # where a control sits.
        self.root.addWidget(_fields(
            (t("Theme"), self.theme,
             t("System follows the Windows setting and switches with it.")),
            (t("Language"), self.language,
             t("Windows opened after this read the new language. This one keeps the words "
               "it was built with — its labels are set when it opens, and rewriting them "
               "under somebody mid-sentence is worse than reopening a window."))))
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
        # Where it is installed, and not where the data lives: that path is on the History
        # page, under the rows it holds, which is where somebody looking for it already is.
        # Copy build details still carries both, because a ticket wants both.
        where = _label(f"Installed in  {version.install_dir()}", "hint", wrap=True)
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
        self._remember(theme=value)
        self.theme_changed.emit(value)

    def _set_language(self, index):
        """Store it and read in it from now on.

        Applied to this process immediately, so the tray menu and the flyout — rebuilt on
        save — come back in the new language. An open panel keeps its own: every label on it
        was set when it was built, and Qt has no way to reword a window in place that does
        not amount to building it again.
        """
        code = i18n.LANGUAGES[index][0] if 0 <= index < len(i18n.LANGUAGES) else i18n.DEFAULT
        i18n.use(code)
        self._remember(language=code)

    def _set_auto(self, on):
        self._remember(auto_start=bool(on))

    def _remember(self, **choices) -> None:
        """Write a display choice to this computer's own file, at once.

        At once, not on Save: these are not part of the landscape, so there is nothing for
        Save to commit them with, and a person who picks a theme and closes the window has
        made a choice either way.
        """
        settings = local_mod.load()
        for field, value in choices.items():
            setattr(settings, field, value)
        if not local_mod.save(settings):
            QMessageBox.warning(self, "Service Officer",
                                "Could not store that on this computer.")
            return
        self.mine_changed.emit()

    def _set_note(self, attr, on):
        setattr(self.cfg().notifications, attr, on)
        self.changed.emit()

    def load_from(self, cfg):
        mine = local_mod.taste(cfg)
        for box, value in ((self.auto, mine.auto_start),
                           (self.on_crash, cfg.notifications.on_crash),
                           (self.on_recovery, cfg.notifications.on_recovery),
                           (self.on_give_up, cfg.notifications.on_give_up)):
            box.blockSignals(True)
            box.setChecked(value)
            box.blockSignals(False)
        self.language.blockSignals(True)
        codes = [code for code, _name in i18n.LANGUAGES]
        wanted = mine.language
        self.language.setCurrentIndex(codes.index(wanted) if wanted in codes else 0)
        self.language.blockSignals(False)
        self.theme.blockSignals(True)
        self.theme.setCurrentIndex(self._THEMES.index(
            mine.theme if mine.theme in self._THEMES else "system"))
        self.theme.blockSignals(False)
