"""One tray app per session, and a way for the second launch to say so.

Six copies were running at once, because nothing stopped the seventh. A tray app that starts
again on every double-click is not a cosmetic problem:

* six tray icons, and no way to tell which one you are looking at;
* six engines, when the panel watches this computer itself — so six SCM watchers holding six
  handles to every service, six recovery timers racing to restart the same service, and six
  schedulers firing the same job;
* six clients on the hub's client list, all named after the same computer.

The guard is a named mutex rather than a lock file: the kernel owns it, so a process that is
killed or crashes releases it immediately and there is no stale file to reason about. That
matters here — a lock file left behind by a crash would keep the app from ever starting again,
which is a worse failure than the one being fixed.

Per *session*, not per machine. Two people signed into the same server each get their own tray
icon, because a tray icon belongs to a desktop; the unprefixed name puts the object in the
session's own namespace, which is exactly that rule. `Global\\` would have been one icon for
the whole box, shared by whoever logged in first.

The second launch is not silently dropped either. Somebody who double-clicks the icon is asking
to see the app, so it pokes the running one into showing its panel and exits — the alternative
is a double-click that appears to do nothing at all.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import threading

log = logging.getLogger(__name__)

_kernel = ctypes.windll.kernel32

_kernel.CreateMutexW.restype = ctypes.c_void_p
_kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.wintypes.BOOL,
                                 ctypes.c_wchar_p]
_kernel.CreateEventW.restype = ctypes.c_void_p
_kernel.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.wintypes.BOOL,
                                 ctypes.wintypes.BOOL, ctypes.c_wchar_p]
_kernel.OpenEventW.restype = ctypes.c_void_p
_kernel.OpenEventW.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL,
                               ctypes.c_wchar_p]
_kernel.SetEvent.argtypes = [ctypes.c_void_p]
_kernel.ResetEvent.argtypes = [ctypes.c_void_p]
_kernel.WaitForSingleObject.restype = ctypes.wintypes.DWORD
_kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.wintypes.DWORD]
_kernel.CloseHandle.argtypes = [ctypes.c_void_p]

#: The two names. No `Global\` prefix on purpose — see the module docstring.
MUTEX_NAME = "ServiceOfficer.Panel.OnePerSession"
EVENT_NAME = "ServiceOfficer.Panel.ShowYourself"

ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x0
WAIT_TIMEOUT = 0x102


class Claim:
    """The right to be the tray app in this session, held for as long as this lives.

    Kept in a variable that outlives startup: letting it be collected would release the mutex
    and the next launch would happily start a second copy.
    """

    def __init__(self, handle):
        self._handle = handle

    def release(self) -> None:
        if self._handle:
            _kernel.CloseHandle(self._handle)
            self._handle = None


def claim() -> Claim | None:
    """The claim if this process is the first, or None if somebody already has it."""
    handle = _kernel.CreateMutexW(None, False, MUTEX_NAME)
    err = ctypes.get_last_error() or ctypes.GetLastError()
    if handle and err != ERROR_ALREADY_EXISTS:
        return Claim(handle)
    if handle:
        _kernel.CloseHandle(handle)
    if not handle and err == ERROR_ACCESS_DENIED:
        # Somebody has it and this process may not even look at it — an elevated copy from the
        # installer's path, say. Access denied means it exists, so the answer is the same.
        log.info("another copy holds the single-instance mutex (access denied)")
        return None
    if not handle:
        # Something else went wrong. Better one too many tray icons than an app that refuses
        # to start because a kernel call failed for a reason nobody anticipated.
        log.warning("could not take the single-instance mutex (error %s) — starting anyway",
                    err)
        return Claim(None)
    return None


def poke() -> bool:
    """Ask the running copy to show its panel. False if there was nobody to ask."""
    handle = _kernel.OpenEventW(EVENT_MODIFY_STATE, False, EVENT_NAME)
    if not handle:
        return False
    try:
        return bool(_kernel.SetEvent(handle))
    finally:
        _kernel.CloseHandle(handle)


class Listener:
    """Waits to be asked to show the panel, until told to stop.

    Stoppable, even though the app itself never stops it: the wait holds the event's handle, so
    a listener with no way out means the name stays taken for the life of the process. That
    turned up as one test finding the previous test's listener and reporting somebody was
    there — the same shape as the real failure, one process along.
    """

    def __init__(self, handle, on_poke):
        self._handle = handle
        self._on_poke = on_poke
        self._stop = threading.Event()
        # A 500ms wait rather than an infinite one, so stopping does not need a second kernel
        # object to wake it. A double-click still lands immediately: the event fires the wait,
        # the timeout only bounds how long stopping takes.
        self.thread = threading.Thread(target=self._wait, daemon=True,
                                       name="show-yourself")
        self.thread.start()

    def _wait(self):
        while not self._stop.is_set():
            if _kernel.WaitForSingleObject(self._handle, 500) != WAIT_OBJECT_0:
                continue
            _kernel.ResetEvent(self._handle)
            if self._stop.is_set():
                break
            try:
                self._on_poke()
            except Exception:
                log.exception("failed to show the panel on request")
        _kernel.CloseHandle(self._handle)

    def stop(self) -> None:
        self._stop.set()
        self.thread.join(timeout=2.0)


def listen(on_poke) -> Listener | None:
    """Call `on_poke()` whenever another launch asks this copy to show itself.

    A kernel wait rather than a polled timer: this fires on a double-click, so it has to be
    immediate, and a wait that is asleep costs nothing between them.
    """
    handle = _kernel.CreateEventW(None, True, False, EVENT_NAME)   # manual reset
    if not handle:
        log.warning("could not create the show-yourself event (error %s)",
                    ctypes.GetLastError())
        return None
    return Listener(handle, on_poke)
