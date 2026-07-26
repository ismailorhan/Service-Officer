"""Service Officer — application entry point.

Wiring only: build the core (config, status store, SCM notifications, watchdog,
history, stack runner), build the Qt interface, and connect them. Everything of
substance lives in core/ or ui/.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

import autostart
from core import (applog, config as cfg_mod, connectors, control, db, health,
                  history, poller as poller_mod, scm, schedule, stacks)
from core import state as st
from core.watchdog import Watchdog
from ui import flyout as flyout_mod, hover as hover_mod, icons, panel as panel_mod
from ui import theme
from ui.tray import StateBridge, Tray

log = applog.get("app")


class StackSignals(QObject):
    """A stack run happens on a worker thread; these carry it back to the UI."""
    step = Signal(int, int, str, str, str)      # index, total, service, action, phase
    done = Signal(object)


class TriggerSignals(QObject):
    """A trigger fires on the scheduler's thread; the work belongs on the UI one."""
    fire = Signal(object)


class PollSignals(QObject):
    """A polled status arrives on the poller's thread; the store belongs to Qt's.

    `status` carries None when the machine could not be reached at all, which is
    a different thing from a service being stopped and has to stay different all
    the way to the screen.
    """
    status = Signal(str, str, object)        # service, machine, Status or None
    unreachable = Signal(str, str)           # machine, why


class HealthSignals(QObject):
    """A health verdict lands on the monitor's thread; the UI belongs on Qt's."""
    verdict = Signal(str, str, str, str)     # service, machine, verdict, detail
    act = Signal(str, str, str)              # service, machine, why


class ActionSignals(QObject):
    """Same for a single service action.

    It has to be a signal, not QTimer.singleShot: a worker thread has no Qt
    event loop, so a timer started there never fires — which left the tray
    spinning forever after every start/stop because the completion handler that
    clears the busy counter was never called.
    """
    #: service, machine, action, error, announce errors, part of a bulk run, and
    #: the status the service ended up in.
    #: The flags travel with the result rather than living on the app: a bulk
    #: action has several of these in flight at once, and a shared attribute
    #: would be whatever the last caller set. The status travels with it because
    #: asking for it in the handler meant asking from the thread that paints — on
    #: another machine that is a network round trip, and the window stops redrawing
    #: for the length of it.
    done = Signal(str, str, str, object, bool, bool, str)


