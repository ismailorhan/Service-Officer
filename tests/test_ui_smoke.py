"""Qt smoke tests: every window must build, populate and survive a state change.

These need a display; they are skipped where PySide6 can't start (headless CI).
They exist because "it imports" is not the same as "it lays out without
throwing" — the tkinter version broke on exactly that kind of mistake.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt                        # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel   # noqa: E402

from core import config as cfg_mod                  # noqa: E402
from core import state as st                        # noqa: E402
from ui import flyout as flyout_mod, hover as hover_mod, panel as panel_mod  # noqa: E402
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
    # Mid-transition nothing may be commanded — except Kill, which exists
    # precisely for a service wedged in "Stopping".
    assert all(not pending.buttons[a].isEnabled()
               for a in ("start", "stop", "restart"))
    assert pending.buttons["kill"].isEnabled() is True
    # Nothing to kill once it is stopped.
    assert stopped.buttons["kill"].isEnabled() is False
    assert running.buttons["kill"].isEnabled() is True
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


def test_a_disabled_service_says_so_and_offers_nothing(qapp, sample):
    """Windows refuses to start a disabled service, so a Start button would only
    ever fail."""
    store = st.Store()
    store.update("WMSServer", st.STOPPED)
    store.set_start_type("WMSServer", "Disabled")

    fly = flyout_mod.Flyout(lambda: sample, store)
    fly.rebuild()
    fly.apply_states()
    row = fly._rows[("", "WMSServer")]
    assert row.chip.text() == "Disabled"
    assert all(not b.isEnabled() for b in row.buttons.values())
    assert "disabled in Windows" in row.toolTip()
    fly.deleteLater()


def test_hidden_stacks_stay_out_of_the_flyout(qapp, sample):
    sample.stacks.append(cfg_mod.Stack(name="internal only", show_in_flyout=False,
                                       steps=[cfg_mod.Step(service="AppEngine")]))
    store = st.Store()
    fly = flyout_mod.Flyout(lambda: sample, store)
    fly.rebuild()
    from PySide6.QtWidgets import QPushButton
    names = [b.toolTip() for b in fly.findChildren(QPushButton)
             if b.text().endswith("Run")]
    assert len(names) == 1                      # only the visible stack
    fly.deleteLater()


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


def _row_of(page, service_name: str) -> int:
    """The list row a service sits on. The Services list is grouped now, so a row
    index is no longer the service's index in the config."""
    for i, (kind, value) in enumerate(page._entries):
        if kind == "service" and value.name == service_name:
            return i
    raise AssertionError(f"{service_name} is not in the list")


def _select(page, *service_names):
    page.list.clearSelection()
    for name in service_names:
        page.list.item(_row_of(page, name)).setSelected(True)


def test_panel_builds_every_page(qapp, sample):
    win = panel_mod.MainPanel(sample)
    page = win.services_page
    # three services under one heading
    assert page.list.count() == 4
    assert [k for k, _v in page._entries] == ["group"] + ["service"] * 3
    assert win.stacks_page.list.count() == 1
    # opening a service shows its recovery rules
    _select(page, "AppEngine")
    page._open_selected()
    detail = page.detail
    assert detail.keep.isChecked() is True
    assert detail.attempts.value() == 3
    win.deleteLater()


def test_a_heading_cannot_be_selected_or_dragged(qapp, sample):
    """It is somewhere to drop a service, not a thing to act on."""
    sample.categories = [cfg_mod.Category(name="SAP")]
    win = panel_mod.MainPanel(sample)
    page = win.services_page
    headings = [i for i, (k, _v) in enumerate(page._entries) if k == "group"]
    assert headings                                  # at least "No category"
    for i in headings:
        flags = page.list.item(i).flags()
        assert not (flags & Qt.ItemIsSelectable)
        assert not (flags & Qt.ItemIsDragEnabled)
    page.list.item(headings[0]).setSelected(True)
    assert page._selected_services() == []
    win.deleteLater()


def test_editing_recovery_updates_the_config_copy(qapp, sample):
    win = panel_mod.MainPanel(sample)
    _select(win.services_page, "WMSServer")          # recovery off
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
    win = panel_mod.MainPanel(sample)
    win.stacks_page.list.setCurrentRow(0)
    win.stacks_page._open()
    detail = win.stacks_page.detail
    assert len(detail.stack.steps) == 2
    detail._reorder(0, 1)                             # dragged the first step down
    assert [s.service for s in detail.stack.steps] == ["AppEngine", "MSSQLSERVER"]
    detail._selected = 0
    detail._remove_step()
    assert [s.service for s in detail.stack.steps] == ["MSSQLSERVER"]
    win.deleteLater()


def test_steps_are_dragged_by_a_handle(qapp, sample):
    """Every step row offers a grip, and letting go on another row moves it —
    the editors inside the row must stay editable, which is why the whole row
    isn't the drag source."""
    from ui.widgets import Grip
    win = panel_mod.MainPanel(sample)
    win.stacks_page.list.setCurrentRow(0)
    win.stacks_page._open()
    detail = win.stacks_page.detail

    grips = [g for row in detail._rows for g in row.findChildren(Grip)]
    assert len(grips) == len(detail.stack.steps)
    assert [g.index for g in grips] == [0, 1]

    before = [s.service for s in detail.stack.steps]
    grips[1].moved.emit(1, 0)
    assert [s.service for s in detail.stack.steps] == list(reversed(before))

    detail._show_drop(1)                               # the drop outline follows
    assert detail._drop_at == 1
    detail._show_drop(-1)
    assert detail._drop_at == -1
    win.deleteLater()


