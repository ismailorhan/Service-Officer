"""Starting a window on somebody's desktop from a process that has no desktop.

A Windows service runs in session 0, which has no visible desktop. Anything it starts is
started there too, so `CreateProcess` from the hub produces a tray application nobody can see —
running, invisible, and holding the single-instance mutex so the real one cannot start either.

That is not hypothetical: an automatic update kills the tray application to replace its files
and then has to put it back. Measured on the first one, 2.2.9: the update worked, the service
came back, and the tray icon was simply gone until somebody signed in again.

The documented way across is `WTSQueryUserToken` for the console session and
`CreateProcessAsUser` with that token. It needs SYSTEM, which a service has and an elevated
installer does not — so there are two paths here, and which one applies is asked rather than
assumed.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import subprocess

from . import applog

log = applog.get("session")

_kernel = ctypes.windll.kernel32
_wts = ctypes.windll.wtsapi32
_userenv = ctypes.windll.userenv

#: No console session at all — a server with nobody signed in. Not an error: there is simply
#: nobody to show a window to, which is a fact and not a failure.
NOBODY = 0xFFFFFFFF

CREATE_UNICODE_ENVIRONMENT = 0x00000400
DETACHED_PROCESS = 0x00000008
#: The interactive window station and its default desktop. Without this the process starts on
#: no desktop at all and a Qt application exits on the spot.
DESKTOP = "winsta0\\default"


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


def console_session() -> int:
    """The session somebody is actually sitting at, or NOBODY."""
    return int(_wts.WTSGetActiveConsoleSessionId())


def _as_the_person(session: int, command: str, folder: str) -> int:
    """CreateProcessAsUser into `session`. Returns the pid, or 0 with a reason logged."""
    token = ctypes.wintypes.HANDLE()
    if not _wts.WTSQueryUserToken(ctypes.wintypes.DWORD(session),
                                  ctypes.byref(token)):
        # Access denied here means this process is not SYSTEM — an elevated installer run by a
        # person, for instance. The caller falls back, which is right: that person *has* a
        # desktop, so an ordinary launch reaches it.
        log.info("cannot borrow session %s: error %s", session,
                 ctypes.GetLastError())
        return 0
    environment = ctypes.c_void_p()
    made_environment = bool(_userenv.CreateEnvironmentBlock(
        ctypes.byref(environment), token, False))
    startup = _StartupInfo()
    startup.cb = ctypes.sizeof(startup)
    startup.lpDesktop = DESKTOP
    info = _ProcessInfo()
    try:
        started = _kernel.CreateProcessAsUserW(
            token, None, ctypes.create_unicode_buffer(command), None, None,
            False, CREATE_UNICODE_ENVIRONMENT | DETACHED_PROCESS,
            environment if made_environment else None,
            folder or None, ctypes.byref(startup), ctypes.byref(info))
        if not started:
            log.warning("could not start %s in session %s: error %s", command,
                        session, ctypes.GetLastError())
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


def start_for_the_person(exe: str, arguments=()) -> int:
    """Start `exe` where somebody can see it. Returns a pid, or 0 if nobody could.

    Tries the console session first, because the caller is usually a service. Falls back to an
    ordinary launch, because the caller is sometimes an installer a person is running — and
    then this process is already on that person's desktop.
    """
    if not os.path.isfile(exe):
        log.warning("%s is not there, so nothing was started", exe)
        return 0
    command = subprocess.list2cmdline([exe, *arguments])
    folder = os.path.dirname(exe)

    session = console_session()
    if session != NOBODY:
        pid = _as_the_person(session, command, folder)
        if pid:
            return pid
    else:
        # A server with nobody signed in. The Startup shortcut takes it from here, whenever
        # somebody does sign in, and there is nothing to do now.
        log.info("nobody is signed in, so no window was opened")
        return 0

    try:
        started = subprocess.Popen([exe, *arguments], cwd=folder,
                                   close_fds=True, creationflags=DETACHED_PROCESS)
    except OSError as exc:
        log.warning("could not start %s: %s", exe, exc)
        return 0
    log.info("started %s as pid %s", exe, started.pid)
    return started.pid
