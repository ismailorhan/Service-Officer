"""Signing in to another Windows machine as a named account.

None of the SCM calls take credentials. The RPC leaves as whoever this process is,
which is why a remote Windows machine only worked when the person at the keyboard
happened to be an administrator on it. The way to change that on Windows is to
establish a session to the target *first* — `net use \\HOST\\IPC$ /user:DOMAIN\\who`,
in program form — after which every later call to that machine rides on it.

Two consequences worth knowing:

* The session belongs to this logon session, not to a thread. One is enough for the
  poller, the action threads and the watcher together, which is why this is used
  instead of LogonUser + ImpersonateLoggedOnUser: that is per-thread, and would have
  to be repeated on every thread that ever touches the SCM.
* Windows allows only one set of credentials per target at a time. A session someone
  else already made — a mapped drive, an Explorer window — is a conflict, not
  something to work around silently, so it is reported.

`IPC$` rather than a real share: it is the named-pipe root every Windows machine
has, it is what the SCM's RPC travels over anyway, and it needs no share to exist.
"""

from __future__ import annotations

import threading

import pywintypes
import win32netcon
import win32wnet

from . import applog

log = applog.get("winsession")

#: host (lower case) -> the account we signed in as
_open: dict = {}
_lock = threading.RLock()

#: Win32 errors worth a sentence of their own. Anything else is passed through with
#: its own text, which is more use than a number we guessed the meaning of.
_REASONS = {
    5:    "access denied — the account exists but may not administer that machine",
    51:   "the network path was not found — is the machine on and reachable?",
    53:   "the machine could not be found on the network",
    64:   "the machine refused the connection (SMB may be blocked)",
    67:   "the network name could not be found",
    86:   "the password is not right for that account",
    1219: ("Windows already has a session to that machine as a different account. "
           "Disconnect it first (net use \\\\HOST\\IPC$ /delete) — Windows allows "
           "only one account per machine at a time."),
    1231: "the network is unreachable from here",
    1326: "the user name or password is wrong",
    1327: "that account is not allowed to sign in (blank password, or restricted)",
    1385: "that account is denied network logon on the target machine",
    1909: "that account is locked out",
}


def _target(host: str) -> str:
    return rf"\\{host}\IPC$"


def reason(exc) -> str:
    """A Win32 error as a sentence. Falls back to what Windows said."""
    code = getattr(exc, "winerror", None)
    known = _REASONS.get(code)
    if known:
        return known
    text = (getattr(exc, "strerror", "") or str(exc)).strip()
    return f"{text} (error {code})" if code else text


def ensure(host: str, user: str, password: str) -> None:
    """Make sure this machine can talk to `host` as `user`. Idempotent.

    Raises RuntimeError with something a person can act on. Does nothing when the
    same account is already signed in, so it is cheap to call before every command.
    """
    if not host or not user:
        raise RuntimeError("a user name is needed to sign in to that machine")
    key = host.lower()
    with _lock:
        if _open.get(key) == user:
            return
        if key in _open:
            # Signed in as somebody else: that has to go first, or Windows answers
            # 1219 and the new credentials are simply ignored.
            _cancel(host)
        try:
            win32wnet.WNetAddConnection2(win32netcon.RESOURCETYPE_ANY, None,
                                         _target(host), None, user,
                                         password or "")
        except pywintypes.error as exc:
            raise RuntimeError(reason(exc)) from exc
        _open[key] = user
        log.info("signed in to %s as %s", host, user)


def _cancel(host: str) -> None:
    """Drop our session, ignoring "there wasn't one"."""
    try:
        win32wnet.WNetCancelConnection2(_target(host), 0, True)
    except pywintypes.error as exc:
        if getattr(exc, "winerror", None) not in (2250, 1219):
            log.info("could not disconnect from %s: %s", host, reason(exc))
    _open.pop(host.lower(), None)


def forget(host: str = None) -> None:
    """Sign out of one machine, or all of them. Called when settings change, so a
    session left open as the old account cannot answer for the new one."""
    with _lock:
        for name in ([host] if host else list(_open)):
            _cancel(name)