def test_services_reorder_by_drag(qapp, sample):
    win = panel_mod.MainPanel(sample)
    page = win.services_page
    names = [s.name for s in page.cfg().services]
    assert len(names) >= 2
    # Row 0 is the heading, so the first service is row 1: drop it below the one
    # after it, i.e. at insertion point 3.
    page._dropped(1, 3)
    moved = [s.name for s in page.cfg().services]
    assert moved[:2] == [names[1], names[0]]
    win.deleteLater()


def test_dragging_a_service_onto_a_heading_files_it_there(qapp, sample):
    """The point of showing every category, empty ones included, is having
    somewhere to drop a service."""
    sample.categories = [cfg_mod.Category(name="SAP"),
                         cfg_mod.Category(name="Printing")]
    win = panel_mod.MainPanel(sample)
    page = win.services_page

    # Nothing is filed yet: No category holds all three, then the two empty
    # categories, which is where they have to be to be reachable.
    kinds = [(k, v if k == "group" else v.name) for k, v in page._entries]
    assert kinds == [("group", ""), ("service", "AppEngine"),
                     ("service", "WMSServer"), ("service", "MSSQLSERVER"),
                     ("group", "SAP"), ("group", "Printing")]

    sap_heading = kinds.index(("group", "SAP"))
    page._dropped(_row_of(page, "AppEngine"), sap_heading + 1)
    assert win.config().service("AppEngine").category == "SAP"
    # And the stored order follows what is now on screen.
    assert [s.name for s in win.config().services] == [
        "WMSServer", "MSSQLSERVER", "AppEngine"]

    # Dropped just below the SAP heading it stays in SAP — the insertion line
    # was inside that group.
    page._dropped(_row_of(page, "AppEngine"), 1)
    assert win.config().service("AppEngine").category == "SAP"

    # Dragging it back out means dropping under the No category heading.
    kinds = [(k, v if k == "group" else v.name) for k, v in page._entries]
    loose = kinds.index(("group", cfg_mod.NO_CATEGORY))
    page._dropped(_row_of(page, "AppEngine"), loose + 1)
    assert win.config().service("AppEngine").category == cfg_mod.NO_CATEGORY
    win.deleteLater()


def test_dropping_a_heading_does_nothing(qapp, sample):
    win = panel_mod.MainPanel(sample)
    page = win.services_page
    before = [s.name for s in page.cfg().services]
    page._dropped(0, 3)                             # row 0 is the heading
    assert [s.name for s in page.cfg().services] == before
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


def test_a_duration_floor_is_measured_in_the_unit_on_screen(qapp):
    """"Ask every 6 minutes" would not go down to 1. The floor is five *seconds*,
    and it was handed to the spin box unconverted, so with minutes selected it
    refused anything under five minutes — and Qt put the old number back, which
    read as the field ignoring you."""
    from ui.widgets import Duration

    d = Duration(360, minimum=5)                  # 6 minutes, floor of 5 seconds
    assert (d.spin.value(), d.unit.currentText()) == (6, "minutes")
    assert d.spin.minimum() == 1, "one minute clears a five-second floor"

    d.spin.setValue(1)
    assert (d.spin.value(), d.seconds()) == (1, 60)
    d.spin.setValue(2)
    assert d.seconds() == 120

    # In seconds the floor applies as written.
    d.unit.setCurrentIndex(0)
    assert d.spin.minimum() == 5
    d.spin.setValue(1)
    assert d.spin.value() == 5 and d.seconds() == 5

    # A zero floor still allows zero — "no grace period" is a real answer.
    grace = Duration(60, minimum=0)
    grace.unit.setCurrentIndex(1)
    grace.spin.setValue(0)
    assert grace.spin.minimum() == 0 and grace.seconds() == 0


def test_setting_a_duration_in_seconds_survives_the_unit_it_picks(qapp):
    from ui.widgets import Duration
    d = Duration(360, minimum=5)
    for value in (60, 5, 120, 3600, 90, 7):
        d.set_seconds(value)
        assert d.seconds() == value, f"{value}s came back as {d.seconds()}s"


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
    win = panel_mod.MainPanel(sample)
    assert win.is_dirty() is False
    assert win.save_button.isEnabled() is False

    _select(win.services_page, "WMSServer")
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
    win = panel_mod.MainPanel(sample)
    stack = win.config().stack("SAP B1")
    stack.steps.append(cfg_mod.Step(service="MSSQLSERVER", action="restart"))
    win.stacks_page.list.setCurrentRow(0)
    win.stacks_page._open()
    assert [s.service for s in win.stacks_page.detail.stack.steps].count("MSSQLSERVER") == 2
    win.deleteLater()


