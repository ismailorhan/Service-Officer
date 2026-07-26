"""Does the application actually connect the pieces up?

Every other test here builds one widget or one core object. That left a blind spot:
three times this session a feature worked in isolation and did nothing in the app,
because a signal was never connected or a repaint never asked for. The tray icon
reflecting health was the clearest — a unit test called tray.apply_state() by hand
and passed, while nothing in production ever called it.

So this builds the real Application. It never starts the event loop or any thread;
Application.__init__ only wires things together, which is exactly what is under
test.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication          # noqa: E402

import app as app_mod                               # noqa: E402
from core import config as cfg_mod                  # noqa: E402
from core import health                             # noqa: E402
from core import state as st                        # noqa: E402


@pytest.fixture
def application(monkeypatch):
    """The real Application, sharing the test's QApplication.

    A second QApplication cannot be constructed, so the class is swapped for one
    that hands back the existing instance. Everything else is genuine.
    """
    qapp = QApplication.instance() or QApplication([])
    monkeypatch.setattr(app_mod, "QApplication", lambda _argv: qapp)
    # A fresh store per test. The application uses the module-level singleton, so
    # without this one test's statuses and health verdicts leak into the next —
    # and worse, a status that is already Running publishes no event, so the
    # handler under test never runs and the stale verdict is what gets asserted.
    monkeypatch.setattr(st, "store", st.Store())
    built = app_mod.Application([])
    built.cfg = cfg_mod.Config(services=[
        cfg_mod.Service(name="AppEngine", label="CompuTec AppEngine"),
        cfg_mod.Service(name="WMSServer", label="CompuTec WMS Server"),
    ])
    for svc in built.cfg.services:
        built.store.update(svc.name, st.RUNNING)
    yield built
    built.tray.hide()
    built.flyout.deleteLater()
    built.hover.deleteLater()


def _icon_pixels(app):
    image = app.tray.icon.icon().pixmap(32, 32).toImage()
    return tuple(image.pixel(x, y) for x, y in ((16, 16), (10, 20), (22, 20)))


def test_a_health_verdict_repaints_the_tray_icon(application, qtbot=None):
    """It only got repainted from the SCM event handler, and a health verdict is
    not an SCM event — so the icon stayed green while a service was dead."""
    application.tray.apply_state()
    green = _icon_pixels(application)
    assert application.tray._anything_unsettled() is False

    application.health_signals.verdict.emit(
        "AppEngine", "", health.UNHEALTHY,
        "failed: something answers on CTL052:54002")

    assert application.store.health_of("AppEngine") == "unhealthy"
    assert application.tray._anything_unsettled() is True
    assert _icon_pixels(application) != green, "the icon did not change"

    application.health_signals.verdict.emit("AppEngine", "", health.HEALTHY, "ok")
    assert _icon_pixels(application) == green


def test_the_health_monitor_is_wired_to_the_live_config(application):
    """It has to read the config the app is using, or saved changes never apply."""
    assert application.health._config() is application.cfg
    application.cfg.services[0].health = cfg_mod.Health(
        checks=[cfg_mod.HealthCheck(kind="tcp", port=1)])
    assert application.health._config().services[0].health.active is True


def test_every_list_that_shows_state_is_refreshed_together(application):
    """One place decides what gets repainted, so a new caller can't forget one."""
    called = []
    application.tray.apply_state = lambda: called.append("tray")
    application.hover.refresh = lambda: called.append("hover")
    application._refresh_lists()
    assert called == ["tray", "hover"]


def test_the_flyout_and_the_panel_reach_the_same_actions(application):
    """Both lists offer Start and Stop; both must go through the app, not their
    own copy of the logic."""
    asked = []
    application.do_action = lambda *a, **k: asked.append(a)
    # Already connected in __init__ — wiring it again here would connect a second
    # time and every action would fire twice. (Production only ever wires a
    # freshly built flyout, after a theme change.)
    application.flyout.action_requested.emit("stop", "AppEngine", "")
    assert asked == [("stop", "AppEngine", "")]

    bulk = []
    application.do_bulk = lambda *a, **k: bulk.append(a)
    application.flyout.bulk_requested.emit("restart", [("AppEngine", "")])
    assert bulk == [("restart", [("AppEngine", "")])]


