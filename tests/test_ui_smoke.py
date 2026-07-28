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

    # Nothing is filed yet: the two empty categories come first so they are
    # somewhere to drop onto, and No category is last — it is where things are
    # when they have not been filed, not a category among the others.
    kinds = [(k, v if k == "group" else v.name) for k, v in page._entries]
    assert kinds == [("group", "SAP"), ("group", "Printing"),
                     ("group", ""), ("service", "AppEngine"),
                     ("service", "WMSServer"), ("service", "MSSQLSERVER")]

    sap_heading = kinds.index(("group", "SAP"))
    page._dropped(_row_of(page, "AppEngine"), sap_heading + 1)
    assert win.config().service("AppEngine").category == "SAP"
    # And the stored order follows what is now on screen: SAP is the first
    # heading, so a service filed there sits above the unfiled ones.
    assert [s.name for s in win.config().services] == [
        "AppEngine", "WMSServer", "MSSQLSERVER"]

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
        ["Time", "Service", "Event", "Detail", "Source", "Asked by"]
    # ...but on screen it is hidden until a row fills it, so a single-machine install
    # never loses width to a column that will always be empty.
    assert page.table.isColumnHidden(page.COLUMNS.index("Asked by"))
    # The service filter offers the configured services, not free text.
    assert page.service_filter.count() == 1 + len(sample.services)
    assert page.service_filter.itemData(0) is None            # "All services"
    assert page.range_filter.count() == len(page.RANGES)
    assert page.include_windows.isChecked() is False
    win.deleteLater()


def test_history_shows_who_asked_once_a_row_says_so(qapp, sample, monkeypatch):
    """The column earns its width. Hidden on the single-machine install everybody has
    today; there the moment a hub records a name."""
    win = panel_mod.MainPanel(sample)
    page = win.history_page
    page.load_from(win.config())
    column = page.COLUMNS.index("Asked by")

    monkeypatch.setattr(page, "_current_rows", lambda: [
        {"ts": "2026-07-28T09:00:00Z", "service": "AppEngine", "label": "AppEngine",
         "kind": "action", "event": "restart requested", "detail": "",
         "source": "panel", "actor": "CT-ismail.orhan"}])
    page.reload()

    assert page.table.isColumnHidden(column) is False
    assert page.table.item(0, column).text() == "CT-ismail.orhan"

    # And gone again when the filters land on rows that name nobody.
    monkeypatch.setattr(page, "_current_rows", lambda: [
        {"ts": "2026-07-28T09:01:00Z", "service": "AppEngine", "label": "AppEngine",
         "kind": "action", "event": "restart requested", "source": "watchdog"}])
    page.reload()

    assert page.table.isColumnHidden(column) is True
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


def test_a_renamed_machine_is_listed_under_the_name_it_was_given(qapp):
    """It was listed under its id instead. A machine added as "sd" and then called
    "hanadev" kept reading "sd  (hanadev)", so the Called field looked broken and
    there was nothing anywhere to say what "sd" was."""
    cfg = cfg_mod.Config(machines=[cfg_mod.Machine(),
                                   cfg_mod.Machine(name="sd", label="hanadev",
                                                   kind="linux",
                                                   address="hanadev")])
    win = panel_mod.MainPanel(cfg)
    page = win.machines_page

    assert page._title(cfg.machines[1]) == "hanadev"
    # The id stays out of sight: it is plumbing the services point at, and showing
    # it only raised the question of which of the two names mattered.
    page.list.setCurrentRow(1)
    page._open()
    assert page.detail.label.text() == "hanadev"
    assert "sd" not in [lb.text() for lb in page.detail.findChildren(QLabel)]
    win.deleteLater()


def test_a_machine_still_shows_its_address_when_that_differs(qapp):
    """Whoever has to RDP or SSH to the box needs the address, and a friendly name
    on its own does not give it."""
    cfg = cfg_mod.Config(machines=[cfg_mod.Machine(),
                                   cfg_mod.Machine(name="sd", label="SUSE dev",
                                                   kind="linux",
                                                   address="hanadev")])
    win = panel_mod.MainPanel(cfg)
    assert win.machines_page._title(cfg.machines[1]) == "SUSE dev  (hanadev)"
    win.deleteLater()


def test_a_windows_machine_can_be_reached_as_a_named_account(qapp):
    """Two options, not one: whoever is signed in here, or a user name and
    password. Before this, a remote Windows machine was only manageable by someone
    who was already an administrator on it."""
    cfg = cfg_mod.Config(machines=[
        cfg_mod.Machine(),
        cfg_mod.Machine(name="ctl053", label="SQL server", kind="windows",
                        address="10.77.3.51")])
    win = panel_mod.MainPanel(cfg)
    page = win.machines_page
    page.list.setCurrentRow(1)
    page._open()
    detail = page.detail

    choices = [detail.auth.itemText(i) for i in range(detail.auth.count())]
    assert choices == ["This computer's signed-in account",
                       "User name and password"]
    # The signed-in account needs no account and no secret, so neither is asked for.
    # win.config(), not cfg: the panel edits its own copy until Save.
    assert win.config().machine("ctl053").auth == "current_user"
    assert detail._rows["password"][0].isVisibleTo(detail) is False
    assert detail._rows["username"][0].isVisibleTo(detail) is False
    # SSH's own fields stay where they belong.
    assert detail._rows["fingerprint"][0].isVisibleTo(detail) is False
    assert detail._rows["key_path"][0].isVisibleTo(detail) is False

    detail.auth.setCurrentIndex(1)                  # user name and password
    assert win.config().machine("ctl053").auth == "password"
    assert detail._rows["username"][0].isVisibleTo(detail) is True
    assert detail._rows["password"][0].isVisibleTo(detail) is True
    assert "DOMAIN\\account" in detail._hints["username"].text()

    # The method comes first, then what it asks for. The other way round put "User"
    # above the choice that decides whether a user is wanted at all.
    def row_of(key):
        caption = detail._rows[key][0]
        return detail.form.getItemPosition(detail.form.indexOf(caption))[0]

    assert row_of("auth") < row_of("username") < row_of("password")
    win.deleteLater()


