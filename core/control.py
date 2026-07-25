"""Service control — query, start, stop, restart, enumerate.

Every call takes an optional machine, because pywin32 accepts one throughout and
that is what makes managing another server (roadmap #4) a UI problem rather than
a plumbing one. An empty machine means this computer.
"""

from __future__ import annotations

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
