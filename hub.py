"""Service Officer Hub — the engine, with no interface, as a Windows service.

    hub.exe install          register the service (also: remove, start, stop, restart)
    hub.exe --console        run in this window, logging to it, for debugging
    hub.exe client add NAME  issue a token for a client and print it once
    hub.exe client list      who is paired, and when each last spoke
    hub.exe client revoke N  stop that client
    hub.exe client pair --local   pair this machine's own client, unattended
    hub.exe --fingerprint    the certificate's fingerprint, to give a client

Why a service rather than a scheduled task or a tray app: it starts before anybody logs
in, Windows restarts it if it dies, and it can run as a domain service account — which is
what lets it manage servers in that domain with no password stored anywhere.

**Staying up is the point**, so it is worth saying where that comes from, in order of how
much it does:

1. *Windows' own recovery.* `sc failure` is set at install: restart after 5 s, then 10 s,
   then 30 s, with the count reset daily. If the process dies, it comes back. This is the
   layer that matters most and it costs nothing.
2. *Nothing inside brings the whole thing down.* Every worker — a poll, a health check, a
   trigger, a request — catches its own exceptions, and `Engine._call` catches a listener
   that raises. A machine that has gone away is one thread's problem.
3. *No unbounded growth.* One held connection per machine rather than one per call, one
   SSH session per machine, a bounded queue per event stream, and history trimmed daily.
4. *It says what it is doing.* Everything above logs to the same rotating file, so "why
   did it restart at 04:12" has an answer.
"""

from __future__ import annotations

import os
import sys
import threading

from core import applog, config as cfg_mod, engine as engine_mod
from core import hub_auth, hub_server, local as local_mod

log = applog.get("hub")

SERVICE_NAME = "ServiceOfficerHub"
DISPLAY_NAME = "Service Officer Hub"
#: Restart after 5 s, then 10 s, then 30 s for any further failure, and forget the count
#: after a day. Applied with sc.exe at install: pywin32's service framework has no API
#: for the recovery tab, and this is the layer that actually keeps the hub up.
FAILURE_ACTIONS = "restart/5000/restart/10000/restart/30000"
FAILURE_RESET_SECONDS = 86400


def build() -> tuple:
    """The engine and the server, in the order they have to be built.

    The config is held in a box rather than read once: saving replaces the object, and
    everything downstream reads through the getter, so a save has to be visible to the
    poller and the scheduler without rebuilding them.
    """
    box = {"cfg": cfg_mod.load()}
    engine = engine_mod.Engine(lambda: box["cfg"],
                              on_config_saved=lambda config: box.update(cfg=config))
    settings = box["cfg"].hub
    certfile, fingerprint = hub_auth.ensure_certificate(
        cfg_mod.in_app_dir("hub.pem"))
    server = hub_server.HubServer(engine,
                                  host=settings.bind or "0.0.0.0",
                                  port=settings.port,
                                  certfile=certfile)
    return engine, server, fingerprint


def start(engine, server, fingerprint: str) -> None:
    engine.start()
    server.start()
    log.info("hub serving on %s  ·  certificate %s", server.url, fingerprint)


def stop(engine, server) -> None:
    """Stop taking requests before stopping the engine: a request that arrived while the
    poller was already gone would be answered out of a half-dismantled store."""
    server.stop()
    engine.wait_for_actions(timeout=15)
    engine.stop()


# ---------------------------------------------------------------------------
# the console commands
# ---------------------------------------------------------------------------
def _client_command(argv) -> int | None:
    """`hub.exe client add|list|revoke|pair` — the only way a token is created.

    Printed once and never stored in a readable form, so a hub whose store is copied
    does not hand over its clients with it.
    """
    if len(argv) < 2 or argv[1] != "client":
        return None
    what = argv[2] if len(argv) > 2 else "list"

    if what == "add" and len(argv) > 3:
        name = argv[3]
        token = hub_auth.add_client(name)
        _p, fingerprint = hub_auth.ensure_certificate(cfg_mod.in_app_dir("hub.pem"))
        import socket
        where = f"https://{socket.gethostname()}:{cfg_mod.load().hub.port}"
        print(f"\nToken for {name}:\n\n  {token}\n")
        print("Give it to that client once:\n")
        print(f'  ServiceOfficer.exe --connect {where} --token {token}\n')
        print(f"It should see this certificate:\n\n  {fingerprint}\n")
        print("The token is not shown again. Make another if it is lost.")
        return 0

    if what == "pair" and "--local" in argv:
        # The installer's path: a machine that installs both components comes out
        # already working, with nobody shown a token they would have to carry.
        name = f"{os.environ.get('COMPUTERNAME', 'this-computer')}-local"
        token = hub_auth.add_client(name)
        _p, fingerprint = hub_auth.ensure_certificate(cfg_mod.in_app_dir("hub.pem"))
        import socket
        # This computer's *name*, not localhost: the certificate is issued for the host
        # name, and a client that pinned localhost could not later be pointed at the
        # same hub by name without failing its own check.
        url = f"https://{socket.gethostname()}:{cfg_mod.load().hub.port}"
        settings = local_mod.load()
        settings.hub_url = url
        settings.hub_fingerprint = fingerprint
        local_mod.save(settings)
        local_mod.set_token(url, token)
        print(f"paired this machine's client to {url}")
        return 0

    if what == "revoke" and len(argv) > 3:
        print("revoked" if hub_auth.revoke(argv[3]) else "no such client")
        return 0

    listed = hub_auth.clients()
    if not listed:
        print("no clients paired. Use: hub.exe client add <name>")
    for client in listed:
        print(f"  {client['name']:24s} added {client['added']}  "
              f"last seen {client['last_seen'] or 'never'}")
    return 0