class Application(QObject):
    def __init__(self, argv):
        super().__init__()
        self.qt = QApplication(argv)
        self.qt.setApplicationName("Service Officer")
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setWindowIcon(icons.base_icon("green"))

        self.cfg = cfg_mod.load()
        # How a machine is reached is a property of the machine, so the transport
        # registry reads it from the config rather than being told per call.
        connectors.use_config(lambda: self.cfg)
        self.store = st.store
        #: set while a trigger's action is in flight, so its outcome is recorded
        self._pending_trigger = None
        #: the running bulk action, so one tally is reported instead of N dialogs
        self._bulk = None
        theme.set_mode(self.cfg.theme)
        self.qt.setStyleSheet(theme.sheet())
        # With "system" chosen, follow Windows when it flips light/dark.
        try:
            self.qt.styleHints().colorSchemeChanged.connect(
                lambda _s: self.apply_theme(self.cfg.theme))
        except Exception:
            pass

        # --- core ---------------------------------------------------------
        history.attach(self.store, lambda: self.cfg.history.enabled)
        # Once shortly after start, then daily. A server runs for weeks without
        # this app being restarted, and retention that only ran at startup was
        # retention that never ran.
        QTimer.singleShot(3000, self._trim_history)
        self._trim_timer = QTimer(self)
        self._trim_timer.setInterval(24 * 60 * 60 * 1000)
        self._trim_timer.timeout.connect(self._trim_history)
        self._trim_timer.start()

        self.watchdog = Watchdog(
            config_getter=lambda: self.cfg,
            control=control,
            store=self.store,
            notify=lambda title, text: self.tray.notify(title, text),
            on_log=lambda event, note: history.record(event, note=note),
        )
        self.watchdog.attach(self.store)

        self.runner = stacks.Runner(control, self.store,
                                    on_log=lambda text: log.info(text))

        self.health_signals = HealthSignals()
        self.health_signals.verdict.connect(self._on_health_verdict)
        self.health_signals.act.connect(self._on_health_action)
        self.health = health.Monitor(
            config_getter=lambda: self.cfg,
            store=self.store,
            control=control,
            on_verdict=lambda svc, verdict, detail, _results:
                self.health_signals.verdict.emit(svc.name, svc.machine,
                                                 verdict, detail),
            on_action=lambda svc, _action, detail:
                self.health_signals.act.emit(svc.name, svc.machine, detail),
        )

        # Remote machines cannot push, so they are asked. Local ones are not:
        # the SCM already tells us within ~32 ms.
        self.poll_signals = PollSignals()
        self.poll_signals.status.connect(self._on_polled)
        self.poll_signals.unreachable.connect(self._on_unreachable)
        self.poller = poller_mod.Poller(
            config_getter=lambda: self.cfg,
            on_status=lambda name, machine, status:
                self.poll_signals.status.emit(name, machine, status),
            on_unreachable=lambda machine, why:
                self.poll_signals.unreachable.emit(machine, why))

        self.scheduler = schedule.Scheduler(
            config_getter=lambda: self.cfg,
            on_fire=lambda trigger: self.trigger_signals.fire.emit(trigger),
            log=lambda text: log.info(text))

        # --- ui -----------------------------------------------------------
        self.tray = Tray(lambda: self.cfg, self.store)
        self.flyout = flyout_mod.Flyout(lambda: self.cfg, self.store)
        self.hover = hover_mod.HoverCard(lambda: self.cfg, self.store)
        self.panel = None

        self.bridge = StateBridge()
        self.bridge.changed.connect(self._on_state_event)
        self.bridge.attach(self.store)

        self.stack_signals = StackSignals()
        self.stack_signals.step.connect(self._on_stack_step)
        self.stack_signals.done.connect(self._on_stack_done)

        self.action_signals = ActionSignals()
        self.action_signals.done.connect(self._action_done)

        self.trigger_signals = TriggerSignals()
        self.trigger_signals.fire.connect(self.run_trigger)

        self.tray.left_clicked.connect(self._toggle_flyout)
        self.tray.hover.connect(self._on_hover)
        self.tray.panel_requested.connect(self.open_panel)
        self.tray.services_requested.connect(self._open_services_mmc)
        self.tray.refresh_requested.connect(self.refresh)
        self.tray.quit_requested.connect(self.quit)
        self.tray.stack_requested.connect(self.run_stack)
        self.tray.menu_opened.connect(self.hover.dismiss)

        self._wire_flyout()

        # Start type is not pushed to us — the SCM only reports status — so
        # disabling a service in services.msc was invisible until someone hit
        # Refresh. Reading it costs 0.2 ms per service, measured, so poll.
        self.start_type_timer = QTimer(self)
        self.start_type_timer.setInterval(30_000)
        self.start_type_timer.timeout.connect(self._poll_start_types)
        self.start_type_timer.start()

        # --- SCM push notifications ---------------------------------------
        self.watcher = scm.Watcher(
            get_names=lambda: [s.name for s in self.cfg.services if not s.machine],
            on_change=self._on_scm,
            safety_query=control.query_status,
        )

    def _wire_flyout(self):
        """One place for the flyout's connections — it is rebuilt on a theme
        change, and a signal wired in only one of the two places is a bug that
        appears hours later."""
        self.flyout.action_requested.connect(self.do_action)
        self.flyout.bulk_requested.connect(self.do_bulk)
        self.flyout.run_stack.connect(self.run_stack)
        self.flyout.open_settings.connect(lambda: self.open_panel())
        self.flyout.open_services_mmc.connect(self._open_services_mmc)

    def _trim_history(self):
        """Apply the retention window. Says so in the log when it drops anything,
        because "where did last month go" deserves an answer."""
        try:
            dropped = history.trim(self.cfg.history.retention_days)
        except Exception:
            log.exception("could not trim history")
            return
        if dropped:
            log.info("history: dropped %d rows past %d days", dropped,
                     self.cfg.history.retention_days)

    def _poll_start_types(self):
        """Notice a service being disabled or re-enabled outside this app.

        Local services only, and on a 30-second timer: reading a start type costs
        0.2 ms here and a network round trip elsewhere, so including remote ones
        froze the window every half minute for as long as they took. A remote
        machine reports its start types with each poll instead — see poller.
        """
        changed = False
        for svc in self.cfg.services:
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
        if changed:
            if self.flyout.isVisible():
                self.flyout.apply_states()
            self.hover.refresh()

    # -- startup -----------------------------------------------------------
    def start(self) -> int:
        self._prime_states()
        self.tray.show()
        self.tray.apply_state()
        self.watcher.start()
        self.poller.start()
        # What already ran today is on disk, so a restart doesn't repeat it.
        try:
            seeded = self.scheduler.seed_from(history.runs(kind="trigger", limit=200))
            if seeded:
                log.info("scheduler: %d trigger(s) already ran today", seeded)
        except Exception as exc:
            log.warning("could not read past trigger runs: %s", exc)
        self.scheduler.start()
        self.scheduler.run_startup_triggers()
        self.health.start()
        watched = sum(1 for s in self.cfg.services if s.health.active)
        log.info("started with %d service(s), %d stack(s), %d trigger(s), "
                 "%d health-checked",
                 len(self.cfg.services), len(self.cfg.stacks),
                 len(self.cfg.triggers), watched)
        return self.qt.exec()

    def _prime_states(self):
        """Fill the store before the first paint so nothing shows as Unknown.

        This computer only. Asking another machine here is asking on the thread that
        paints: the local SCM answers in a fraction of a millisecond, a remote one
        took fifteen seconds, and a firewalled one forty-two — each of them a window
        that has stopped redrawing and says "Not Responding". Remote states arrive
        from the poller, which has a thread for waiting in, within its interval.
        """
        for svc in self.cfg.services:
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

    # -- core events -------------------------------------------------------
    def _on_scm(self, name, status, exit_code=0, pid=0):
        self.store.update(name, status, exit_code=exit_code, pid=pid)

    def _on_polled(self, name, machine, status):
        """GUI thread: an answer from a machine we had to ask."""
        if status is None:
            # Unreachable. Not "stopped" — we do not know, and saying Stopped
            # about a server that is merely unreachable would send someone to fix
            # the wrong thing.
            self.store.update(name, st.UNKNOWN, machine=machine)
            return
        self.store.update(name, status.state, exit_code=status.exit_code,
                          pid=status.pid, machine=machine)
        if status.start_type:
            self.store.set_start_type(name, status.start_type, machine=machine)

    def _on_unreachable(self, machine, why):
        """Said once per outage, not once per interval: a machine that is down for
        an hour must not write 720 identical lines into the log."""
        if getattr(self, "_down_note", None) == (machine, why):
            return
        self._down_note = (machine, why)
        log.warning("%s is not answering — %s", machine or "this computer", why)

    # -- health ------------------------------------------------------------
    def _on_health_verdict(self, name, machine, verdict, detail):
        """GUI thread: a service changed between answering and not."""
        self.store.set_health(name, verdict, detail, machine=machine)
        # "starting" is not something that happened to a service, it is us saying
        # we do not know yet. Writing it down would put a row in the timeline
        # after every restart, next to the restart that already explains it.
        if self.cfg.history.enabled and verdict != health.STARTING:
            history.record_health(name, verdict, detail, machine=machine)
        self._refresh_lists()
        label = next((s.display() for s in self.cfg.services
                      if s.name == name and (s.machine or "") == (machine or "")),
                     name)
        if verdict == health.UNHEALTHY and self.cfg.notifications.on_crash:
            # Reuses the crash switch on purpose: "it stopped working" is the
            # same news to whoever is on call, whether the process died or not.
            self.tray.notify("Service Officer",
                             f"{label} is running but not responding.")
        elif verdict == health.HEALTHY and self.cfg.notifications.on_recovery:
            self.tray.notify("Service Officer", f"{label} is responding again.")

    def _on_health_action(self, name, machine, detail):
        """Health checks asked for a restart. Recorded as its own cause, so the
        history says why rather than showing an unexplained restart."""
        log.info("health restart of %s: %s", name, detail)
        if self.cfg.history.enabled:
            history.record_action(name, "restart", st.SRC_HEALTH,
                                  machine=machine, note=detail[:200])
        self.do_action("restart", name, machine, announce_errors=False)

    def _refresh_lists(self):
        """Everything that shows a service's state, in one place.

        The tray icon belongs here. It was only repainted from the SCM event
        handler, so a health verdict — which is not an SCM event — updated the
        store and left the icon green until something unrelated happened. The
        icon is the only thing most people look at.
        """
        self.tray.apply_state()
        self.hover.refresh()
        if self.flyout.isVisible():
            self.flyout.apply_states()
        if self.panel is not None and self.panel.isVisible():
            self.panel.dashboard.apply_states()

    def _on_state_event(self, event):
        """GUI thread: refresh whatever is on screen."""
        self._refresh_lists()
        # Health is judged from when a service reached Running, and a stopped
        # service is not unhealthy — it is stopped.
        machine = event.state.machine
        if event.status == st.RUNNING:
            self._note_started(event.name, machine)
            if self.cfg.notifications.on_recovery and \
                    self.watchdog.attempts_for(event.name, machine):
                self.tray.notify("Service Officer", f"{event.name} is running again.")
        elif not st.is_pending(event.status):
            self.health.note_stopped(event.name, machine)
            self._copy_verdict(event.name, machine)

    def _note_started(self, name: str, machine: str = "") -> None:
        """Tell the monitor a service has just started, and show what it says.

        Said explicitly rather than left to be inferred from a status change,
        because the change is often never seen: `store.update` publishes only when
        the status *string* differs, and `systemctl restart` returns with the unit
        already active again. The app asked, got "Running", saw no difference and
        stayed quiet — so a restart from the panel went straight back to green with
        no warm-up at all, on exactly the surface someone was watching.
        """
        self.health.note_running(name, machine)
        self._copy_verdict(name, machine)

    def _copy_verdict(self, name: str, machine: str = "") -> None:
        """Copy the monitor's verdict into the store, whatever it is.

        Not a hard-coded "unknown": that overwrote the verdict note_running had
        *just* published, which is why "Starting…" never reached the screen while
        the monitor was producing it correctly. The monitor is the authority on
        health; this only carries its answer to the widgets.
        """
        self.store.set_health(name, self.health.verdict(name, machine),
                              self.health.detail(name, machine), machine=machine)

    # -- actions -----------------------------------------------------------
    def do_action(self, action: str, name: str, machine: str = "",
                  announce_errors: bool = True, bulk: bool = False):
        if action == "kill":
            self.kill_process(name, machine)
            return
        verb = {"start": "Starting", "stop": "Stopping", "restart": "Restarting"}[action]
        self._mark_busy(name, machine, verb + "…")
        self.tray.action_started()
        if self.cfg.history.enabled:
            history.record_action(name, action, st.SRC_PANEL, machine=machine)

        def work():
            error = None
            try:
                if action in ("stop", "restart"):
                    self.store.expect_stop(name, machine)
                getattr(control, f"{action}_service")(name, machine=machine)
            except Exception as exc:
                # "The service has not been started" when stopping something
                # already stopped is not a failure, so it isn't reported as one.
                harmless = control.nothing_to_do(exc)
                if harmless:
                    log.info("%s %s: nothing to do, %s", action, name, harmless)
                else:
                    error = getattr(exc, "strerror", None) or str(exc)
                self.store.clear_expected(name, machine)
            # Asked here, on this thread, and carried to the handler: it is the same
            # round trip the action just made, and the handler runs on the UI thread.
            try:
                status = control.query_status(name, machine)
            except Exception:
                status = ""
            self.action_signals.done.emit(name, machine, action, error,
                                          announce_errors, bulk, status)
        threading.Thread(target=work, daemon=True).start()

    def _action_done(self, name, machine, action, error, announce=True,
                     bulk=False, status=""):
        self.tray.action_finished()
        # `status` was read on the worker thread. Falling back to asking is for the
        # tests that call this directly; the running app always brings it along.
        if not status:
            try:
                status = control.query_status(name, machine)
            except Exception:
                status = ""
        if status:
            self.store.update(name, status, machine=machine, source=st.SRC_PANEL)
            # We started it, so we know it just started — even if the status
            # published nothing because it read "Running" before and after.
            if error is None and action in ("start", "restart") \
                    and status == st.RUNNING:
                self._note_started(name, machine)
        self._clear_busy(name, machine)
        if self.flyout.isVisible():
            self.flyout.apply_states()
        if self.panel is not None and self.panel.isVisible():
            self.panel.dashboard.apply_states()

        if bulk:
            self._bulk_report(name, error)
            return

        pending = self._pending_trigger
        self._pending_trigger = None
        if pending is not None:
            _trigger, finish = pending
            finish("failed" if error else "success", error or "")
        elif error and announce:
            QMessageBox.warning(None, "Service Officer",
                                f"Could not {action} '{name}':\n{error}")

    def do_bulk(self, action: str, targets: list):
        """Apply one action to several services at once.

        Asked once for the whole batch, not per service, and reported once at the
        end: a dialog per failure is what made the old per-row loop unusable.
        Services that can't take the action, or don't need it, are set aside up
        front rather than failing one by one — stopping a stopped service is not a
        problem worth a warning, it is nothing to do.
        """
        chosen = [(n, m or "") for n, m in targets]
        skipped = []
        #: what each action is trying to achieve, so a service already there can
        #: be left alone. Restart has no such state — it always does something.
        settled = {"start": st.RUNNING, "stop": st.STOPPED, "kill": st.STOPPED}
        for name, machine in list(chosen):
            reason = None
            if action == "start" and self.store.is_disabled(name, machine):
                reason = "disabled in Windows"
            elif action == "kill" and machine:
                reason = f"on {machine}, not this computer"
            elif self.store.status_of(name, machine) == settled.get(action):
                reason = f"already {settled[action].lower()}"
            if reason:
                chosen.remove((name, machine))
                skipped.append(f"{name} — {reason}")

        if not chosen:
            # Nothing failed; there was simply nothing left to do.
            log.info("bulk %s: nothing to do (%s)", action, "; ".join(skipped))
            self.refresh()
            return

        verb = {"start": "Start", "stop": "Stop", "restart": "Restart",
                "kill": "Kill the process of"}[action]
        lines = "\n".join(f"  ·  {n}" for n, _m in chosen[:12])
        if len(chosen) > 12:
            lines += f"\n  ·  … and {len(chosen) - 12} more"
        subject = ("this service" if len(chosen) == 1
                   else f"these {len(chosen)} services")
        question = f"{verb} {subject}?\n\n{lines}"
        if action == "kill":
            question += ("\n\nEach is killed outright, with no chance to shut "
                         "down cleanly.")
        if skipped:
            question += "\n\nSkipping:\n" + "\n".join(f"  ·  {s}" for s in skipped)
        title = f"{verb} {len(chosen)} service{'s' if len(chosen) != 1 else ''}"
        if QMessageBox.question(None, title, question,
                               QMessageBox.Yes | QMessageBox.No,
                               QMessageBox.No) != QMessageBox.Yes:
            return

        log.info("bulk %s on %d service(s)", action, len(chosen))
        self._bulk = {"action": action, "left": len(chosen), "failed": []}
        for name, machine in chosen:
            if action == "kill":
                self._bulk_kill(name, machine)
            else:
                self.do_action(action, name, machine, announce_errors=False,
                               bulk=True)

    def _bulk_kill(self, name: str, machine: str):
        """Kill without its own confirmation — the batch was already confirmed."""
        error = None
        try:
            self.store.expect_stop(name, machine)
            if self.cfg.history.enabled:
                history.record_action(name, "kill", st.SRC_PANEL, machine=machine)
            if not control.process_id(name, machine):
                # Nothing running to kill — that is the desired end state anyway.
                log.info("kill %s: no process to kill", name)
            else:
                control.kill_process(name, machine)
        except Exception as exc:
            self.store.clear_expected(name, machine)
            if not control.nothing_to_do(exc):
                error = getattr(exc, "strerror", None) or str(exc)
        self._bulk_report(name, error)
        try:
            self.store.update(name, control.query_status(name, machine),
                              machine=machine, source=st.SRC_PANEL)
        except Exception:
            pass

    def _bulk_report(self, name: str, error):
        """One tally for the whole batch, announced when the last one lands."""
        batch = getattr(self, "_bulk", None)
        if not batch:
            return
        if error:
            batch["failed"].append(f"{name}: {error}")
        batch["left"] -= 1
        if batch["left"] > 0:
            return
        self._bulk = None
        self.refresh()
        if batch["failed"]:
            QMessageBox.warning(
                None, "Service Officer",
                f"{len(batch['failed'])} of the selected services could not "
                f"{batch['action']}:\n\n" + "\n".join(batch["failed"][:10]))

    def kill_process(self, name: str, machine: str = ""):
        """Terminate the service's process — abrupt, so it asks first."""
        label = next((s.display() for s in self.cfg.services
                      if s.name == name and (s.machine or "") == (machine or "")), name)
        pid = 0
        try:
            pid = control.process_id(name, machine)
        except Exception:
            pass
        if not pid:
            QMessageBox.information(None, "Service Officer",
                                    f"{label} has no running process to kill.")
            return
        answer = QMessageBox.question(
            None, "Kill process",
            f"Terminate {label} (process {pid})?\n\n"
            "The service is killed outright — it gets no chance to shut down "
            "cleanly. Use this when Stop has no effect.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return

        self.store.expect_stop(name, machine)     # we did this on purpose
        if self.cfg.history.enabled:
            history.record_action(name, "kill", st.SRC_PANEL, machine=machine,
                                  note=f"process {pid}")
        try:
            control.kill_process(name, machine)
            log.info("killed %s (pid %s)", name, pid)
        except Exception as exc:
            self.store.clear_expected(name, machine)
            QMessageBox.warning(None, "Service Officer",
                                f"Could not kill {label}:\n"
                                f"{getattr(exc, 'strerror', None) or exc}")
            return
        self.refresh()

    def run_trigger(self, trigger):
        """Do what a trigger says. Shared by the scheduler and its Run now button.

        A trigger that asks for something already true is *skipped*, not failed:
        "start AppEngine" at 03:00 when it is already running is the normal case,
        and it used to raise "service already running" in the user's face.
        """
        if trigger is None:
            return
        began = time.monotonic()

        def finish(outcome: str, detail: str = ""):
            history.record_run("trigger", trigger.name, outcome,
                               seconds=time.monotonic() - began, detail=detail,
                               source=st.SRC_SCHEDULE)
            if trigger.wants_notice(outcome):
                self.tray.notify("Service Officer",
                                 f"{trigger.name}: {outcome}"
                                 + (f" — {detail}" if detail else ""))

        if trigger.action == "service":
            if not trigger.service:
                finish("failed", "no service chosen")
                return
            target = {"start": st.RUNNING, "stop": st.STOPPED}.get(
                trigger.service_action)
            current = self.store.status_of(trigger.service, trigger.machine)
            if target and current == target:
                log.info("trigger “%s” skipped: %s is already %s",
                         trigger.name, trigger.service, current.lower())
                finish("skipped", f"{trigger.service} was already {current.lower()}")
                return
            self._pending_trigger = (trigger, finish)
            self.do_action(trigger.service_action, trigger.service, trigger.machine,
                           announce_errors=False)
            return

        stack = self.cfg.stack(trigger.stack)
        if not stack or not stack.steps:
            finish("skipped", "the stack has no steps")
            return
        self._pending_trigger = (trigger, finish)
        self.run_stack(stack)

    def run_stack(self, stack_or_name, _action=None):
        """The second argument exists only because Settings' test-run signal
        still carries one; a stack has a single way to run."""
        """Accepts a name (tray menu) or a Stack (a test run from Settings, which
        must use the values currently on screen rather than the saved ones)."""
        stack = (self.cfg.stack(stack_or_name)
                 if isinstance(stack_or_name, str) else stack_or_name)
        if not stack or not stack.steps:
            return
        if self.runner.busy:
            QMessageBox.information(None, "Service Officer",
                                    "A stack run is already in progress.")
            return
        self.hover.dismiss()
        self.tray.action_started()
        machine_for = {s.name: s.machine for s in self.cfg.services}

        def work():
            result = self.runner.run(
                stack,
                on_step=lambda i, total, svc, act, phase:
                    self.stack_signals.step.emit(i, total, svc, act, phase),
                machine_for=lambda n: machine_for.get(n, ""))
            self.stack_signals.done.emit(result)
        threading.Thread(target=work, daemon=True).start()

    def _on_stack_step(self, index, total, service, action, phase):
        if phase == "begin":
            self.flyout.mark_busy(service, "", f"{action} {index}/{total}…")
            if self.cfg.history.enabled:
                history.record_action(service, action, st.SRC_STACK,
                                      note=f"step {index} of {total}")
        elif phase in ("ok", "fail"):
            machine = next((s.machine for s in self.cfg.services
                            if s.name == service), "")
            self._clear_busy(service, machine)
            # A stack step starts a service just as a button does, and the status
            # it leaves behind is just as likely to be unchanged.
            if phase == "ok" and action in ("start", "restart") \
                    and self.store.status_of(service, machine) == st.RUNNING:
                self._note_started(service, machine)

    def _on_stack_done(self, result):
        self.tray.action_finished()
        self.refresh()
        log.info(result.summary())
        outcome = ("cancelled" if result.cancelled
                   else "success" if result.ok else "failed")
        history.record_run("stack", result.stack, outcome,
                           seconds=sum(s.seconds for s in result.steps),
                           detail=result.summary(), source=st.SRC_STACK)

        pending = self._pending_trigger
        self._pending_trigger = None
        if pending is not None:
            _trigger, finish = pending
            finish(outcome, result.summary())
        else:
            self.tray.notify("Service Officer", result.summary())

    # -- ui plumbing -------------------------------------------------------
    def _toggle_flyout(self):
        self.hover.dismiss()
        if self.flyout.isVisible():
            self.flyout.hide()
        else:
            self._poll_start_types()      # so Disabled is right the moment it opens
            self.flyout.popup(self.tray.geometry())

    def _on_hover(self):
        if not self.flyout.isVisible() and (self.panel is None
                                           or not self.panel.isVisible()):
            self.hover.request(self.tray.geometry())

    def _mark_busy(self, name: str, machine: str, label: str) -> None:
        """Both lists show the same service, so both have to say it is busy."""
        self.flyout.mark_busy(name, machine, label)
        if self.panel is not None and self.panel.isVisible():
            self.panel.dashboard.mark_busy(name, machine, label)

    def _clear_busy(self, name: str, machine: str) -> None:
        """And both have to stop saying it once the action has reported back."""
        self.flyout.clear_busy(name, machine)
        if self.panel is not None:
            self.panel.dashboard.clear_busy(name, machine)

    def refresh(self):
        """Ask again now. This computer directly; other machines through the poller,
        which is the only thing allowed to wait for them."""
        self.poller.poll_soon()
        for svc in self.cfg.services:
            if svc.machine:
                continue
            try:
                self.store.update(svc.name, control.query_status(svc.name, svc.machine),
                                  machine=svc.machine)
                self.store.set_start_type(svc.name,
                                          control.start_type(svc.name, svc.machine),
                                          machine=svc.machine)
            except Exception:
                pass
        self.flyout.refresh()
        if self.panel is not None:
            self.panel.dashboard.apply_states()
        self.tray.apply_state()

    def apply_theme(self, requested: str) -> None:
        """Repaint everything. The flyout and hover card are rebuilt rather than
        restyled: they carry per-status colours that are chosen when built."""
        theme.set_mode(requested)
        self.qt.setStyleSheet(theme.sheet())
        icons.clear_cache()

        was_visible = self.flyout.isVisible()
        was_pinned = self.flyout.pinned
        self.flyout.deleteLater()
        self.flyout = flyout_mod.Flyout(lambda: self.cfg, self.store)
        self.flyout.pin.setChecked(was_pinned)     # a repaint isn't an unpin
        self._wire_flyout()
        if was_visible:
            self.flyout.popup(self.tray.geometry())

        self.hover.deleteLater()
        self.hover = hover_mod.HoverCard(lambda: self.cfg, self.store)
        self.tray.apply_state()
        log.info("theme set to %s (%s)", requested, theme.resolved)

    def open_panel(self, page: str = ""):
        """Show the management panel, optionally on a named page."""
        self.hover.dismiss()
        if self.panel is not None and self.panel.isVisible():
            if page:
                self.panel.go_to(page)
            self.panel.raise_()
            self.panel.activateWindow()
            return
        win = panel_mod.MainPanel(self.cfg, store=self.store,
                                  live_config=lambda: self.cfg)
        win.saved.connect(self._settings_saved)
        win.test_run.connect(self.run_stack)
        win.run_trigger.connect(self.run_trigger)
        win.theme_changed.connect(self.apply_theme)
        # The dashboard's controls act on real services, through the same paths
        # as the tray flyout's.
        win.action_requested.connect(self.do_action)
        win.bulk_requested.connect(self.do_bulk)
        win.run_stack.connect(self.run_stack)
        win.refresh_requested.connect(self.refresh)
        win.open_services_mmc.connect(self._open_services_mmc)
        self.panel = win
        if page:
            win.go_to(page)
        win.show()
        win.raise_()
        win.activateWindow()

    def _settings_saved(self, new_cfg):
        old_auto = self.cfg.auto_start
        # A machine may have been repointed, given a different account, or had its
        # transport changed: those connections have to go, along with any session
        # still open to where the machine used to be.
        #
        # Only those, though. Dropping every connection on every save cost a fresh
        # connection to each machine, and to one remote Windows box that is
        # twenty-one seconds of "Unknown" for changing an unrelated setting.
        for name in self._machines_changed(self.cfg, new_cfg):
            connectors.forget(name)
        self.cfg = new_cfg
        try:
            cfg_mod.save(self.cfg)
        except Exception as exc:
            QMessageBox.warning(None, "Service Officer",
                                f"Could not save settings:\n{exc}")
            return
        if self.cfg.auto_start != old_auto:
            try:
                autostart.apply(self.cfg.auto_start)
            except Exception as exc:
                QMessageBox.warning(None, "Service Officer",
                                    f"Could not apply the auto-start setting:\n{exc}")
        self.store.keep_only([(s.machine, s.name) for s in self.cfg.services])
        self.tray.rebuild_menu()
        self._prime_states()
        self.flyout.rebuild()
        # The dashboard reads the saved config, so it only changes now.
        if self.panel is not None:
            self.panel.dashboard.rebuild()
        self.tray.apply_state()
        log.info("settings saved: %d service(s), %d stack(s)",
                 len(self.cfg.services), len(self.cfg.stacks))

    @staticmethod
    def _machines_changed(old, new) -> list:
        """Machines whose settings differ between two configs, plus any that have
        appeared or gone. Dataclass equality does the comparing.

        A changed password is not visible here — it lives in the secret store and the
        config only holds the unchanged name of the entry — so the panel drops that
        machine's connection itself when it stores one.
        """
        before = {m.name: m for m in getattr(old, "machines", [])}
        after = {m.name: m for m in getattr(new, "machines", [])}
        return [name for name in set(before) | set(after)
                if before.get(name) != after.get(name)]

    @staticmethod
    def _open_services_mmc():
        ctypes.windll.shell32.ShellExecuteW(None, "open", "services.msc", None, None, 1)

    def quit(self):
        log.info("quitting")
        self.watcher.stop()
        self.poller.stop()
        self.scheduler.stop()
        self.watchdog.stop()
        self.health.stop()
        self.tray.hide()
        self.qt.quit()


def prepare_history() -> None:
    """Make the event store usable before anything writes to it.

    Order matters. The integrity check comes first, because a JSONL import needs
    a working file to import *into*; and a store that cannot be opened is moved
    aside rather than deleted, because damaged or not it is the customer's
    evidence. Neither failure stops the app: a tray icon with no history is worth
    more than no tray icon.

    Its own function so it can be tested. Buried inside main(), next to
    QApplication, nobody could.
    """
    verdict = db.integrity(history.path())
    if verdict != "ok":
        aside = db.set_aside(history.path())
        log.error("history unusable (%s); moved to %s and starting a new one",
                  verdict, aside or "nowhere — it could not be moved")
    imported = history.migrate_jsonl()
    if imported:
        log.info("imported %d rows from %s into %s", imported,
                 history.LEGACY_JSONL, history.path())


def main() -> int:
    # Before anything opens a file under the new directory: the old per-user copy
    # is only carried over while the destination is still empty, and the log
    # handler would otherwise create its target first.
    brought = cfg_mod.migrate_from_legacy()
    applog.setup()
    if brought:
        log.info("brought %s forward into %s", ", ".join(brought),
                 cfg_mod.APP_DIR)

    prepare_history()
    # No manual DPI call here: Qt already opts into per-monitor v2 awareness
    # before we could, and calling SetProcessDpiAwareness afterwards just fails
    # with "access denied" and prints a warning.
    app = Application(sys.argv)
    return app.start()


if __name__ == "__main__":
    sys.exit(main())
