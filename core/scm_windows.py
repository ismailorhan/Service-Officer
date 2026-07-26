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

import win32service
import win32serviceutil
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


def query_status(service_name: str, machine: str = "") -> str:
    try:
        status = win32serviceutil.QueryServiceStatus(service_name, _m(machine))
        return _STATUS_MAP.get(status[1], st.UNKNOWN)
    except pywintypes.error:
        return st.NOT_FOUND


def start_service(service_name: str, machine: str = "") -> None:
    win32serviceutil.StartService(service_name, machine=_m(machine))


def stop_service(service_name: str, machine: str = "") -> None:
    win32serviceutil.StopService(service_name, _m(machine))


def restart_service(service_name: str, machine: str = "") -> None:
    win32serviceutil.RestartService(service_name, machine=_m(machine))


def list_all_services(machine: str = "") -> list:
    """Every installed Win32 service as {"name", "display", "status"}, sorted by
    display name — what the settings picker offers."""
    scm = win32service.OpenSCManager(_m(machine), None,
                                     win32service.SC_MANAGER_ENUMERATE_SERVICE)
    try:
        raw = win32service.EnumServicesStatus(
            scm, win32service.SERVICE_WIN32, win32service.SERVICE_STATE_ALL)
    finally:
        win32service.CloseServiceHandle(scm)

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
        scm = win32service.OpenSCManager(_m(machine), None,
                                         win32service.SC_MANAGER_CONNECT)
    except pywintypes.error:
        return ""
    try:
        handle = win32service.OpenService(scm, service_name,
                                         win32service.SERVICE_QUERY_CONFIG)
        try:
            config = win32service.QueryServiceConfig(handle)
            return _START_TYPES.get(config[1], "")
        finally:
            win32service.CloseServiceHandle(handle)
    except pywintypes.error:
        return ""
    finally:
        win32service.CloseServiceHandle(scm)


def process_id(service_name: str, machine: str = "") -> int:
    """The service's process, or 0 if it isn't running.

    Needed for the last resort below, and worth having anyway: it identifies the
    process for resource figures later.
    """
    try:
        scm = win32service.OpenSCManager(_m(machine), None,
                                         win32service.SC_MANAGER_CONNECT)
    except pywintypes.error:
        return 0
    try:
        handle = win32service.OpenService(scm, service_name,
                                         win32service.SERVICE_QUERY_STATUS)
        try:
            info = win32service.QueryServiceStatusEx(handle)
            return int(info.get("ProcessId", 0) or 0)
        finally:
            win32service.CloseServiceHandle(handle)
    except pywintypes.error:
        return 0
    finally:
        win32service.CloseServiceHandle(scm)


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


def _ask_scm(machine: str) -> bool:
    try:
        scm = win32service.OpenSCManager(_m(machine), None,
                                         win32service.SC_MANAGER_CONNECT)
        win32service.CloseServiceHandle(scm)
        return True
    except pywintypes.error:
        return False


#: How long to wait for another machine's SCM before calling it unreachable.
#: Measured against a box with RPC's dynamic ports firewalled: OpenSCManager took
#: **42 seconds** to give up with "the RPC server is unavailable". That is a hang as
#: far as anyone watching is concerned, and it blocked the poll of every other
#: machine behind it.
REMOTE_TIMEOUT = 8.0


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
        """Drop the session, so edited credentials are actually used."""
        if self.machine and getattr(self.record, "auth", "") == "password":
            from . import win_session
            win_session.forget(self.host)

    def abilities(self):
        from .connectors import Abilities
        # Kill is local-only: terminating a process on another machine needs a
        # mechanism we do not have. Push notifications are local-only too, in the
        # code as it stands — scm.Watcher skips remote services.
        local = not self.machine
        return Abilities(
            control=True, kill=local, logs=True, push=local,
            why="" if local else "This is another computer: its process cannot be "
                                 "terminated from here, and status is polled.")

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
        return Status(state=query_status(name, self.host),
                      start_type=start_type(name, self.host),
                      pid=process_id(name, self.host))

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
        rather than growing a second, worse one."""
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
        if self.machine:
            raise RuntimeError("Checking a file on another computer is not "
                               "supported yet.")
        import os
        import time
        try:
            return True, max(0.0, time.time() - os.path.getmtime(path))
        except OSError:
            return False, 0.0