def _console() -> int:
    applog.setup()
    engine, server, fingerprint = build()
    start(engine, server, fingerprint)
    print(f"hub serving on {server.url}")
    print(f"certificate    {fingerprint}")
    print("Ctrl-C to stop")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nstopping…")
    stop(engine, server)
    return 0


def _apply_recovery() -> None:
    """Tell Windows to restart the service if it dies.

    Layer one of staying up, and the only one that survives the process itself being
    gone. pywin32 exposes no API for this, so it is sc.exe — and a failure here is
    logged rather than fatal: a hub that is running without a recovery policy is worth
    more than an install that refused to finish.
    """
    import subprocess
    for args in (["failure", SERVICE_NAME, "reset=", str(FAILURE_RESET_SECONDS),
                  "actions=", FAILURE_ACTIONS],
                 # Delayed automatic start: at boot, the services this hub manages are
                 # starting too, and asking about them while they do produces a page of
                 # "not responding" nobody needs.
                 ["config", SERVICE_NAME, "start=", "delayed-auto"]):
        try:
            done = subprocess.run(["sc.exe", *args], capture_output=True, text=True,
                                  timeout=30)
            if done.returncode != 0:
                log.warning("sc %s said: %s", args[0],
                            (done.stdout or done.stderr).strip()[:200])
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("could not apply the %s policy: %s", args[0], exc)


# ---------------------------------------------------------------------------
# the service
# ---------------------------------------------------------------------------
def _service_class():
    """Built lazily so the console and client commands work on a machine where the
    service framework is not importable — and so importing this module for a test does
    not need pywin32's service bits at all."""
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil

    class HubService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = DISPLAY_NAME
        _svc_description_ = ("Watches and controls the services listed in Service "
                             "Officer, and answers its clients.")

        def __init__(self, args):
            super().__init__(args)
            self._wait = win32event.CreateEvent(None, 0, 0, None)
            self._parts = None

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self._wait)

        def SvcDoRun(self):
            applog.setup()
            servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                                  servicemanager.PYS_SERVICE_STARTED,
                                  (self._svc_name_, ""))
            try:
                engine, server, fingerprint = build()
                self._parts = (engine, server)
                start(engine, server, fingerprint)
                win32event.WaitForSingleObject(self._wait, win32event.INFINITE)
            except Exception:
                # Logged before it propagates: Windows will restart us, and the reason
                # has to survive into the file or the restart is a mystery.
                log.exception("the hub stopped because of an error")
                raise
            finally:
                if self._parts:
                    stop(*self._parts)
                log.info("hub stopped")

    return HubService


def _dispatch() -> int:
    """Hand this process to the service control manager.

    This is how a service actually *starts*. The SCM launches the exe with no arguments
    and expects the process to connect back to it within about thirty seconds; nothing
    on a command line says which service it is. Without this branch the launch falls
    through to `HandleCommandLine`, which finds no command, prints usage and exits — and
    Windows reports error 1053, "the service did not respond in a timely fashion", which
    says nothing about the actual cause.

    It cannot be reached from a console (`StartServiceCtrlDispatcher` fails with
    error 1063 there, which is the correct answer), so `--console` exists for debugging.
    """
    import servicemanager
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(_service_class())
    servicemanager.StartServiceCtrlDispatcher()
    return 0


def main() -> int:
    if "--fingerprint" in sys.argv:
        _p, fingerprint = hub_auth.ensure_certificate(cfg_mod.in_app_dir("hub.pem"))
        print(fingerprint)
        return 0

    handled = _client_command(sys.argv)
    if handled is not None:
        return handled

    if "--console" in sys.argv:
        return _console()

    # No arguments at all means the SCM started us — see _dispatch. A person typing the
    # name of this exe with nothing after it gets the same path and a readable refusal
    # from Windows (error 1063: not started by the service control manager).
    if len(sys.argv) == 1:
        return _dispatch()

    import win32serviceutil
    win32serviceutil.HandleCommandLine(_service_class())
    if "install" in sys.argv:
        _apply_recovery()
    return 0


if __name__ == "__main__":
    sys.exit(main())