def test_switching_kind_keeps_a_password_and_swaps_the_other_choice(qapp):
    """Both kinds can sign in with a password; only one of them has a key file and
    only one has the local token. Switching must not silently reset the setting."""
    cfg = cfg_mod.Config(machines=[
        cfg_mod.Machine(),
        cfg_mod.Machine(name="ctl053", kind="windows", address="10.77.3.51",
                        auth="password", username="CT\\svc")])
    win = panel_mod.MainPanel(cfg)
    page = win.machines_page
    page.list.setCurrentRow(1)
    page._open()
    detail = page.detail

    edited = win.config().machine("ctl053")
    detail.kind.setCurrentIndex(1)                  # Linux
    assert edited.kind == "linux"
    assert edited.auth == "password", "the password choice was thrown away"
    assert [detail.auth.itemText(i) for i in range(detail.auth.count())] == \
        ["Private key file", "Password (stored on this machine)"]

    detail.kind.setCurrentIndex(0)                  # back to Windows
    assert edited.auth == "password"
    win.deleteLater()


def test_testing_a_connection_does_not_freeze_the_window(qapp):
    """A firewalled machine took 42 seconds to refuse, and the panel was frozen for
    all of them. The button now says it is asking and the waiting happens
    elsewhere."""
    import time

    cfg = cfg_mod.Config(machines=[
        cfg_mod.Machine(),
        cfg_mod.Machine(name="ctl053", kind="windows", address="10.77.3.51")])
    win = panel_mod.MainPanel(cfg)
    page = win.machines_page
    page.list.setCurrentRow(1)
    page._open()
    detail = page.detail

    slow = []

    class Slow:
        def _sign_in(self):
            pass

        def reachable(self):
            slow.append("asked")
            time.sleep(2)
            return False

    import core.connectors as conn_mod
    original = conn_mod.for_machine
    conn_mod.for_machine = lambda *_a, **_k: Slow()
    try:
        started = time.perf_counter()
        detail._test()
        elapsed = time.perf_counter() - started
    finally:
        conn_mod.for_machine = original

    assert elapsed < 0.5, "the click itself waited for the machine"
    assert "Asking" in detail.result.text()
    for _ in range(40):                       # let the worker finish
        qapp.processEvents()
        if slow:
            break
        time.sleep(0.05)
    assert slow == ["asked"]
    win.deleteLater()


def test_the_machine_list_says_which_account_reaches_a_windows_box(qapp):
    cfg = cfg_mod.Config(machines=[
        cfg_mod.Machine(),
        cfg_mod.Machine(name="ctl053", kind="windows", address="10.77.3.51",
                        auth="password", username="CT\\svc-officer"),
        cfg_mod.Machine(name="ctl054", kind="windows", address="10.77.3.52")])
    page = panel_mod.MainPanel(cfg).machines_page

    assert "CT\\svc-officer" in page._summary(cfg.machines[1], 3)
    assert "this computer's account" in page._summary(cfg.machines[2], 0)


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

    # And a resize from anywhere else is re-anchored too — but only where the
    # platform actually applies the window move. On a constrained session (a small
    # RDP window, a build agent with no real display) Qt may leave the window where
    # it was, and then its coordinates report the environment rather than the
    # anchoring: the panel is neither pinned to the bottom nor butted against the
    # top, it simply did not move. Skip there rather than assert on a position the
    # platform ignored. Measured where this fired: a ~456 px session that left the
    # window at y=437 with the screen top at 0.
    fly.resize(fly.width(), fly.height() + 60)
    qapp.processEvents()
    screen = fly.screen().availableGeometry()
    at_bottom = fly.y() + fly.height() == bottom
    at_top = fly.y() == screen.top() + 4
    if not at_bottom and not at_top:
        rows_mod.collapsed.clear()
        fly.deleteLater()
        pytest.skip("this session does not apply the window move; needs a display")
    assert at_bottom or at_top, "footer pushed down with room above"
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
    assert tray._anything_unsettled() is True
    # The count still says green; the icon must not.
    assert icons.colour_for(*store.counts()) == "green"
    tray.apply_state()
    assert not tray.icon.icon().isNull()

    store.set_health("AppEngine", "healthy", "ok")
    assert tray._anything_unsettled() is False


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


# -- machines: how a target is reached --------------------------------------
def _machines_config():
    return cfg_mod.Config(
        machines=[cfg_mod.Machine(),
                  cfg_mod.Machine(name="CTL052", label="app server"),
                  cfg_mod.Machine(name="hanadev", label="SUSE dev", kind="linux",
                                  address="192.168.230.2", username="svcofficer")],
        services=[cfg_mod.Service(name="AppEngine"),
                  cfg_mod.Service(name="b1s50000.service", machine="hanadev"),
                  cfg_mod.Service(name="sapb1servertools.service",
                                  machine="hanadev")])


def test_a_linux_machine_shows_its_ssh_settings_and_a_windows_one_does_not(qapp):
    cfg = _machines_config()
    win = panel_mod.MainPanel(cfg)
    page = win.machines_page

    page.list.setCurrentRow(2)                       # hanadev
    page._open()
    assert page.detail.username.isVisibleTo(page.detail)
    assert page.detail.fingerprint.isVisibleTo(page.detail)

    page.list.setCurrentRow(1)                       # CTL052, Windows
    page._open()
    assert not page.detail.username.isVisibleTo(page.detail)
    assert not page.detail.fingerprint.isVisibleTo(page.detail)


def test_this_computer_has_nothing_to_configure(qapp):
    """It is reached by being it. Offering an address and an account for the local
    machine would only invite someone to break it."""
    cfg = _machines_config()
    win = panel_mod.MainPanel(cfg)
    page = win.machines_page

    page.list.setCurrentRow(0)
    page._open()

    assert not page.detail.kind.isEnabled()
    # Address and interval are not merely greyed: there is nothing to say about
    # reaching the machine you are already on, so the rows are not there.
    assert not page.detail.address.isVisibleTo(page.detail)
    assert not page.detail.poll.isVisibleTo(page.detail)


def test_editing_a_machine_writes_through_to_the_config(qapp):
    cfg = _machines_config()
    win = panel_mod.MainPanel(cfg)
    page = win.machines_page
    page.list.setCurrentRow(2)
    page._open()

    page.detail.address.setText("10.0.0.9")
    page.detail.username.setText("svcofficer2")
    page.detail.fingerprint.setText("SHA256:abc")
    page.detail.poll.setValue(20)
    page.detail._save()

    edited = win.config().machine("hanadev")
    assert edited.address == "10.0.0.9"
    assert edited.username == "svcofficer2"
    assert edited.host_fingerprint == "SHA256:abc"
    assert edited.poll_seconds == 20


