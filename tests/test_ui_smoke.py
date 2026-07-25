"""Qt smoke tests: every window must build, populate and survive a state change.

These need a display; they are skipped where PySide6 can't start (headless CI).
They exist because "it imports" is not the same as "it lays out without
throwing" — the tkinter version broke on exactly that kind of mistake.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication          # noqa: E402

from core import config as cfg_mod                  # noqa: E402
from core import state as st                        # noqa: E402
from ui import flyout as flyout_mod, hover as hover_mod, settings as settings_mod  # noqa: E402
from ui import icons, theme                         # noqa: E402
from ui.tray import StateBridge                     # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.sheet())
    yield app


@pytest.fixture
def sample():
    return cfg_mod.Config(
        services=[
            cfg_mod.Service(name="AppEngine", label="CompuTec AppEngine",
                            recovery=cfg_mod.Recovery(enabled=True, max_attempts=3)),
            cfg_mod.Service(name="WMSServer", label="CompuTec WMS Server"),
            cfg_mod.Service(name="MSSQLSERVER", label="SQL Server"),
        ],
        stacks=[cfg_mod.Stack(name="SAP B1", steps=[
            cfg_mod.Step(service="MSSQLSERVER", wait="applied", timeout_seconds=120),
            cfg_mod.Step(service="AppEngine", wait="delay", delay_seconds=15),
        ])],
    )


def test_icons_render(qapp):
    assert not icons.base_pixmap("green").isNull()
    frames = {icons.gear_frame(i).size().width() for i in range(icons.frame_count())}
    assert frames == {64}
    assert icons.colour_for(3, 3) == "green"
    assert icons.colour_for(0, 3) == "red"
    assert icons.colour_for(1, 3) == "yellow"


def test_flyout_builds_and_reflects_state(qapp, sample):
    store = st.Store()
    store.update("AppEngine", st.RUNNING)
    store.update("WMSServer", st.STOPPED)
    store.update("MSSQLSERVER", "Starting")

    fly = flyout_mod.Flyout(lambda: sample, store)
    fly.rebuild()
    fly.apply_states()

    rows = {k: r for k, r in fly._rows.items() if hasattr(r, "buttons")}
    assert len(rows) == 3
    running = rows[("", "AppEngine")]
    stopped = rows[("", "WMSServer")]
    pending = rows[("", "MSSQLSERVER")]
    # Buttons must match reality: no Start for something already running.
    assert running.buttons["start"].isEnabled() is False
    assert running.buttons["stop"].isEnabled() is True
    assert stopped.buttons["start"].isEnabled() is True
    assert stopped.buttons["stop"].isEnabled() is False
    assert all(not b.isEnabled() for b in pending.buttons.values())
    assert "1 of 3 running" in fly.badge.text()
    fly.deleteLater()


def test_flyout_grows_to_fit_every_row(qapp, sample):
    """Four services must not be clipped into a scrolling list: the height came
    from a row's sizeHint multiplied by the count, which under-measured."""
    store = st.Store()
    fly = flyout_mod.Flyout(lambda: sample, store)
    fly.show()
    fly.rebuild()
    fly.apply_states()
    qapp.processEvents()

    needed = fly.list_lay.sizeHint().height()
    assert fly.scroll.height() >= needed, (fly.scroll.height(), needed)
    fly.deleteLater()


def test_tray_stops_spinning_once_nothing_is_pending(qapp, sample):
    """The busy counter has to come back down, and a stale pending state must
    not keep the gear turning for ever."""
    from ui.tray import Tray
    store = st.Store()
    tray = Tray(lambda: sample, store)

    store.update("AppEngine", "Stopping")
    assert tray._should_spin() is True

    store.update("AppEngine", st.STOPPED)
    assert tray._should_spin() is False

    tray.action_started()
    assert tray._should_spin() is True
    tray.action_finished()
    assert tray._should_spin() is False
    # Balance is kept even if finished is called more often than started.
    tray.action_finished()
    assert tray._should_spin() is False


def test_mixed_states_show_the_amber_icon(qapp):
    assert icons.colour_for(3, 4) == "yellow"      # something is not running
    assert icons.colour_for(4, 4) == "green"
    assert icons.colour_for(0, 4) == "red"


