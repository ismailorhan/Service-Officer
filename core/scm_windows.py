"""The Windows service control manager, through pywin32.

One of the transports behind `core/control.py`. Everything here speaks SCM; a
machine name is passed straight through, because pywin32 accepts one on every
call and that is what made managing another Windows server a UI problem rather
than a plumbing one.

Nothing above `control.py` should import this — that is the whole point of the
split. When a target is a Linux box the router picks a different transport, and
neither one knows the other exists.
"""

from __future__ import annotations

import threading
import time

import win32service
import pywintypes

from . import applog
from . import state as st

log = applog.get("scm")


_STATUS_MAP = {
    win32service.SERVICE_STOPPED:          st.STOPPED,
    win32service.SERVICE_START_PENDING:    "Starting",
    win32service.SERVICE_STOP_PENDING:     "Stopping",
    win32service.SERVICE_RUNNING:          st.RUNNING,
    win32service.SERVICE_CONTINUE_PENDING: "Resuming",
    win32service.SERVICE_PAUSE_PENDING:    "Pausing",
    win32service.SERVICE_PAUSED:           st.PAUSED,
}


def _m(machine: str):
    """pywin32 wants None for the local machine, not an empty string."""
    return machine or None


def _admin_share_path(host: str, path: str) -> str:
    r"""`C:\b1\beat` on `host` as `\\host\C$\b1\beat`, or "" if it is not a local
    drive-letter path.

    The administrative share (C$, D$, …) is the drive itself, reachable to an
    administrator over the same SMB session the service manager already uses. It is
    empty for a UNC path or a relative one, because neither names a drive on the
    target — and answering those from this computer is exactly the confusion this
    replaced.
    """
    if len(path) < 3 or path[1] != ":" or path[2] not in "\\/":
        return ""
    if not path[0].isalpha():
        return ""
    drive, rest = path[0].upper(), path[3:]
    return rf"\\{host}\{drive}$\{rest}"


def query_status(service_name: str, machine: str = "") -> str:
    try:
        return held_for(machine).do(lambda scm: state_on(scm, service_name)[0])
    except pywintypes.error:
        return st.NOT_FOUND


def start_service(service_name: str, machine: str = "") -> None:
    def work(scm):
        handle = win32service.OpenService(scm, service_name,
                                          win32service.SERVICE_START)
        try:
            win32service.StartService(handle, None)
        finally:
            win32service.CloseServiceHandle(handle)
    held_for(machine).do(work)


#: How long to wait for a service to admit it has stopped, before starting it again.
#: Only used by restart — a stop on its own is reported as soon as it is accepted,
#: and the poller sees the rest.
#:
#: Thirty seconds was not enough for a real one: SAP's Server Tools is a Tomcat, and
#: a restart of it left the service stopped for good. Windows answers a start request
#: with 1056, "already running", for a service in *any* state other than Stopped —
#: Stopping included — so the start was refused, and 1056 was on the list of errors
#: treated as nothing to worry about.
STOP_WAIT = 120.0


def stop_service(service_name: str, machine: str = "") -> None:
    def work(scm):
        handle = win32service.OpenService(scm, service_name,
                                          win32service.SERVICE_STOP)
        try:
            win32service.ControlService(handle, win32service.SERVICE_CONTROL_STOP)
        finally:
            win32service.CloseServiceHandle(handle)
    held_for(machine).do(work)


#: Windows' answer to "start this" for a service that is not Stopped. It says
#: "already running" even when the service is Stopping, which is what made a restart
#: give up quietly in the middle.
ALREADY_RUNNING = 1056


def _wait_until_stopped(service_name: str, machine: str, seconds: float) -> str:
    """The state it reached. Returns as soon as it is Stopped."""
    deadline = time.monotonic() + seconds
    state = query_status(service_name, machine)
    while state != st.STOPPED and time.monotonic() < deadline:
        time.sleep(0.5)
        state = query_status(service_name, machine)
    return state