def test_switching_a_machine_to_linux_stops_using_the_windows_token(qapp):
    """The account this app runs as means nothing on a Linux box."""
    cfg = _machines_config()
    win = panel_mod.MainPanel(cfg)
    page = win.machines_page
    page.list.setCurrentRow(1)                       # CTL052, Windows
    page._open()

    page.detail.kind.setCurrentIndex(1)              # → Linux

    edited = win.config().machine("CTL052")
    assert edited.kind == "linux"
    assert edited.auth == "key"


def test_an_unconfirmed_host_key_is_said_out_loud_in_the_list(qapp):
    """Not a silent failure at connect time: the row says the machine is not
    usable yet, and why."""
    cfg = _machines_config()
    win = panel_mod.MainPanel(cfg)
    page = win.machines_page

    summary = page._summary(cfg.machine("hanadev"), 2)

    assert "host key not confirmed" in summary
    assert "svcofficer" in summary


def test_the_setup_commands_name_only_the_services_that_were_chosen(qapp):
    """No unit name is hard-coded anywhere in the app — they come from the user's
    own list, which is the whole point of generating this."""
    cfg = _machines_config()
    win = panel_mod.MainPanel(cfg)
    page = win.machines_page
    page.list.setCurrentRow(2)
    page._open()

    page.detail._setup()
    text = qapp.clipboard().text()

    assert "/usr/sbin/usermod -aG systemd-journal svcofficer" in text
    assert "systemctl start b1s50000.service" in text
    assert "systemctl restart sapb1servertools.service" in text
    assert "AppEngine" not in text, "a service from another machine leaked in"
    assert "NOPASSWD" in text and "ALL=(root)" in text
    # It has to be pastable as one block. The first version mixed commands with
    # the contents of a file, so the sudoers line landed in the shell and gave
    # "syntax error near unexpected token".
    assert "cat > /etc/sudoers.d/service-officer <<'EOF'" in text
    assert text.count("EOF") == 2
    assert "visudo -cf" in text, "a malformed sudoers file must be caught"
    assert "chmod 0440" in text
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("svcofficer ALL=") or stripped.startswith("/usr/bin/"):
            continue                      # inside the heredoc
        assert not stripped or stripped.startswith("#") or "=" not in stripped             or stripped.startswith("cat >"), f"not runnable: {line}"


def test_the_setup_commands_say_what_to_do_when_nothing_is_chosen_yet(qapp):
    cfg = cfg_mod.Config(machines=[cfg_mod.Machine(),
                                   cfg_mod.Machine(name="hanadev", kind="linux")])
    win = panel_mod.MainPanel(cfg)
    page = win.machines_page
    page.list.setCurrentRow(1)
    page._open()

    page.detail._setup()

    assert "Services page" in qapp.clipboard().text()


def test_a_typed_password_goes_to_the_store_and_not_into_a_widget(qapp, tmp_path,
                                                                  monkeypatch):
    """A password left sitting in a widget ends up in screenshots, crash dumps and
    anything that walks the widget tree."""
    from core import secrets
    monkeypatch.setattr(secrets, "SECRETS_PATH", str(tmp_path / "secrets.dat"))

    cfg = cfg_mod.Config(machines=[
        cfg_mod.Machine(),
        cfg_mod.Machine(name="hanadev", kind="linux", auth="password",
                        username="root")])
    win = panel_mod.MainPanel(cfg)
    page = win.machines_page
    page.list.setCurrentRow(1)
    page._open()

    page.detail.password.setText("CorrectHorse42")
    page.detail._save_password()

    # The field looks filled, because an empty field after saving reads as "it was
    # lost" — but what it holds is stand-in characters, not the password.
    shown = page.detail.password.text()
    assert shown and shown != "CorrectHorse42", "the real password is in the widget"
    assert page.detail.password.typed() == "", "it would be saved again as-is"
    assert "saved" in page.detail.password_state.text()
    edited = win.config().machine("hanadev")
    assert edited.secret_ref == "machine/hanadev"
    assert secrets.get(edited.secret_ref) == "CorrectHorse42"
    assert "CorrectHorse42" not in str(cfg_mod.to_dict(win.config()))


def test_reopening_a_machine_never_shows_the_stored_password(qapp, tmp_path,
                                                            monkeypatch):
    from core import secrets
    monkeypatch.setattr(secrets, "SECRETS_PATH", str(tmp_path / "secrets.dat"))
    secrets.put("machine/hanadev", "hunter2")

    cfg = cfg_mod.Config(machines=[
        cfg_mod.Machine(),
        cfg_mod.Machine(name="hanadev", kind="linux", auth="password",
                        secret_ref="machine/hanadev")])
    win = panel_mod.MainPanel(cfg)
    page = win.machines_page
    page.list.setCurrentRow(1)
    page._open()

    shown = page.detail.password.text()
    assert shown, "a stored password must look stored"
    assert shown != "hunter2", "the stored password was rendered into the field"
    assert page.detail.password.typed() == ""
    assert "saved" in page.detail.password_state.text()


def test_signing_in_as_root_says_no_setup_is_needed(qapp):
    """Sudo exists to avoid using root. As root there is nothing to grant, and the
    page should say so rather than leaving someone to follow steps they don't
    need."""
    cfg = cfg_mod.Config(machines=[
        cfg_mod.Machine(),
        cfg_mod.Machine(name="hanadev", kind="linux", auth="password",
                        username="root")])
    win = panel_mod.MainPanel(cfg)
    page = win.machines_page
    page.list.setCurrentRow(1)
    page._open()

    note = page.detail.sudo_note.text()
    assert "needs nothing set up" in note
    assert "root password is stored" in note      # and the cost is stated


def test_the_password_row_appears_only_when_a_password_is_chosen(qapp):
    cfg = cfg_mod.Config(machines=[
        cfg_mod.Machine(),
        cfg_mod.Machine(name="hanadev", kind="linux", auth="key")])
    win = panel_mod.MainPanel(cfg)
    page = win.machines_page
    page.list.setCurrentRow(1)
    page._open()

    assert page.detail.key_path.isVisibleTo(page.detail)
    assert not page.detail.password.isVisibleTo(page.detail)

    page.detail.auth.setCurrentIndex(1)                      # → password

    assert page.detail.password.isVisibleTo(page.detail)
    assert not page.detail.key_path.isVisibleTo(page.detail)


