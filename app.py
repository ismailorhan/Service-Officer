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
from core import i18n
from core.i18n import t
from core import state as st
from ui import flyout as flyout_mod, hover as hover_mod, icons, panel as panel_mod
from ui import theme
from ui.tray import StateBridge, Tray

log = applog.get("app")


#: Everything a call into a hub raises when the hub is the problem rather than the request.
#: Named once, because there are four call sites and a fifth will be added by somebody who did
#: not write these: a missing one is not a message on a row, it is "Failed to execute script".
HUB_IS_DOWN = (hub_client.Unreachable, hub_client.Refused, hub_client.WrongHub, OSError)


class StackSignals(QObject):
    """A stack run happens on a worker thread; these carry it back to the UI."""
    step = Signal(int, int, str, str, str)      # index, total, service, action, phase
    #: the result, and whether a trigger owns this run — see Engine.run_trigger. The flag
    #: travels with the result rather than living on the app: two triggers at 03:00 would
    #: share one attribute, which is exactly what `_pending_trigger` got wrong.
    done = Signal(object, bool)


class TriggerSignals(QObject):
    """A trigger fired on the scheduler's thread — or on a hub's — and finished. Only the
    notification belongs on Qt's thread; the doing is the engine's."""
    done = Signal(str, str, str)         # name, outcome, detail


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


