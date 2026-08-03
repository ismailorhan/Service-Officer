"""Starting a window on somebody's desktop from a process that has no desktop.

A Windows service runs in session 0, which has no visible desktop. Anything it starts is started
there too, so `CreateProcess` from the hub produces a tray application nobody can see — running,
invisible, and holding that session's single-instance mutex.

That is not hypothetical: an automatic update kills the tray application to replace its files and
has to put it back. Three attempts, each wrong in its own way, and each only visible on a real
machine:

* **2.2.10** used the wrong DLL for two calls. `ctypes.windll.x` loads happily and fails only
  when a missing name is *used*, so it was an AttributeError at the call, 130ms and exit 1 with
  nothing logged.
* **2.2.12** asked `WTSGetActiveConsoleSessionId`, which names the *physical* console. Measured
  on the machine it failed on:

      session   0  Services     Disconnected
      session   1  RDP-Tcp#0    Active        <- the person
      session   2  Console      Connected     <- nobody signed in
      WTSGetActiveConsoleSessionId -> 2

  So `WTSQueryUserToken(2)` correctly answered ERROR_NO_TOKEN. On a server the operator is
  almost always on RDP, which makes the console session the wrong answer nearly always rather
  than in an edge case.
* **2.2.12** also fell back to an ordinary `CreateProcess` when that failed — from a service,
  which is precisely how the invisible process this module exists to prevent gets made. It did
  exactly that: a tray application running in session 0 that nobody could see.

So: ask which sessions somebody is actually *in*, and never fall back to an ordinary launch from
a process that has no desktop of its own.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import subprocess

from . import applog

log = applog.get("session")

# `use_last_error=True`, so `ctypes.get_last_error()` reads the error *this* call set. Without
# it, `ctypes.GetLastError()` reads whatever the thread last saw — which ctypes' own machinery
# may have overwritten — and a diagnosis built on that is a diagnosis of nothing.
_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi = ctypes.WinDLL("advapi32", use_last_error=True)
_wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
_userenv = ctypes.WinDLL("userenv", use_last_error=True)

#: WTS_CONNECTSTATE_CLASS. Only `Active` means a person is signed in and looking at something:
#: `Connected` is a session that exists with nobody in it, which is what the physical console of
#: a server looks like all day.
WTS_ACTIVE = 0
#: Session 0 — services. It has no visible desktop, and a process there can only hand work to a
#: session that does.
SERVICES_SESSION = 0

CREATE_UNICODE_ENVIRONMENT = 0x00000400
DETACHED_PROCESS = 0x00000008
#: The interactive window station and its default desktop. Without this the process starts on no
#: desktop at all and a Qt application exits on the spot.
DESKTOP = "winsta0\\default"


class _SessionInfo(ctypes.Structure):
    _fields_ = [("SessionId", ctypes.wintypes.DWORD),
                ("pWinStationName", ctypes.wintypes.LPWSTR),
                ("State", ctypes.c_int)]


class _StartupInfo(ctypes.Structure):
    _fields_ = [("cb", ctypes.wintypes.DWORD),
                ("lpReserved", ctypes.wintypes.LPWSTR),
                ("lpDesktop", ctypes.wintypes.LPWSTR),
                ("lpTitle", ctypes.wintypes.LPWSTR),
                ("dwX", ctypes.wintypes.DWORD),
                ("dwY", ctypes.wintypes.DWORD),
                ("dwXSize", ctypes.wintypes.DWORD),
                ("dwYSize", ctypes.wintypes.DWORD),
                ("dwXCountChars", ctypes.wintypes.DWORD),
                ("dwYCountChars", ctypes.wintypes.DWORD),
                ("dwFillAttribute", ctypes.wintypes.DWORD),
                ("dwFlags", ctypes.wintypes.DWORD),
                ("wShowWindow", ctypes.wintypes.WORD),
                ("cbReserved2", ctypes.wintypes.WORD),
                ("lpReserved2", ctypes.POINTER(ctypes.wintypes.BYTE)),
                ("hStdInput", ctypes.wintypes.HANDLE),
                ("hStdOutput", ctypes.wintypes.HANDLE),
                ("hStdError", ctypes.wintypes.HANDLE)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [("hProcess", ctypes.wintypes.HANDLE),
                ("hThread", ctypes.wintypes.HANDLE),
                ("dwProcessId", ctypes.wintypes.DWORD),
                ("dwThreadId", ctypes.wintypes.DWORD)]


_wts.WTSEnumerateSessionsW.argtypes = [
    ctypes.c_void_p, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD,
    ctypes.POINTER(ctypes.POINTER(_SessionInfo)),
    ctypes.POINTER(ctypes.wintypes.DWORD)]
_wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]
_kernel.ProcessIdToSessionId.argtypes = [ctypes.wintypes.DWORD,
                                         ctypes.POINTER(ctypes.wintypes.DWORD)]


def my_session() -> int:
    """Which session this process is in. Zero means services — no desktop of its own."""
    answer = ctypes.wintypes.DWORD()
    if not _kernel.ProcessIdToSessionId(_kernel.GetCurrentProcessId(),
                                        ctypes.byref(answer)):
        # Unknowable. Treated as session 0, which is the cautious reading: it only ever stops
        # this from launching something invisible.
        log.info("could not ask which session this process is in: error %s",
                 ctypes.get_last_error())
        return SERVICES_SESSION
    return int(answer.value)


def active_sessions() -> list:
    """The sessions somebody is signed into and looking at, in order.

    Enumerated rather than asking for "the console": on a server the operator is on RDP, and the
    console session sits there `Connected` with nobody in it. That was the whole of 2.2.12's
    failure — see the module docstring for the measurement.
    """
    array = ctypes.POINTER(_SessionInfo)()
    count = ctypes.wintypes.DWORD()
    if not _wts.WTSEnumerateSessionsW(None, 0, 1, ctypes.byref(array),
                                      ctypes.byref(count)):
        log.warning("could not list the sessions: error %s", ctypes.get_last_error())
        return []
    try:
        found = [int(array[i].SessionId) for i in range(count.value)
                 if array[i].State == WTS_ACTIVE
                 and int(array[i].SessionId) != SERVICES_SESSION]
    finally:
        _wts.WTSFreeMemory(array)
    return found


def _as_the_person(session: int, command: str, folder: str) -> int:
    """CreateProcessAsUser into `session`. Returns the pid, or 0 with a reason logged."""
    token = ctypes.wintypes.HANDLE()
    if not _wts.WTSQueryUserToken(ctypes.wintypes.DWORD(session),
                                  ctypes.byref(token)):
        # 1314 ERROR_PRIVILEGE_NOT_HELD or 5 ERROR_ACCESS_DENIED: this process is not SYSTEM.
        # 1008 ERROR_NO_TOKEN: nobody is signed into that session.
        log.info("cannot borrow session %s: error %s", session,
                 ctypes.get_last_error())
        return 0
    environment = ctypes.c_void_p()
    made_environment = bool(_userenv.CreateEnvironmentBlock(
        ctypes.byref(environment), token, False))
    startup = _StartupInfo()
    startup.cb = ctypes.sizeof(startup)
    startup.lpDesktop = DESKTOP
    info = _ProcessInfo()
    try:
        started = _advapi.CreateProcessAsUserW(
            token, None, ctypes.create_unicode_buffer(command), None, None,
            False, CREATE_UNICODE_ENVIRONMENT | DETACHED_PROCESS,
            environment if made_environment else None,
            folder or None, ctypes.byref(startup), ctypes.byref(info))
        if not started:
            log.warning("could not start %s in session %s: error %s", command,
                        session, ctypes.get_last_error())
            return 0
        log.info("started %s in session %s as pid %s", command, session,
                 info.dwProcessId)
        return int(info.dwProcessId)
    finally:
        if made_environment:
            _userenv.DestroyEnvironmentBlock(environment)
        for handle in (info.hProcess, info.hThread, token):
            if handle:
                _kernel.CloseHandle(handle)


def start_for_the_person(exe: str, arguments=()) -> list:
    """Start `exe` wherever somebody can see it. Returns the pids started.

    One per session with somebody signed into it, because that is what was taken away: the
    installer's `taskkill /F /IM`, run as SYSTEM, ends every copy on the machine.

    An ordinary launch is the fallback **only** from a process that has a desktop of its own.
    From a service it is not a fallback, it is the bug: a tray application in session 0, running
    and invisible. 2.2.12 did that, and the process was still there afterwards.
    """
    if not os.path.isfile(exe):
        log.warning("%s is not there, so nothing was started", exe)
        return []
    command = subprocess.list2cmdline([exe, *arguments])
    folder = os.path.dirname(exe)

    started = [pid for pid in
               (_as_the_person(session, command, folder)
                for session in active_sessions())
               if pid]
    if started:
        return started

    if my_session() == SERVICES_SESSION:
        # Nobody signed in, or no token to be had. The Startup shortcut takes it from here
        # whenever somebody does sign in — and an invisible copy in session 0 would stop even
        # that, by holding a mutex in a session nobody will ever look at.
        log.info("no session to open a window in, so nothing was started")
        return []

    try:
        process = subprocess.Popen([exe, *arguments], cwd=folder, close_fds=True,
                                   creationflags=DETACHED_PROCESS)
    except OSError as exc:
        log.warning("could not start %s: %s", exe, exc)
        return []
    log.info("started %s as pid %s, in this process's own session", exe, process.pid)
    return [process.pid]