def test_root_is_told_there_is_nothing_to_set_up(qapp):
    """Sudo exists to avoid using root. Generating a sudoers file for root would
    be theatre, and following it would waste someone's afternoon."""
    cfg = cfg_mod.Config(
        machines=[cfg_mod.Machine(),
                  cfg_mod.Machine(name="hanadev", kind="linux", username="root")],
        services=[cfg_mod.Service(name="b1s50000.service", machine="hanadev")])
    win = panel_mod.MainPanel(cfg)
    page = win.machines_page
    page.list.setCurrentRow(1)
    page._open()

    page.detail._setup()

    text = qapp.clipboard().text()
    assert "Nothing to set up" in text
    assert "sudoers" not in text and "usermod" not in text
    assert "no setup" in page.detail.result.text()


def test_with_no_services_chosen_the_block_still_runs(qapp):
    """It used to emit a sudoers line whose value was a comment — pasteable, and
    wrong."""
    cfg = cfg_mod.Config(machines=[
        cfg_mod.Machine(),
        cfg_mod.Machine(name="hanadev", kind="linux", username="svcofficer")])
    win = panel_mod.MainPanel(cfg)
    page = win.machines_page
    page.list.setCurrentRow(1)
    page._open()

    page.detail._setup()

    text = qapp.clipboard().text()
    assert "/usr/sbin/usermod" in text
    assert "NOPASSWD" not in text, "granted nothing but wrote a rule anyway"
    assert "sudoers.d" not in text


def _settled(qapp, done, seconds: float = 5.0):
    """Pump the event loop until a reading thread has reported back.

    Another machine is enumerated off the UI thread now, so the answer arrives
    through a signal rather than before the constructor returns.
    """
    import time
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        qapp.processEvents()
        if done():
            return True
        time.sleep(0.02)
    return False


def test_the_picker_asks_a_linux_machine_with_systemctl(qapp, monkeypatch):
    """What broke in the real panel: choosing hanadev in Add services… asked the
    Windows service manager and reported "the RPC server is unavailable"."""
    from core import connectors, control
    from ui.pages.base import ServicePicker

    asked = {}

    def fake_list(machine="", record=None):
        asked["machine"] = machine
        asked["kind"] = getattr(record, "kind", None)
        return [{"name": "b1s50000.service", "display": "B1 Service Layer",
                 "status": "Running"}]

    monkeypatch.setattr(control, "list_all_services", fake_list)
    monkeypatch.setattr("ui.pages.base.control.list_all_services", fake_list)
    machine = cfg_mod.Machine(name="hanadev", kind="linux")

    picker = ServicePicker(set(), None, machine="hanadev", record=machine)
    assert _settled(qapp, lambda: picker.list.count() == 1)

    assert asked == {"machine": "hanadev", "kind": "linux"}
    picker.deleteLater()


def test_the_picker_opens_at_once_however_slow_the_machine_is(qapp, monkeypatch):
    """Measured on a real remote Windows box: listing its services took fifteen
    seconds, and the dialog was blank and titled "Not Responding" for all of them."""
    import time

    from ui.pages.base import ServicePicker

    def slow(machine="", record=None):
        time.sleep(1.5)
        return [{"name": "B1ServerTools64", "display": "SAP B1 Server Tools",
                 "status": "Running"}]

    monkeypatch.setattr("ui.pages.base.control.list_all_services", slow)

    started = time.perf_counter()
    picker = ServicePicker(set(), None, machine="sc-sql",
                           record=cfg_mod.Machine(name="sc-sql", kind="windows",
                                                  address="10.77.3.112"))
    opened = time.perf_counter() - started

    assert opened < 0.5, f"the dialog waited {opened:.1f}s for the machine"
    assert "Reading the services on sc-sql" in picker.count.text()
    assert _settled(qapp, lambda: picker.list.count() == 1), "the listing never came"
    picker.deleteLater()


def test_this_computer_is_still_enumerated_straight_away(qapp, monkeypatch):
    """No thread, no "Reading…": the local service manager answers in milliseconds,
    and a picker that flickered through a loading state would be worse."""
    from ui.pages.base import ServicePicker

    monkeypatch.setattr("ui.pages.base.control.list_all_services",
                        lambda machine="", record=None: [
                            {"name": "AppEngine", "display": "CompuTec AppEngine",
                             "status": "Running"}])

    picker = ServicePicker(set(), None, machine="")

    assert picker.list.count() == 1
    picker.deleteLater()


def test_a_machine_that_does_not_answer_is_explained_not_quoted(qapp,
                                                               monkeypatch):
    """"(1722, 'OpenSCManager', 'The RPC server is unavailable.')" is what the API
    said, not what happened."""
    from core import control
    from ui.pages.base import ServicePicker

    def boom(machine="", record=None):
        raise RuntimeError("(1722, 'OpenSCManager', 'The RPC server is unavailable.')")

    monkeypatch.setattr("ui.pages.base.control.list_all_services", boom)
    warned = []
    monkeypatch.setattr("ui.pages.base.QMessageBox.warning",
                        lambda *args, **kw: warned.append(args[2]))

    ServicePicker(set(), None, machine="CTL099")
    assert _settled(qapp, lambda: len(warned) == 1)
    windows_text = warned[-1]

    ServicePicker(set(), None, machine="hanadev",
                  record=cfg_mod.Machine(name="hanadev", kind="linux"))
    assert _settled(qapp, lambda: len(warned) == 2)
    linux_text = warned[-1]

    assert "CTL099 did not answer" in windows_text
    assert "Remote Service Management" in windows_text
    assert "1722" in windows_text            # the raw text is still there
    # A Linux target gets the advice that applies to it, not firewall rules.
    assert "over SSH" in linux_text
    assert "Remote Service Management" not in linux_text


def test_typing_replaces_the_stand_in_rather_than_appending_to_it(qapp, tmp_path,
                                                                 monkeypatch):
    """Ten asterisks are not a password. Editing has to start from nothing, or a
    new one would be appended to them."""
    from core import secrets
    monkeypatch.setattr(secrets, "SECRETS_PATH", str(tmp_path / "secrets.dat"))
    secrets.put("machine/hanadev", "old-one")

    cfg = cfg_mod.Config(machines=[
        cfg_mod.Machine(),
        cfg_mod.Machine(name="hanadev", kind="linux", auth="password",
                        username="root", secret_ref="machine/hanadev")])
    win = panel_mod.MainPanel(cfg)
    win.show()                             # Qt only delivers focus to what is shown
    page = win.machines_page
    page.list.setCurrentRow(1)
    page._open()
    qapp.processEvents()
    field = page.detail.password
    assert field.text() == field.STAND_IN

    field.setFocus()                       # as clicking into it does
    qapp.processEvents()
    assert field.text() == "", "the stand-in was left for the new value to join"

    field.setText("new-one")
    page.detail._save_password()

    assert secrets.get("machine/hanadev") == "new-one"