# -- the store is made usable before anything writes to it -------------------
def test_startup_imports_an_older_installs_jsonl(tmp_path, monkeypatch):
    import json

    import app as app_mod
    from core import db, history

    store = tmp_path / "history.db"
    legacy = tmp_path / "history.jsonl"
    legacy.write_text("\n".join(json.dumps(r) for r in (
        {"ts": "2026-07-20T06:00:00+00:00", "service": "AppEngine",
         "to": "Running", "source": "scm"},
        {"ts": "2026-07-20T06:05:00+00:00", "service": "AppEngine",
         "action": "restart", "source": "panel"},
    )) + "\n", encoding="utf-8")
    monkeypatch.setattr(history, "HISTORY_PATH", str(store))
    monkeypatch.setattr(history, "LEGACY_JSONL", str(legacy))

    app_mod.prepare_history()

    rows = history.read(path=str(store))
    assert [r.get("action") or r.get("to") for r in rows] == ["restart", "Running"]
    assert not legacy.exists(), "the source was left where it would import twice"
    assert (tmp_path / "history.jsonl.migrated").exists(), "evidence was deleted"
    db.close(str(store))


def test_startup_survives_a_corrupt_store(tmp_path, monkeypatch):
    """A history that cannot be opened must cost the history, not the app."""
    import app as app_mod
    from core import db, history

    store = tmp_path / "history.db"
    store.write_bytes(b"not a database, not even close")
    monkeypatch.setattr(history, "HISTORY_PATH", str(store))
    monkeypatch.setattr(history, "LEGACY_JSONL", str(tmp_path / "absent.jsonl"))

    app_mod.prepare_history()

    assert list(tmp_path.glob("history.db.corrupt*")), "the damaged file vanished"
    history.record_action("A", "start", "panel", path=str(store))
    assert len(history.read(path=str(store))) == 1
    db.close(str(store))


# -- the window between started and ready, end to end -----------------------
def _watched(name="webclient.service", machine="hanadev", grace=60):
    return cfg_mod.Service(
        name=name, label="Web Client", machine=machine,
        health=cfg_mod.Health(enabled=True, grace_seconds=grace,
                              interval_seconds=5, checks=[
                                  cfg_mod.HealthCheck(
                                      kind="http", expect_status=401,
                                      url="https://hanadev/tcli/dbtype/get.svc")]))


def test_reaching_running_shows_starting_all_the_way_to_the_store(application):
    """The unit tests passed while nothing appeared on screen: the handler set
    health to "unknown" immediately after note_running had published "starting",
    throwing it away. This drives the real signal path."""
    application.cfg = cfg_mod.Config(services=[_watched()],
                                     machines=[cfg_mod.Machine(),
                                               cfg_mod.Machine(name="hanadev",
                                                               kind="linux")])
    application.flyout.rebuild()

    application.store.update("webclient.service", st.RUNNING, machine="hanadev")

    assert application.store.health_of("webclient.service", "hanadev") == \
        health.STARTING
    assert "60s" in application.store.health_detail("webclient.service", "hanadev")


def test_the_row_says_starting_rather_than_running(application):
    """And it reaches the pixels: the chip is what the user was looking at."""
    application.cfg = cfg_mod.Config(services=[_watched()],
                                     machines=[cfg_mod.Machine(),
                                               cfg_mod.Machine(name="hanadev",
                                                               kind="linux")])
    application.flyout.rebuild()

    application.store.update("webclient.service", st.RUNNING, machine="hanadev")
    application.flyout.apply_states()

    row = application.flyout._rows[("hanadev", "webclient.service")]
    assert row.chip.text() == "Starting…"
    assert "checks" in row.toolTip() or "check" in row.toolTip()


