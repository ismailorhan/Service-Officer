"""A stack is a script: ordered steps, each with its own action."""

from core import config as cfg
from core import stacks
from core import state as st


class FakeSCM:
    """Services that come up/down instantly unless told otherwise."""

    def __init__(self, initial=None, never_starts=()):
        self.status = dict(initial or {})
        self.never_starts = set(never_starts)
        self.calls = []

    def query_status(self, name, machine=""):
        return self.status.get(name, st.STOPPED)

    def start_service(self, name, machine=""):
        self.calls.append(("start", name))
        if name not in self.never_starts:
            self.status[name] = st.RUNNING

    def stop_service(self, name, machine=""):
        self.calls.append(("stop", name))
        self.status[name] = st.STOPPED

    def restart_service(self, name, machine=""):
        self.calls.append(("restart", name))
        if name not in self.never_starts:
            self.status[name] = st.RUNNING


def step(service, action="start", wait="applied", timeout=5, grace=0, delay=0):
    return cfg.Step(service=service, action=action, wait=wait,
                    timeout_seconds=timeout, grace_seconds=grace,
                    delay_seconds=delay)


def a_stack():
    """The SAP-shaped case: bring the stack up in dependency order."""
    return cfg.Stack(name="SAP B1", steps=[
        step("SQL"), step("Licence"), step("AppEngine", wait="delay", delay=0),
        step("WMS")])


def runner(scm):
    return stacks.Runner(scm, st.Store(), poll=0.01)


def order(scm):
    return [f"{k}:{n}" for k, n in scm.calls]


def test_steps_run_in_the_order_written():
    scm = FakeSCM()
    result = runner(scm).run(a_stack())
    assert result.ok
    assert order(scm) == ["start:SQL", "start:Licence", "start:AppEngine", "start:WMS"]


def test_each_step_does_its_own_action():
    """That is what makes a stack a script rather than one big start or stop."""
    scm = FakeSCM({"WMS": st.RUNNING, "AppEngine": st.RUNNING})
    stack = cfg.Stack(name="fix", steps=[
        step("WMS", action="stop"),
        step("AppEngine", action="restart"),
        step("WMS", action="start")])        # the same service twice, on purpose
    result = runner(scm).run(stack)
    assert result.ok
    assert order(scm) == ["stop:WMS", "restart:AppEngine", "start:WMS"]


def test_a_stop_step_waits_for_stopped_not_running():
    """Otherwise every stop step would sit there until it timed out."""
    scm = FakeSCM({"WMS": st.RUNNING})
    result = runner(scm).run(cfg.Stack(name="s", steps=[
        step("WMS", action="stop"), step("SQL")]))
    assert result.ok and result.steps[0].ok


def test_a_step_that_never_comes_up_aborts_the_run():
    scm = FakeSCM(never_starts=["Licence"])
    result = runner(scm).run(a_stack())
    assert not result.ok
    bad = result.failed_step
    assert (bad.service, bad.index) == ("Licence", 2)
    assert "still" in bad.detail                       # timed out waiting
    assert "AppEngine" not in [n for _k, n in scm.calls]   # nothing after it ran
    assert "failed at step 2" in result.summary()


def test_already_in_the_target_state_is_not_touched():
    scm = FakeSCM({"SQL": st.RUNNING})
    result = runner(scm).run(a_stack())
    assert result.ok
    assert ("start", "SQL") not in scm.calls
    assert result.steps[0].detail == "already running"


def test_progress_is_reported_per_step():
    scm = FakeSCM()
    seen = []
    runner(scm).run(a_stack(), on_step=lambda i, total, svc, act, phase:
                    seen.append((i, svc, act, phase)))
    assert seen[0] == (1, "SQL", "start", "begin")
    assert (4, "WMS", "start", "ok") in seen


def test_grace_pauses_after_the_state_is_reached_but_not_on_the_last_step():
    scm = FakeSCM()
    stack = cfg.Stack(name="two", steps=[step("SQL", grace=1), step("AppEngine", grace=1)])
    result = runner(scm).run(stack)
    assert result.ok
    assert result.steps[0].seconds >= 1.0       # waited out its grace
    assert result.steps[1].seconds < 1.0        # last step has no gap to fill


def test_the_last_step_is_verified_even_when_set_to_a_fixed_wait():
    """A stack whose final service never starts must not report success."""
    scm = FakeSCM(never_starts=["WMS"])
    stack = cfg.Stack(name="two", steps=[
        step("SQL"), step("WMS", wait="delay", delay=0, timeout=1)])
    result = runner(scm).run(stack)
    assert not result.ok and result.failed_step.service == "WMS"


def test_a_fixed_wait_ignores_the_status_mid_stack():
    scm = FakeSCM(never_starts=["SQL"])
    stack = cfg.Stack(name="two", steps=[
        step("SQL", wait="delay", delay=0), step("AppEngine")])
    result = runner(scm).run(stack)
    assert result.ok                             # step 1 didn't block the run
    assert order(scm) == ["start:SQL", "start:AppEngine"]


def test_a_stop_step_marks_the_stop_as_expected_so_recovery_stays_out():
    """Otherwise the watchdog would immediately undo what the stack just did."""
    scm = FakeSCM({"WMS": st.RUNNING})
    store = st.Store()
    stacks.Runner(scm, store, poll=0.01).run(
        cfg.Stack(name="s", steps=[step("WMS", action="stop")]))
    store.update("WMS", st.RUNNING)
    event = store.update("WMS", st.STOPPED, exit_code=1067)
    assert event.source == st.SRC_PANEL           # recognised as deliberate


def test_cancel_stops_between_steps():
    scm = FakeSCM()
    r = runner(scm)
    stack = cfg.Stack(name="s", steps=[step("SQL", grace=5), step("WMS")])
    import threading
    threading.Timer(0.2, r.cancel).start()
    result = r.run(stack)
    assert result.cancelled and not result.ok
    assert "WMS" not in [n for _k, n in scm.calls]


def test_describe_spells_out_each_step():
    stack = cfg.Stack(name="s", steps=[
        step("SQL", timeout=120, grace=5),
        step("AppEngine", wait="delay", delay=15),
        step("WMS", action="stop", timeout=60)])
    lines = stack.describe().splitlines()
    assert "1. start SQL — then wait until applied + 5s, timeout 120s" == lines[0]
    assert "2. start AppEngine — then wait 15s, always" == lines[1]
    assert "3. stop WMS — verified stopped (up to 60s)" == lines[2]


def test_legacy_config_is_migrated():
    """Old files said wait="running" and action="auto"."""
    from core.config import from_dict
    cfg_obj = from_dict({"stacks": [{"name": "old", "steps": [
        {"service": "SQL", "action": "auto", "wait": "running"}]}]})
    s = cfg_obj.stack("old").steps[0]
    assert (s.action, s.wait) == ("start", "applied")