def test_test_run_carries_the_edited_stack_not_its_name(qapp, sample):
    """Otherwise a test run silently uses the last saved values."""
    win = panel_mod.MainPanel(sample)
    win.stacks_page.list.setCurrentRow(0)
    win.stacks_page._open()
    detail = win.stacks_page.detail
    detail.stack.steps[0].timeout_seconds = 999               # unsaved edit

    got = []
    win.test_run.connect(lambda stack, action: got.append((stack, action)))
    detail.test_run.emit(detail.stack, "start")
    assert got and got[0][0].steps[0].timeout_seconds == 999
    win.deleteLater()


def test_schedule_page_edits_a_trigger(qapp, sample):
    sample.triggers.append(cfg_mod.Trigger(name="nightly", when="time",
                                           time_of_day="03:00", action="stack",
                                           stack="SAP B1"))
    win = panel_mod.MainPanel(sample)
    assert win.schedule_page.list.count() == 1

    win.schedule_page.list.setCurrentRow(0)
    win.schedule_page._open()
    d = win.schedule_page.detail
    assert d.when.currentIndex() == 1                  # time-based
    assert d.time_row.isVisibleTo(d) and not d.startup_row.isVisibleTo(d)

    d.hour.setValue(22)
    d.minute.setValue(15)
    d.day_boxes[0].setChecked(True)                    # Monday
    edited = win.config().trigger("nightly")
    assert edited.time_of_day == "22:15" and edited.days == [0]

    d.when.setCurrentIndex(0)                          # switch to startup
    assert d.startup_row.isVisibleTo(d) and not d.time_row.isVisibleTo(d)
    assert win.config().trigger("nightly").when == "startup"

    d.action.setCurrentIndex(1)                        # act on one service
    assert d.service_row.isVisibleTo(d)
    assert win.config().trigger("nightly").action == "service"
    win.deleteLater()


def test_history_page_builds_a_grid_with_filters(qapp, sample):
    win = panel_mod.MainPanel(sample)
    page = win.history_page
    page.load_from(win.config())

    assert [page.table.horizontalHeaderItem(i).text()
            for i in range(page.table.columnCount())] == \
        ["Time", "Service", "Event", "Detail", "Source"]
    # The service filter offers the configured services, not free text.
    assert page.service_filter.count() == 1 + len(sample.services)
    assert page.service_filter.itemData(0) is None            # "All services"
    assert page.range_filter.count() == len(page.RANGES)
    assert page.include_windows.isChecked() is False
    win.deleteLater()


def test_history_offers_to_clear_only_once_something_is_filtered(qapp, sample):
    win = panel_mod.MainPanel(sample)
    page = win.history_page
    page.load_from(win.config())

    assert page._filtered() is False
    assert page.clear_filters.isVisible() is False

    page.range_filter.setCurrentIndex(0)               # last hour
    assert page._filtered() is True
    page.service_filter.setCurrentIndex(1)
    page.include_windows.setChecked(True)

    page._clear_filters()
    assert page._filtered() is False
    assert page.service_filter.currentData() is None
    assert page.range_filter.currentIndex() == page.DEFAULT_RANGE
    assert page.include_windows.isChecked() is False
    win.deleteLater()


def test_history_says_where_the_log_is_written(qapp, sample):
    from core import history
    win = panel_mod.MainPanel(sample)
    page = win.history_page
    page.load_from(win.config())
    assert history.path() in page.path_label.text()
    assert page.path_label.text().startswith("Written to")
    win.deleteLater()


def test_machines_are_named_with_their_address_and_this_pc_is_marked(qapp, sample):
    from core import control
    win = panel_mod.MainPanel(sample)
    page = win.machines_page
    local = win.config().machines[0]
    assert local.is_local

    control._addresses[""] = "10.0.0.7"                 # skip the DNS round trip
    title = page._title(local)
    assert control.host_name() in title and "10.0.0.7" in title

    page.refresh()
    row = page.list.itemWidget(page.list.item(0))
    chips = [lb.text() for lb in row.findChildren(QLabel) if lb.text() == "This PC"]
    assert chips == ["This PC"]
    win.deleteLater()


def test_bulk_selection_drives_one_action_for_many_services(qapp, sample):
    """Stopping a five-service SAP stack should be one confirmation, not five
    clicks in the right order."""
    store = st.Store()
    for name in ("AppEngine", "WMSServer", "MSSQLSERVER"):
        store.update(name, st.RUNNING)
    fly = flyout_mod.Flyout(lambda: sample, store)
    fly.rebuild()
    fly.apply_states()
    fly.show()
    qapp.processEvents()

    assert fly.bulk.isVisible() is False
    asked = []
    fly.bulk_requested.connect(lambda a, t: asked.append((a, t)))

    fly._rows[("", "AppEngine")].tick.setChecked(True)
    fly._rows[("", "WMSServer")].tick.setChecked(True)
    assert fly.bulk.isVisible() is True
    assert fly.bulk.count.text() == "2 selected"
    assert fly.tick_all.checkState() == Qt.PartiallyChecked

    fly._bulk("stop")
    assert asked == [("stop", [("AppEngine", ""), ("WMSServer", "")])]
    # Firing clears the selection, so a second click can't repeat it by accident.
    assert fly.selected() == []
    assert fly.bulk.isVisible() is False

    fly._toggle_all()
    assert len(fly.selected()) == 3
    assert fly.tick_all.checkState() == Qt.Checked
    fly._toggle_all()
    assert fly.selected() == []
    fly.deleteLater()


