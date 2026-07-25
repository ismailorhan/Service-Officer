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
