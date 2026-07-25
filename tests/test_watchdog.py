"""The watchdog's whole value is knowing when *not* to act."""

from core import config as cfg
from core import state as st
from core.watchdog import Watchdog


class FakeControl:
    def __init__(self):
        self.started = []

    def start_service(self, name, machine=""):
        self.started.append((name, machine))

    def stop_service(self, name, machine=""):
        pass

    def query_status(self, name, machine=""):
        return st.UNKNOWN


class ManualTimer:
    """Runs nothing until the test says so, so delays don't slow the suite."""
    pending = []

    def __init__(self, delay, fn):
        self.delay, self.fn, self.cancelled = delay, fn, False
        ManualTimer.pending.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    @classmethod
    def fire_all(cls):
        due, cls.pending = cls.pending, []
        for t in due:
            if not t.cancelled:
                t.fn()

    @classmethod
    def reset(cls):
        cls.pending = []


def build(recovery=None, notifications=None):
    ManualTimer.reset()
    rec = recovery or cfg.Recovery(enabled=True, max_attempts=3, delay_seconds=10)
    conf = cfg.Config(services=[cfg.Service(name="AppEngine", label="AppEngine",
                                            recovery=rec)])
    if notifications:
        conf.notifications = notifications
    store = st.Store()
    control = FakeControl()
    notes = []
    wd = Watchdog(lambda: conf, control, store,
                  notify=lambda t, x: notes.append(x),
                  timer_factory=ManualTimer)
    wd.attach(store)
    return wd, store, control, notes, conf


def crash(store, exit_code=1067):
    store.update("AppEngine", st.RUNNING)
    store.update("AppEngine", st.STOPPED, exit_code=exit_code)


def test_restarts_after_a_crash():
    wd, store, control, notes, _ = build()
    crash(store)                      # non-zero exit code = died
    ManualTimer.fire_all()
    assert control.started == [("AppEngine", "")]
    assert any("stopped unexpectedly" in n for n in notes)


def test_leaves_a_clean_external_stop_alone():
    """Exit code 0 means somebody stopped it on purpose in services.msc.
    Restarting it would be fighting the administrator."""
    wd, store, control, _, _ = build()
    crash(store, exit_code=0)
    ManualTimer.fire_all()
    assert control.started == []


def test_clean_stop_is_restarted_when_asked_to():
    wd, store, control, _, _ = build(
        cfg.Recovery(enabled=True, restart_on_clean_stop=True))
    crash(store, exit_code=0)
    ManualTimer.fire_all()
    assert control.started == [("AppEngine", "")]


def test_our_own_stop_is_never_undone():
    wd, store, control, _, _ = build()
    store.update("AppEngine", st.RUNNING)
    store.expect_stop("AppEngine")            # the panel is about to stop it
    store.update("AppEngine", st.STOPPED, exit_code=1067)
    ManualTimer.fire_all()
    assert control.started == []


def test_disabled_recovery_does_nothing():
    wd, store, control, _, _ = build(cfg.Recovery(enabled=False))
    crash(store)
    ManualTimer.fire_all()
    assert control.started == []


def test_unconfigured_service_is_ignored():
    wd, store, control, _, _ = build()
    store.update("SomethingElse", st.RUNNING)
    store.update("SomethingElse", st.STOPPED, exit_code=1067)
    ManualTimer.fire_all()
    assert control.started == []


def test_gives_up_after_max_attempts():
    wd, store, control, notes, _ = build(
        cfg.Recovery(enabled=True, max_attempts=2, delay_seconds=1))
    for _ in range(4):
        crash(store)
        ManualTimer.fire_all()
    assert len(control.started) == 2, control.started
    assert any("did not come back" in n for n in notes)


def test_backoff_grows_between_attempts():
    wd, store, control, _, _ = build(
        cfg.Recovery(enabled=True, max_attempts=3, delay_seconds=10, backoff=2.0))
    delays = []
    for _ in range(3):
        store.update("AppEngine", st.RUNNING)
        ManualTimer.reset()            # drop the 60s "has it settled" timer
        store.update("AppEngine", st.STOPPED, exit_code=1067)
        delays += [t.delay for t in ManualTimer.pending]
        ManualTimer.fire_all()
    assert delays == [10.0, 20.0, 40.0]


def test_flapping_suspends_recovery():
    wd, store, control, notes, _ = build(
        cfg.Recovery(enabled=True, max_attempts=0, delay_seconds=1,
                     flap_threshold=3, flap_window_minutes=30))
    for _ in range(5):
        crash(store)
        ManualTimer.fire_all()
    assert wd.is_suspended("AppEngine")
    assert any("keeps stopping" in n for n in notes)
    # It stays suspended until someone resumes it.
    before = len(control.started)
    crash(store); ManualTimer.fire_all()
    assert len(control.started) == before
    wd.resume("AppEngine")
    crash(store); ManualTimer.fire_all()
    assert len(control.started) == before + 1


def test_no_restart_if_it_came_back_on_its_own():
    wd, store, control, _, _ = build()
    crash(store)
    store.update("AppEngine", st.RUNNING)     # recovered by itself
    ManualTimer.fire_all()
    assert control.started == []