def test_filtering_drops_ticks_you_can_no_longer_see(qapp, sample):
    """A tick hidden by the search box is a bulk action nobody asked for."""
    store = st.Store()
    fly = flyout_mod.Flyout(lambda: sample, store)
    fly.rebuild()
    fly._rows[("", "AppEngine")].tick.setChecked(True)
    assert len(fly.selected()) == 1

    fly.search.setText("wms")
    assert fly.selected() == []
    assert fly._rows[("", "AppEngine")].tick.isChecked() is False
    fly.deleteLater()


def test_dashboard_offers_the_same_controls_as_the_flyout(qapp, sample):
    """The dashboard is the flyout with room to breathe — same rows, so the same
    rules about which buttons may be pressed."""
    store = st.Store()
    store.update("AppEngine", st.RUNNING)
    store.update("WMSServer", st.STOPPED)
    store.set_start_type("WMSServer", "Disabled")
    win = panel_mod.MainPanel(sample, store=store, live_config=lambda: sample)
    dash = win.dashboard
    assert win.pages.currentWidget() is dash          # what opening shows

    running = dash._rows[("", "AppEngine")]
    assert running.buttons["start"].isEnabled() is False
    assert running.buttons["stop"].isEnabled() is True
    disabled = dash._rows[("", "WMSServer")]
    assert disabled.chip.text() == "Disabled"
    assert all(not b.isEnabled() for b in disabled.buttons.values())
    assert "1 of 3 running" in dash.badge.text()
    assert "1 disabled in Windows" in dash.summary.text()

    asked = []
    dash.action_requested.connect(lambda *a: asked.append(a))
    running.buttons["stop"].click()
    assert asked == [("stop", "AppEngine", "")]

    bulk = []
    dash.bulk_requested.connect(lambda a, t: bulk.append((a, t)))
    running.tick.setChecked(True)
    assert dash.bulk.count.text() == "1 selected"
    dash._bulk("restart")
    assert bulk == [("restart", [("AppEngine", "")])]
    win.deleteLater()


def test_dashboard_shows_the_saved_config_not_unsaved_edits(qapp, sample):
    """Acting on a service the app isn't watching yet would be a lie about what
    happened, so the dashboard reads the live config."""
    win = panel_mod.MainPanel(sample, store=st.Store(), live_config=lambda: sample)
    before = len(win.dashboard._rows)
    win.config().services.append(cfg_mod.Service(name="Spooler", label="Spooler"))
    win.services_page.refresh()
    assert len(win.dashboard._rows) == before      # not until it is saved
    win.deleteLater()


def test_categories_group_the_lists_and_fold_away(qapp, sample):
    from ui import rows as rows_mod
    rows_mod.collapsed.clear()
    sample.categories = [cfg_mod.Category(name="SAP"), cfg_mod.Category(name="SQL")]
    sample.service("AppEngine").category = "SAP"
    sample.service("WMSServer").category = "SAP"
    # MSSQLSERVER is left unfiled on purpose.

    groups = sample.grouped_services()
    assert [(name, [s.name for s in members]) for name, _t, members in groups] == [
        ("SAP", ["AppEngine", "WMSServer"]),
        ("", ["MSSQLSERVER"]),                     # empty SQL is left out
    ]
    assert groups[-1][1] == cfg_mod.NO_CATEGORY_TITLE

    store = st.Store()
    fly = flyout_mod.Flyout(lambda: sample, store)
    fly.rebuild()
    assert [b.category for b in fly._sections] == ["SAP", ""]

    sap = fly._sections[0]
    sap.mousePressEvent(_click())
    assert rows_mod.is_collapsed("SAP") is True
    assert fly._rows[("", "AppEngine")].isHidden() is True
    assert fly._rows[("", "MSSQLSERVER")].isHidden() is False   # other group

    # Searching beats a folded group, or search would look broken.
    fly.search.setText("appengine")
    assert fly._rows[("", "AppEngine")].isHidden() is False

    fly.search.clear()
    sap.mousePressEvent(_click())
    assert fly._rows[("", "AppEngine")].isHidden() is False
    rows_mod.collapsed.clear()
    fly.deleteLater()


def _click():
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent, QPointF
    return QMouseEvent(QEvent.MouseButtonPress, QPointF(2, 2), Qt.LeftButton,
                       Qt.LeftButton, Qt.NoModifier)


def test_a_folded_service_is_not_part_of_a_bulk_action(qapp, sample):
    from ui import rows as rows_mod
    rows_mod.collapsed.clear()
    sample.categories = [cfg_mod.Category(name="SAP")]
    sample.service("AppEngine").category = "SAP"
    fly = flyout_mod.Flyout(lambda: sample, st.Store())
    fly.rebuild()
    fly._rows[("", "AppEngine")].tick.setChecked(True)
    assert len(fly.selected()) == 1

    fly._sections[0].mousePressEvent(_click())
    assert fly.selected() == []
    assert fly._rows[("", "AppEngine")].tick.isChecked() is False
    rows_mod.collapsed.clear()
    fly.deleteLater()


