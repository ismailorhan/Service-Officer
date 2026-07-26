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

import socket

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
    assert "waiting for it to answer" in \
        application.store.health_detail("webclient.service", "hanadev")


def _watching_control(monkeypatch):
    """Record every question asked of a machine, and how long each took."""
    asked = []
    monkeypatch.setattr(app_mod.control, "query_status",
                        lambda name, machine="": asked.append(("status", machine))
                        or st.RUNNING)
    monkeypatch.setattr(app_mod.control, "start_type",
                        lambda name, machine="": asked.append(("start_type", machine))
                        or "Automatic")
    return asked


def _two_machines():
    return cfg_mod.Config(
        machines=[cfg_mod.Machine(),
                  cfg_mod.Machine(name="sc-sql", kind="windows",
                                  address="10.77.3.112", auth="password",
                                  username="SC\\ismailorhan")],
        services=[cfg_mod.Service(name="AppEngine"),
                  cfg_mod.Service(name="B1ServerTools64", machine="sc-sql")])


def test_the_ui_thread_never_asks_another_machine_anything(application, monkeypatch):
    """The frozen window, measured: enumerating a remote Windows box took fifteen
    seconds and a firewalled one forty-two, all of it on the thread that paints.
    Priming, the start-type sweep and Refresh each walked every service.

    Remote answers come from the poller, which exists to wait."""
    application.cfg = _two_machines()
    asked = _watching_control(monkeypatch)
    soon = []
    monkeypatch.setattr(application.poller, "poll_soon",
                        lambda machine=None: soon.append(machine))

    application._prime_states()
    application._poll_start_types()
    application.refresh()

    remote = [(what, where) for what, where in asked if where]
    assert remote == [], f"asked another machine from the UI thread: {remote}"
    # And this computer is still asked, or the rows would be empty until a poll.
    assert ("status", "") in asked and ("start_type", "") in asked
    # The poller was told to go and look instead.
    assert "sc-sql" in soon and None in soon


def test_an_action_brings_its_status_back_rather_than_asking_again(application,
                                                                  monkeypatch):
    """The handler runs on the UI thread, so the round trip belongs to the worker
    that already made one."""
    application.cfg = _two_machines()
    asked = _watching_control(monkeypatch)

    application._action_done("B1ServerTools64", "sc-sql", "restart", None,
                            status=st.RUNNING)

    assert [(w, m) for w, m in asked if m] == [], "asked the machine again"
    assert application.store.status_of("B1ServerTools64", "sc-sql") == st.RUNNING


def test_a_restart_says_starting_even_though_the_status_never_changed(
        application, monkeypatch):
    """The case the user hit twice: restart from the panel, and the row went
    straight back to green.

    `systemctl restart` returns with the unit already active, so the app queried
    "Running", compared it with the "Running" it had, published no event, and
    nothing ever told the monitor the service had restarted. Nothing was wrong with
    the monitor — it was simply never asked.
    """
    application.cfg = cfg_mod.Config(services=[_watched()],
                                     machines=[cfg_mod.Machine(),
                                               cfg_mod.Machine(name="hanadev",
                                                               kind="linux")])
    # A check that will pass, so the service can be got into the state a restart
    # starts from — running, asked, and vouched for — through the real code rather
    # than by calling note_running here, which would have produced "Starting…" by
    # itself and made this test pass with or without the fix. It did, at first.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        service = application.cfg.services[0]
        service.health.checks = [cfg_mod.HealthCheck(kind="tcp", host="127.0.0.1",
                                                     port=port)]
        application.flyout.rebuild()
        application.store.update("webclient.service", st.RUNNING,
                                 machine="hanadev")
        application.health.check_now(service)
        assert application.store.health_of("webclient.service", "hanadev") == \
            health.HEALTHY, "could not reach the pre-restart state"

        monkeypatch.setattr(app_mod.control, "query_status",
                            lambda *_a, **_k: st.RUNNING)
        application._action_done("webclient.service", "hanadev", "restart", None)
    assert application.store.health_of("webclient.service", "hanadev") == \
        health.STARTING
    application.flyout.apply_states()
    assert application.flyout._rows[("hanadev", "webclient.service")] \
        .chip.text() == st.LABEL_STARTING