def restart_service(service_name: str, machine: str = "") -> None:
    """Stop, wait for it to actually be stopped, start.

    Written out rather than using win32serviceutil.RestartService, which opens its
    own connection to the machine for each step — 21 seconds each, measured, against
    a remote box whose held connection answers in 7 milliseconds.

    The waiting is the whole job. A service that has been asked to stop and has not
    finished refuses a start with "already running", and a restart that treats that as
    nothing to worry about leaves the service stopped and reports success — which is
    the worst outcome available, because nobody goes to look.
    """
    try:
        stop_service(service_name, machine)
    except pywintypes.error as exc:
        if not nothing_to_do(exc):
            raise

    state = _wait_until_stopped(service_name, machine, STOP_WAIT)
    if state != st.STOPPED:
        raise RuntimeError(
            f"it was still {state.lower()} {STOP_WAIT:.0f}s after being asked to "
            f"stop, so it has not been started again")
    try:
        start_service(service_name, machine)
    except pywintypes.error as exc:
        if getattr(exc, "winerror", None) != ALREADY_RUNNING:
            raise
        # It had not finished stopping after all: the state we read was a moment old.
        # Wait the rest of the budget out and try the one thing that remains.
        state = _wait_until_stopped(service_name, machine, STOP_WAIT)
        if state != st.STOPPED:
            raise RuntimeError(
                f"it would not stop — Windows still calls it {state.lower()} — so it "
                f"has not been started again") from exc
        start_service(service_name, machine)


def list_all_services(machine: str = "") -> list:
    """Every installed Win32 service as {"name", "display", "status"}, sorted by
    display name — what the settings picker offers."""
    raw = held_for(machine).do(lambda scm: win32service.EnumServicesStatus(
        scm, win32service.SERVICE_WIN32, win32service.SERVICE_STATE_ALL))

    services = [{"name": name, "display": display,
                 "status": _STATUS_MAP.get(status[1], st.UNKNOWN)}
                for name, display, status in raw]
    services.sort(key=lambda s: s["display"].lower())
    return services


_START_TYPES = {
    win32service.SERVICE_BOOT_START:   "Boot",
    win32service.SERVICE_SYSTEM_START: "System",
    win32service.SERVICE_AUTO_START:   "Automatic",
    win32service.SERVICE_DEMAND_START: "Manual",
    win32service.SERVICE_DISABLED:     "Disabled",
}


def start_type(service_name: str, machine: str = "") -> str:
    """Automatic / Manual / Disabled …

    Worth knowing because a disabled service cannot be started at all — Windows
    refuses with "the service cannot be started because it is disabled", and
    offering a Start button for it is a lie.
    """
    try:
        return held_for(machine).do(lambda scm: start_type_on(scm, service_name))
    except pywintypes.error:
        return ""


def process_id(service_name: str, machine: str = "") -> int:
    """The service's process, or 0 if it isn't running.

    Needed for the last resort below, and worth having anyway: it identifies the
    process for resource figures later.
    """
    try:
        return held_for(machine).do(lambda scm: state_on(scm, service_name)[1])
    except pywintypes.error:
        return 0


def kill_process(service_name: str, machine: str = "") -> int:
    """Terminate the service's process outright. Returns the pid killed.

    For when a service is wedged and Stop does nothing: the SCM keeps reporting
    "Stopping" for ever because the process never acknowledges the control
    request. Terminating it is abrupt by definition — the service gets no chance
    to flush anything — so the UI asks before calling this.

    Only local services: terminating a process on another machine needs a
    different mechanism entirely.
    """
    if machine:
        raise RuntimeError("Killing a process is only possible on this computer.")
    pid = process_id(service_name)
    if not pid:
        raise RuntimeError("That service has no running process.")

    import win32api
    import win32con
    handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, pid)
    try:
        win32api.TerminateProcess(handle, 1)
    finally:
        win32api.CloseHandle(handle)
    return pid


#: Windows refusing because the service is already where you asked it to be.
#: Not failures — there was nothing to do. 1056 ERROR_SERVICE_ALREADY_RUNNING,
#: 1062 ERROR_SERVICE_NOT_ACTIVE, 1058 ERROR_SERVICE_DISABLED.
NOTHING_TO_DO = {1056: "it is already running",
                 1062: "it is already stopped",
                 1058: "it is disabled in Windows"}


