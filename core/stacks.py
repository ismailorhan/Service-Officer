"""Stack runs.

A stack is a script: an ordered list of steps, each naming a service and what to
do to it (start, stop or restart), with a wait before the next step begins. There
is no stack-level start/stop/restart — a step carries its own action, so one
click runs the sequence exactly as written.

Each wait is either "until applied" — poll until the service reaches the state
the step was trying to produce, then optionally pause a little longer — or a
fixed pause, because several services report Running well before they can
actually serve and a fixed wait is the honest way to say so.

No UI dependency: the runner takes its own control object, so it can be driven
headlessly with fake services.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from . import state as st

START = "start"
STOP = "stop"
RESTART = "restart"


@dataclass
class StepResult:
    index: int          # 1-based position in the run
    service: str
    action: str
    ok: bool
    detail: str = ""
    seconds: float = 0.0


@dataclass
class RunResult:
    stack: str
    steps: list          # list[StepResult]
    ok: bool
    cancelled: bool = False

    @property
    def failed_step(self):
        return next((s for s in self.steps if not s.ok), None)

    def summary(self) -> str:
        """One line suitable for a notification or a ticket."""
        if self.cancelled:
            return f"{self.stack}: cancelled after {len(self.steps)} steps"
        if self.ok:
            total = sum(s.seconds for s in self.steps)
            return (f"{self.stack}: {len(self.steps)} steps completed "
                    f"in {total:.0f}s")
        bad = self.failed_step
        return (f"{self.stack}: failed at step {bad.index} "
                f"({bad.action} {bad.service}) — {bad.detail}")


class Runner:
    """Runs one stack at a time. Call run() from a worker thread."""

    def __init__(self, control, store: st.Store, on_log=None, poll=0.25):
        self._control = control
        self._store = store
        self._on_log = on_log or (lambda text: None)
        self._poll = poll
        self._cancel = threading.Event()
        self._busy = threading.Lock()

    @property
    def busy(self) -> bool:
        return self._busy.locked()

    def cancel(self) -> None:
        self._cancel.set()

    # -- helpers -----------------------------------------------------------
    def _status(self, name: str, machine: str = "") -> str:
        """Prefer the live store (SCM notifications keep it current); fall back
        to asking the SCM directly so a run works before any notification."""
        s = self._store.status_of(name, machine)
        if s and s != st.UNKNOWN:
            return s
        try:
            return self._control.query_status(name, machine=machine)
        except Exception:
            return st.UNKNOWN

    def _wait_for(self, name: str, target: str, timeout: float,
                  machine: str = "") -> tuple:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._cancel.is_set():
                return False, "cancelled"
            status = self._status(name, machine)
            if status == target:
                return True, ""
            if status == st.NOT_FOUND:
                return False, "service not found"
            try:
                if self._control.query_status(name, machine=machine) == target:
                    return True, ""
            except Exception:
                pass
            time.sleep(self._poll)
        return False, f"still {self._status(name, machine)} after {timeout:.0f}s"

    def _sleep(self, seconds: float) -> bool:
        """Interruptible pause; False if cancelled."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._cancel.is_set():
                return False
            time.sleep(min(0.2, max(0.0, end - time.monotonic())))
        return not self._cancel.is_set()

    def _apply(self, step, machine: str) -> str:
        """Issue the step's action. Returns a detail note; raises on failure."""
        current = self._status(step.service, machine)
        if step.action == START:
            if current == st.RUNNING:
                return "already running"
            self._control.start_service(step.service, machine=machine)
        elif step.action == STOP:
            if current == st.STOPPED:
                return "already stopped"
            self._store.expect_stop(step.service, machine)
            self._control.stop_service(step.service, machine=machine)
        else:                                   # restart
            self._store.expect_stop(step.service, machine)
            self._control.restart_service(step.service, machine=machine)
        return ""

    # -- the run -----------------------------------------------------------
    def run(self, stack, on_step=None, machine_for=None) -> RunResult:
        """on_step(index, total, service, action, phase) — phase is
        'begin' | 'ok' | 'fail'. machine_for(name) -> machine, so a stack can
        span machines."""
        on_step = on_step or (lambda *a: None)
        machine_for = machine_for or (lambda _n: "")

        with self._busy:
            self._cancel.clear()
            steps = list(stack.steps)
            total = len(steps)
            results = []

            for i, step in enumerate(steps, start=1):
                if self._cancel.is_set():
                    return RunResult(stack.name, results, ok=False, cancelled=True)

                machine = machine_for(step.service)
                began = time.monotonic()
                on_step(i, total, step.service, step.action, "begin")
                self._on_log(f"{stack.name}: step {i}/{total} "
                             f"{step.action} {step.service}")

                try:
                    detail = self._apply(step, machine)
                except Exception as exc:
                    msg = getattr(exc, "strerror", None) or str(exc)
                    results.append(StepResult(i, step.service, step.action, False,
                                              msg, time.monotonic() - began))
                    on_step(i, total, step.service, step.action, "fail")
                    return RunResult(stack.name, results, ok=False)

                # The wait belongs to the gap after this step. The last step has
                # no gap but is still verified, or a stack whose final service
                # never came up would report success.
                is_last = (i == total)
                if is_last or step.wait == "applied":
                    ok, why = self._wait_for(step.service, step.target_state,
                                             step.timeout_seconds, machine)
                    if ok and not is_last and step.grace_seconds:
                        if not self._sleep(step.grace_seconds):
                            ok, why = False, "cancelled"
                else:
                    ok = self._sleep(step.delay_seconds)
                    why = "" if ok else "cancelled"

                results.append(StepResult(i, step.service, step.action, ok,
                                          why or detail, time.monotonic() - began))
                on_step(i, total, step.service, step.action, "ok" if ok else "fail")
                if not ok:
                    return RunResult(stack.name, results, ok=False,
                                     cancelled=(why == "cancelled"))

            return RunResult(stack.name, results, ok=True)