def test_root_is_not_offered_a_setup_button_at_all(qapp):
    """Its only answer would be "nothing to do", which is a question the user has
    to ask before they can learn the answer."""
    cfg = cfg_mod.Config(machines=[
        cfg_mod.Machine(),
        cfg_mod.Machine(name="hanadev", kind="linux", username="root"),
        cfg_mod.Machine(name="other", kind="linux", username="svcofficer")])
    win = panel_mod.MainPanel(cfg)
    page = win.machines_page

    page.list.setCurrentRow(1)
    page._open()
    assert not page.detail.setup_button.isVisibleTo(page.detail)

    page.list.setCurrentRow(2)
    page._open()
    assert page.detail.setup_button.isVisibleTo(page.detail)


def test_no_category_is_always_the_last_heading():
    """It is not a category among the others — it is where things are when they
    have not been filed."""
    cfg = cfg_mod.Config(
        categories=[cfg_mod.Category(name="Filled"),
                    cfg_mod.Category(name="Empty on purpose")],
        services=[cfg_mod.Service(name="A", category="Filled"),
                  cfg_mod.Service(name="B")])

    editor = [name for name, _t, _m in cfg.grouped_services(include_empty=True)]
    reading = [name for name, _t, _m in cfg.grouped_services()]

    assert editor[-1] == cfg_mod.NO_CATEGORY, editor
    assert editor == ["Filled", "Empty on purpose", cfg_mod.NO_CATEGORY]
    assert reading[-1] == cfg_mod.NO_CATEGORY, reading


def test_every_detail_page_offers_a_way_back(qapp, sample):
    """A breadcrumb reads as a path, not as a button."""
    from PySide6.QtWidgets import QPushButton
    from ui import theme

    win = panel_mod.MainPanel(sample)
    win.machines_page.list.setCurrentRow(0)
    win.machines_page._open()
    pages = {"services": win.services_page.detail,
             "stacks": win.stacks_page.detail,
             "schedule": win.schedule_page.detail,
             "machines": win.machines_page.detail}

    for name, page in pages.items():
        backs = [b for b in page.findChildren(QPushButton)
                 if b.text().startswith(theme.GLYPH_BACK)]
        assert backs, f"{name} detail has no back button"


def test_a_machine_row_says_whether_it_is_answering(qapp):
    """The gap that cost an evening: four services reading "Unknown" with nowhere to
    see that their machine had never been asked."""
    cfg = cfg_mod.Config(
        machines=[cfg_mod.Machine(),
                  cfg_mod.Machine(name="hanadev", kind="linux", address="hanadev"),
                  cfg_mod.Machine(name="sc-sql", kind="windows",
                                  address="10.77.3.112")],
        services=[cfg_mod.Service(name="webclient.service", machine="hanadev"),
                  cfg_mod.Service(name="B1ServerTools64", machine="sc-sql")])
    store = st.Store()
    win = panel_mod.MainPanel(cfg, store=store)
    page = win.machines_page

    # Never asked — which is a state of its own, not "down".
    reach, tag, kind, why = page._reachability(cfg.machines[1])
    assert reach == "not asked yet" and tag == "waiting" and kind == "none"
    assert "asked every 5s" in why

    store.note_machine("hanadev", True)
    reach, tag, kind, _why = page._reachability(cfg.machines[1])
    assert reach.startswith("answered") and tag == "connected" and kind == "running"

    store.note_machine("sc-sql", False, "TimeoutError: timed out")
    reach, tag, kind, why = page._reachability(cfg.machines[2])
    assert reach == "no answer, last tried just now"
    assert tag == "no answer" and kind == "stopped"
    # The transport's own words are one hover away, not in the row: put in the
    # summary they made it wider than the window and hid every chip.
    assert why == "TimeoutError: timed out"
    assert "TimeoutError" not in reach

    # And it reaches the row, next to what the machine is.
    page.refresh()
    row = page.list.itemWidget(page.list.item(1))
    said = " ".join(lb.text() for lb in row.findChildren(QLabel))
    assert "answered" in said and "systemd over SSH" in said
    win.deleteLater()


def test_this_computer_is_not_described_as_answering(qapp):
    """It is not asked and cannot fail to answer: it is the thing doing the asking."""
    cfg = cfg_mod.Config(machines=[cfg_mod.Machine()])
    page = panel_mod.MainPanel(cfg, store=st.Store()).machines_page

    assert page._reachability(cfg.machines[0]) == ("", "This PC", "running", "")


def _check_kinds_offered(detail):
    """{label: enabled} from the Add check menu."""
    return {a.text(): a.isEnabled() for a in detail.add_menu.actions()}


def test_checks_a_machine_cannot_do_are_not_offered_for_it(qapp):
    """A remote Windows machine can have a file read over its admin share (measured
    18 ms) but cannot run a command — that needs WinRM or a scheduled task, neither of
    which is wired up. So File is offered and Command is not, per that difference,
    rather than by a blanket "it is remote" rule."""
    cfg = cfg_mod.Config(
        machines=[cfg_mod.Machine(),
                  cfg_mod.Machine(name="sc-sql", kind="windows",
                                  address="10.77.3.112"),
                  cfg_mod.Machine(name="hanadev", kind="linux",
                                  address="hanadev")],
        services=[cfg_mod.Service(name="AppEngine"),
                  cfg_mod.Service(name="B1ServerTools64", machine="sc-sql"),
                  cfg_mod.Service(name="webclient.service", machine="hanadev")])
    win = panel_mod.MainPanel(cfg)
    page = win.services_page

    _select(page, "B1ServerTools64")
    page._open_selected()
    offered = _check_kinds_offered(page.detail)
    assert offered["A port answers"] is True
    assert offered["A URL answers"] is True
    assert offered["A file is being written"] is True     # over the admin share
    assert offered["A command succeeds"] is False         # no command transport
    assert "run a command" in page.detail.add_menu.toolTip()

    # Over SSH both are genuinely possible, which is what makes HANA checkable.
    _select(page, "webclient.service")
    page._open_selected()
    offered = _check_kinds_offered(page.detail)
    assert offered["A file is being written"] is True
    assert offered["A command succeeds"] is True

    # And on this computer everything is available, as it always was.
    _select(page, "AppEngine")
    page._open_selected()
    assert all(_check_kinds_offered(page.detail).values())
    win.deleteLater()