def nothing_to_do(exc) -> str:
    """A plain reason if this exception only means "no change needed", else "".

    Stopping a stopped service raises "The service has not been started", which
    is not a problem and must not be reported as one.
    """
    code = getattr(exc, "winerror", None)
    return NOTHING_TO_DO.get(code, "")


#: Errors that mean the held connection is no longer usable, so reopen and retry.
_STALE = (6, 1722, 1723, 1726, 1727, 5)
#: One held connection per machine, "" being this computer.
_held: dict = {}
_held_lock = threading.RLock()


def held_for(machine: str = ""):
    """The kept-open connection to a machine's service manager."""
    key = machine or ""
    with _held_lock:
        found = _held.get(key)
        if found is None:
            found = _Held(key)
            _held[key] = found
        return found


def disconnect(machine: str = None) -> None:
    """Close held connections, so changed credentials are actually used and a
    machine that has gone away is not represented by a stale handle."""
    with _held_lock:
        keys = [machine or ""] if machine is not None else list(_held)
        for key in keys:
            found = _held.pop(key, None)
            if found is not None:
                found.drop()


class _Held:
    """One machine's service manager, kept open between calls.

    Measured against a remote Windows box in another domain: `OpenSCManager` took
    **21 seconds**, every single time. On the connection it returns, listing every
    service took 18 ms and querying one took 7 ms. So the entire cost was opening,
    and the code opened afresh for every question — three per service per poll, which
    is where sixty-three seconds to read one service's status came from.

    Held, that becomes 21 seconds once per machine per run, and milliseconds after.
    The handle is a kernel/RPC object, safe to use from several threads; only opening
    and closing are serialised, so a stop that takes a while cannot block a poll.
    """

    ACCESS = (win32service.SC_MANAGER_CONNECT
              | win32service.SC_MANAGER_ENUMERATE_SERVICE)

    def __init__(self, host: str = ""):
        self.host = host or ""
        self._handle = None
        self._lock = threading.RLock()

    def handle(self):
        with self._lock:
            if self._handle is None:
                self._handle = win32service.OpenSCManager(_m(self.host), None,
                                                          self.ACCESS)
                if self.host:
                    log.info("connected to %s's service manager", self.host)
            return self._handle

    def drop(self) -> None:
        with self._lock:
            handle, self._handle = self._handle, None
        if handle is not None:
            try:
                win32service.CloseServiceHandle(handle)
            except Exception:
                pass

    def do(self, work):
        """Run work(scm), reopening once if the connection has gone stale.

        A machine that was rebooted, or a session that expired, leaves a handle that
        fails on use rather than announcing itself — so the retry is what keeps a
        held connection from being worse than opening every time.
        """
        try:
            return work(self.handle())
        except pywintypes.error as exc:
            if getattr(exc, "winerror", None) not in _STALE:
                raise
            log.info("%s: reconnecting (%s)", self.host or "this computer",
                     getattr(exc, "strerror", exc))
            self.drop()
            return work(self.handle())


def state_on(scm, service_name: str) -> tuple:
    """(status, pid, exit code) for one service, on an open connection."""
    handle = win32service.OpenService(scm, service_name,
                                      win32service.SERVICE_QUERY_STATUS)
    try:
        found = win32service.QueryServiceStatusEx(handle)
        return (_STATUS_MAP.get(found["CurrentState"], st.UNKNOWN),
                int(found.get("ProcessId") or 0),
                int(found.get("Win32ExitCode") or 0))
    finally:
        win32service.CloseServiceHandle(handle)


def start_type_on(scm, service_name: str) -> str:
    handle = win32service.OpenService(scm, service_name,
                                      win32service.SERVICE_QUERY_CONFIG)
    try:
        return _START_TYPES.get(win32service.QueryServiceConfig(handle)[1], "")
    finally:
        win32service.CloseServiceHandle(handle)


def _ask_scm(machine: str) -> bool:
    try:
        held_for(machine).do(lambda scm: True)
        return True
    except pywintypes.error:
        return False


