"""What "a service" means, independently of how we reach it.

The app manages Windows services through the SCM and Linux services through
`systemctl` over SSH, and one day through an agent. Those are transports. Above
this line nothing knows which one it is talking to: the watchdog, the health
monitor, the stack runner and the scheduler all speak the same seven verbs, so
supporting another kind of target is writing a transport, not editing them.

The vocabulary is deliberately the smaller of the two worlds. Anything that only
one platform has — a Windows start type, a systemd `SubState` — is carried as a
plain string and shown, never branched on up here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from . import state as st


@dataclass(frozen=True)
class Status:
    """Where a service is right now.

    `state` is the shared vocabulary (`core/state.py`): Running, Stopped,
    Starting, Stopping, Paused, Not found, Unknown. Everything else is detail a
    particular platform happens to know.
    """

    state: str = st.UNKNOWN
    #: The platform's own word for it — systemd's "running"/"exited", or "".
    sub_state: str = ""
    #: "Automatic" / "Manual" / "Disabled" on Windows, "enabled" / "disabled" /
    #: "static" on systemd. Shown as-is; only "Disabled" is acted on.
    start_type: str = ""
    pid: int = 0
    exit_code: int = 0
    #: False when the target has the service listed but cannot load it — a broken
    #: or masked systemd unit, say. Not the same as Stopped: there is nothing to
    #: start, so the watchdog must leave it alone rather than fight it.
    installed: bool = True
    #: Why, when something above wants to explain itself.
    detail: str = ""


@dataclass(frozen=True)
class ServiceInfo:
    """One entry in the "which service?" picker."""

    name: str                       # what we address it by
    display: str = ""               # what a person calls it
    status: str = st.UNKNOWN
    start_type: str = ""
    installed: bool = True


@dataclass(frozen=True)
class Abilities:
    """What this transport can actually do on this target, so the UI can disable
    what it cannot rather than failing when a button is pressed.

    A Linux box reached with an account that has no sudo is a perfectly good
    monitoring target: statuses read fine, control does not. That is a state to
    show, not an error to raise — the same rule that already greys Kill for a
    remote Windows service.
    """

    control: bool = True            # start / stop / restart
    kill: bool = False              # terminate the process outright
    logs: bool = False              # the target's own log for one service
    push: bool = False              # changes arrive; otherwise we poll
    why: str = ""                   # what to tell the user when something is off


class Connector(Protocol):
    """One way of reaching services. Implementations live next to their transport
    (`scm_windows.py`, and `ssh_linux.py` when it lands)."""

    def abilities(self) -> Abilities: ...

    def reachable(self) -> bool: ...

    def list_services(self) -> list[ServiceInfo]: ...

    def status(self, name: str) -> Status: ...

    def start(self, name: str) -> None: ...

    def stop(self, name: str) -> None: ...

    def restart(self, name: str) -> None: ...

    def kill(self, name: str) -> int: ...

    def logs(self, name: str, lines: int = 50) -> list[str]: ...

    def run(self, command: str, timeout: float = 10.0) -> tuple[int, str]:
        """A command on the target. What a "command" health check needs, and what
        a service controlled by commands of its own needs."""

    def stat(self, path: str) -> tuple[bool, float]:
        """(exists, seconds since it was written) — what a "file" health check
        asks. On the target, not here: the file is over there."""


#: machine name -> the connector that reaches it. Cached because a transport may
#: hold a connection; cleared when a machine's settings change.
_cache: dict = {}


def for_machine(machine: str = "", config=None) -> Connector:
    """The connector for a target.

    Today every target is Windows, so this always answers the same way. It exists
    now, before there is a choice to make, because the alternative is call sites
    that assume the SCM — and those are what make a second platform expensive.
    """
    key = machine or ""
    found = _cache.get(key)
    if found is None:
        from . import scm_windows
        found = scm_windows.WindowsConnector(key)
        _cache[key] = found
    return found


def forget(machine: str = None) -> None:
    """Drop cached connectors, so edited machine settings take effect."""
    if machine is None:
        _cache.clear()
    else:
        _cache.pop(machine or "", None)