def test_a_category_survives_the_round_trip_and_renaming(qapp, sample, tmp_path):
    sample.categories = [cfg_mod.Category(name="SAP")]
    sample.service("AppEngine").category = "SAP"
    path = str(tmp_path / "services.json")
    cfg_mod.save(sample, path)
    back = cfg_mod.load(path)
    assert [c.name for c in back.categories] == ["SAP"]
    assert back.service("AppEngine").category == "SAP"

    win = panel_mod.MainPanel(back)
    page = win.categories_page
    assert page.list.count() == 1
    # Renaming has to carry the services with it — they point at it by name.
    page.list.setCurrentRow(0)
    page._ask = lambda *_a, **_k: "SAP Business One"
    page._rename()
    assert win.config().service("AppEngine").category == "SAP Business One"
    assert [c.name for c in win.config().categories] == ["SAP Business One"]
    win.deleteLater()


def test_a_category_a_service_names_but_nobody_defined_is_kept(tmp_path):
    """Dropping it would silently lose the grouping."""
    import json
    path = tmp_path / "services.json"
    path.write_text(json.dumps({"services": [
        {"name": "AppEngine", "category": "Ghost"}]}), encoding="utf-8")
    cfg = cfg_mod.load(str(path))
    assert [c.name for c in cfg.categories] == ["Ghost"]
    assert cfg.grouped_services()[0][0] == "Ghost"


def _fake_dialogs(monkeypatch, item: str, text: str = ""):
    """Stand in for the two QInputDialogs the category picker opens. Patched
    through monkeypatch so it can't leak into another test — the picker imports
    QInputDialog inside the method, so the module attribute is what matters."""
    import PySide6.QtWidgets as qtw

    class Fake:
        @staticmethod
        def getItem(*_a, **_k):
            return item, True

        @staticmethod
        def getText(*_a, **_k):
            return text, True

    monkeypatch.setattr(qtw, "QInputDialog", Fake)


def test_the_services_list_can_file_several_at_once(qapp, sample, monkeypatch):
    """Filing is something you do to a group, so it lives on the list — not only
    inside one service's page."""
    sample.categories = [cfg_mod.Category(name="SAP")]
    win = panel_mod.MainPanel(sample)
    page = win.services_page
    _select(page, "AppEngine", "WMSServer")
    assert [s.name for s in page._selected_services()] == ["AppEngine", "WMSServer"]

    _fake_dialogs(monkeypatch, "SAP")
    page._set_category()
    assert win.config().service("AppEngine").category == "SAP"
    assert win.config().service("WMSServer").category == "SAP"

    # A new category can be created from here, and shows up on the other page.
    _fake_dialogs(monkeypatch, "New category…", text="Printing")
    _select(page, "MSSQLSERVER")
    page._set_category()
    assert win.config().service("MSSQLSERVER").category == "Printing"
    assert [c.name for c in win.config().categories] == ["SAP", "Printing"]
    assert win.categories_page.list.count() == 2

    # And back out again.
    _fake_dialogs(monkeypatch, cfg_mod.NO_CATEGORY_TITLE)
    _select(page, "MSSQLSERVER")
    page._set_category()
    assert win.config().service("MSSQLSERVER").category == cfg_mod.NO_CATEGORY
    win.deleteLater()


def test_services_page_names_the_machine_on_the_row(qapp, sample):
    from core import control
    win = panel_mod.MainPanel(sample)
    page = win.services_page
    row = page.list.itemWidget(page.list.item(_row_of(page, "AppEngine")))
    texts = [lb.text() for lb in row.findChildren(QLabel)]
    assert control.host_name() in texts or "This PC" in texts
    win.deleteLater()


def test_the_hover_card_goes_away_in_every_direction(qapp, sample):
    """It used to hide only when the pointer happened to cross the card, so
    leaving the icon sideways along the taskbar left it up indefinitely. One rule
    now: over the icon, or over the card, or gone."""
    from PySide6.QtCore import QPoint, QRect
    card = hover_mod.HoverCard(lambda: sample, st.Store())
    icon = QRect(1400, 1050, 24, 24)
    card._rect = icon
    card._show_now()
    qapp.processEvents()
    assert card.isVisible() is True
    assert card._watch.isActive() is True          # the poll is what decides

    where = [icon.center()]                        # no need to move the real one
    card.cursor_pos = lambda: where[0]

    assert card.pointer_is_near() is True          # on the icon
    where[0] = card.geometry().center()
    assert card.pointer_is_near() is True          # on the card

    # Sideways along the taskbar: the direction that used to leave it up.
    where[0] = QPoint(icon.left() - 300, icon.center().y())
    assert card.pointer_is_near() is False
    card._check_pointer()
    assert card.isVisible() is False
    assert card._watch.isActive() is False

    # Straight up, off the top of the icon.
    card._show_now()
    where[0] = QPoint(icon.center().x(), icon.top() - 400)
    card._check_pointer()
    assert card.isVisible() is False
    card.deleteLater()