#: How long to wait for another machine's SCM before calling it unreachable.
#:
#: Two measurements set this. A box with RPC's dynamic ports firewalled took **42
#: seconds** to give up with "the RPC server is unavailable" — a hang, to anyone
#: watching. A box that works, in another domain, took **21 seconds** to open and
#: then answered in milliseconds. So the limit has to sit above the second and below
#: the first, and it is only ever paid once per machine now that the connection is
#: held: eight seconds was wrong and reported a working machine as unreachable.
REMOTE_TIMEOUT = 30.0


def reachable(machine: str, timeout: float = REMOTE_TIMEOUT) -> bool:
    """Can we talk to this machine's SCM at all? Used to show a machine as
    offline instead of every service on it as 'Not Found'.

    The wait is bounded for another machine, and unbounded for this one — there is
    no network in the way of the local SCM, and it answers in under a millisecond.

    Bounded by abandoning rather than cancelling, because there is nothing to cancel:
    the RPC call is inside Windows and has no timeout to set. The thread it is stuck
    in is a daemon reading a status, so leaving it to finish on its own costs one
    thread until the firewall finishes refusing it.
    """
    if not machine:
        return _ask_scm(machine)
    answer: list = []
    thread = threading.Thread(target=lambda: answer.append(_ask_scm(machine)),
                              daemon=True)
    thread.start()
    thread.join(timeout)
    if not answer:
        log.info("%s: no answer from its service manager within %.0fs", machine,
                 timeout)
        return False
    return answer[0]


