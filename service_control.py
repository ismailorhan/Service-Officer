import win32service
import win32serviceutil
import pywintypes

_STATUS_MAP = {
    win32service.SERVICE_STOPPED:          "Stopped",
    win32service.SERVICE_START_PENDING:    "Starting",
    win32service.SERVICE_STOP_PENDING:     "Stopping",
    win32service.SERVICE_RUNNING:          "Running",
    win32service.SERVICE_CONTINUE_PENDING: "Resuming",
    win32service.SERVICE_PAUSE_PENDING:    "Pausing",
    win32service.SERVICE_PAUSED:           "Paused",
}


def query_status(service_name: str) -> str:
    try:
        status = win32serviceutil.QueryServiceStatus(service_name)
        return _STATUS_MAP.get(status[1], "Unknown")
    except pywintypes.error:
        return "Not Found"


def start_service(service_name: str) -> None:
    win32serviceutil.StartService(service_name)


def stop_service(service_name: str) -> None:
    win32serviceutil.StopService(service_name)


def restart_service(service_name: str) -> None:
    win32serviceutil.RestartService(service_name)


def list_all_services() -> list:
    """Enumerate every installed Win32 service.

    Returns a list of {"name", "display", "status"} dicts sorted by display
    name. Used by the settings service picker so the user can choose a service
    instead of typing its short name.
    """
    scm = win32service.OpenSCManager(
        None, None, win32service.SC_MANAGER_ENUMERATE_SERVICE
    )
    try:
        raw = win32service.EnumServicesStatus(
            scm, win32service.SERVICE_WIN32, win32service.SERVICE_STATE_ALL
        )
    finally:
        win32service.CloseServiceHandle(scm)

    services = []
    for name, display, status in raw:
        services.append({
            "name": name,
            "display": display,
            "status": _STATUS_MAP.get(status[1], "Unknown"),
        })
    services.sort(key=lambda s: s["display"].lower())
    return services