class _RemoteRun:
    """A stack result that happened on the hub, in the shape the panel's handlers read.

    The real one carries a record per step, which a client has no use for and JSON has no
    room for. The summary comes down already worded by the runner, so a client and the hub
    cannot describe the same run differently.
    """

    def __init__(self, stack: str, ok: bool, summary: str, cancelled: bool = False):
        self.stack, self.ok, self.cancelled = stack, ok, cancelled
        self._summary = summary or stack

    def summary(self) -> str:
        return self._summary


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

    It pins the certificate too, which is the part that has to happen *here* rather than
    on that first launch: at install time the address came from whoever is deploying, and
    on first launch it comes from whatever answers. Both are trust-on-first-use; only one
    of them is under the administrator's control.

    A hub that cannot be reached right now is not a failure — a workstation may be
    imaged before the server is up. The token is kept, the certificate is pinned on the
    first launch that reaches it, and the reason is printed rather than swallowed.
    """
    url = _pair(argv)
    if not url:
        print("nothing to pair: --connect <url> is needed")
        return 1
    settings = local_mod.load()
    if not settings.hub_fingerprint:
        client = hub_client.HubClient(url, local_mod.token(url) or "")
        try:
            settings.hub_fingerprint = client.check_identity()
            local_mod.save(settings)
            print(f"pinned {settings.hub_fingerprint}")
        except Exception as exc:
            print(f"could not reach {url} to pin its certificate ({exc});"
                  " it will be pinned on the first launch that can")
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
        #: This person's own choices — theme, language, auto-start. On their own computer,
        #: not in the landscape: a client that saved them to its hub found them reverted on
        #: the next launch, because its own disk never recorded them.
        self.mine = local_mod.taste(self.cfg)
        # Before any widget is built: every label is worded when it is constructed, so the
        # language has to be chosen before the first one exists.
        i18n.use(self.mine.language)
        #: Where the engine is. "" means here — which is what everybody has today and
        #: what a single-machine install keeps. There is no *mode* to pick: if the hub
        #: runs on this computer you point at this computer, and if it is elsewhere you
        #: point there. One field, one code path, and a client that needs no
        #: administrator rights because it controls nothing itself.
        self.hub_url = _pair(argv)
        self.hub = None
        self.store = st.store
        #: set while a trigger's action is in flight, so its outcome is recorded
        #: the running bulk action, so one tally is reported instead of N dialogs
        self._bulk = None
        #: whether to put a dialog up for a failure, per service, remembered from
        #: the request because the engine's answer arrives with no opinion about it
        self._announce: dict = {}
        theme.set_mode(self.mine.theme)
        self.qt.setStyleSheet(theme.sheet())
        # With "system" chosen, follow Windows when it flips light/dark.
        try:
            self.qt.styleHints().colorSchemeChanged.connect(
                lambda _s: self.apply_theme(self.mine.theme))
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
        self.trigger_signals.done.connect(self._on_trigger_done)
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
                # Every token this computer holds, not just the first: see
                # HubClient._tokens.
                self.hub_url, local_mod.tokens(self.hub_url),
                fingerprint=settings.hub_fingerprint,
                on_event=lambda payload: self.hub_signals.event.emit(payload),
                on_connected=lambda ok: self.hub_signals.connected.emit(ok))
            self._pin_hub(settings)
            self.store = self.hub.store
            # The landscape is the hub's, not this disk's. Without this a client listed
            # whatever was in its own services.json, which on a fresh client machine is
            # nothing at all — an empty panel in front of a store holding nine services.
            self._adopt_hub_config()
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
                on_stack_done=lambda result, by_trigger=False:
                    self.stack_signals.done.emit(result, by_trigger),
                on_trigger=lambda trigger, outcome="", detail="":
                    self.trigger_signals.done.emit(
                        getattr(trigger, "name", str(trigger)), outcome, detail),
                on_error=lambda kind, text: self.tray.notify("Service Officer", text),
                # Through the wire-shaped event path even without a hub: `_on_hub_event`
                # repaints for anything that arrives, so one handler serves both installs.
                on_start_type=lambda service, machine, start_type, disabled:
                    self.hub_signals.event.emit(
                        {"kind": "start_type", "service": service, "machine": machine,
                         "start_type": start_type, "disabled": disabled}),
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
        self.flyout = flyout_mod.Flyout(lambda: self.cfg, self.store, hub=lambda: self.hub)
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

        # No timer here for the start types any more. It was a QTimer, so it existed only in
        # this process — and a hub has no Qt, which is why disabling a service in services.msc
        # reached no client at all. The engine owns that schedule now and sends an event when
        # anything moves, so both installs learn it the same way.

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
        if payload.get("kind") == "config":
            self._adopt_hub_config()
        kind = payload.get("kind")
        if kind == "stack_step":
            # A stack run by the hub's scheduler, or by somebody at another panel. Invisible
            # here until now: these four callbacks were wired to nothing at all on a hub.
            self.stack_signals.step.emit(
                int(payload.get("index", 0)), int(payload.get("total", 0)),
                payload.get("service", ""), payload.get("action", ""),
                payload.get("phase", ""))
        elif kind == "stack_done":
            self.stack_signals.done.emit(
                _RemoteRun(payload.get("stack", ""), bool(payload.get("ok")),
                           payload.get("summary", ""),
                           bool(payload.get("cancelled"))),
                False)
        elif kind == "trigger":
            self.trigger_signals.done.emit(
                payload.get("trigger", ""), payload.get("outcome", ""),
                payload.get("detail", ""))
        elif kind == "error":
            # A hub has no tray. Before this an engine error on a hub was reported nowhere
            # at all — not to a client, not to a screen, only to its log file.
            self.tray.notify("Service Officer", payload.get("text", ""))
        if payload.get("kind") == "health":
            # Same signal as the engine's, so a connected panel gets the repaint *and* the
            # toast. Unpublished until now, a service whose checks had been failing for
            # hours stayed green here: health is what st.effective() turns into the chip's
            # colour and the tray icon.
            self.health_signals.verdict.emit(
                payload.get("service", ""), payload.get("machine", ""),
                payload.get("verdict", "unknown"), payload.get("detail", ""))
        if payload.get("kind") == "action":
            # Through the same signal the engine uses, so one handler clears the busy
            # label, counts a batch and reports a failure whether this panel owns an
            # engine or reads a hub. announce=False: `_action_done` looks up `_announce`,
            # which only has an entry for an action *this* panel started — somebody
            # else's refusal must not raise a dialog here.
            self.action_signals.done.emit(
                payload.get("service", ""), payload.get("machine", ""),
                payload.get("action", ""), payload.get("error", ""),
                False, False, payload.get("status", ""))
        self._refresh_lists()

    def _pin_hub(self, settings) -> None:
        """Pin the hub's certificate on the first connection, if it can be reached at all.

        **Nothing here may stop the app from starting.** After a Windows restart the tray
        application is launched from this user's Run key while the Hub service is still coming
        up, so the very first thing it does is connect to something that is not listening yet.
        Only `WrongHub` was caught and a ConnectionRefusedError went all the way out: "Failed
        to execute script 'app'" and a traceback, at every reboot that lost the race. Reported
        by somebody restarting Windows, which is the ordinary case rather than an edge one.

        Unreachable is not a decision, so there is nothing to refuse and nothing to pin: the
        client's own reader loop asks again on every attempt, and the first one that gets
        through pins it. A *changed* certificate is still refused, because silently accepting
        one is what a pin exists to prevent.
        """
        try:
            pinned = self.hub.check_identity()
        except hub_client.WrongHub as exc:
            # Not fatal, and not accepted either: the panel shows it and the client keeps
            # refusing until somebody decides.
            log.error("%s", exc)
            return
        except Exception as exc:
            # Refused, timed out, DNS, a proxy — one answer for all of them: not now. The
            # reader loop keeps trying and the tray shows disconnected, which is the truth.
            log.info("cannot reach %s yet (%s); it will be pinned on the first connection "
                     "that gets through", self.hub_url, exc)
            return
        if pinned and pinned != settings.hub_fingerprint:
            settings.hub_fingerprint = pinned
            local_mod.save(settings)

    def _adopt_hub_config(self) -> None:
        """Take the hub's landscape and keep this computer's own taste.

        Theme, auto-start and notifications stay local: auto-start is a registry key on this
        machine, and a theme is one person's eyesight — see config.LOCAL_TASTE. Everything
        else is what the hub is watching, and a client that answered from its own disk was
        answering about a different landscape.

        A hub that cannot be reached leaves what is already here, which for a first launch is
        this machine's own file. Empty is honest; inventing a landscape is not.
        """
        if self.hub is None:
            return
        try:
            landscape, _etag = self.hub.config()
        except Exception as exc:
            log.info("could not read the hub's settings yet: %s", exc)
            return
        was = cfg_mod.to_dict(self.cfg)
        self.cfg = cfg_mod.merged(landscape, self.cfg)
        if cfg_mod.to_dict(self.cfg) == was:
            return
        log.info("adopted the hub's settings: %d service(s), %d machine(s)",
                 len(self.cfg.services), len(self.cfg.machines))
        self._rebuild_for_new_config()

    def _rebuild_for_new_config(self) -> None:
        """Everything that was built from the old one. Shared by adopting and by saving."""
        # The first adoption happens while this object is still being built — the hub has to
        # be connected before the tray and the flyout are made, or they would be made from
        # the wrong landscape. There is nothing to rebuild then: they have not been built,
        # and when they are it will be from the config this just replaced.
        if getattr(self, "flyout", None) is None:
            return
        # The language may have changed with it, and the tray and the flyout are about to
        # be rebuilt — so they come back in it.
        i18n.use(self.mine.language)
        self.tray.rebuild_menu()
        self.flyout.rebuild()
        # An open panel is *not* replaced. It edits a deep copy and Save commits it, so
        # swapping its config underneath would throw away whatever somebody is halfway
        # through typing. The etag already turns a genuine collision into a refusal at save
        # time rather than a silent loss — see test_two_clients_editing_at_once. Its
        # dashboard shows state rather than edits, so that half does rebuild.
        if self.panel is not None:
            self.panel.dashboard.rebuild()
        self._refresh_lists()

    def _on_hub_connected(self, connected: bool) -> None:
        log.info("hub %s", "connected" if connected else "disconnected")
        if connected:
            # Anything could have been edited while this client was away, including by
            # somebody at the hub itself. Cheap: one small request, and it is also how a
            # client that started while the hub was down ever gets a landscape at all.
            self._adopt_hub_config()
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
            # The Machines page too: it was only redrawn on the way in, so a machine that
            # started answering while it was open kept its `waiting` chip while the very
            # services on it were streaming in as Running. Seen on 2026-07-29.
            self.panel.machines_page.refresh()

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
        except HUB_IS_DOWN as gone:
            # The hub went away between the click and the request. Same treatment: undo
            # what was set optimistically and say it on the row. Unhandled, this was the
            # startup crash again — from a click instead of a reboot.
            self._clear_busy(name, machine)
            self.tray.action_finished()
            self._mark_busy(name, machine, t("the hub is not answering"))
            log.info("%s %s: the hub is not answering (%s)", action, name, gone)

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

    def _hub_changed(self, url: str) -> None:
        """The hub address was changed on the General page and stored.

        Restarting is the honest answer. Whether this process runs an engine of its own is
        settled when it starts: switching live would mean building a poller, a watchdog, a
        scheduler and an SCM watcher in place — or tearing them down — while the panel is
        on screen holding references to the store they write to. A second is cheaper than
        a whole class of bug.
        """
        where = url or "this computer's own services"
        answer = QMessageBox.question(
            self.panel, "Service Officer",
            f"Saved. Service Officer will read {where} after a restart.\n\n"
            "Restart it now?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer != QMessageBox.Yes:
            return
        log.info("restarting to read %s", where)
        import subprocess
        program = sys.executable
        arguments = [] if getattr(sys, "frozen", False) else [sys.argv[0]]
        try:
            subprocess.Popen([program, *arguments], close_fds=True)
        except OSError as exc:
            QMessageBox.warning(self.panel, "Service Officer",
                                f"Could not start it again: {exc}\n\n"
                                "Close it and open it yourself.")
            return
        self.quit()

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

        if error and announce:
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

    def run_trigger(self, trigger_or_name):
        """Ask for a trigger to be run — here or on the hub. The doing is the engine's.

        It used to be *this method's*, which is why a hub ran its schedule not at all: the
        engine handed the trigger to whoever was listening, and on a hub nobody was. Now the
        engine performs it and tells us how it went, through the same path for both installs.
        """
        if trigger_or_name is None:
            return
        name = getattr(trigger_or_name, "name", trigger_or_name)
        if self.hub is not None:
            try:
                self.hub.run_trigger(name, actor=self._actor())
            except Exception as exc:
                QMessageBox.warning(None, "Service Officer",
                                    f"Could not run '{name}':\n{exc}")
            return
        self.engine.run_trigger(trigger_or_name, actor=self._actor())

    def _on_trigger_done(self, name: str, outcome: str, detail: str) -> None:
        """A trigger fired and finished. The history row is the engine's; this is the toast.

        Whether it is wanted is the trigger's own setting, and the trigger lives in the
        config — which a client now holds the hub's copy of, so this reads the same either
        way.
        """
        trigger = next((t for t in self.cfg.triggers if t.name == name), None)
        if trigger is not None and not trigger.wants_notice(outcome):
            return
        self.tray.notify("Service Officer",
                         f"{name}: {outcome}" + (f" — {detail}" if detail else ""))

    def run_stack(self, stack_or_name, _action=None):
        """Accepts a name (tray menu) or a Stack (a test run from Settings, which
        must use the values currently on screen rather than the saved ones).

        The second argument exists only because Settings' test-run signal still
        carries one; a stack has a single way to run.
        """
        self.hover.dismiss()
        self.tray.action_started()
        try:
            started = (self.hub.run_stack(
                           stack_or_name if isinstance(stack_or_name, str)
                           else stack_or_name.name, actor=self._actor())
                       if self.hub is not None
                       else self.engine.run_stack(stack_or_name, actor=self._actor()))
        except HUB_IS_DOWN as gone:
            self.tray.action_finished()
            log.info("could not run that stack: the hub is not answering (%s)", gone)
            QMessageBox.information(None, "Service Officer",
                                    t("The hub is not answering, so nothing was run. It is "
                                      "being retried; the tray shows when it is back."))
            return
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

    def _on_stack_done(self, result, by_trigger: bool = False):
        self.tray.action_finished()
        self.refresh()
        log.info(result.summary())
        if self.engine is not None:
            self.engine.record_stack_run(result)

        # A stack run *by* a trigger reports through on_trigger instead, with that
        # trigger's own notification setting — so this one would be a second toast for the
        # same run. The fact travels with the result rather than living on this object: two
        # triggers at 03:00 would share one attribute, which is the bug _pending_trigger was.
        if not by_trigger:
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
            try:
                self.hub.refresh_machine()
                self.hub.refresh_now()
            except HUB_IS_DOWN as gone:
                # Refresh is the button somebody presses *because* something looks wrong, so
                # it is the most likely one to be pressed while the hub is down. Nothing to
                # say here beyond what the badge already says.
                log.info("could not refresh: the hub is not answering (%s)", gone)
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
        self.flyout = flyout_mod.Flyout(lambda: self.cfg, self.store, hub=lambda: self.hub)
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
                                  live_config=lambda: self.cfg,
                                  # A callable, not the object: the Clients page asks
                                  # each time, and this window outlives a reconnection.
                                  hub=lambda: self.hub)
        win.saved.connect(self._settings_saved)
        win.test_run.connect(self.run_stack)
        win.run_trigger.connect(self.run_trigger)
        win.theme_changed.connect(self.apply_theme)
        win.mine_changed.connect(self._mine_changed)
        win.hub_changed.connect(self._hub_changed)
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

    def _mine_changed(self) -> None:
        """A display choice was stored on this computer: read it and rebuild what shows it.

        Not part of the config, so it does not go through Save and never travels to a hub —
        a client that sent its theme to one had it reverted on the next launch, and wrote one
        person's choice into a file every other client reads.
        """
        self.mine = local_mod.load()
        was = i18n.current()
        i18n.use(self.mine.language)
        if i18n.current() != was:
            # Every label is worded when it is built, so the two windows that live for the
            # whole session have to be built again. An open panel keeps its own words, which
            # the hint under the picker says.
            self.tray.rebuild_menu()
            self.flyout.rebuild()
        try:
            autostart.apply(self.mine.auto_start)
        except Exception as exc:
            log.warning("could not apply the auto-start setting: %s", exc)

    def _settings_saved(self, new_cfg):
        """Persist what the panel edited, then rebuild what shows it.

        The saving, the connection dropping and the store pruning are the engine's;
        the dialogs and the rebuilds are ours. Auto-start, the theme and the language are
        neither: they belong to this installation rather than to the landscape, so they are
        stored the moment they are picked and arrive here through `_mine_changed`. They were
        in this method, and on a client that meant sending them to the hub — where every
        other client correctly ignored them and this one found them reverted on its next
        launch, because its own disk had never recorded them.
        """
        try:
            self._save_config(new_cfg)
        except Exception as exc:
            QMessageBox.warning(None, "Service Officer",
                                f"Could not save settings:\n{exc}")
            return
        self.cfg = new_cfg
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
        """Whichever of the two this process has, stop it.

        `self.engine` is None when this is a client of a hub — so this used to raise
        AttributeError on the way out, on every client, every time. Nothing noticed
        because a daemon thread dies with the process anyway and the exception went to a
        Qt slot; the app still closed. It is still wrong, and the hub connection deserves
        the clean close it now gets in well under a second.
        """
        log.info("quitting")
        if self.engine is not None:
            self.engine.stop()
        if self.hub is not None:
            self.hub.stop()
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