class WindowsConnector:
    """The SCM as a connector. A thin wrapper: the functions above are the
    implementation, this gives them the shape everything above `control.py`
    speaks.

    It also owns *who* we are on the target. A remote machine can be reached either
    as whoever is signed in here — nothing to configure, and the only option there
    used to be — or as a named account, which needs a session established first.
    See core/win_session.py for why that is how Windows does it.
    """

    def __init__(self, machine: str = "", record=None):
        self.machine = machine or ""
        self.record = record
        #: what to tell the SCM. The machine's id is a label, not necessarily a
        #: host name: a machine described as "app server" with host 10.0.0.9 was
        #: previously looked up by the label, which does not resolve.
        self.host = (getattr(record, "address", "") or self.machine) if self.machine \
            else ""

    # -- who we are --------------------------------------------------------
    def _sign_in(self) -> None:
        """Before any call, be the account this machine is configured for.

        Cheap to call repeatedly: it returns immediately once the session exists.
        """
        record = self.record
        if not self.machine or record is None:
            return
        if getattr(record, "auth", "current_user") != "password":
            return                      # this session's own token, as before
        from . import secrets, win_session
        password = secrets.get(getattr(record, "secret_ref", ""))
        if not password:
            raise RuntimeError("no password saved for this machine — set one in "
                               "Machines")
        win_session.ensure(self.host, record.username, password)

    def forget(self) -> None:
        """Let go of the machine: the held service-manager connection first, then
        the Windows session, so edited credentials are actually used."""
        if not self.machine:
            return
        disconnect(self.host)
        if getattr(self.record, "auth", "") == "password":
            from . import win_session
            win_session.forget(self.host)

    def abilities(self):
        from .connectors import Abilities
        # Kill is local-only: terminating a process on another machine needs a
        # mechanism we do not have. Push notifications are local-only too, in the
        # code as it stands — scm.Watcher skips remote services.
        local = not self.machine
        # logs=local for the same reason as kill: the event log reader opens *this*
        # computer's log, so claiming it for another machine would offer our own
        # events under that service's name.
        return Abilities(
            control=True, kill=local, logs=local, push=local,
            # A File check reaches another machine over its admin share; a Command
            # check does not, because running one there needs WinRM or a scheduled
            # task and neither is wired up.
            file_check=True, command_check=local,
            why="" if local else "This is another computer: its process cannot be "
                                 "terminated from here, its event log cannot be read "
                                 "from here, a command cannot be run on it, and status "
                                 "is polled.")

    def reachable(self) -> bool:
        try:
            self._sign_in()
        except RuntimeError:
            # Cannot sign in, so cannot talk to it — which is what unreachable
            # means here. The reason is reported by Test connection, not by a
            # background poll that has nowhere to put it.
            return False
        return reachable(self.host)

    def list_services(self) -> list:
        from .connectors import ServiceInfo
        self._sign_in()
        return [ServiceInfo(name=s["name"], display=s["display"],
                            status=s["status"])
                for s in list_all_services(self.host)]

    def status(self, name: str):
        from .connectors import Status
        self._sign_in()
        return self.statuses([name]).get(name) or Status(state=st.NOT_FOUND)

    def statuses(self, names: list) -> dict:
        """Every service in one pass over one held connection.

        The poller asks for this by name. Doing it per service, each opening its own
        connection, is what made a single remote service cost sixty-three seconds:
        three opens at twenty-one seconds each. Here the opens happen once, ever, and
        each service costs about fourteen milliseconds.
        """
        from .connectors import Status
        self._sign_in()

        def work(scm):
            out = {}
            for name in names:
                try:
                    state, pid, code = state_on(scm, name)
                    kind = start_type_on(scm, name)
                    out[name] = Status(state=state, pid=pid, exit_code=code,
                                       start_type=kind)
                except pywintypes.error as exc:
                    if getattr(exc, "winerror", None) in _STALE:
                        raise           # the connection, not the service: retry it
                    # 1060 is "the service does not exist", which is about this one
                    # service and must not sink the whole batch.
                    out[name] = Status(state=st.NOT_FOUND, installed=False,
                                       detail=getattr(exc, "strerror", str(exc)))
            return out
        return held_for(self.host).do(work)

    def start(self, name: str) -> None:
        self._sign_in()
        start_service(name, self.host)

    def stop(self, name: str) -> None:
        self._sign_in()
        stop_service(name, self.host)

    def restart(self, name: str) -> None:
        self._sign_in()
        restart_service(name, self.host)

    def kill(self, name: str) -> int:
        return kill_process(name, self.machine)

    def logs(self, name: str, lines: int = 50) -> list:
        """The Windows event log already has its own reader, which merges several
        logs and matches on more than the service name — so this defers to it
        rather than growing a second, worse one.

        This computer only. `eventlog.read` opens the log with
        `OpenEventLog(None, …)`: there is no machine to pass, so for another machine
        it would return *our* events under that service's name. Reading a remote
        event log needs its own path (RPC to the target's log, or `wevtutil /r:`),
        and until it exists, saying so beats answering with the wrong machine's
        history.
        """
        if self.machine:
            raise RuntimeError("Reading the event log of another computer is not "
                               "supported yet.")
        from . import eventlog
        return [f"{r['ts']}  {r['level']}  {r['summary'] or r['message']}"
                for r in eventlog.read([name], [name], limit=lines)]

    def run(self, command: str, timeout: float = 10.0):
        """Only on this computer. Running a command on another Windows box needs
        WinRM or a scheduled task, and pretending otherwise would make a health
        check quietly measure the wrong machine."""
        if self.machine:
            raise RuntimeError("Running a command on another computer is not "
                               "supported yet.")
        import subprocess
        try:
            done = subprocess.run(command, shell=True, capture_output=True,
                                  text=True, timeout=timeout)
            return done.returncode, (done.stdout or done.stderr or "").strip()
        except subprocess.TimeoutExpired:
            return -1, f"no answer within {timeout:g}s"

    def stat(self, path: str):
        """Does the file exist, and how long since it was written?

        On another machine this goes over its administrative share — measured at 18 ms
        on the session already open to IPC$, and needing nothing installed there. A
        local drive letter only: `C:\\x` becomes `\\\\host\\C$\\x`. A UNC path or a
        relative one cannot be reached this way and is reported as absent rather than
        silently checked here — which was the bug this whole line of work started from.
        """
        import os
        import time
        if self.machine:
            self._sign_in()
            unc = _admin_share_path(self.host, path)
            if not unc:
                return False, 0.0
            path = unc
        try:
            return True, max(0.0, time.time() - os.path.getmtime(path))
        except OSError:
            return False, 0.0