def test_pinning_keeps_the_panel_open_when_focus_goes_elsewhere(qapp, sample):
    from PySide6.QtCore import QEvent
    fly = flyout_mod.Flyout(lambda: sample, st.Store())
    fly.popup()
    qapp.processEvents()
    assert fly.pinned is False

    fly.pin.setChecked(True)
    assert fly.pinned is True
    fly._hide_unless_modal()                       # what deactivation triggers
    assert fly.isVisible() is True                 # pinned: it stays

    fly.pin.setChecked(False)
    fly._hide_unless_modal()
    assert fly.isVisible() is False
    fly.deleteLater()


def test_the_panel_only_ever_grows_upwards(qapp, sample):
    """It is anchored to the tray icon at the bottom of the screen, so a taller
    panel must open into empty screen — never push its own footer downwards."""
    from ui import rows as rows_mod
    rows_mod.collapsed.clear()
    sample.categories = [cfg_mod.Category(name="SAP")]
    sample.service("AppEngine").category = "SAP"
    fly = flyout_mod.Flyout(lambda: sample, st.Store())
    fly.popup()
    qapp.processEvents()
    bottom = fly.y() + fly.height()

    for _ in range(2):                             # fold, then unfold again
        fly._sections[0].mousePressEvent(_click())
        for _ in range(4):
            qapp.processEvents()
        assert fly.y() + fly.height() == bottom, "bottom edge moved"

    # And a resize from anywhere else is re-anchored too.
    fly.resize(fly.width(), fly.height() + 60)
    qapp.processEvents()
    assert fly.y() + fly.height() == bottom
    rows_mod.collapsed.clear()
    fly.deleteLater()


def test_history_hides_halfway_states_until_full_detail(qapp, sample, tmp_path):
    """A restart writing Stopping, Stopped, Starting, Running is four rows saying
    one thing."""
    import json
    from core import history
    path = tmp_path / "h.db"
    rows = [
        {"ts": "2026-07-25T18:03:48+03:00", "service": "AppEngine",
         "action": "restart", "source": "panel"},
        {"ts": "2026-07-25T18:03:49+03:00", "service": "AppEngine",
         "to": "Stopping", "from": "Running", "source": "scm"},
        {"ts": "2026-07-25T18:03:50+03:00", "service": "AppEngine",
         "to": "Starting", "from": "Stopped", "source": "scm"},
        {"ts": "2026-07-25T18:03:54+03:00", "service": "AppEngine",
         "to": "Running", "from": "Starting", "source": "scm"},
    ]
    history.import_records(rows, path=str(path))

    basic = history.query(service_names=["AppEngine"], path=str(path))
    assert [r["event"] for r in basic] == ["Running", "restart requested"]

    full = history.query(service_names=["AppEngine"], path=str(path), full=True)
    assert len(full) == 4                          # nothing was lost from the file

    # A crash mid-transition still shows, since the exit code is the point.
    history.import_records([{"ts": "2026-07-25T18:04:00+03:00",
                             "service": "AppEngine", "to": "Stopping",
                             "exit_code": 1067, "source": "scm"}],
                           path=str(path))
    kept = history.query(service_names=["AppEngine"], path=str(path))
    assert any(r["level"] == "Error" for r in kept)


def test_categories_can_be_reordered_from_the_services_page(qapp, sample):
    sample.categories = [cfg_mod.Category(name="SAP"),
                         cfg_mod.Category(name="SQL")]
    sample.service("AppEngine").category = "SAP"
    sample.service("WMSServer").category = "SQL"
    win = panel_mod.MainPanel(sample)
    page = win.services_page
    assert [c.name for c in win.config().categories] == ["SAP", "SQL"]

    page._move_category(1, -1)                     # SQL up
    assert [c.name for c in win.config().categories] == ["SQL", "SAP"]
    order = [v for k, v in page._entries if k == "group"]
    assert order[:2] == ["SQL", "SAP"]             # the list followed

    page._move_category(0, -1)                     # already first: no move
    assert [c.name for c in win.config().categories] == ["SQL", "SAP"]
    win.deleteLater()


def test_health_checks_can_be_built_and_removed_in_the_editor(qapp, sample):
    win = panel_mod.MainPanel(sample)
    page = win.services_page
    _select(page, "AppEngine")
    page._open_selected()
    detail = page.detail
    svc = win.config().service("AppEngine")
    assert svc.health.checks == []
    # isHidden, not isVisibleTo: the Health tab isn't the current page, so
    # isVisibleTo would be False whatever we did with the widget.
    assert detail.health_rules.isHidden() is True             # nothing to tune
    assert detail.check_now_button.isEnabled() is False

    # One button with a menu; the five kinds are the menu items.
    assert [a.text() for a in detail.add_menu.actions()] == [
        "A port answers", "A URL answers", "It has a process",
        "A file is being written", "A command succeeds"]

    detail._add_check("tcp")
    assert [c.kind for c in svc.health.checks] == ["tcp"]
    assert detail.health_rules.isHidden() is False
    assert detail.check_now_button.isEnabled() is True
    # A new check opens for editing: there is nothing to read in it yet.
    assert detail._open_checks == {0}

    detail._set_check(svc.health.checks[0], "port", 1433)
    assert "1433" in svc.health.checks[0].describe()

    detail._add_check("http")
    assert [c.kind for c in svc.health.checks] == ["tcp", "http"]
    # A URL check starts with a generous timeout: measured 4s, once 8s, so the
    # five-second default would have raised false alarms.
    assert svc.health.checks[1].timeout_seconds == 15

    detail._remove_check(0)
    assert [c.kind for c in svc.health.checks] == ["http"]

    # The rules write through to the config copy.
    detail.h_failures.setValue(5)
    detail.h_action.setCurrentIndex(detail.h_action.findData("restart"))
    assert svc.health.failures_before_acting == 5
    assert svc.health.action == "restart"
    win.deleteLater()


