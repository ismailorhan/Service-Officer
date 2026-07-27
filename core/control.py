"""Service control — query, start, stop, restart, enumerate.

The public face of every transport. Everything in the app calls these functions
and none of them knows what is on the other end: the Windows SCM today, a Linux
box over SSH next, an agent later. `core/connectors.py` defines the vocabulary and
picks the transport; `core/scm_windows.py` is the one that exists.

Every call still takes an optional machine, and an empty machine still means this
computer — the signatures did not change when the routing appeared underneath
them, which is the point of putting the seam here.
"""

from __future__ import annotations

import os
import socket
import threading

from . import connectors


def _for(machine: str = "", record=None):
    """The transport for a machine.

    `record` exists because the caller sometimes knows more than the registry
    does: the panel edits a *copy* of the config, so a machine just added there is
    not yet in what the registry can see — and the standalone panel has no registry
    wired at all. Without it, a Linux machine was reached through the Windows
    service manager and answered "the RPC server is unavailable".
    """
    return connectors.for_machine(machine, record)


def connector_for(machine: str = "", record=None):
    """The transport itself, for the two checks that need to reach past a status.

    A file's age and a command's exit code are questions about the machine rather
    than about a service, so there is no verb below to route them through — but they
    are still questions about *that* machine, and answering them here is how a
    heartbeat check on a Linux service ended up measuring a Windows path.

    Named rather than `_for` because health imports this deliberately: a caller
    reaching for the transport is doing something the seven verbs do not cover, and
    that should be visible at the call site.
    """
    return _for(machine, record)


# -- the seven verbs --------------------------------------------------------
def query_status(service_name: str, machine: str = "") -> str:
    return _for(machine).status(service_name).state


def status_of(service_name: str, machine: str = "") -> connectors.Status:
    """Everything the transport knows, for callers that want more than a word:
    the start type, the pid, whether the service is even installable."""
    return _for(machine).status(service_name)


def start_service(service_name: str, machine: str = "") -> None:
    _for(machine).start(service_name)


def stop_service(service_name: str, machine: str = "") -> None:
    _for(machine).stop(service_name)


def restart_service(service_name: str, machine: str = "") -> None:
    _for(machine).restart(service_name)


def list_all_services(machine: str = "", record=None) -> list:
    """Every service on that machine as {"name", "display", "status"}, sorted by
    display name — what the picker offers. A dict, not the dataclass, because
    that is the shape the picker has always been handed."""
    return [{"name": s.name, "display": s.display, "status": s.status}
            for s in _for(machine, record).list_services()]


def start_type(service_name: str, machine: str = "") -> str:
    """Automatic / Manual / Disabled …

    Worth knowing because a disabled service cannot be started at all — Windows
    refuses with "the service cannot be started because it is disabled", and
    offering a Start button for it is a lie. systemd's `disabled` means the same
    thing for the same reason.
    """
    return _for(machine).status(service_name).start_type


def process_id(service_name: str, machine: str = "") -> int:
    """The service's process, or 0 if it isn't running."""
    return _for(machine).status(service_name).pid


def kill_process(service_name: str, machine: str = "") -> int:
    """Terminate the service's process outright. Returns the pid killed.

    For when a service is wedged and Stop does nothing: the SCM keeps reporting
    "Stopping" for ever because the process never acknowledges the control
    request. Terminating it is abrupt by definition — the service gets no chance
    to flush anything — so the UI asks before calling this.
    """
    return _for(machine).kill(service_name)


def abilities(machine: str = "", record=None):
    """What can actually be done to this target. The UI disables what cannot,
    rather than offering a button that fails."""
    return _for(machine, record).abilities()


def reachable(machine: str, record=None) -> bool:
    """Can we talk to this machine at all? Used to show a machine as offline
    instead of every service on it as 'Not Found'."""
    return _for(machine, record).reachable()


def nothing_to_do(exc) -> str:
    """A plain reason if this exception only means "no change needed", else "".

    Stopping a stopped service raises "The service has not been started", which
    is not a problem and must not be reported as one. Transport-specific by
    nature: `systemctl start` on a started unit simply exits 0, so there is
    nothing to translate there.
    """
    from . import scm_windows
    return scm_windows.nothing_to_do(exc)


# -- naming and addresses ---------------------------------------------------
# Not transport-specific: a machine's name and address are the same questions
# whatever runs on it.
def host_name() -> str:
    """This computer's name as Windows knows it, so the local machine can be
    named rather than called "this computer"."""
    return (os.environ.get("COMPUTERNAME") or socket.gethostname() or "").strip()


_addresses: dict = {}
_resolving: set = set()


def cached_address(machine: str = ""):
    """The address if we already know it; None if it has never been looked up.
    Painting a list must not wait on DNS — a name that doesn't resolve costs
    three seconds, measured."""
    return _addresses.get(machine or "")


def resolve_address(machine: str = "", done=None) -> None:
    """Look an address up on a worker thread, calling done(machine, address)
    when it lands. Repeat calls while one is in flight are ignored."""
    key = machine or ""
    if key in _addresses:
        if done:
            done(key, _addresses[key])
        return
    if key in _resolving:
        return
    _resolving.add(key)

    def work():
        try:
            found = address_of(machine)
        finally:
            _resolving.discard(key)
        if done:
            done(key, found)

    threading.Thread(target=work, daemon=True).start()


def address_of(machine: str = "") -> str:
    """Best-effort IP address for a machine, empty when it can't be resolved.

    Blocking — call resolve_address() from anything that paints.
    """
    key = machine or ""
    if key in _addresses:
        return _addresses[key]
    target = machine or socket.gethostname()
    found = ""
    try:
        # Prefer a routable v4 address; a machine usually has several.
        infos = socket.getaddrinfo(target, None, socket.AF_INET)
        found = infos[0][4][0] if infos else ""
    except (socket.gaierror, OSError, IndexError):
        found = ""
    if found.startswith("127.") and not machine:
        # A hosts-file entry pointing at loopback tells the user nothing; ask
        # the routing table which address this computer actually uses.
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.settimeout(0.2)
            probe.connect(("10.255.255.255", 1))       # never sends a packet
            found = probe.getsockname()[0]
            probe.close()
        except OSError:
            pass
    _addresses[key] = found
    return found
