"""The engine: everything Service Officer does that is not drawing.

It owns the status store, the poller, the health monitor, the watchdog, the
scheduler, the stack runner and the SCM watcher, and it turns a request — "restart
this", "run that stack", "save this config" — into the work, on a worker thread. It
reports what happens through plain-Python callbacks, on whatever thread the work
finished on; a caller that has a UI marshals onto it.

**No Qt, no UI, no service manager knowledge.** That is the whole point: this is what
runs in the tray application today and in the hub service tomorrow, unchanged. The
one used to be an inseparable part of `app.Application`; pulling it out is what lets
the same code answer a socket instead of a signal.

Callbacks (all optional, all called on a worker thread):

  on_event(st.Event)                       a service changed state
  on_health(service, machine, verdict, detail)
  on_machine(machine, reachable, detail)
  on_action_done(**{id, action, service, machine, error, status, actor})
  on_stack_step(index, total, service, action, phase)
  on_stack_done(RunResult)
  on_trigger(trigger)                      a scheduled trigger fired; run it
  on_error(kind, text)                     something the user should be told
  on_config_saved(Config)                  the saved config, so the hub keeps it
"""

from __future__ import annotations

import itertools
import threading
import time

from . import applog, config as cfg_mod, connectors, control, history
from . import health as health_mod
from . import poller as poller_mod
from . import schedule as schedule_mod
from . import scm, stacks
from . import state as st
from .watchdog import Watchdog

log = applog.get("engine")


class Busy(RuntimeError):
    """Somebody is already doing something to this service.

    Carries who and what, because "busy" on its own invites pressing the button
    again — and with several clients, the somebody may not be you.
    """

    def __init__(self, actor: str, action: str, since: float):
        self.actor, self.action, self.since = actor, action, since
        who = actor or "something else"
        super().__init__(f"{who} is already running {action} on this service")


class Engine:
    def __init__(self, config_getter, store=None, *,
                 on_event=None, on_health=None, on_machine=None,
                 on_action_done=None, on_stack_step=None, on_stack_done=None,
                 on_trigger=None, on_error=None, on_config_saved=None,
                 on_start_type=None):
        self._config = config_getter
        self.store = store if store is not None else st.store
        self._on_event = on_event
        self._on_health = on_health
        self._on_machine = on_machine
        self._on_action_done = on_action_done
        self._on_stack_step = on_stack_step
        self._on_stack_done = on_stack_done
        self._on_trigger = on_trigger
        self._on_error = on_error
        self._on_config_saved = on_config_saved
        self._on_start_type = on_start_type
        #: Set on stop(), so the start-type sweep leaves promptly rather than sleeping out its
        #: thirty seconds while the process waits to exit.
        self._stop_sweeping = threading.Event()

        #: action ids in flight, so a caller in another process can be told what
        #: happened to the one it asked for, and so shutdown can wait for them.
        self._in_flight: set = set()
        self._counter = itertools.count(1)
        #: one action per (machine, service) at a time — see act(). Held by the
        #: watchdog, the health restart, a stack step and a trigger alike, because
        #: all of them come through act().
        self._acting: dict = {}
        #: Who asked for the stack that is running, for the rows it writes.
        self._stack_actor = ""
        self._acting_lock = threading.RLock()
        #: set while a trigger's action is in flight, so its outcome is recorded
        self._pending_trigger = None
        #: the running bulk action, so one tally is reported instead of N
        self._bulk = None
        #: last unreachable note, so a machine down for an hour is logged once
        self._down_note = None

        # How a machine is reached is a property of the machine, so the transport
        # registry reads it from the config rather than being told per call.
        connectors.use_config(config_getter)

        history.attach(self.store, lambda: self._config().history.enabled)

        self.watchdog = Watchdog(
            config_getter=config_getter,
            control=control,
            store=self.store,
            notify=lambda title, text: self._notify(title, text),
            on_log=lambda event, note: history.record(event, note=note),
        )
        self.watchdog.attach(self.store)

        self.runner = stacks.Runner(control, self.store,
                                    on_log=lambda text: log.info(text))

        self.health = health_mod.Monitor(
            config_getter=config_getter,
            store=self.store,
            control=control,
            on_verdict=lambda svc, verdict, detail, _results:
                self._health_verdict(svc.name, svc.machine, verdict, detail),
            on_action=lambda svc, _action, detail:
                self._health_action(svc.name, svc.machine, detail),
        )

        # Remote machines cannot push, so they are asked. Local ones are not: the
        # SCM already tells us within ~32 ms.
        self.poller = poller_mod.Poller(
            config_getter=config_getter,
            on_status=self._on_polled,
            on_unreachable=self._on_unreachable)

        self.scheduler = schedule_mod.Scheduler(
            config_getter=config_getter,
            # Performed here, not handed to whoever is listening. It was handed out,
            # and a hub has nobody listening — so a hub install ran its schedule not at
            # all, silently, and its own Run now answered 200 having done nothing.
            on_fire=lambda trigger: self.run_trigger(trigger, actor="the schedule"),
            log=lambda text: log.info(text))

        self.watcher = scm.Watcher(
            get_names=lambda: [s.name for s in self._config().services
                               if not s.machine],
            on_change=self._on_scm,
            safety_query=control.query_status,
        )

        # The store's own events (a status changed) are carried out to the caller.
        self.store.subscribe(self._store_event)

    # -- config ------------------------------------------------------------
    def config(self):
        """The live config. A method because the caller passes a getter — the panel
        edits a copy, and the engine must always read the current one."""
        return self._config()

    def snapshot(self) -> dict:
        """Everything a client needs to draw its first frame. Imported here rather
        than at the top: wire imports nothing from the engine, and keeping it that way
        is what stops the format and the machinery growing into each other."""
        from . import wire
        return wire.snapshot(self)

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self.prime_states()
        self.watcher.start()
        self.poller.start()
        try:
            seeded = self.scheduler.seed_from(history.runs(kind="trigger", limit=200))
            if seeded:
                log.info("scheduler: %d trigger(s) already ran today", seeded)
        except Exception as exc:
            log.warning("could not read past trigger runs: %s", exc)
        self.scheduler.start()
        self.scheduler.run_startup_triggers()
        self.health.start()
        threading.Thread(target=self._sweep_start_types, daemon=True,
                         name="start-types").start()
        cfg = self._config()
        watched = sum(1 for s in cfg.services if s.health.active)
        log.info("started with %d service(s), %d stack(s), %d trigger(s), "
                 "%d health-checked",
                 len(cfg.services), len(cfg.stacks), len(cfg.triggers), watched)

    def stop(self) -> None:
        self._stop_sweeping.set()
        self.watcher.stop()
        self.poller.stop()
        self.scheduler.stop()
        self.watchdog.stop()
        self.health.stop()

    def trim_history(self) -> None:
        """Apply the retention window. Says so in the log when it drops anything,
        because "where did last month go" deserves an answer."""
        try:
            dropped = history.trim(self._config().history.retention_days)
        except Exception:
            log.exception("could not trim history")
            return
        if dropped:
            log.info("history: dropped %d rows past %d days", dropped,
                     self._config().history.retention_days)

    def poll_start_types(self) -> bool:
        """Notice a service being disabled or re-enabled outside this app. Returns
        whether anything changed, so a caller can repaint only when it did.

        Local services only, and meant for a 30-second timer: reading a start type
        costs 0.2 ms here and a network round trip elsewhere, so including remote
        ones froze the window every half minute. A remote machine reports its start
        types with each poll instead — see poller.
        """
        changed = False
        for svc in self._config().services:
            if svc.machine:
                continue
            try:
                found = control.start_type(svc.name, svc.machine)
            except Exception:
                continue
            if found != self.store.start_type(svc.name, svc.machine):
                self.store.set_start_type(svc.name, found, machine=svc.machine)
                changed = True
                log.info("%s start type is now %s", svc.name, found or "unknown")
                # Said, not just stored. A start type is configuration rather than status, so
                # nothing pushes it — and a client that only hears status events would show a
                # Start button on a service Windows will refuse to start until it happened to
                # take a fresh snapshot.
                self._call(self._on_start_type, service=svc.name, machine=svc.machine or "",
                           start_type=found,
                           disabled=self.store.is_disabled(svc.name, svc.machine))
        return changed

    #: How often the start types are re-read. Configuration, so nothing announces it: this is
    #: the only way "somebody disabled it in services.msc" is ever noticed.
    #:
    #: Five seconds, because it was measured rather than guessed. On this hardware one read is
    #: **0.205 ms** against a held SCM handle, so a sweep is 0.83 ms for the four services on a
    #: typical box and 5.97 ms for thirty — more than anybody watches on one machine. At five
    #: seconds that is 0.017% of one core in the first case and 0.12% in the second, and no
    #: network traffic at all: a sweep that finds nothing changed sends nothing. Thirty seconds
    #: bought nothing measurable and cost half a minute of showing a Start button on a service
    #: Windows would refuse to start.
    START_TYPE_SECONDS = 5

    def _sweep_start_types(self) -> None:
        """The thirty-second re-read, on its own thread.

        In the engine rather than in a QTimer in app.py, which is where it was: a hub has no
        Qt, so on a hub install nothing ever re-read a start type and disabling a service
        outside the app reached no screen at all.
        """
        while not self._stop_sweeping.wait(self.START_TYPE_SECONDS):
            try:
                self.poll_start_types()
            except Exception:
                log.exception("re-reading the start types failed")

    def prime_states(self) -> None:
        """Fill the store before the first paint so nothing shows as Unknown.

        This computer only. Asking another machine here would be asking on whatever
        thread called start(): the local SCM answers in a fraction of a millisecond,
        a remote one took fifteen seconds, a firewalled one forty-two. Remote states
        arrive from the poller, which has a thread for waiting in.
        """
        for svc in self._config().services:
            if svc.machine:
                self.poller.poll_soon(svc.machine)
                continue
            try:
                status = control.query_status(svc.name, svc.machine)
            except Exception:
                status = st.UNKNOWN
            self.store.update(svc.name, status, machine=svc.machine)
            try:
                self.store.set_start_type(svc.name,
                                          control.start_type(svc.name, svc.machine),
                                          machine=svc.machine)
            except Exception:
                pass

    def refresh(self, machine: str = None) -> None:
        """Ask again now. This computer directly; other machines through the poller,
        which is the only thing allowed to wait for them."""
        self.poller.poll_soon(machine)
        for svc in self._config().services:
            if svc.machine:
                continue
            try:
                self.store.update(svc.name,
                                  control.query_status(svc.name, svc.machine),
                                  machine=svc.machine)
                self.store.set_start_type(svc.name,
                                          control.start_type(svc.name, svc.machine),
                                          machine=svc.machine)
            except Exception:
                pass

    # -- core events -------------------------------------------------------
    def _on_scm(self, name, status, exit_code=0, pid=0):
        self.store.update(name, status, exit_code=exit_code, pid=pid)

    def _on_polled(self, name, machine, status):
        """A poller answer, on the poller's thread."""
        if status is None:
            # Unreachable. Not "stopped" — we do not know, and saying Stopped about
            # a server that is merely unreachable would send someone to the wrong
            # fix.
            self.store.update(name, st.UNKNOWN, machine=machine)
            if machine:
                # And the machine is *recorded* as not answering. Without this the chip
                # stayed on "not asked yet", which is a different thing and sends somebody
                # looking for a poller that never ran instead of at a machine that will
                # not answer. Found on 2026-07-28: sc-sql showed `waiting` while Test
                # connection said `answered`.
                self.store.note_machine(machine, False, "it did not answer")
                self._call(self._on_machine, machine=machine, reachable=False,
                           detail="it did not answer")
            return
        self.store.update(name, status.state, exit_code=status.exit_code,
                          pid=status.pid, machine=machine)
        if status.start_type:
            self.store.set_start_type(name, status.start_type, machine=machine)
        self.store.note_machine(machine, True)
        self._call(self._on_machine, machine=machine, reachable=True, detail="")

    def _on_unreachable(self, machine, why):
        """Said once per outage, not once per interval: a machine down for an hour
        must not write 720 identical lines into the log."""
        self.store.note_machine(machine, False, why)
        self._call(self._on_machine, machine=machine, reachable=False, detail=why)
        if self._down_note == (machine, why):
            return
        self._down_note = (machine, why)
        log.warning("%s is not answering — %s", machine or "this computer", why)

    def _store_event(self, event):
        """A status changed. Judge health from it, then carry it out to the caller."""
        machine = event.state.machine
        if event.status == st.RUNNING:
            self.note_started(event.name, machine)
        elif not st.is_pending(event.status):
            self.health.note_stopped(event.name, machine)
            self._copy_verdict(event.name, machine)
        self._call(self._on_event, event=event)

    # -- health ------------------------------------------------------------
    def _health_verdict(self, name, machine, verdict, detail):
        """A service changed between answering and not, on the monitor's thread."""
        self.store.set_health(name, verdict, detail, machine=machine)
        # "starting" is not something that happened to a service, it is us saying we
        # do not know yet. Writing it down would put a row in the timeline after
        # every restart, next to the restart that already explains it.
        if self._config().history.enabled and verdict != health_mod.STARTING:
            history.record_health(name, verdict, detail, machine=machine)
        self._call(self._on_health, service=name, machine=machine,
                   verdict=verdict, detail=detail)

    def _health_action(self, name, machine, detail):
        """Health checks asked for a restart. Recorded as its own cause, so the
        history says why rather than showing an unexplained restart."""
        log.info("health restart of %s: %s", name, detail)
        if self._config().history.enabled:
            history.record_action(name, "restart", st.SRC_HEALTH,
                                  machine=machine, note=detail[:200])
        try:
            self.act("restart", name, machine, actor=st.SRC_HEALTH)
        except Busy:
            log.info("health restart of %s skipped: already being acted on", name)

    def note_started(self, name: str, machine: str = "") -> None:
        """Tell the monitor a service has just started, and publish what it says.

        Said explicitly rather than inferred from a status change, because the change
        is often never seen: store.update publishes only when the status *string*
        differs, and `systemctl restart` returns with the unit already active. The
        app asked, got "Running", saw no difference and stayed quiet — so a restart
        went straight back to green with no warm-up on the surface being watched.
        """
        self.health.note_running(name, machine)
        self._copy_verdict(name, machine)

    def _copy_verdict(self, name: str, machine: str = "") -> None:
        """Copy the monitor's verdict into the store, whatever it is. Not a hard-coded
        "unknown": that overwrote the verdict note_running had just published, which
        is why "Starting…" never reached the screen while the monitor produced it."""
        self.store.set_health(name, self.health.verdict(name, machine),
                              self.health.detail(name, machine), machine=machine)

    # -- actions -----------------------------------------------------------
    def act(self, action: str, service: str, machine: str = "",
            actor: str = "", bulk: bool = False, then=None) -> str:
        """Do it, on a worker thread, and answer with an id.

        An id rather than a name because two of the same action can be in flight, and
        "the restart finished" then answers the wrong question. Raises Busy if this
        service is already being acted on — by another client, or by the watchdog.

        `then(error, status)` is called when it lands, on the worker's thread. It exists
        because registering interest *after* this returns is a race the short actions win:
        a stop of an already-stopping service finished before the caller had written down
        that it was waiting, and the outcome was lost. Captured in the closure instead.
        """
        if action == "kill":
            return self.kill(service, machine, actor)

        key = (machine or "", service)
        with self._acting_lock:
            held = self._acting.get(key)
            if held is not None:
                raise Busy(held["actor"], held["action"], held["at"])
            self._acting[key] = {"actor": actor, "action": action, "at": time.time()}

        action_id = self._next_id()
        if self._config().history.enabled:
            history.record_action(service, action, st.SRC_PANEL, machine=machine,
                                  actor=actor)

        def work():
            error = None
            try:
                if action in ("stop", "restart"):
                    self.store.expect_stop(service, machine)
                getattr(control, f"{action}_service")(service, machine=machine)
            except Exception as exc:
                # "The service has not been started" when stopping something already
                # stopped is not a failure, so it isn't reported as one.
                harmless = control.nothing_to_do(exc)
                if harmless:
                    log.info("%s %s: nothing to do, %s", action, service, harmless)
                else:
                    error = getattr(exc, "strerror", None) or str(exc)
                    # Logged, not only handed back: a dialog is gone the moment it is
                    # dismissed, and the one failure that mattered left nothing behind
                    # but a line saying it was harmless.
                    log.warning("%s %s%s failed: %s", action, service,
                                f" on {machine}" if machine else "", error)
                self.store.clear_expected(service, machine)
            # Asked here, on this thread, and carried out: it is the same round trip
            # the action just made, and the caller's handler runs on its own thread.
            try:
                status = control.query_status(service, machine)
            except Exception:
                status = ""
            if status:
                self.store.update(service, status, machine=machine,
                                  source=st.SRC_PANEL)
                if error is None and action in ("start", "restart") \
                        and status == st.RUNNING:
                    self.note_started(service, machine)
            with self._acting_lock:
                self._acting.pop(key, None)
            self._finished(action_id)
            self._call(self._on_action_done, id=action_id, action=action,
                       service=service, machine=machine, error=error,
                       status=status, actor=actor, bulk=bulk)
            if then is not None:
                self._call(then, error=error, status=status)

        self._in_flight.add(action_id)
        threading.Thread(target=work, daemon=True,
                         name=f"act-{action}-{service}").start()
        return action_id

    def kill(self, service: str, machine: str = "", actor: str = "") -> str:
        """Terminate the process outright, on a worker thread. Returns an id like
        act. No confirmation here — that belongs to whoever called it."""
        key = (machine or "", service)
        with self._acting_lock:
            held = self._acting.get(key)
            if held is not None:
                raise Busy(held["actor"], held["action"], held["at"])
            self._acting[key] = {"actor": actor, "action": "kill", "at": time.time()}
        action_id = self._next_id()
        if self._config().history.enabled:
            history.record_action(service, "kill", st.SRC_PANEL, machine=machine,
                                  actor=actor)

        def work():
            error = None
            try:
                self.store.expect_stop(service, machine)
                if not control.process_id(service, machine):
                    log.info("kill %s: no process to kill", service)
                else:
                    control.kill_process(service, machine)
            except Exception as exc:
                self.store.clear_expected(service, machine)
                if not control.nothing_to_do(exc):
                    error = getattr(exc, "strerror", None) or str(exc)
            try:
                status = control.query_status(service, machine)
            except Exception:
                status = ""
            if status:
                self.store.update(service, status, machine=machine,
                                  source=st.SRC_PANEL)
            with self._acting_lock:
                self._acting.pop(key, None)
            self._finished(action_id)
            self._call(self._on_action_done, id=action_id, action="kill",
                       service=service, machine=machine, error=error,
                       status=status, actor=actor, bulk=False)

        self._in_flight.add(action_id)
        threading.Thread(target=work, daemon=True,
                         name=f"kill-{service}").start()
        return action_id

    # -- stacks and triggers ----------------------------------------------
    def run_trigger(self, trigger_or_name, actor: str = "") -> bool:
        """Do what a trigger says. The scheduler's path and Run now's, on either install.

        This used to live in the Qt layer, reached through `on_trigger` — which is None on a
        hub, so a hub ran its schedule not at all. It belongs here: the engine is the half
        that acts, and the only half a hub has.

        Returns whether anything was *started*. An outcome — done, skipped or failed —
        arrives through `on_trigger` when it is known, which for a service or a stack is
        after the work, not now.
        """
        cfg = self._config()
        trigger = (cfg.trigger(trigger_or_name)
                   if isinstance(trigger_or_name, str) else trigger_or_name)
        if trigger is None:
            return False
        began = time.monotonic()

        def finish(outcome: str, detail: str = ""):
            """The history row and the notification, for an outcome known at any point."""
            history.record_run("trigger", trigger.name, outcome,
                               seconds=time.monotonic() - began, detail=detail,
                               source=st.SRC_SCHEDULE)
            self._call(self._on_trigger, trigger=trigger, outcome=outcome,
                       detail=detail)

        if trigger.action == "service":
            if not trigger.service:
                finish("failed", "no service chosen")
                return False
            # Already there is *skipped*, not failed: "start AppEngine" at 03:00 when it is
            # already running is the normal case, and it used to raise "service already
            # running" in somebody's face at three in the morning.
            target = {"start": st.RUNNING, "stop": st.STOPPED}.get(
                trigger.service_action)
            current = self.store.status_of(trigger.service, trigger.machine)
            if target and current == target:
                log.info("trigger %r skipped: %s is already %s", trigger.name,
                         trigger.service, current.lower())
                finish("skipped", f"{trigger.service} was already {current.lower()}")
                return False
            try:
                self.act(trigger.service_action, trigger.service, trigger.machine,
                         actor=actor or "the schedule",
                         then=lambda error, status: finish(
                             "failed" if error else "done", error or ""))
            except Busy as clash:
                finish("skipped", str(clash))
                return False
            return True

        stack = cfg.stack(trigger.stack)
        if not stack or not stack.steps:
            finish("skipped", "the stack has no steps")
            return False
        # Handed to run_stack rather than written down after it returns. A one-step stack
        # that fails immediately can finish before the caller has recorded that it is
        # waiting, and the outcome was then lost — the same race `act(then=…)` exists for.
        # It showed up once in a full-suite run and passed a hundred times alone, which is
        # how a race announces itself.
        if not self.run_stack(stack, actor=actor or "the schedule", then=finish):
            finish("skipped", "a stack run is already in progress")
            return False
        return True

    def run_stack(self, stack_or_name, actor: str = "", then=None) -> bool:
        """Run a stack by name, or a Stack object (a test run from Settings uses the
        values on screen, not the saved ones). Returns False if it could not start —
        already running, or empty."""
        cfg = self._config()
        stack = (cfg.stack(stack_or_name)
                 if isinstance(stack_or_name, str) else stack_or_name)
        if not stack or not stack.steps:
            return False
        if self.runner.busy:
            return False
        # One stack runs at a time (the guard above), so the actor can be held on the
        # engine rather than threaded through the runner's callbacks — which belong to
        # the stack's own steps and have no room for it.
        self._stack_actor = actor
        machine_for = {s.name: s.machine for s in cfg.services}

        def work():
            result = self.runner.run(
                stack,
                on_step=lambda i, total, svc, act, phase:
                    self._call(self._on_stack_step, index=i, total=total,
                               service=svc, action=act, phase=phase),
                machine_for=lambda n: machine_for.get(n, ""))
            # Whether a trigger owns this run travels *with* it. Whoever shows a
            # notification has to know, or a trigger's stack is announced twice: once as
            # the trigger's outcome and once as a stack somebody asked for.
            self._call(self._on_stack_done, result=result, by_trigger=then is not None)
            waiting = then
            if waiting is not None:
                # `ok` and `cancelled` are what a RunResult actually has, and `summary()` is
                # the sentence it words for a notification — so a trigger's history row says
                # what the stack said rather than a second version of it.
                outcome = ("done" if getattr(result, "ok", False)
                           else "cancelled" if getattr(result, "cancelled", False)
                           else "failed")
                waiting(outcome, result.summary() if hasattr(result, "summary") else "")

        threading.Thread(target=work, daemon=True, name="stack").start()
        return True

    def stack_step_landed(self, service, action, phase) -> None:
        """The state-touching half of a stack step, kept out of the on_stack_step
        callback so the UI half (busy labels) can live with the caller. A stack step
        starts a service just as a button does, and the status it leaves is just as
        likely to be unchanged."""
        if phase == "begin" and self._config().history.enabled:
            history.record_action(service, action, st.SRC_STACK,
                                  note="stack step", actor=self._stack_actor)
        elif phase == "ok" and action in ("start", "restart"):
            machine = next((s.machine for s in self._config().services
                            if s.name == service), "")
            if self.store.status_of(service, machine) == st.RUNNING:
                self.note_started(service, machine)

    def record_stack_run(self, result) -> str:
        """Write a finished stack run to history and return its outcome word."""
        outcome = ("cancelled" if result.cancelled
                   else "success" if result.ok else "failed")
        history.record_run("stack", result.stack, outcome,
                           seconds=sum(s.seconds for s in result.steps),
                           detail=result.summary(), source=st.SRC_STACK,
                           actor=self._stack_actor)
        return outcome

    # -- config ------------------------------------------------------------
    def save_config(self, new_cfg) -> None:
        """Persist a new config and let go of only the machines that changed.

        Dropping every connection on every save cost a fresh connection to each
        machine, and to one remote Windows box that is twenty-one seconds of
        "Unknown" for changing an unrelated setting.
        """
        old = self._config()
        for name in self.machines_changed(old, new_cfg):
            connectors.forget(name)
        cfg_mod.save(new_cfg)
        self.store.keep_only([(s.machine, s.name) for s in new_cfg.services])
        self._call(self._on_config_saved, config=new_cfg)
        log.info("settings saved: %d service(s), %d stack(s)",
                 len(new_cfg.services), len(new_cfg.stacks))

    @staticmethod
    def machines_changed(old, new) -> list:
        """Machines whose settings differ between two configs, plus any that have
        appeared or gone. A changed password is not visible here — it lives in the
        secret store — so the panel drops that machine's connection when it stores
        one."""
        before = {m.name: m for m in getattr(old, "machines", [])}
        after = {m.name: m for m in getattr(new, "machines", [])}
        return [name for name in set(before) | set(after)
                if before.get(name) != after.get(name)]

    # -- machinery ---------------------------------------------------------
    def wait_for_actions(self, timeout: float = 10.0) -> bool:
        """For tests and for a clean shutdown: True if everything finished in time."""
        deadline = time.monotonic() + timeout
        while self._in_flight and time.monotonic() < deadline:
            time.sleep(0.02)
        return not self._in_flight

    def _next_id(self) -> str:
        # Not time-based: Date.now-style ids collide when two land in the same ms,
        # and the counter is enough to tell them apart within one run.
        return f"a{next(self._counter)}"

    def _finished(self, action_id: str) -> None:
        self._in_flight.discard(action_id)

    def _notify(self, title: str, text: str) -> None:
        """The watchdog's notify hook. Surfaced as an error-channel message so the
        caller decides whether it is a toast, a log line or nothing."""
        self._call(self._on_error, kind="notify", text=text)

    #: The callbacks a second listener may be added to. Named, so a typo is a refusal
    #: rather than a listener quietly attached to nothing.
    LISTENABLE = ("event", "health", "machine", "action_done", "stack_step",
                  "stack_done", "trigger", "error", "config_saved", "start_type")

    def also_on(self, kind: str, fn) -> None:
        """Add a listener beside whoever already has one.

        The hub server is built after the engine — it needs one to serve — so it cannot pass
        these in at construction time, and replacing a callback would silence whoever already
        had it: the hub's own `config_saved` listener is what swaps the config every other
        part reads.
        """
        if kind not in self.LISTENABLE:
            raise ValueError(f"nothing listens to {kind!r}; one of {self.LISTENABLE}")
        self._chain(f"_on_{kind}", fn)

    def _chain(self, attribute: str, fn) -> None:
        """Add a listener beside whoever already has one.

        The hub server is built after the engine — it needs one to serve — so it cannot pass
        these in at construction time, and replacing a callback would silence whoever already
        had it. A raising listener still cannot take the engine down: both go through _call.
        """
        first = getattr(self, attribute)

        def both(**facts):
            self._call(first, **facts)
            self._call(fn, **facts)

        setattr(self, attribute, both)

    def _call(self, callback, **facts) -> None:
        """A listener that raises must not take the engine down with it: it belongs
        to whoever is watching, and the engine has services to look after."""
        if callback is None:
            return
        try:
            callback(**facts)
        except Exception:
            log.exception("a listener failed handling an engine event")
