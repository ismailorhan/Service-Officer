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
