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
#: How to find out what a machine *is*. Set once at startup. Without it every
#: target is Windows, which is what the tests that predate this rely on and what
#: an install with no remote machines wants anyway.
_config_getter = None


def use_config(getter) -> None:
    """Tell the registry where the machine records live.

    A getter rather than a Config, because the panel edits a copy and saves it:
    the transport for a machine has to come from what is saved now, not from
    whatever was loaded at startup.
    """
    global _config_getter
    _config_getter = getter
    forget()


def machine_record(name: str = ""):
    """The Machine for this name, or None if we have no config to ask."""
    if _config_getter is None:
        return None
    try:
        return _config_getter().machine(name or "")
    except Exception:
        return None


def for_machine(machine: str = "", record=None) -> Connector:
    """The connector for a target, by transport.

    This is the only place that decides how a machine is reached. Everything else
    — the watchdog, the health monitor, the stack runner, four pages — asks
    `control.py` and never learns the answer.
    """
    key = machine or ""
    found = _cache.get(key)
    if found is not None:
        return found

    record = record or machine_record(key)
    if record is not None and getattr(record, "kind", "windows") == "linux" and key:
        from . import ssh_linux
        found = ssh_linux.LinuxConnector(record)
    else:
        from . import scm_windows
        found = scm_windows.WindowsConnector(key)
    _cache[key] = found
    return found


def forget(machine: str = None) -> None:
    """Drop cached connectors, so edited machine settings take effect.

    Closes anything holding a connection first — an SSH session left open to a
    machine the user just repointed would keep answering for the old one.
    """
    names = [machine or ""] if machine is not None else list(_cache)
    for name in names:
        conn = _cache.pop(name, None)
        closer = getattr(getattr(conn, "_run", None), "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass
