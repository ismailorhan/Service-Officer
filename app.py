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

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

import autostart
from core import (applog, config as cfg_mod, control, history, scm, schedule,
                  stacks)
from core import state as st
from core.watchdog import Watchdog
from ui import flyout as flyout_mod, hover as hover_mod, icons, settings as settings_mod
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
    done = Signal(str, str, str, object)        # service, machine, action, error


class Application(QObject):
    def __init__(self, argv):
        super().__init__()
        self.qt = QApplication(argv)
        self.qt.setApplicationName("Service Officer")
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setWindowIcon(icons.base_icon("green"))

        self.cfg = cfg_mod.load()
        self.store = st.store
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
        self.settings_window = None

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
        self.tray.settings_requested.connect(self.open_settings)
        self.tray.services_requested.connect(self._open_services_mmc)
        self.tray.refresh_requested.connect(self.refresh)
        self.tray.quit_requested.connect(self.quit)
        self.tray.stack_requested.connect(self.run_stack)
        self.tray.menu_opened.connect(self.hover.dismiss)

        self.flyout.action_requested.connect(self.do_action)
        self.flyout.run_stack.connect(self.run_stack)
        self.flyout.open_settings.connect(self.open_settings)
        self.flyout.open_services_mmc.connect(self._open_services_mmc)

        # --- SCM push notifications ---------------------------------------
        self.watcher = scm.Watcher(
            get_names=lambda: [s.name for s in self.cfg.services if not s.machine],
            on_change=self._on_scm,
            safety_query=control.query_status,
        )

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
    def do_action(self, action: str, name: str, machine: str = ""):
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
                self.action_signals.done.emit(name, machine, action, error)
        threading.Thread(target=work, daemon=True).start()

    def _action_done(self, name, machine, action, error):
        self.tray.action_finished()
        try:
            self.store.update(name, control.query_status(name, machine),
                              machine=machine, source=st.SRC_PANEL)
        except Exception:
            pass
        if self.flyout.isVisible():
            self.flyout.apply_states()
        if error:
            QMessageBox.warning(None, "Service Officer",
                                f"Could not {action} '{name}':\n{error}")

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
        """Do what a trigger says. Shared by the scheduler and its Run now button."""
        if trigger is None:
            return
        if self.cfg.history.enabled:
            target = trigger.stack if trigger.action == "stack" else trigger.service
            history.record_action(target or trigger.name,
                                  trigger.service_action if trigger.action == "service"
                                  else "run stack",
                                  st.SRC_SCHEDULE, note=f"trigger “{trigger.name}”")
        if trigger.action == "stack":
            self.run_stack(trigger.stack)
        elif trigger.service:
            self.do_action(trigger.service_action, trigger.service)

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
        self.tray.notify("Service Officer", result.summary())

    # -- ui plumbing -------------------------------------------------------
    def _toggle_flyout(self):
        self.hover.dismiss()
        if self.flyout.isVisible():
            self.flyout.hide()
        else:
            self.flyout.popup(self.tray.geometry())

    def _on_hover(self):
        if not self.flyout.isVisible() and (self.settings_window is None
                                           or not self.settings_window.isVisible()):
            self.hover.request(self.tray.geometry())

    def refresh(self):
        for svc in self.cfg.services:
            try:
                self.store.update(svc.name, control.query_status(svc.name, svc.machine),
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
        self.flyout.action_requested.connect(self.do_action)
        self.flyout.run_stack.connect(self.run_stack)
        self.flyout.open_settings.connect(self.open_settings)
        self.flyout.open_services_mmc.connect(self._open_services_mmc)
        if was_visible:
            self.flyout.popup(self.tray.geometry())

        self.hover.deleteLater()
        self.hover = hover_mod.HoverCard(lambda: self.cfg, self.store)
        self.tray.apply_state()
        log.info("theme set to %s (%s)", requested, theme.resolved)

    def open_settings(self):
        self.hover.dismiss()
        if self.settings_window is not None and self.settings_window.isVisible():
            self.settings_window.raise_()
            self.settings_window.activateWindow()
            return
        win = settings_mod.SettingsWindow(self.cfg)
        win.saved.connect(self._settings_saved)
        win.test_run.connect(self.run_stack)
        win.run_trigger.connect(self.run_trigger)
        win.theme_changed.connect(self.apply_theme)
        self.settings_window = win
        win.show()

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