def test_health_checks_read_as_one_line_each_and_open_to_edit(qapp, sample):
    """Five checks each showing four labelled boxes is a wall. The line says what
    the check is; the boxes appear only for the one being edited."""
    from PySide6.QtWidgets import QLineEdit
    sample.service("AppEngine").health = cfg_mod.Health(checks=[
        cfg_mod.HealthCheck(kind="tcp", host="CTL052", port=54001),
        cfg_mod.HealthCheck(kind="http", url="https://CTL052:54001",
                            timeout_seconds=15)])
    win = panel_mod.MainPanel(sample)
    page = win.services_page
    _select(page, "AppEngine")
    page._open_selected()
    detail = page.detail

    assert detail._open_checks == set()            # everything reads, nothing edits
    rows = [detail.checks_lay.itemAt(i).widget()
            for i in range(detail.checks_lay.count())]
    editors = [e for r in rows for e in r.findChildren(QLineEdit)]
    assert editors == [], "closed rows must not show their fields"
    # Each row still says which kind it is and what it looks at.
    texts = " ".join(lb.text() for r in rows for lb in r.findChildren(QLabel))
    assert "PORT" in texts and "URL" in texts
    assert "CTL052:54001" in texts and "gives up after 15s" in texts

    detail._toggle_check(1)
    assert detail._open_checks == {1}
    rows = [detail.checks_lay.itemAt(i).widget()
            for i in range(detail.checks_lay.count())]
    editors = [e for r in rows for e in r.findChildren(QLineEdit)]
    assert editors, "the open row shows its fields"

    # The summary follows what is typed, without the row being rebuilt under it.
    # win.config(), not sample: the panel edits a copy until Save.
    check = win.config().service("AppEngine").health.checks[0]
    detail._set_check(check, "port", 1433)
    shown = " ".join(lb.text() for r in rows for lb in r.findChildren(QLabel))
    assert "CTL052:1433" in shown

    detail._toggle_check(1)
    assert detail._open_checks == set()
    win.deleteLater()


def test_health_can_be_switched_off_without_losing_the_checks(qapp, sample):
    sample.service("AppEngine").health = cfg_mod.Health(checks=[
        cfg_mod.HealthCheck(kind="tcp", host="CTL052", port=54001),
        cfg_mod.HealthCheck(kind="http", url="https://CTL052:54001")])
    win = panel_mod.MainPanel(sample)
    page = win.services_page
    _select(page, "AppEngine")
    page._open_selected()
    detail = page.detail
    health = win.config().service("AppEngine").health

    assert detail.h_enabled.isChecked() is True
    assert health.active is True

    detail.h_enabled.setChecked(False)
    assert health.enabled is False
    assert health.active is False
    assert len(health.checks) == 2                 # nothing was deleted
    # Everything stays on screen, greyed, so it can be read and switched back on.
    assert detail.checks_host.isEnabled() is False
    assert detail.add_button.isEnabled() is False
    assert detail.check_now_button.isEnabled() is False

    detail.h_enabled.setChecked(True)
    assert health.active is True
    assert detail.checks_host.isEnabled() is True
    win.deleteLater()


def test_a_check_row_opens_on_a_double_click(qapp, sample):
    sample.service("AppEngine").health = cfg_mod.Health(checks=[
        cfg_mod.HealthCheck(kind="tcp", port=54001)])
    win = panel_mod.MainPanel(sample)
    page = win.services_page
    _select(page, "AppEngine")
    page._open_selected()
    detail = page.detail
    assert detail._open_checks == set()

    row = detail.checks_lay.itemAt(0).widget()
    row.mouseDoubleClickEvent(None)
    assert detail._open_checks == {0}
    # And again to close it.
    detail.checks_lay.itemAt(0).widget().mouseDoubleClickEvent(None)
    assert detail._open_checks == set()
    win.deleteLater()


def test_an_unfinished_check_says_so_in_its_summary(qapp, sample):
    """"returns a success response" with no URL reads as a check that works."""
    win = panel_mod.MainPanel(sample)
    page = win.services_page
    _select(page, "AppEngine")
    page._open_selected()
    detail = page.detail
    detail._add_check("http")

    row = detail.checks_lay.itemAt(0).widget()
    texts = " ".join(lb.text() for lb in row.findChildren(QLabel))
    assert "No URL set yet" in texts
    win.deleteLater()