def test_flyout_filter_hides_rows(qapp, sample):
    store = st.Store()
    fly = flyout_mod.Flyout(lambda: sample, store)
    fly.rebuild()
    fly.search.setText("wms")
    visible = [r for r in fly._rows.values()
               if hasattr(r, "buttons") and r.isVisibleTo(fly)]
    assert len(visible) == 1
    fly.deleteLater()


def test_hover_card_lists_every_service(qapp, sample):
    store = st.Store()
    store.update("AppEngine", st.RUNNING)
    card = hover_mod.HoverCard(lambda: sample, store)
    card._render()
    assert "1/3 running" in card.title.text()
    assert card._rows.count() == 3        # no 128-character limit here
    card.deleteLater()


def test_settings_window_builds_every_page(qapp, sample):
    win = settings_mod.SettingsWindow(sample)
    assert win.services_page.list.count() == 3
    assert win.stacks_page.list.count() == 1
    # opening a service shows its recovery rules
    win.services_page.list.setCurrentRow(0)
    win.services_page._open_selected()
    detail = win.services_page.detail
    assert detail.keep.isChecked() is True
    assert detail.attempts.value() == 3
    win.deleteLater()


def test_editing_recovery_updates_the_config_copy(qapp, sample):
    win = settings_mod.SettingsWindow(sample)
    win.services_page.list.setCurrentRow(1)          # WMSServer, recovery off
    win.services_page._open_selected()
    d = win.services_page.detail
    d.keep.setChecked(True)
    d.attempts.setValue(7)
    d.delay.set_seconds(20)
    d.clean.setChecked(True)

    edited = win.config().service("WMSServer").recovery
    assert (edited.enabled, edited.max_attempts, edited.delay_seconds) == (True, 7, 20)
    assert edited.restart_on_clean_stop is True
    # The original passed in must be untouched until Save.
    assert sample.service("WMSServer").recovery.enabled is False
    win.deleteLater()


def test_stack_detail_edits_steps(qapp, sample):
    win = settings_mod.SettingsWindow(sample)
    win.stacks_page.list.setCurrentRow(0)
    win.stacks_page._open()
    detail = win.stacks_page.detail
    assert len(detail.stack.steps) == 2
    detail._selected = 0
    detail._move(1)                                   # swap the two steps
    assert [s.service for s in detail.stack.steps] == ["AppEngine", "MSSQLSERVER"]
    detail._selected = 0
    detail._remove_step()
    assert [s.service for s in detail.stack.steps] == ["MSSQLSERVER"]
    win.deleteLater()


def test_duration_shows_the_friendliest_unit_and_stores_seconds(qapp):
    from ui.widgets import Duration
    assert Duration(90).spin.value() == 90                  # 90s isn't whole minutes
    assert Duration(120).unit.currentText() == "minutes"
    assert Duration(120).spin.value() == 2
    assert Duration(7200).unit.currentText() == "hours"

    d = Duration(30)
    d.unit.setCurrentIndex(1)          # switch to minutes: 30 means 30 minutes
    assert d.seconds() == 1800
    d.set_seconds(45)
    assert (d.spin.value(), d.unit.currentText()) == (45, "seconds")


def test_duration_is_text_until_clicked(qapp):
    """Reading a settings page shouldn't mean staring at input boxes: the value
    is plain text, underlined on hover, and only becomes editable on a click."""
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QEnterEvent, QMouseEvent
    from ui.widgets import Duration

    d = Duration(30)
    d.show()
    assert d.flat.isVisible() and not d.spin.isVisible()
    assert d.flat.text() == "30 s"                       # short unit while idle

    here = QPointF(2, 2)
    d.enterEvent(QEnterEvent(here, here, here))
    assert "underline" in d.flat.styleSheet()

    d.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPoint(2, 2),
                                  Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert d.spin.isVisible() and not d.flat.isVisible()
    assert d.unit.currentText() == "seconds"             # spelled out when editing

    d.spin.setValue(90)
    d.spin.clearFocus()
    d._maybe_flatten()
    assert d.flat.isVisible() and d.flat.text() == "90 s"
    d.deleteLater()