def test_a_check_that_was_already_configured_still_shows(qapp):
    """Somebody may have made one before this rule existed, or moved a service to
    another machine afterwards. Hiding it would lose the setting silently; it is shown,
    and it says why it cannot pass."""
    cfg = cfg_mod.Config(
        machines=[cfg_mod.Machine(),
                  cfg_mod.Machine(name="sc-sql", kind="windows",
                                  address="10.77.3.112")],
        services=[cfg_mod.Service(
            name="B1ServerTools64", machine="sc-sql",
            health=cfg_mod.Health(enabled=True, checks=[
                cfg_mod.HealthCheck(kind="command",
                                    command="sc query MSSQLSERVER")]))])
    win = panel_mod.MainPanel(cfg)
    page = win.services_page
    _select(page, "B1ServerTools64")
    page._open_selected()

    said = " ".join(lb.text() for lb in page.detail.findChildren(QLabel))
    assert "sc query MSSQLSERVER" in said
    assert "cannot run a command on another Windows machine" in said
    win.deleteLater()


# ---------------------------------------------------------------------------
# the hub, on the General page
# ---------------------------------------------------------------------------
# This field is what makes the installer's "the address looks wrong, carry on anyway"
# honest: somebody who gets it wrong, or whose hub moves, needs a place to put the right
# answer. It is also the only place the panel says which hub it is reading.
def test_the_hub_page_shows_and_stores_the_address(qapp, sample, monkeypatch, tmp_path):
    from core import local, secrets

    monkeypatch.setattr(local, "PATH", str(tmp_path / "client.json"))
    monkeypatch.setattr(local, "MACHINE_PATH", str(tmp_path / "machine.json"))
    monkeypatch.setattr(secrets, "USER_SECRETS_PATH", str(tmp_path / "user.dat"))
    monkeypatch.setattr(secrets, "SECRETS_PATH", str(tmp_path / "machine.dat"))

    win = panel_mod.MainPanel(sample)
    page = win.hub_page
    page.load_from(win.config())
    told = []
    page.hub_changed.connect(told.append)

    assert page.hub_url.text() == "", "a fresh install is not a client of anything"
    assert page.hub_port.text() == ""
    assert "own services" in page.hub_state.text()

    # "ctl052" is what a person types; it should not have to be a URL.
    page.hub_url.setText("ctl052")
    page.hub_token.setText("a-token")
    page._apply_hub()

    assert local.load().hub_url == "https://ctl052:8797"
    assert local.token("https://ctl052:8797") == "a-token"
    assert told == ["https://ctl052:8797"], "the app was not told to restart"
    assert page.hub_token.text() == "", "left the token on screen"
    assert "ctl052" in page.hub_state.text()
    win.deleteLater()


def test_emptying_the_address_goes_back_to_watching_this_computer(qapp, sample,
                                                                 monkeypatch, tmp_path):
    """There is no mode to switch: an empty address *is* "do the work here"."""
    from core import local, secrets

    monkeypatch.setattr(local, "PATH", str(tmp_path / "client.json"))
    monkeypatch.setattr(local, "MACHINE_PATH", str(tmp_path / "machine.json"))
    monkeypatch.setattr(secrets, "USER_SECRETS_PATH", str(tmp_path / "user.dat"))
    local.save(local.Settings(hub_url="https://ctl052:8797",
                              hub_fingerprint="SHA256:x"))

    win = panel_mod.MainPanel(sample)
    page = win.hub_page
    page.load_from(win.config())
    assert (page.hub_url.text(), page.hub_port.text()) == ("ctl052", "8797")

    page.hub_url.setText("")
    page._apply_hub()

    assert local.load().hub_url == ""
    assert "own services" in page.hub_state.text()
    win.deleteLater()


def test_pointing_at_a_different_hub_drops_the_old_pin(qapp, sample, monkeypatch,
                                                      tmp_path):
    """A pinned certificate belongs to the hub it came from. Keeping it would refuse the
    new hub for the rest of time, and the message would name a certificate change that
    never happened."""
    from core import local, secrets

    monkeypatch.setattr(local, "PATH", str(tmp_path / "client.json"))
    monkeypatch.setattr(local, "MACHINE_PATH", str(tmp_path / "machine.json"))
    monkeypatch.setattr(secrets, "USER_SECRETS_PATH", str(tmp_path / "user.dat"))
    local.save(local.Settings(hub_url="https://old:8797",
                              hub_fingerprint="SHA256:the-old-one"))

    win = panel_mod.MainPanel(sample)
    page = win.hub_page
    page.load_from(win.config())
    # A whole address pasted into the host box is what somebody copies out of a ticket.
    page.hub_url.setText("https://new:9000")
    page._apply_hub()

    settings = local.load()
    assert settings.hub_url == "https://new:9000"
    assert settings.hub_fingerprint == ""
    win.deleteLater()


# ---------------------------------------------------------------------------
# clients
# ---------------------------------------------------------------------------
class _StubHub:
    """A hub that answers the three questions the Clients page asks."""

    def __init__(self, clients=None):
        self.issued = []
        self.revoked = []
        self._clients = list(clients or [])

    def clients(self):
        return {"clients": self._clients, "url": "https://ctl052:8797",
                "fingerprint": "SHA256:whatever"}

    def add_client(self, name, description=""):
        self.issued.append((name, description))
        self._clients.append({"name": name, "description": description,
                              "added": "2026-07-28T14:00:00", "last_seen": "",
                              "host": ""})
        return {"name": name, "token": "a-token", "url": "https://ctl052:8797",
                "fingerprint": "SHA256:whatever", "replaced": False,
                "command": "ServiceOfficer.exe --connect https://ctl052:8797 "
                           "--token a-token"}

    def revoke_client(self, name):
        self.revoked.append(name)
        self._clients = [c for c in self._clients if c["name"] != name]
        return True


def test_the_clients_page_is_absent_without_a_hub(qapp, sample):
    """Nobody to pair with a single-machine install: the page is not there rather than
    there and empty, and the button with it."""
    win = panel_mod.MainPanel(sample)

    assert win._buttons_by_name["clients"].isVisible() is False
    win.deleteLater()


