"""The three features have to work as one chain, not just individually.

A crash arrives as an SCM notification, lands in the store, the watchdog decides
to act, the restart is issued, and every step of that story is written to
history. This assembles the same wiring app.py does, with a fake SCM so it runs
headlessly and deterministically.
"""

from core import config as cfg_mod
from core import history
from core import stacks
from core import state as st
from core.watchdog import Watchdog


class FakeControl:
    def __init__(self, statuses=None):
        self.status = dict(statuses or {})
        self.started, self.stopped = [], []

    def query_status(self, name, machine=""):
        return self.status.get(name, st.STOPPED)

    def start_service(self, name, machine=""):
        self.started.append(name)
        self.status[name] = st.RUNNING

    def stop_service(self, name, machine=""):
        self.stopped.append(name)
        self.status[name] = st.STOPPED


class ImmediateTimer:
    """Runs the callback as soon as it's started, so no test waits on backoff."""

    def __init__(self, delay, fn):
        self.delay, self.fn = delay, fn

    def start(self):
        self.fn()

    def cancel(self):
        pass


def wire(tmp_path, cfg):
    """Mirror app.py's wiring: history + watchdog on one store."""
    hist = str(tmp_path / "history.db")
    store = st.Store()
    control = FakeControl()
    history.attach(store, lambda: cfg.history.enabled, path=hist)
    wd = Watchdog(lambda: cfg, control, store,
                  on_log=lambda event, note: history.record(event, path=hist, note=note),
                  timer_factory=ImmediateTimer)
    wd.attach(store)
    return store, control, wd, hist


def test_crash_is_recovered_and_the_whole_story_is_recorded(tmp_path):
    cfg = cfg_mod.Config(services=[cfg_mod.Service(
        name="AppEngine", label="CompuTec AppEngine",
        recovery=cfg_mod.Recovery(enabled=True, max_attempts=3, delay_seconds=1))])
    store, control, wd, hist = wire(tmp_path, cfg)

    store.update("AppEngine", st.RUNNING)                       # steady state
    store.update("AppEngine", st.STOPPED, exit_code=1067)       # crash
    store.update("AppEngine", st.RUNNING)                       # came back

    assert control.started == ["AppEngine"]

    rows = list(reversed(history.read(hist)))                   # oldest first
    told = [(r["to"], r.get("note", "")) for r in rows]
    assert told[0][0] == st.RUNNING
    assert told[1][0] == st.STOPPED
    assert any("watchdog attempt 1" in note for _to, note in told)
    assert told[-1][0] == st.RUNNING
    assert rows[1]["exit_code"] == 1067


def test_a_deliberate_stop_is_recorded_but_not_undone(tmp_path):
    cfg = cfg_mod.Config(services=[cfg_mod.Service(
        name="AppEngine", recovery=cfg_mod.Recovery(enabled=True))])
    store, control, wd, hist = wire(tmp_path, cfg)

    store.update("AppEngine", st.RUNNING)
    store.expect_stop("AppEngine")                              # the user pressed Stop
    store.update("AppEngine", st.STOPPED, exit_code=0)

    assert control.started == []
    rows = history.read(hist)
    assert rows[0]["to"] == st.STOPPED
    assert rows[0]["source"] == st.SRC_PANEL                    # attributed, not "scm"


def test_a_stack_stop_is_not_fought_by_recovery(tmp_path):
    """The two features could easily contradict each other: a stack stop must
    not be immediately undone by the watchdog."""
    cfg = cfg_mod.Config(
        services=[cfg_mod.Service(name="AppEngine",
                                  recovery=cfg_mod.Recovery(enabled=True)),
                  cfg_mod.Service(name="WMSServer",
                                  recovery=cfg_mod.Recovery(enabled=True))],
        stacks=[cfg_mod.Stack(name="Shut down", steps=[
            cfg_mod.Step(service="WMSServer", action="stop", timeout_seconds=5),
            cfg_mod.Step(service="AppEngine", action="stop", timeout_seconds=5)])])
    store, control, wd, hist = wire(tmp_path, cfg)
    control.status = {"AppEngine": st.RUNNING, "WMSServer": st.RUNNING}
    store.update("AppEngine", st.RUNNING)
    store.update("WMSServer", st.RUNNING)

    runner = stacks.Runner(control, store, poll=0.01)
    result = runner.run(cfg.stack("Shut down"))
    assert result.ok
    assert control.stopped == ["WMSServer", "AppEngine"]        # as written

    # The SCM would now report both as stopped; neither may be restarted.
    store.update("WMSServer", st.STOPPED, exit_code=0)
    store.update("AppEngine", st.STOPPED, exit_code=0)
    assert control.started == []


def test_history_can_be_off_while_recovery_still_works(tmp_path):
    cfg = cfg_mod.Config(
        services=[cfg_mod.Service(name="AppEngine",
                                  recovery=cfg_mod.Recovery(enabled=True))],
        history=cfg_mod.History(enabled=False))
    store, control, wd, hist = wire(tmp_path, cfg)

    store.update("AppEngine", st.RUNNING)
    store.update("AppEngine", st.STOPPED, exit_code=1067)

    assert control.started == ["AppEngine"]
    # on_log still writes watchdog notes; the per-event stream is what's off.
    assert all(r.get("note") for r in history.read(hist))


def test_config_survives_a_full_round_trip_with_all_three_features(tmp_path):
    """Everything the new UI can edit must reach disk and come back."""
    path = str(tmp_path / "services.json")
    cfg = cfg_mod.Config(
        services=[cfg_mod.Service(name="AppEngine", label="CompuTec AppEngine",
                                  recovery=cfg_mod.Recovery(enabled=True,
                                                            max_attempts=4,
                                                            delay_seconds=12,
                                                            backoff=1.5,
                                                            restart_on_clean_stop=True,
                                                            flap_threshold=6,
                                                            flap_window_minutes=45))],
        stacks=[cfg_mod.Stack(name="SAP B1", steps=[
            cfg_mod.Step(service="MSSQLSERVER", wait="running", timeout_seconds=180),
            cfg_mod.Step(service="AppEngine", wait="delay", delay_seconds=20)])],
        history=cfg_mod.History(enabled=True, retention_days=14),
        notifications=cfg_mod.Notifications(on_crash=True, on_recovery=False,
                                           on_give_up=True),
        auto_start=False)
    cfg_mod.save(cfg, path)
    back = cfg_mod.load(path)

    r = back.service("AppEngine").recovery
    assert (r.max_attempts, r.delay_seconds, r.backoff) == (4, 12, 1.5)
    assert r.restart_on_clean_stop and r.flap_threshold == 6 and r.flap_window_minutes == 45
    steps = back.stack("SAP B1").steps
    assert (steps[0].timeout_seconds, steps[1].wait, steps[1].delay_seconds) == (180, "delay", 20)
    assert back.history.retention_days == 14
    assert back.notifications.on_recovery is False
    assert back.auto_start is False
