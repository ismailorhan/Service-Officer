from core import config as cfg
from core import state as st
from core import stacks


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


def a_stack():
    return cfg.Stack(name="SAP B1", steps=[
        cfg.Step(service="SQL", wait="running", timeout_seconds=5),
        cfg.Step(service="Licence", wait="running", timeout_seconds=5),
        cfg.Step(service="AppEngine", wait="delay", delay_seconds=0),
        cfg.Step(service="WMS", wait="running", timeout_seconds=5),
    ])


def runner(scm):
    return stacks.Runner(scm, st.Store(), poll=0.01)


def test_start_runs_in_order():
    scm = FakeSCM()
    result = runner(scm).run(a_stack(), stacks.START)
    assert result.ok
    assert [n for k, n in scm.calls if k == "start"] == ["SQL", "Licence", "AppEngine", "WMS"]


def test_stop_runs_in_reverse():
    scm = FakeSCM({n: st.RUNNING for n in ("SQL", "Licence", "AppEngine", "WMS")})
    result = runner(scm).run(a_stack(), stacks.STOP)
    assert result.ok
    assert [n for k, n in scm.calls if k == "stop"] == ["WMS", "AppEngine", "Licence", "SQL"]


def test_restart_is_reverse_stop_then_forward_start():
    scm = FakeSCM({n: st.RUNNING for n in ("SQL", "Licence", "AppEngine", "WMS")})
    result = runner(scm).run(a_stack(), stacks.RESTART)
    assert result.ok
    order = [f"{k}:{n}" for k, n in scm.calls]
    assert order == ["stop:WMS", "stop:AppEngine", "stop:Licence", "stop:SQL",
                     "start:SQL", "start:Licence", "start:AppEngine", "start:WMS"]


def test_a_step_that_never_comes_up_aborts_the_run():
    scm = FakeSCM(never_starts=["Licence"])
    result = runner(scm).run(a_stack(), stacks.START)
    assert not result.ok
    bad = result.failed_step
    assert bad.service == "Licence" and bad.index == 2
    assert "still" in bad.detail                      # timed out waiting
    # Nothing after the failure was attempted.
    assert "AppEngine" not in [n for _k, n in scm.calls]
    assert "failed at step 2" in result.summary()


def test_already_running_service_is_not_started_again():
    scm = FakeSCM({"SQL": st.RUNNING})
    result = runner(scm).run(a_stack(), stacks.START)
    assert result.ok
    assert ("start", "SQL") not in scm.calls
    assert result.steps[0].detail == "already running"


def test_progress_is_reported_per_step():
    scm = FakeSCM()
    seen = []
    runner(scm).run(a_stack(), stacks.START,
                    on_step=lambda i, total, svc, phase: seen.append((i, svc, phase)))
    assert seen[0] == (1, "SQL", "begin")
    assert (4, "WMS", "ok") in seen


def test_stop_marks_the_stop_as_expected_so_the_watchdog_stays_out():
    """Otherwise a stack stop would be immediately undone by recovery."""
    scm = FakeSCM({"WMS": st.RUNNING})
    store = st.Store()
    r = stacks.Runner(scm, store, poll=0.01)
    r.run(cfg.Stack(name="s", steps=[cfg.Step(service="WMS", timeout_seconds=5)]),
          stacks.STOP)
    store.update("WMS", st.RUNNING)
    event = store.update("WMS", st.STOPPED, exit_code=1067)
    assert event.source == st.SRC_PANEL       # recognised as deliberate
