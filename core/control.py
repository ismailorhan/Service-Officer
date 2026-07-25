"""Service control — query, start, stop, restart, enumerate.

Every call takes an optional machine, because pywin32 accepts one throughout and
that is what makes managing another server (roadmap #4) a UI problem rather than
a plumbing one. An empty machine means this computer.
"""

from __future__ import annotations

import os
import socket
import threading

import win32service
import win32serviceutil
import pywintypes

from . import state as st

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


def host_name() -> str:
    """This computer's name as Windows knows it, so the local machine can be
    named rather than called "this computer"."""
    return (os.environ.get("COMPUTERNAME") or socket.gethostname() or "").strip()


_addresses: dict = {}
_resolving: set = set()


def cached_address(machine: str = ""):
    """The address if we already know it; None if it has never been looked up.
    Painting a list must not wait on DNS — a name that doesn't resolve costs
    three seconds, measured."""
    return _addresses.get(machine or "")


def resolve_address(machine: str = "", done=None) -> None:
    """Look an address up on a worker thread, calling done(machine, address)
    when it lands. Repeat calls while one is in flight are ignored."""
    key = machine or ""
    if key in _addresses:
        if done:
            done(key, _addresses[key])
        return
    if key in _resolving:
        return
    _resolving.add(key)

    def work():
        try:
            found = address_of(machine)
        finally:
            _resolving.discard(key)
        if done:
            done(key, found)

    threading.Thread(target=work, daemon=True).start()


def address_of(machine: str = "") -> str:
    """Best-effort IP address for a machine, empty when it can't be resolved.

    Blocking — call resolve_address() from anything that paints.
    """
    key = machine or ""
    if key in _addresses:
        return _addresses[key]
    target = machine or socket.gethostname()
    found = ""
    try:
        # Prefer a routable v4 address; a machine usually has several.
        infos = socket.getaddrinfo(target, None, socket.AF_INET)
        found = infos[0][4][0] if infos else ""
    except (socket.gaierror, OSError, IndexError):
        found = ""
    if found.startswith("127.") and not machine:
        # A hosts-file entry pointing at loopback tells the user nothing; ask
        # the routing table which address this computer actually uses.
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.settimeout(0.2)
            probe.connect(("10.255.255.255", 1))       # never sends a packet
            found = probe.getsockname()[0]
            probe.close()
        except OSError:
            pass
    _addresses[key] = found
    return found


def reachable(machine: str) -> bool:
    """Can we talk to this machine's SCM at all? Used to show a machine as
    offline instead of every service on it as 'Not Found'."""
    try:
        scm = win32service.OpenSCManager(_m(machine), None,
                                         win32service.SC_MANAGER_CONNECT)
        win32service.CloseServiceHandle(scm)
        return True
    except pywintypes.error:
        return False
