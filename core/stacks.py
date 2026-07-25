"""Ordered stack runs.

A stack is a sequence: SQL Server → Licence Manager → AppEngine → WMS. Starting
walks it forwards; stopping walks it backwards. Each step waits before the next
begins — either until the service reports Running, or for a fixed number of
seconds, because several services report Running well before they can actually
serve anything and a fixed pause is the honest way to say so.

The runner has no UI dependency and takes its own control object, so it can be
driven headlessly in tests with fake services.
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
    ok: bool
    detail: str = ""
    seconds: float = 0.0


@dataclass
class RunResult:
    stack: str
    action: str
    steps: list          # list[StepResult]
    ok: bool
    cancelled: bool = False

    @property
    def failed_step(self):
        return next((s for s in self.steps if not s.ok), None)

    def summary(self) -> str:
        """One line suitable for a notification or a ticket."""
        if self.cancelled:
            return f"{self.stack}: {self.action} cancelled after {len(self.steps)} steps"
        if self.ok:
            total = sum(s.seconds for s in self.steps)
            return (f"{self.stack}: {self.action} completed, "
                    f"{len(self.steps)} steps in {total:.0f}s")
        bad = self.failed_step
        return (f"{self.stack}: {self.action} failed at step {bad.index} "
                f"({bad.service}) — {bad.detail}")


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
                fresh = self._control.query_status(name, machine=machine)
                if fresh == target:
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

    # -- the run -----------------------------------------------------------
    def run(self, stack, action: str, on_step=None, machine_for=None) -> RunResult:
        """on_step(index, total, service, phase) — phase is 'begin'|'ok'|'fail'.
        machine_for(service_name) -> machine, so a stack can span machines."""
        on_step = on_step or (lambda *a: None)
        machine_for = machine_for or (lambda _n: "")

        with self._busy:
            self._cancel.clear()
            if action == RESTART:
                down = self._execute(stack, STOP, on_step, machine_for, phase_offset=0)
                if not down.ok or down.cancelled:
                    return down
                up = self._execute(stack, START, on_step, machine_for,
                                   phase_offset=len(down.steps))
                return RunResult(stack=stack.name, action=RESTART,
                                 steps=down.steps + up.steps,
                                 ok=up.ok, cancelled=up.cancelled)
            return self._execute(stack, action, on_step, machine_for)

    def _execute(self, stack, action: str, on_step, machine_for,
                 phase_offset: int = 0) -> RunResult:
        steps = list(stack.steps)
        if action == STOP:
            steps.reverse()          # unwind dependencies in reverse
        total = len(steps)
        results = []

        for i, step in enumerate(steps, start=1):
            if self._cancel.is_set():
                return RunResult(stack.name, action, results, ok=False, cancelled=True)

            machine = machine_for(step.service)
            began = time.monotonic()
            on_step(i + phase_offset, total, step.service, "begin")
            self._on_log(f"{stack.name}: {action} step {i}/{total} {step.service}")

            try:
                if action == START:
                    if self._status(step.service, machine) == st.RUNNING:
                        detail = "already running"
                    else:
                        self._control.start_service(step.service, machine=machine)
                        detail = ""
                else:
                    if self._status(step.service, machine) == st.STOPPED:
                        detail = "already stopped"
                    else:
                        self._store.expect_stop(step.service, machine)
                        self._control.stop_service(step.service, machine=machine)
                        detail = ""
            except Exception as exc:
                msg = getattr(exc, "strerror", None) or str(exc)
                results.append(StepResult(i, step.service, False, msg,
                                          time.monotonic() - began))
                on_step(i + phase_offset, total, step.service, "fail")
                return RunResult(stack.name, action, results, ok=False)

            # Wait before the next step begins.
            target = st.RUNNING if action == START else st.STOPPED
            if step.wait == "delay" and action == START:
                ok = self._sleep(step.delay_seconds)
                why = "" if ok else "cancelled"
            else:
                ok, why = self._wait_for(step.service, target,
                                         step.timeout_seconds, machine)

            results.append(StepResult(i, step.service, ok, why or detail,
                                      time.monotonic() - began))
            on_step(i + phase_offset, total, step.service, "ok" if ok else "fail")
            if not ok:
                cancelled = why == "cancelled"
                return RunResult(stack.name, action, results, ok=False,
                                 cancelled=cancelled)

        return RunResult(stack.name, action, results, ok=True)