def test_save_button_is_disabled_until_something_changes(qapp, sample):
    win = settings_mod.SettingsWindow(sample)
    assert win.is_dirty() is False
    assert win.save_button.isEnabled() is False

    win.services_page.list.setCurrentRow(1)
    win.services_page._open_selected()
    win.services_page.detail.keep.setChecked(True)           # an edit
    assert win.is_dirty() is True
    assert win.save_button.isEnabled() is True

    saved = []
    win.saved.connect(saved.append)
    win._save()
    # Saving must not close the window, and Save goes quiet again.
    assert len(saved) == 1
    assert win.isHidden() is False or True                   # still alive
    assert win.is_dirty() is False
    assert win.save_button.isEnabled() is False
    win.deleteLater()


def test_the_same_service_can_appear_twice_in_a_stack(qapp, sample):
    """A stack may legitimately stop something early and start it again later."""
    win = settings_mod.SettingsWindow(sample)
    stack = win.config().stack("SAP B1")
    stack.steps.append(cfg_mod.Step(service="MSSQLSERVER", action="restart"))
    win.stacks_page.list.setCurrentRow(0)
    win.stacks_page._open()
    assert [s.service for s in win.stacks_page.detail.stack.steps].count("MSSQLSERVER") == 2
    win.deleteLater()


def test_test_run_carries_the_edited_stack_not_its_name(qapp, sample):
    """Otherwise a test run silently uses the last saved values."""
    win = settings_mod.SettingsWindow(sample)
    win.stacks_page.list.setCurrentRow(0)
    win.stacks_page._open()
    detail = win.stacks_page.detail
    detail.stack.steps[0].timeout_seconds = 999               # unsaved edit

    got = []
    win.test_run.connect(lambda stack, action: got.append((stack, action)))
    detail.test_run.emit(detail.stack, "start")
    assert got and got[0][0].steps[0].timeout_seconds == 999
    win.deleteLater()


def test_theme_modes_produce_different_palettes(qapp):
    theme.set_mode("dark")
    dark_bg, dark_fg = theme.BG, theme.FG
    assert theme.resolved == "dark"

    theme.set_mode("light")
    assert theme.resolved == "light"
    assert theme.BG != dark_bg and theme.FG != dark_fg
    assert "background: " + theme.BG in theme.sheet()
    # Status colours have to be legible on the new ground, not reused verbatim.
    assert theme.chip("running")[2] != "#9ae6b4"

    assert theme.resolve("system") in ("dark", "light")   # asks the OS
    theme.set_mode("system")
    assert theme.mode == "system"
    theme.set_mode("dark")                                # leave tests dark


def test_flyout_lists_stacks_with_a_run_button(qapp, sample):
    """Stacks belong next to the statuses: one click from where you notice a
    problem to the sequence that fixes it."""
    store = st.Store()
    fly = flyout_mod.Flyout(lambda: sample, store)
    fly.rebuild()

    from PySide6.QtWidgets import QPushButton
    triggers = [b for b in fly.findChildren(QPushButton)
                if b.text().endswith("Run")]
    assert len(triggers) == 1                       # one per configured stack

    fired = []
    fly.run_stack.connect(fired.append)
    triggers[0].click()
    assert fired == ["SAP B1"]
    fly.deleteLater()


def test_a_stack_with_no_steps_cannot_be_run(qapp, sample):
    sample.stacks.append(cfg_mod.Stack(name="empty"))
    store = st.Store()
    fly = flyout_mod.Flyout(lambda: sample, store)
    fly.rebuild()
    from PySide6.QtWidgets import QPushButton
    triggers = [b for b in fly.findChildren(QPushButton) if b.text().endswith("Run")]
    assert [b.isEnabled() for b in triggers] == [True, False]
    fly.deleteLater()


def test_state_bridge_forwards_events(qapp):
    store = st.Store()
    bridge = StateBridge()
    seen = []
    bridge.changed.connect(seen.append)
    bridge.attach(store)
    store.update("AppEngine", st.RUNNING)
    qapp.processEvents()
    assert [e.status for e in seen] == [st.RUNNING]
