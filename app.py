"""Service Officer — application entry point.

Wiring only: build the core (config, status store, SCM notifications, watchdog,
history, stack runner), build the Qt interface, and connect them. Everything of
substance lives in core/ or ui/.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

import autostart
from core import (applog, config as cfg_mod, control, db, engine as engine_mod,
                  health, history, hub_client, local as local_mod)
from core import state as st
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


class HubSignals(QObject):
    """A hub's answers arrive on its reader thread; the widgets are on Qt's."""
    event = Signal(object)          # one wire event
    connected = Signal(bool)


# ---------------------------------------------------------------------------
# administrator rights
# ---------------------------------------------------------------------------
# Not a manifest. A client of a hub controls nothing itself — it asks the hub, and the
# hub is a LocalSystem service — so requiring elevation on every launch would charge
# every workstation for something only the single-machine install needs. But that
# install does drive this computer's service manager directly, and without the rights
# every button on it fails with access denied, which is worse than a prompt.
#
# So the question is asked at run time: am I about to do the work myself?
def _is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        # Not Windows, or the call is unavailable: assume yes rather than relaunch in
        # a loop. Being wrong here costs a clear "access denied"; a loop costs the app.
        return True


def needs_elevation(argv=None) -> bool:
    """Whether this launch has to be elevated to do what it is about to do."""
    if _is_elevated():
        return False
    argv = list(argv if argv is not None else sys.argv)
    if any(flag in argv for flag in ("--connect", "-c")):
        return False                      # being pointed at a hub right now
    return not local_mod.load().hub_url    # "" means the engine runs here


def relaunch_elevated(argv=None) -> bool:
    """Ask Windows for the rights and start again. True if the new process started, in
    which case this one should exit quietly — two panels would fight over one tray."""
    argv = list(argv if argv is not None else sys.argv)
    if getattr(sys, "frozen", False):
        program, arguments = sys.executable, argv[1:]
    else:
        program, arguments = sys.executable, argv
    try:
        answer = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", program, subprocess.list2cmdline(arguments), None, 1)
    except Exception as exc:
        log.warning("could not ask for administrator rights: %s", exc)
        return False
    # Anything above 32 is success; 5 is "the user said no", and that is an answer, not
    # an error — they keep an unelevated app that will say so when a button fails.
    if answer <= 32:
        log.info("administrator rights were refused (%s); carrying on without", answer)
        return False
    return True


def _pair(argv) -> str:
    """The hub to talk to, storing a token given on the command line.

    `--connect URL [--token T]` once; after that `client.json` remembers and the flag is
    not needed. The token is accepted on a command line and then stored because a command
    line is visible in Task Manager for as long as the process starts — once is a small
    window, every launch is not.
    """
    argv = list(argv or [])
    url = ""
    for flag in ("--connect", "-c"):
        if flag in argv:
            index = argv.index(flag)
            if index + 1 < len(argv):
                url = argv[index + 1].rstrip("/")
    token = ""
    if "--token" in argv:
        index = argv.index("--token")
        if index + 1 < len(argv):
            token = argv[index + 1]

    settings = local_mod.load()
    if not url:
        return settings.hub_url
    if url != settings.hub_url:
        # A different hub: whatever was pinned belongs to the old one.
        settings.hub_url = url
        settings.hub_fingerprint = ""
        local_mod.save(settings)
    if token:
        local_mod.set_token(url, token)
    return url


def pair_only(argv) -> int:
    """`--store-only`: pair and exit, with nothing shown.

    The installer's path. A tray icon appearing in the middle of an install, and then
    vanishing, is alarming for no reason — and the first real launch is already connected.
    """
    url = _pair(argv)
    if not url:
        print("nothing to pair: --connect <url> is needed")
        return 1
    print(f"paired with {url}")
    return 0


class Application(QObject):
    def __init__(self, argv):
        super().__init__()
        self.qt = QApplication(argv)
        self.qt.setApplicationName("Service Officer")
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setWindowIcon(icons.base_icon("green"))

        self.cfg = cfg_mod.load()
        #: Where the engine is. "" means here — which is what everybody has today and
        #: what a single-machine install keeps. There is no *mode* to pick: if the hub
        #: runs on this computer you point at this computer, and if it is elsewhere you
        #: point there. One field, one code path, and a client that needs no
        #: administrator rights because it controls nothing itself.
        self.hub_url = _pair(argv)
        self.hub = None
        self.store = st.store
        #: set while a trigger's action is in flight, so its outcome is recorded
        self._pending_trigger = None
        #: the running bulk action, so one tally is reported instead of N dialogs
        self._bulk = None
        #: whether to put a dialog up for a failure, per service, remembered from
        #: the request because the engine's answer arrives with no opinion about it
        self._announce: dict = {}
        theme.set_mode(self.cfg.theme)
        self.qt.setStyleSheet(theme.sheet())
        # With "system" chosen, follow Windows when it flips light/dark.
        try:
            self.qt.styleHints().colorSchemeChanged.connect(
                lambda _s: self.apply_theme(self.cfg.theme))
        except Exception:
            pass

        # --- the engine ---------------------------------------------------
        # Everything that is not drawing lives in core/engine.py, so the same code
        # runs here and in the hub service. Its callbacks arrive on worker threads;
        # each one is turned into a Qt signal, because touching a widget from
        # another thread is the crash that has no stack trace worth reading.
        self.health_signals = HealthSignals()
        self.health_signals.verdict.connect(self._on_health_verdict)
        self.health_signals.act.connect(self._on_health_action)
        self.trigger_signals = TriggerSignals()
        self.trigger_signals.fire.connect(self.run_trigger)
        self.stack_signals = StackSignals()
        self.stack_signals.step.connect(self._on_stack_step)
        self.stack_signals.done.connect(self._on_stack_done)
        self.action_signals = ActionSignals()
        self.action_signals.done.connect(self._action_done)
        self.hub_signals = HubSignals()
        self.hub_signals.event.connect(self._on_hub_event)
        self.hub_signals.connected.connect(self._on_hub_connected)

        if self.hub_url:
            # Connected: the hub owns the engine and this process owns the pixels.
            # Nothing below may reach a service manager or an SSH session — that is the
            # whole point of the split, and the way to keep it true is to have no engine
            # here at all.
            self.engine = None
            settings = local_mod.load()
            self.hub = hub_client.HubClient(
                self.hub_url, local_mod.token(self.hub_url),
                fingerprint=settings.hub_fingerprint,
                on_event=lambda payload: self.hub_signals.event.emit(payload),
                on_connected=lambda ok: self.hub_signals.connected.emit(ok))
            try:
                pinned = self.hub.check_identity()
                if pinned and pinned != settings.hub_fingerprint:
                    settings.hub_fingerprint = pinned
                    local_mod.save(settings)
            except hub_client.WrongHub as exc:
                # Not fatal, and not accepted either: the panel shows it and the client
                # keeps refusing until somebody decides. Silently trusting a changed
                # certificate is the one thing a pin must never do.
                log.error("%s", exc)
            self.store = self.hub.store
            self.hub.start()
        else:
            self.engine = engine_mod.Engine(
                lambda: self.cfg,
                store=self.store,
                on_health=lambda service, machine, verdict, detail:
                    self.health_signals.verdict.emit(service, machine, verdict,
                                                     detail),
                on_action_done=lambda **facts:
                    self.action_signals.done.emit(
                        facts["service"], facts["machine"], facts["action"],
                        facts["error"], not facts.get("bulk"),
                        facts.get("bulk", False), facts.get("status", "")),
                on_stack_step=lambda index, total, service, action, phase:
                    self.stack_signals.step.emit(index, total, service, action,
                                                 phase),
                on_stack_done=lambda result: self.stack_signals.done.emit(result),
                on_trigger=lambda trigger: self.trigger_signals.fire.emit(trigger),
                on_error=lambda kind, text: self.tray.notify("Service Officer", text),
            )

            # Once shortly after start, then daily. A server runs for weeks without
            # this app being restarted, and retention that only ran at startup was
            # retention that never ran. The hub does its own when connected.
            QTimer.singleShot(3000, self.engine.trim_history)
            self._trim_timer = QTimer(self)
            self._trim_timer.setInterval(24 * 60 * 60 * 1000)
            self._trim_timer.timeout.connect(self.engine.trim_history)
            self._trim_timer.start()

        # --- ui -----------------------------------------------------------
        self.tray = Tray(lambda: self.cfg, self.store)
        self.flyout = flyout_mod.Flyout(lambda: self.cfg, self.store)
        self.hover = hover_mod.HoverCard(lambda: self.cfg, self.store)
        self.panel = None

        self.bridge = StateBridge()
        self.bridge.changed.connect(self._on_state_event)
        self.bridge.attach(self.store)

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

    # The engine's parts, under the names the rest of this file and the wiring tests
    # already use. Properties rather than copies: there is one poller, and a second
    # reference to it that went stale would be a bug nobody could see.
    # None when connected to a hub: the hub has them, and a client reaching for a
    # watchdog of its own would be a second thing recovering the same services.
    @property
    def health(self):
        return self.engine.health if self.engine else None

    @property
    def poller(self):
        return self.engine.poller if self.engine else None

    @property
    def watchdog(self):
        return self.engine.watchdog if self.engine else None

    @property
    def scheduler(self):
        return self.engine.scheduler if self.engine else None

    @property
    def runner(self):
        return self.engine.runner if self.engine else None

    @property
    def watcher(self):
        return self.engine.watcher if self.engine else None

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
        """Ask the engine to re-read local start types, and repaint if any moved.

        The reading is the engine's; the repainting is ours. Kept as a method here
        because the flyout calls it as it opens, so Disabled is right the moment
        somebody looks. Connected, there is nothing to do here: the hub reads them and
        sends them with the snapshot.
        """
        if self.engine is None:
            return
        if self.engine.poll_start_types():
            if self.flyout.isVisible():
                self.flyout.apply_states()
            self.hover.refresh()

    # -- the hub, when there is one ----------------------------------------
    def _on_hub_event(self, payload) -> None:
        """Something happened on the hub. The store has already been updated by the
        client; this is the repaint, and the notifications that belong to this screen."""
        self._refresh_lists()

    def _on_hub_connected(self, connected: bool) -> None:
        log.info("hub %s", "connected" if connected else "disconnected")
        self._refresh_lists()
        if not connected and local_mod.load().notify:
            self.tray.notify("Service Officer",
                             "Lost the connection to the hub. Trying again.")

    # -- startup -----------------------------------------------------------
    def start(self) -> int:
        if self.engine is not None:
            self.engine.start()
        self.tray.show()
        self.tray.apply_state()
        return self.qt.exec()

    # -- health ------------------------------------------------------------
    def _on_health_verdict(self, name, machine, verdict, detail):
        """GUI thread: a service changed between answering and not.

        The store and the history are the engine's already; this is the half that
        needs a window — the repaint and the toast.
        """
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
        """Health checks asked for a restart. The engine does it and writes the
        history; this only marks the row busy, since it is our row."""
        self._mark_busy(name, machine, "Restarting…")
        self.tray.action_started()

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
        """GUI thread: refresh whatever is on screen, and say when a service that
        the watchdog was fighting for came back.

        The health bookkeeping this used to do — note_running, note_stopped, copying
        the verdict — is the engine's now, done before this signal is delivered.
        """
        self._refresh_lists()
        machine = event.state.machine
        if event.status == st.RUNNING and self.cfg.notifications.on_recovery \
                and self.watchdog.attempts_for(event.name, machine):
            self.tray.notify("Service Officer", f"{event.name} is running again.")

    def _note_started(self, name: str, machine: str = "") -> None:
        """Kept as a name the wiring tests use; the work is the engine's."""
        if self.engine is not None:
            self.engine.note_started(name, machine)

    def _copy_verdict(self, name: str, machine: str = "") -> None:
        """Kept as a name the wiring tests use; the work is the engine's."""
        if self.engine is not None:
            self.engine._copy_verdict(name, machine)

    # -- actions -----------------------------------------------------------
    def do_action(self, action: str, name: str, machine: str = "",
                  announce_errors: bool = True, bulk: bool = False):
        """Ask the engine to do it, and say on screen that it is being done.

        The doing, the history row and the status that comes back are the engine's;
        the busy label and the spinning gear are ours, because they are pixels.
        """
        if action == "kill":
            self.kill_process(name, machine)
            return
        verb = {"start": "Starting", "stop": "Stopping", "restart": "Restarting"}[action]
        self._mark_busy(name, machine, verb + "…")
        self.tray.action_started()
        self._announce.setdefault((name, machine or ""), announce_errors)
        try:
            self._ask_for(action, name, machine, bulk=bulk)
        except (engine_mod.Busy, hub_client.Busy) as clash:
            # Somebody else — a person on another client, or the watchdog — is
            # already doing this. Said on the row rather than in a dialog: the row
            # is where the click happened.
            self._clear_busy(name, machine)
            self.tray.action_finished()
            self._mark_busy(name, machine, str(clash))
            log.info("%s %s: %s", action, name, clash)

    def _kill(self, name: str, machine: str = "") -> str:
        """Terminate the process, here or over there. The confirmation happened before
        this was called; the engine and the hub both take it without asking again."""
        if self.hub is not None:
            return self.hub.act("kill", name, machine, actor=self._actor())
        return self.engine.kill(name, machine, actor=self._actor())

    def _save_config(self, new_cfg) -> None:
        """Persist the config, wherever it lives.

        Connected, a save can be *refused*: somebody else got there first, and the
        panel has to say so rather than pretend. Raised on, so the caller's own error
        dialog reports it.
        """
        if self.hub is None:
            self.engine.save_config(new_cfg)
            return
        _current, etag = self.hub.config()
        self.hub.save_config(new_cfg, etag, actor=self._actor())

    def _ask_for(self, action: str, name: str, machine: str = "",
                 bulk: bool = False) -> str:
        """The one place that knows whether the work happens here or over there.

        Everything else — the flyout, the dashboard, the watchdog's own path, a
        trigger — goes through do_action and never learns which.
        """
        if self.hub is not None:
            return self.hub.act(action, name, machine, actor=self._actor())
        return self.engine.act(action, name, machine, actor=self._actor(), bulk=bulk)

    @staticmethod
    def _actor() -> str:
        """Who asked. One operator today, so it is whoever is signed in; with a hub
        it is still this person, and the hub records the client they came from."""
        import os
        return os.environ.get("USERNAME", "")

    def _action_done(self, name, machine, action, error, announce=True,
                     bulk=False, status=""):
        self.tray.action_finished()
        announce = self._announce.pop((name, machine or ""), announce)
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
        """Kill without its own confirmation — the batch was already confirmed. The
        engine reports through on_action_done like any other action, so the tally is
        counted where every other one is."""
        try:
            self._kill(name, machine)
        except (engine_mod.Busy, hub_client.Busy) as clash:
            self._bulk_report(name, str(clash))

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

        self._mark_busy(name, machine, "Killing…")
        self.tray.action_started()
        self._announce.setdefault((name, machine or ""), True)
        try:
            self._kill(name, machine)
        except (engine_mod.Busy, hub_client.Busy) as clash:
            self.tray.action_finished()
            self._clear_busy(name, machine)
            QMessageBox.information(None, "Service Officer", str(clash))

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
        """Accepts a name (tray menu) or a Stack (a test run from Settings, which
        must use the values currently on screen rather than the saved ones).

        The second argument exists only because Settings' test-run signal still
        carries one; a stack has a single way to run.
        """
        self.hover.dismiss()
        self.tray.action_started()
        started = (self.hub.run_stack(
                       stack_or_name if isinstance(stack_or_name, str)
                       else stack_or_name.name, actor=self._actor())
                   if self.hub is not None
                   else self.engine.run_stack(stack_or_name, actor=self._actor()))
        if not started:
            self.tray.action_finished()
            if self.runner.busy:
                QMessageBox.information(None, "Service Officer",
                                        "A stack run is already in progress.")

    def _on_stack_step(self, index, total, service, action, phase):
        """The pixels of a stack step. Its history row and its "it just started"
        are the engine's, done before this signal arrives."""
        if self.engine is not None:
            self.engine.stack_step_landed(service, action, phase)
        if phase == "begin":
            self.flyout.mark_busy(service, "", f"{action} {index}/{total}…")
        elif phase in ("ok", "fail"):
            machine = next((s.machine for s in self.cfg.services
                            if s.name == service), "")
            self._clear_busy(service, machine)

    def _on_stack_done(self, result):
        self.tray.action_finished()
        self.refresh()
        log.info(result.summary())
        outcome = (self.engine.record_stack_run(result) if self.engine is not None
                   else ("success" if getattr(result, "ok", False) else "failed"))

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
        """Ask again now, then repaint. The asking is the engine's — and only it
        knows that another machine goes through the poller rather than being waited
        for on this thread."""
        if self.hub is not None:
            self.hub.refresh_machine()
            self.hub.refresh_now()
        else:
            self.engine.refresh()
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
        """Persist what the panel edited, then rebuild what shows it.

        The saving, the connection dropping and the store pruning are the engine's;
        the dialogs and the rebuilds are ours, and auto-start is neither — it is a
        Windows registry key that belongs to this installation rather than to the
        landscape, so it stays here.
        """
        old_auto = self.cfg.auto_start
        try:
            self._save_config(new_cfg)
        except Exception as exc:
            QMessageBox.warning(None, "Service Officer",
                                f"Could not save settings:\n{exc}")
            return
        self.cfg = new_cfg
        if self.cfg.auto_start != old_auto:
            try:
                autostart.apply(self.cfg.auto_start)
            except Exception as exc:
                QMessageBox.warning(None, "Service Officer",
                                    f"Could not apply the auto-start setting:\n{exc}")
        self.tray.rebuild_menu()
        self._prime_states()
        self.flyout.rebuild()
        # The dashboard reads the saved config, so it only changes now.
        if self.panel is not None:
            self.panel.dashboard.rebuild()
        self.tray.apply_state()

    def _prime_states(self):
        """Kept as a name the wiring tests use; the work is the engine's."""
        self.engine.prime_states()

    @staticmethod
    def _machines_changed(old, new) -> list:
        """Kept as a name the wiring tests use; the work is the engine's."""
        return engine_mod.Engine.machines_changed(old, new)

    @staticmethod
    def _open_services_mmc():
        ctypes.windll.shell32.ShellExecuteW(None, "open", "services.msc", None, None, 1)

    def quit(self):
        log.info("quitting")
        self.engine.stop()
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

    # Pair and leave, without a QApplication or a tray icon. The installer's path.
    if "--store-only" in sys.argv:
        return pair_only(sys.argv)

    # Only the install that does the work itself asks for administrator rights, and it
    # asks here rather than in a manifest — see needs_elevation. Before the QApplication,
    # so the relaunched process is the only one that ever draws anything.
    if needs_elevation(sys.argv) and relaunch_elevated(sys.argv):
        log.info("restarting with administrator rights")
        return 0

    prepare_history()
    # No manual DPI call here: Qt already opts into per-monitor v2 awareness
    # before we could, and calling SetProcessDpiAwareness afterwards just fails
    # with "access denied" and prints a warning.
    app = Application(sys.argv)
    return app.start()


if __name__ == "__main__":
    sys.exit(main())
