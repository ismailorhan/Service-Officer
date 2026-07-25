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
from core import (applog, config as cfg_mod, control, history, scm, schedule,
                  stacks)
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


class ActionSignals(QObject):
    """Same for a single service action.

    It has to be a signal, not QTimer.singleShot: a worker thread has no Qt
    event loop, so a timer started there never fires — which left the tray
    spinning forever after every start/stop because the completion handler that
    clears the busy counter was never called.
    """
    #: service, machine, action, error, announce errors, part of a bulk run.
    #: The flags travel with the result rather than living on the app: a bulk
    #: action has several of these in flight at once, and a shared attribute
    #: would be whatever the last caller set.
    done = Signal(str, str, str, object, bool, bool)


class Application(QObject):
    def __init__(self, argv):
        super().__init__()
        self.qt = QApplication(argv)
        self.qt.setApplicationName("Service Officer")
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setWindowIcon(icons.base_icon("green"))

        self.cfg = cfg_mod.load()
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
        QTimer.singleShot(3000, lambda: history.trim(self.cfg.history.retention_days))

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

    def _poll_start_types(self):
        """Notice a service being disabled or re-enabled outside this app."""
        changed = False
        for svc in self.cfg.services:
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
        self.scheduler.start()
        self.scheduler.run_startup_triggers()
        log.info("started with %d service(s), %d stack(s), %d trigger(s)",
                 len(self.cfg.services), len(self.cfg.stacks), len(self.cfg.triggers))
        return self.qt.exec()

    def _prime_states(self):
        """Fill the store before the first paint so nothing shows as Unknown."""
        for svc in self.cfg.services:
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

    def _on_state_event(self, event):
        """GUI thread: refresh whatever is on screen."""
        self.tray.apply_state()
        if self.flyout.isVisible():
            self.flyout.apply_states()
        self.hover.refresh()
        if event.status == st.RUNNING and self.cfg.notifications.on_recovery:
            if self.watchdog.attempts_for(event.name, event.state.machine):
                self.tray.notify("Service Officer", f"{event.name} is running again.")

    # -- actions -----------------------------------------------------------
    def do_action(self, action: str, name: str, machine: str = "",
                  announce_errors: bool = True, bulk: bool = False):
        if action == "kill":
            self.kill_process(name, machine)
            return
        verb = {"start": "Starting", "stop": "Stopping", "restart": "Restarting"}[action]
        self.flyout.mark_busy(name, machine, verb + "…")
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
                error = getattr(exc, "strerror", None) or str(exc)
                self.store.clear_expected(name, machine)
            finally:
                self.action_signals.done.emit(name, machine, action, error,
                                              announce_errors, bulk)
        threading.Thread(target=work, daemon=True).start()

    def _action_done(self, name, machine, action, error, announce=True,
                     bulk=False):
        self.tray.action_finished()
        try:
            self.store.update(name, control.query_status(name, machine),
                              machine=machine, source=st.SRC_PANEL)
        except Exception:
            pass
        if self.flyout.isVisible():
            self.flyout.apply_states()

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
        Services that can't take the action are named up front rather than
        failing one by one — a disabled service will never start, and a process
        on another machine isn't ours to terminate.
        """
        chosen = [(n, m or "") for n, m in targets]
        skipped = []
        if action == "start":
            for name, machine in list(chosen):
                if self.store.is_disabled(name, machine):
                    chosen.remove((name, machine))
                    skipped.append(f"{name} — disabled in Windows")
        if action == "kill":
            for name, machine in list(chosen):
                if machine:
                    chosen.remove((name, machine))
                    skipped.append(f"{name} — on {machine}, not this computer")

        if not chosen:
            QMessageBox.information(None, "Service Officer",
                                   "Nothing to do:\n\n" + "\n".join(skipped))
            return

        verb = {"start": "Start", "stop": "Stop", "restart": "Restart",
                "kill": "Kill the process of"}[action]
        lines = "\n".join(f"  ·  {n}" for n, _m in chosen[:12])
        if len(chosen) > 12:
            lines += f"\n  ·  … and {len(chosen) - 12} more"
        question = f"{verb} these {len(chosen)} services?\n\n{lines}"
        if action == "kill":
            question += ("\n\nEach is killed outright, with no chance to shut "
                         "down cleanly.")
        if skipped:
            question += "\n\nSkipping:\n" + "\n".join(f"  ·  {s}" for s in skipped)
        if QMessageBox.question(None, f"{verb} {len(chosen)} services", question,
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
            control.kill_process(name, machine)
        except Exception as exc:
            self.store.clear_expected(name, machine)
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

    def refresh(self):
        for svc in self.cfg.services:
            try:
                self.store.update(svc.name, control.query_status(svc.name, svc.machine),
                                  machine=svc.machine)
                self.store.set_start_type(svc.name,
                                          control.start_type(svc.name, svc.machine),
                                          machine=svc.machine)
            except Exception:
                pass
        self.flyout.refresh()
        self.tray.apply_state()

    def apply_theme(self, requested: str) -> None:
        """Repaint everything. The flyout and hover card are rebuilt rather than
        restyled: they carry per-status colours that are chosen when built."""
        theme.set_mode(requested)
        self.qt.setStyleSheet(theme.sheet())
        icons.clear_cache()

        was_visible = self.flyout.isVisible()
        self.flyout.deleteLater()
        self.flyout = flyout_mod.Flyout(lambda: self.cfg, self.store)
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
        win = panel_mod.MainPanel(self.cfg)
        win.saved.connect(self._settings_saved)
        win.test_run.connect(self.run_stack)
        win.run_trigger.connect(self.run_trigger)
        win.theme_changed.connect(self.apply_theme)
        self.panel = win
        if page:
            win.go_to(page)
        win.show()
        win.raise_()
        win.activateWindow()

    def _settings_saved(self, new_cfg):
        old_auto = self.cfg.auto_start
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
        self.tray.apply_state()
        log.info("settings saved: %d service(s), %d stack(s)",
                 len(self.cfg.services), len(self.cfg.stacks))

    @staticmethod
    def _open_services_mmc():
        ctypes.windll.shell32.ShellExecuteW(None, "open", "services.msc", None, None, 1)

    def quit(self):
        log.info("quitting")
        self.watcher.stop()
        self.scheduler.stop()
        self.watchdog.stop()
        self.tray.hide()
        self.qt.quit()


def main() -> int:
    applog.setup()
    # No manual DPI call here: Qt already opts into per-monitor v2 awareness
    # before we could, and calling SetProcessDpiAwareness afterwards just fails
    # with "access denied" and prints a warning.
    app = Application(sys.argv)
    return app.start()


if __name__ == "__main__":
    sys.exit(main())
