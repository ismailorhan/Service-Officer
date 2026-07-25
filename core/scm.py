"""Push notifications from the Service Control Manager.

Polling can't see a short transition: an externally-triggered restart usually
finishes between two ticks, so the state change is never observed and the tray
icon never reacts. The SCM can instead *tell* us the moment a service changes
state, via NotifyServiceStatusChangeW.

Two things make that API awkward, and shape the design here:

* The callback is delivered as an APC, so it only runs while the registering
  thread sits in an alertable wait — hence the SleepEx loop below, and hence
  every registration for a given service belongs to that one thread.
* A registration is one-shot. After it fires it must be re-armed. Microsoft
  warns against calling back into the API from inside the callback, so the
  callback only records what happened and the loop re-arms afterwards.
"""

import ctypes
import ctypes.wintypes
import threading
import time

from . import state as _st

_advapi = ctypes.windll.advapi32
_kernel = ctypes.windll.kernel32

SC_MANAGER_CONNECT   = 0x0001
SERVICE_QUERY_STATUS = 0x0004

SERVICE_NOTIFY_STATUS_CHANGE = 2

# Everything except CREATED/DELETED, which only apply to an SCM handle.
_MASK = (0x0001 |   # STOPPED
         0x0002 |   # START_PENDING
         0x0004 |   # STOP_PENDING
         0x0008 |   # RUNNING
         0x0010 |   # CONTINUE_PENDING
         0x0020 |   # PAUSE_PENDING
         0x0040 |   # PAUSED
         0x0200)    # DELETE_PENDING

_STATE = {
    1: "Stopped", 2: "Starting", 3: "Stopping", 4: "Running",
    5: "Resuming", 6: "Pausing", 7: "Paused",
}

WAIT_IO_COMPLETION = 0x000000C0
ERROR_SERVICE_NOTIFY_CLIENT_LAGGING = 1294


class SERVICE_STATUS_PROCESS(ctypes.Structure):
    _fields_ = [("dwServiceType", ctypes.wintypes.DWORD),
                ("dwCurrentState", ctypes.wintypes.DWORD),
                ("dwControlsAccepted", ctypes.wintypes.DWORD),
                ("dwWin32ExitCode", ctypes.wintypes.DWORD),
                ("dwServiceSpecificExitCode", ctypes.wintypes.DWORD),
                ("dwCheckPoint", ctypes.wintypes.DWORD),
                ("dwWaitHint", ctypes.wintypes.DWORD),
                ("dwProcessId", ctypes.wintypes.DWORD),
                ("dwServiceFlags", ctypes.wintypes.DWORD)]


PFN_SC_NOTIFY_CALLBACK = ctypes.WINFUNCTYPE(None, ctypes.c_void_p)


class SERVICE_NOTIFY_2W(ctypes.Structure):
    _fields_ = [("dwVersion", ctypes.wintypes.DWORD),
                ("pfnNotifyCallback", PFN_SC_NOTIFY_CALLBACK),
                ("pContext", ctypes.c_void_p),
                ("dwNotificationStatus", ctypes.wintypes.DWORD),
                ("ServiceStatus", SERVICE_STATUS_PROCESS),
                ("dwNotificationTriggered", ctypes.wintypes.DWORD),
                ("pszServiceNames", ctypes.wintypes.LPWSTR)]


# Declare the signatures explicitly: SC_HANDLE is pointer-sized, and ctypes
# would otherwise assume c_int and truncate every handle on 64-bit Windows.
_advapi.OpenSCManagerW.restype = ctypes.c_void_p
_advapi.OpenSCManagerW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p,
                                   ctypes.wintypes.DWORD]
_advapi.OpenServiceW.restype = ctypes.c_void_p
_advapi.OpenServiceW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                 ctypes.wintypes.DWORD]
_advapi.CloseServiceHandle.restype = ctypes.wintypes.BOOL
_advapi.CloseServiceHandle.argtypes = [ctypes.c_void_p]
_advapi.NotifyServiceStatusChangeW.restype = ctypes.wintypes.DWORD
_advapi.NotifyServiceStatusChangeW.argtypes = [ctypes.c_void_p,
                                               ctypes.wintypes.DWORD,
                                               ctypes.POINTER(SERVICE_NOTIFY_2W)]
_kernel.SleepEx.restype = ctypes.wintypes.DWORD
_kernel.SleepEx.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL]