def test_restarting_survives_a_repaint_and_stops_when_the_action_reports_back(
        application, monkeypatch):
    """Half of "it went straight back to Running": for the seconds the restart
    took, the row said Running.

    "Restarting…" was written straight onto the chip, and the next apply_states —
    triggered by any status from anywhere, including another machine's poll — wrote
    the plain status over the top of it.
    """
    application.do_action = lambda *_a, **_k: None       # no real service calls
    application.flyout.rebuild()
    application._mark_busy("AppEngine", "", "Restarting…")
    assert application.flyout._rows[("", "AppEngine")].chip.text() == "Restarting…"

    application.flyout.apply_states()                    # what used to wipe it
    assert application.flyout._rows[("", "AppEngine")].chip.text() == "Restarting…"

    monkeypatch.setattr(app_mod.control, "query_status",
                        lambda *_a, **_k: st.RUNNING)
    application._action_done("AppEngine", "", "restart", None)
    application.flyout.apply_states()

    assert application.flyout._rows[("", "AppEngine")].chip.text() == st.RUNNING


def test_a_failed_restart_does_not_claim_the_service_is_starting(application,
                                                                monkeypatch):
    """Otherwise a service that refused to start sits at amber "Starting…"
    for its whole grace window instead of showing what happened."""
    application.cfg = cfg_mod.Config(services=[_watched()],
                                     machines=[cfg_mod.Machine(),
                                               cfg_mod.Machine(name="hanadev",
                                                               kind="linux")])
    monkeypatch.setattr(app_mod.control, "query_status",
                        lambda *_a, **_k: st.STOPPED)
    application._action_done("webclient.service", "hanadev", "restart",
                            "Job for webclient.service failed", announce=False)

    assert application.store.health_of("webclient.service", "hanadev") != \
        health.STARTING


def test_the_header_counts_a_starting_service_as_starting(application):
    """The row said "Starting..." under a green "1 of 1 running", because the
    header counted what the service manager reported and the row did not."""
    application.cfg = cfg_mod.Config(services=[_watched()],
                                     machines=[cfg_mod.Machine(),
                                               cfg_mod.Machine(name="hanadev",
                                                               kind="linux")])
    application.flyout.rebuild()

    application.store.update("webclient.service", st.RUNNING, machine="hanadev")
    application.flyout.apply_states()

    summary = application.flyout.summary.text()
    assert "1 starting" in summary, summary
    assert "1 running" not in summary, summary
    assert application.flyout.badge.text().startswith("0 of 1")
    assert application.flyout.badge.property("cat") == "pending"


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
    # And the reason travels with it, so hovering the row explains the amber.
    assert row.toolTip() == "started just now; waiting for it to answer"


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
    # and the two headings above those rows, which is where the contradiction
    # survived after the rows were fixed: a green "1 of 1 running" and a card
    # titled "1/1 running", both about a service that was still starting.
    assert "1 starting" in application.flyout.summary.text()
    assert application.flyout.badge.property("cat") == "pending"
    assert application.hover.title.text().endswith("0/1 running, 1 to watch"), \
        application.hover.title.text()


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


def test_the_dot_agrees_with_the_word_beside_it(application):
    """The card's dot was painted from the raw status while its label came from
    st.effective — two lines apart, disagreeing. A green dot next to the word
    "starting" is exactly the kind of thing a user has to point out, so this
    asserts the drawn colour, not just the text."""
    from PySide6.QtWidgets import QLabel

    from ui import theme

    application.cfg = cfg_mod.Config(services=[_watched()],
                                     machines=[cfg_mod.Machine(),
                                               cfg_mod.Machine(name="hanadev",
                                                               kind="linux")])

    def dot_and_word():
        application.hover._render()
        labels = application.hover.findChildren(QLabel)
        dot = next(w for w in labels if w.property("dotCategory"))
        word = next(w for w in labels if w.property("role") == "cardState")
        pixel = dot.pixmap().toImage().pixelColor(4, 4).name()
        return dot.property("dotCategory"), word.text(), pixel

    application.store.update("webclient.service", st.RUNNING, machine="hanadev")
    category, word, pixel = dot_and_word()
    assert (category, word) == ("pending", "starting\u2026")
    assert pixel.lower() == theme.chip("pending")[0].lower(), pixel

    application.store.set_health("webclient.service", health.HEALTHY, "ok",
                                 machine="hanadev")
    category, word, pixel = dot_and_word()
    assert (category, word) == ("running", st.RUNNING)
    assert pixel.lower() == theme.chip("running")[0].lower(), pixel

    application.store.set_health("webclient.service", health.UNHEALTHY, "HTTP 500",
                                 machine="hanadev")
    category, word, pixel = dot_and_word()
    assert (category, word) == ("stopped", "not responding")
    assert pixel.lower() == theme.chip("stopped")[0].lower(), pixel