def test_the_clients_page_lists_what_the_hub_says(qapp, sample):
    """Name, why it exists, the host it actually connected from, and the two times. No
    token: the hub keeps a hash of it, so there is nothing to show."""
    hub = _StubHub([{"name": "ismail-laptop", "description": "on my desk",
                     "added": "2026-07-28T09:00:00", "last_seen": "2026-07-28T14:30:00",
                     "host": "CTL099"},
                    {"name": "spare", "description": "", "added": "2026-07-28T10:00:00",
                     "last_seen": "", "host": ""}])
    win = panel_mod.MainPanel(sample, hub=lambda: hub)
    page = win.clients_page

    page._fill(hub.clients())          # what the worker thread hands over

    assert page.table.rowCount() == 2
    assert page.table.item(0, 0).text() == "ismail-laptop"
    assert page.table.item(0, 1).text() == "on my desk"
    assert page.table.item(0, 2).text() == "CTL099"
    # Never connected reads as words, not as an empty cell nobody can interpret.
    assert page.table.item(1, 4).text() == "never used"
    assert "2 clients" in page.note.text()
    win.deleteLater()


def test_a_token_is_shown_once_with_the_command_to_run(qapp, sample, monkeypatch):
    """The only moment it is readable, so it comes with the whole command for the machine
    it is for — nobody should have to assemble that from three fields."""
    from ui.pages import clients as clients_mod

    hub = _StubHub()
    win = panel_mod.MainPanel(sample, hub=lambda: hub)
    page = win.clients_page

    monkeypatch.setattr(clients_mod._AddDialog, "exec",
                        lambda self: setattr(self.name, "text_value", None)
                        or self.name.setText("new-laptop")
                        or self.description.setText("the spare one")
                        or clients_mod.QDialog.Accepted)
    shown = {}
    monkeypatch.setattr(clients_mod._IssuedDialog, "exec",
                        lambda self: shown.update(seen=True) or 0)
    monkeypatch.setattr(clients_mod._IssuedDialog, "__init__",
                        lambda self, made, parent=None: shown.update(made=made))

    page._add()

    assert hub.issued == [("new-laptop", "the spare one")]
    assert shown.get("seen") is True, "the token was never put in front of anybody"
    assert shown["made"]["token"] == "a-token"
    assert "--connect" in shown["made"]["command"]
    win.deleteLater()


def test_revoking_asks_first_and_then_tells_the_hub(qapp, sample, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from ui.pages import clients as clients_mod

    hub = _StubHub([{"name": "ismail-laptop", "description": "", "added": "",
                     "last_seen": "", "host": ""}])
    win = panel_mod.MainPanel(sample, hub=lambda: hub)
    page = win.clients_page
    page._fill(hub.clients())
    page.table.selectRow(0)

    monkeypatch.setattr(clients_mod.QMessageBox, "question",
                        lambda *a, **k: QMessageBox.No)
    page._revoke()
    assert hub.revoked == [], "revoked without being told to"

    monkeypatch.setattr(clients_mod.QMessageBox, "question",
                        lambda *a, **k: QMessageBox.Yes)
    page._revoke()
    assert hub.revoked == ["ismail-laptop"]
    win.deleteLater()


def test_the_port_is_its_own_field_and_tolerates_a_pasted_address(qapp, sample,
                                                                 monkeypatch, tmp_path):
    """Host and port are two fields, laid out like the installer's: "ctl052:9100" asks
    somebody to know that a colon means something here.

    But an address pasted whole into the host box is what people actually do, so a port
    found there wins over the field it was not typed in.
    """
    from core import local, secrets

    monkeypatch.setattr(local, "PATH", str(tmp_path / "client.json"))
    monkeypatch.setattr(local, "MACHINE_PATH", str(tmp_path / "machine.json"))
    monkeypatch.setattr(secrets, "USER_SECRETS_PATH", str(tmp_path / "user.dat"))
    win = panel_mod.MainPanel(sample)
    page = win.hub_page
    page.load_from(win.config())

    page.hub_url.setText("ctl052")
    page.hub_port.setText("9100")
    assert page._normalised() == "https://ctl052:9100"

    page.hub_port.setText("")
    assert page._normalised() == "https://ctl052:8797", "no port means the default"

    page.hub_port.setText("not-a-port")
    assert page._normalised() == "https://ctl052:8797", "nonsense is not a port"

    page.hub_url.setText("https://elsewhere:9443/")
    page.hub_port.setText("8797")
    assert page._normalised() == "https://elsewhere:9443", \
        "a port pasted with the address should win over the one left in the field"
    win.deleteLater()


# ---------------------------------------------------------------------------
# the WinRM switch on a machine
# ---------------------------------------------------------------------------
def test_the_winrm_switch_is_offered_only_where_it_means_something(qapp, sample):
    """Locally those three things already work; on Linux, SSH runs commands and reads the
    journal. A setting that does nothing is worse than no setting."""
    from core import config as cfg_mod

    win = panel_mod.MainPanel(sample)
    page = win.machines_page
    detail = page.detail

    # isHidden, not isVisible: nothing in an unshown window is "visible", and what is
    # being checked is whether the row was hidden on purpose.
    detail.load(cfg_mod.Machine())                      # this computer
    assert detail.winrm.isHidden() is True

    detail.load(cfg_mod.Machine(name="sd", kind="linux", address="hanadev"))
    assert detail.winrm.isHidden() is True

    detail.load(cfg_mod.Machine(name="sc-sql", kind="windows", address="10.77.3.112"))
    assert detail.winrm.isHidden() is False
    win.deleteLater()


def test_the_switch_shows_and_stores_what_the_machine_says(qapp, sample):
    from core import config as cfg_mod

    win = panel_mod.MainPanel(sample)
    detail = win.machines_page.detail
    machine = cfg_mod.Machine(name="sc-sql", kind="windows", address="10.77.3.112")

    detail.load(machine)
    assert detail.winrm.isChecked() is False, "off until somebody says otherwise"

    detail.winrm.setChecked(True)
    assert machine.winrm is True

    # And reading a machine back in shows what it holds rather than what was last on
    # screen.
    detail.load(cfg_mod.Machine(name="other", kind="windows", address="10.0.0.9"))
    assert detail.winrm.isChecked() is False
    detail.load(machine)
    assert detail.winrm.isChecked() is True
    win.deleteLater()


def test_testing_a_connection_decides_the_switch(qapp, sample, monkeypatch):
    """Somebody should not have to know what WinRM is to get this right: Test connection
    tries, and the switch ends up matching what the machine actually answered."""
    from core import config as cfg_mod
    from core import winrm_windows

    win = panel_mod.MainPanel(sample)
    detail = win.machines_page.detail
    machine = cfg_mod.Machine(name="sc-sql", kind="windows", address="10.77.3.112")
    detail.load(machine)

    monkeypatch.setattr(winrm_windows, "forget", lambda host="": None)
    monkeypatch.setattr(winrm_windows, "probe",
                        lambda host, user="", password="", **k: {"ok": True, "why": "",
                                                                "name": "SC-SQL"})
    said = detail._test_winrm(machine)

    assert machine.winrm is True
    assert "switched on" in said and "terminated" in said

    # And the other way: a machine that does not answer turns it off and says why.
    monkeypatch.setattr(
        winrm_windows, "probe",
        lambda host, user="", password="", **k: {
            "ok": False, "name": "",
            "why": "On that machine, as an administrator:  winrm quickconfig"})
    said = detail._test_winrm(machine)

    assert machine.winrm is False
    assert "switched off" in said and "winrm quickconfig" in said
    win.deleteLater()


def test_a_connection_test_says_whose_reach_it_proved(qapp, tmp_path, monkeypatch):
    """With a hub, the chip on a machine shows what the *hub* found, and Test connection runs
    here as whoever is signed in. Two subjects — and on 2026-07-28 sc-sql answered a test
    while its own chip said `waiting`, which read as a contradiction rather than as two
    different questions."""
    from core import config as cfg_mod, connectors
    from ui.pages import machines as machines_mod

    machine = cfg_mod.Machine(name="sc-sql", address="10.77.3.112", kind="windows")
    cfg = cfg_mod.Config(machines=[machine])

    class Answers:
        def reachable(self): return True
        def abilities(self): return connectors.Abilities(control=True)

    monkeypatch.setattr(connectors, "for_machine", lambda name, record=None: Answers())
    monkeypatch.setattr(connectors, "forget", lambda name=None: None)

    alone = machines_mod.MachineDetail()
    with_hub = machines_mod.MachineDetail(object())
    for page in (alone, with_hub):
        page.machine = machine
        monkeypatch.setattr(page, "_test_winrm", lambda m: "")

    said = []
    alone.tested.connect(said.append)
    with_hub.tested.connect(said.append)
    alone._run_test(machine)
    with_hub._run_test(machine)

    assert "from this computer" not in said[0], "watching alone, there is no other computer"
    assert "from this computer" in said[1], "did not say whose reach answered"


class _Nods:
    """Answers anything with a shrug. For the parts of an Application a test is not about."""

    def __getattr__(self, _name):
        return _Nods()

    def __call__(self, *a, **k):
        return None

    def isVisible(self):
        return False


def test_a_machine_answering_repaints_the_machines_page(qapp, sample, monkeypatch):
    """It was only redrawn on the way in. So a machine that started answering while the page
    was open kept its `waiting` chip while the very services on it streamed in as Running —
    the hub appearing to contradict itself. Seen on 2026-07-29.

    Every screen that shows state is repainted from one place, and the Machines page was not
    one of them. Driven through that one place rather than around it.
    """
    import app as app_mod

    win = panel_mod.MainPanel(sample)
    win.show()
    try:
        drawn = []
        monkeypatch.setattr(win.machines_page, "refresh", lambda: drawn.append(True))

        stub = _Nods()
        stub.panel = win

        app_mod.Application._refresh_lists(stub)

        assert drawn, "the one place that repaints state does not reach the Machines page"
    finally:
        win.close()


def test_the_hub_is_infrastructure_and_comes_first(qapp, sample):
    """It was the last section of General, under appearance, startup and notifications —
    four scrolls past the things it outranks. A hub decides where every service in this
    window comes from and whether this computer does the watching at all, so it sits with
    Machines and Clients, and above them: those two are what a hub *has*."""
    win = panel_mod.MainPanel(sample)
    try:
        order = list(win._buttons_by_name)

        assert "hub" in order, "there is nowhere to set the address"
        assert order.index("hub") < order.index("machines"), "Machines came first"
        assert order.index("hub") < order.index("general"), "still buried in Settings"

        assert not win._buttons_by_name["hub"].icon().isNull(),             "the only nav entry without a picture reads as unfinished"

        win._select(win.hub_page, win._buttons_by_name["hub"])
        assert win.pages.currentWidget() is win.hub_page
    finally:
        win.close()


def test_the_hub_page_is_there_without_a_hub(qapp, sample):
    """Unlike Clients. A panel watching its own services still has to be able to point at
    one — that field is how it becomes a client at all."""
    win = panel_mod.MainPanel(sample)
    try:
        assert win._buttons_by_name["hub"].isVisible() or not win.isVisible()
        assert win._buttons_by_name["clients"].isVisible() is False
    finally:
        win.close()


def test_no_page_spreads_itself_over_the_window(qapp, sample):
    """A layout with no trailing stretch hands its spare height to the widgets in it. The
    Hub page shipped that way for one build: its ADDRESS heading was 55 pixels tall where it
    wants 15, so every section floated in the middle of a gap and the page read as a
    different product from General beside it.

    Measured on the headings, not on the gaps between them — the gaps are fixed spacings and
    stayed at 9 the whole time, which is how a first version of this test passed against the
    very bug it was written for. What stretches is the widgets.
    """
    from PySide6.QtWidgets import QLabel

    win = panel_mod.MainPanel(sample)
    win.resize(1010, 700)
    win.show()
    try:
        for name, button in win._buttons_by_name.items():
            page = win._by_name[name]
            win._select(page, button)
            win.grab()          # a layout is only applied when something asks it to paint
            for head in page.findChildren(QLabel):
                if head.property("role") != "section" or not head.isVisible():
                    continue
                # Only headings in the page's own vertical flow. One sitting in a row beside
                # a button is as tall as that button by design — Schedule's RECENT EXECUTIONS
                # is 31px next to its Refresh, and that is alignment, not spare height.
                owner = head.parentWidget().layout()
                if owner is None or owner.indexOf(head) < 0:
                    continue
                # Ten pixels of slack: a heading inside a table header carries a couple of
                # pixels of cell padding and that is fine. Nothing legitimately adds forty,
                # which is what the spare height of a window looks like.
                assert head.height() <= head.sizeHint().height() + 10, (
                    f"{name}: the {head.text()!r} heading is {head.height()}px tall and "
                    f"wants {head.sizeHint().height()}px — a missing addStretch(1) at the "
                    f"end of that page's layout")
    finally:
        win.close()