class _Watch:
    """One service's registration. Keeps the buffer and callback alive — the
    SCM writes into the buffer when the notification fires."""

    def __init__(self, name, handle):
        self.name = name
        self.handle = handle
        self.armed = False
        self.buf = SERVICE_NOTIFY_2W()
        self.cb = PFN_SC_NOTIFY_CALLBACK(self._fired)
        self.hits = []          # states seen, drained by the loop

    def _fired(self, _param):
        # Runs as an APC on the watcher thread. Record only; the loop re-arms.
        self.armed = False
        try:
            if self.buf.dwNotificationStatus == 0:
                s = self.buf.ServiceStatus
                # The exit code is what separates a crash from a deliberate
                # stop, so it has to travel with the status.
                self.hits.append((_STATE.get(s.dwCurrentState),
                                  int(s.dwWin32ExitCode), int(s.dwProcessId)))
        except Exception:
            pass

    def arm(self) -> bool:
        self.buf.dwVersion = SERVICE_NOTIFY_STATUS_CHANGE
        self.buf.pfnNotifyCallback = self.cb
        self.buf.pContext = None
        self.buf.dwNotificationStatus = 0
        rc = _advapi.NotifyServiceStatusChangeW(self.handle, _MASK,
                                               ctypes.byref(self.buf))
        self.armed = (rc == 0)
        return self.armed, rc

    def close(self):
        try:
            _advapi.CloseServiceHandle(self.handle)
        except Exception:
            pass


class Watcher:
    """Watches the configured services and reports state changes immediately."""

    def __init__(self, get_names, on_change, resync=5.0, safety_query=None):
        """get_names: () -> list of service short names to watch.
        on_change: (name, status) called on every observed state.
        safety_query: optional (name) -> status, used for a slow sanity sweep."""
        self._get_names = get_names
        self._on_change = on_change
        self._resync = resync
        self._safety_query = safety_query
        self._scm = None
        self._watches = {}
        self._last = {}          # name -> last reported status, for dedup
        self._stop = threading.Event()
        self._thread = None

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    @property
    def alive(self):
        return bool(self._thread and self._thread.is_alive())

    # -- internals ---------------------------------------------------------
    def _open_scm(self):
        if self._scm is None:
            self._scm = _advapi.OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
        return self._scm

    def _sync(self):
        """Match registrations to the configured service list."""
        wanted = set(self._get_names())
        for name in list(self._watches):
            if name not in wanted:
                self._watches.pop(name).close()
                self._last.pop(name, None)
        scm = self._open_scm()
        if not scm:
            return
        for name in wanted:
            if name in self._watches:
                continue
            h = _advapi.OpenServiceW(scm, name, SERVICE_QUERY_STATUS)
            if h:
                self._watches[name] = _Watch(name, h)

    def _arm_all(self):
        for name, w in list(self._watches.items()):
            if w.armed:
                continue
            ok, rc = w.arm()
            if not ok and rc == ERROR_SERVICE_NOTIFY_CLIENT_LAGGING:
                # Docs: reopen the handle and register again.
                w.close()
                self._watches.pop(name, None)

    def _report(self, name, status, exit_code=0, pid=0):
        """Report a state, skipping repeats. While a service is start/stop
        pending the SCM also notifies on checkpoint progress, which repeats the
        same state several times — the UI only cares about actual changes."""
        if not status or self._last.get(name) == status:
            return
        self._last[name] = status
        try:
            self._on_change(name, status, exit_code, pid)
        except Exception:
            pass

    def _drain(self):
        for w in list(self._watches.values()):
            while w.hits:
                status, exit_code, pid = w.hits.pop(0)
                self._report(w.name, status, exit_code, pid)

    def _loop(self):
        last_sync = 0.0
        last_safety = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_sync >= self._resync:
                self._sync()
                last_sync = now
            self._arm_all()

            # Alertable wait: this is when the SCM's APCs get delivered.
            _kernel.SleepEx(400, True)
            self._drain()

            # Slow belt-and-braces sweep in case a registration was lost.
            if self._safety_query and (time.monotonic() - last_safety) >= 20.0:
                last_safety = time.monotonic()
                for name in list(self._watches):
                    try:
                        self._report(name, self._safety_query(name))
                    except Exception:
                        continue

        for w in self._watches.values():
            w.close()
        self._watches.clear()
        if self._scm:
            _advapi.CloseServiceHandle(self._scm)
            self._scm = None