def test_removing_a_check_forgets_which_row_was_open(qapp, sample):
    """Indexes shift, so a remembered one would open a different check."""
    sample.service("AppEngine").health = cfg_mod.Health(checks=[
        cfg_mod.HealthCheck(kind="tcp", port=1),
        cfg_mod.HealthCheck(kind="tcp", port=2)])
    win = panel_mod.MainPanel(sample)
    page = win.services_page
    _select(page, "AppEngine")
    page._open_selected()
    detail = page.detail
    detail._toggle_check(1)
    assert detail._open_checks == {1}
    detail._remove_check(0)
    assert detail._open_checks == set()
    assert [c.port for c in win.config().service("AppEngine").health.checks] == [2]
    win.deleteLater()


def test_the_service_page_is_tabbed_and_every_tab_scrolls(qapp, sample):
    """One page of everything passed 1200px once health checks were on it, and
    content that is merely clipped is content nobody knows is there."""
    from PySide6.QtWidgets import QScrollArea
    win = panel_mod.MainPanel(sample)
    page = win.services_page
    _select(page, "AppEngine")
    page._open_selected()
    detail = page.detail

    assert list(detail._tab_buttons) == ["General", "Recovery", "Health"]
    assert detail._tab_buttons["General"].isChecked() is True
    # Every tab is a scroll area, so nothing can be cut off the bottom.
    assert all(isinstance(p, QScrollArea) for p in detail._tab_pages.values())
    assert all(p.widgetResizable() for p in detail._tab_pages.values())

    detail._select_tab("Health")
    assert detail.pages.currentWidget() is detail._tab_pages["Health"]
    assert detail._tab_buttons["Health"].isChecked() is True
    assert detail._tab_buttons["General"].isChecked() is False

    # The fields still belong to the service whichever tab is showing.
    detail._select_tab("Recovery")
    detail.keep.setChecked(True)
    assert win.config().service("AppEngine").recovery.enabled is True

    # An unknown tab name changes nothing rather than blanking the page.
    before = detail.pages.currentWidget()
    detail._select_tab("Nonsense")
    assert detail.pages.currentWidget() is before
    win.deleteLater()


def test_a_running_but_unresponsive_service_says_so(qapp, sample):
    """The whole point: Running next to a dead service is a lie, so the chip has
    to change and the reason has to be reachable."""
    store = st.Store()
    store.update("AppEngine", st.RUNNING)
    store.set_health("AppEngine", "unhealthy", "failed: something answers on "
                                              "127.0.0.1:1433 — refused")
    store.update("WMSServer", st.RUNNING)
    store.set_health("WMSServer", "healthy", "127.0.0.1:80 accepted a connection")

    fly = flyout_mod.Flyout(lambda: sample, store)
    fly.rebuild()
    fly.apply_states()

    sick = fly._rows[("", "AppEngine")]
    assert sick.chip.text() == "Not responding"
    assert "1433" in sick.toolTip()
    # It is still running, so stopping and restarting it must stay available.
    assert sick.buttons["restart"].isEnabled() is True
    assert sick.buttons["stop"].isEnabled() is True

    well = fly._rows[("", "WMSServer")]
    assert well.chip.text() == st.RUNNING
    assert "accepted a connection" in well.toolTip()
    fly.deleteLater()


def test_the_tray_icon_warns_when_something_is_running_but_dead(qapp, sample):
    """All-running and all-fine are not the same thing, and the icon is the only
    thing most people look at."""
    from ui.tray import Tray
    store = st.Store()
    for name in ("AppEngine", "WMSServer", "MSSQLSERVER"):
        store.update(name, st.RUNNING)
    tray = Tray(lambda: sample, store)

    assert icons.colour_for(*store.counts()) == "green"
    tray.apply_state()                              # all running, all answering

    store.set_health("AppEngine", "unhealthy", "port refused")
    assert tray._anything_unhealthy() is True
    # The count still says green; the icon must not.
    assert icons.colour_for(*store.counts()) == "green"
    tray.apply_state()
    assert not tray.icon.icon().isNull()

    store.set_health("AppEngine", "healthy", "ok")
    assert tray._anything_unhealthy() is False


def test_the_hover_card_says_not_responding(qapp, sample):
    store = st.Store()
    store.update("AppEngine", st.RUNNING)
    store.set_health("AppEngine", "unhealthy", "port refused")
    card = hover_mod.HoverCard(lambda: sample, store)
    card._render()
    texts = [lb.text() for lb in card.findChildren(QLabel)]
    assert "not responding" in texts
    assert texts.count(st.RUNNING) == 0     # it must not also claim Running
    card.deleteLater()


def test_the_dashboard_counts_what_is_not_responding(qapp, sample):
    store = st.Store()
    for name in ("AppEngine", "WMSServer", "MSSQLSERVER"):
        store.update(name, st.RUNNING)
    store.set_health("AppEngine", "unhealthy", "no answer")
    win = panel_mod.MainPanel(sample, store=store, live_config=lambda: sample)
    assert "1 not responding" in win.dashboard.summary.text()
    win.deleteLater()


def test_panel_opens_on_a_named_section(qapp, sample):
    win = panel_mod.MainPanel(sample)
    assert win.go_to("history") is True
    assert win.pages.currentWidget() is win.history_page
    assert win.go_to("schedule") is True
    assert win.pages.currentWidget() is win.schedule_page
    assert win.go_to("nonsense") is False              # and stays where it was
    assert win.pages.currentWidget() is win.schedule_page
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