def test_a_service_without_checks_still_goes_straight_to_running(application):
    """The fix must not turn every start into a warm-up."""
    application.cfg = cfg_mod.Config(services=[
        cfg_mod.Service(name="Plain", label="Plain")])
    application.flyout.rebuild()

    application.store.update("Plain", st.RUNNING)
    application.flyout.apply_states()

    assert application.store.health_of("Plain") == health.UNKNOWN
    assert application.flyout._rows[("", "Plain")].chip.text() == st.RUNNING


def test_no_grace_means_no_warm_up_state(application):
    """Zero grace is "judge me immediately", so there is no window to report."""
    application.cfg = cfg_mod.Config(services=[_watched(grace=0)],
                                     machines=[cfg_mod.Machine(),
                                               cfg_mod.Machine(name="hanadev",
                                                               kind="linux")])
    application.flyout.rebuild()

    application.store.update("webclient.service", st.RUNNING, machine="hanadev")

    assert application.store.health_of("webclient.service", "hanadev") == \
        health.UNKNOWN


def test_every_surface_says_the_same_thing_about_a_warming_service(application):
    """The row said "Starting...", the hover card said "Running" and the tray icon
    stayed green — three surfaces, three answers, one service. They ask one
    function now, and this test looks at all three."""
    application.cfg = cfg_mod.Config(services=[_watched()],
                                     machines=[cfg_mod.Machine(),
                                               cfg_mod.Machine(name="hanadev",
                                                               kind="linux")])
    application.flyout.rebuild()

    application.store.update("webclient.service", st.RUNNING, machine="hanadev")
    application.flyout.apply_states()
    application.hover._render()
    application.tray.apply_state()

    # the list
    row = application.flyout._rows[("hanadev", "webclient.service")]
    assert row.chip.text() == "Starting\u2026"
    # the hover card
    from PySide6.QtWidgets import QLabel
    said = [w.text() for w in application.hover.findChildren(QLabel)
            if w.property("role") == "cardState"]
    assert said == ["starting\u2026"], said
    # the tray: not green, and turning
    assert application.tray._anything_unsettled() is True
    assert application.tray._should_spin() is True


def test_the_tray_settles_once_the_service_has_answered(application):
    """And the gear has to stop: a spinner that never stops says nothing."""
    application.cfg = cfg_mod.Config(services=[_watched()],
                                     machines=[cfg_mod.Machine(),
                                               cfg_mod.Machine(name="hanadev",
                                                               kind="linux")])
    application.store.update("webclient.service", st.RUNNING, machine="hanadev")
    assert application.tray._should_spin() is True

    # what the monitor does when its first check passes
    application.store.set_health("webclient.service", health.HEALTHY,
                                 "HTTP 401 as expected", machine="hanadev")

    assert application.tray._should_spin() is False
    assert application.tray._anything_unsettled() is False


def test_a_service_that_is_not_answering_is_red_everywhere(application):
    """The case that came before this one, and must keep working."""
    application.cfg = cfg_mod.Config(services=[_watched()],
                                     machines=[cfg_mod.Machine(),
                                               cfg_mod.Machine(name="hanadev",
                                                               kind="linux")])
    application.flyout.rebuild()
    application.store.update("webclient.service", st.RUNNING, machine="hanadev")
    application.store.set_health("webclient.service", health.UNHEALTHY,
                                 "HTTP 500", machine="hanadev")

    application.flyout.apply_states()
    application.hover._render()

    from PySide6.QtWidgets import QLabel
    row = application.flyout._rows[("hanadev", "webclient.service")]
    assert row.chip.text() == "Not responding"
    said = [(w.text(), w.property("bad"))
            for w in application.hover.findChildren(QLabel)
            if w.property("role") == "cardState"]
    assert said == [("not responding", "true")], said
    assert application.tray._anything_unsettled() is True
