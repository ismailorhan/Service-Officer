"""What the hub and its clients say to each other.

Pure translation: dicts in, dicts out, no sockets and no state. Kept apart from the
server so the format can be tested without one, and so the browser UI has a single
file to read rather than a protocol to infer from handler code.

`PROTOCOL` is bumped when a field changes *meaning* — never when one is added. A client
checks it and says "this hub is newer than I am" instead of misreading a field it half
recognises.

Two rules that are not obvious:

* **Nothing but plain JSON types goes out.** No dataclasses, no tuples, no sets. Every
  builder here is checked by a round trip through `json.dumps` in the tests, because a
  field that only fails to serialise under a real client is a field that fails in
  production.
* **A secret never appears.** `services.json` holds the *name* of a secret store entry
  and the config payload carries that name; the value stays on the hub, where the
  transport that needs it lives.
"""

from __future__ import annotations

import hashlib
import json
import time

from . import config as cfg_mod
from . import state as st
from . import version

#: The wire's own version. See the note above about what bumps it.
PROTOCOL = 1


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------
def service_row(svc, store) -> dict:
    """One service as a row a client can draw without asking anything else.

    `state_label` and `state_category` are `st.effective()` already applied — what to
    write and which colour to use, once health is taken into account. They are on the
    wire rather than left to each client because that function exists for a reason
    recorded in its own docstring: three surfaces once worked it out separately and
    disagreed with each other about the same service at the same moment. A browser
    reading this API would have been the fourth.
    """
    machine = svc.machine or ""
    health = store.health_of(svc.name, machine)
    status = store.status_of(svc.name, machine)
    state_label, state_category = st.effective(status, health)
    return {
        "name": svc.name,
        "machine": machine,
        "label": svc.display(),
        "category": svc.category,
        "status": status,
        "state_label": state_label,
        "state_category": state_category,
        "start_type": store.start_type(svc.name, machine),
        "disabled": store.is_disabled(svc.name, machine),
        "health": health,
        "health_detail": store.health_detail(svc.name, machine),
        "watched": bool(svc.health.active),
    }


def machine_row(machine, store) -> dict:
    """One machine, including whether it is answering.

    `reachable` is None when nothing has asked it yet, which is a state of its own:
    "not asked" and "asked and silent" have different fixes, and conflating them cost
    an evening once.
    """
    known = store.machine_state(machine.name)
    return {
        "name": machine.name,
        "label": machine.display(),
        "kind": machine.kind,
        "address": machine.address,
        "auth": machine.auth,
        "username": machine.username,
        "poll_seconds": machine.poll_seconds,
        "reachable": bool(known.get("reachable")) if known else None,
        "detail": known.get("detail", ""),
        "at": known.get("wall", 0),
    }


# ---------------------------------------------------------------------------
# the config, and its version stamp
# ---------------------------------------------------------------------------
def machine_event(machine: str, reachable: bool, detail: str = "") -> dict:
    """A machine started or stopped answering.

    Its own event kind rather than a field on a status event, because it is not about a
    service: every service on a silent machine reads Unknown, and "the machine is not
    answering" is the one sentence that explains all of them at once.

    `at` is the wall clock, not `time.monotonic()` — one machine's monotonic clock means
    nothing on another, which is the same reason a status event carries none.
    """
    return {"kind": "machine", "machine": machine or "",
            "reachable": bool(reachable), "detail": detail or "",
            "at": time.time()}


def health_event(service: str, machine: str, verdict: str, detail: str = "") -> dict:
    """A service's health checks started or stopped passing.

    Its own kind, not a status change: the service manager still says Running either way. What
    changes is `st.effective()` — the words on the chip, its colour, and the tray icon.
    """
    return {"kind": "health", "service": service or "", "machine": machine or "",
            "verdict": verdict or "unknown", "detail": detail or "", "at": time.time()}


def gap_event(missed: int) -> dict:
    """Events were dropped for this client, so what it holds is no longer reliable.

    The one honest thing to send: it cannot be told *which* it missed, and guessing would be
    worse than saying so. The client reads a fresh snapshot.
    """
    return {"kind": "gap", "missed": int(missed), "at": time.time()}


def config_event(actor: str = "", etag: str = "") -> dict:
    """The landscape was edited. Carries no config: it is large, every client holds a copy
    already, and one that wants the new one asks — which also gets it the etag it will need
    to save against. A notification, not a delivery.
    """
    return {"kind": "config", "actor": actor or "", "etag": etag or "",
            "at": time.time()}


def action_event(service: str, machine: str, action: str, error: str = "",
                 status: str = "", actor: str = "") -> dict:
    """An action finished — well or badly.

    Its own kind, because it is not a status change: an action that is refused changes no
    status at all, and that is exactly the case a client used to hear nothing about.

    `bulk` is deliberately absent. It means "part of a batch *this* panel confirmed", which is
    true of the panel that started it and of no other.
    """
    return {"kind": "action", "service": service or "", "machine": machine or "",
            "action": action or "", "error": error or "", "status": status or "",
            "actor": actor or "", "at": time.time()}


def normalised(cfg) -> dict:
    """The config as it will be once stored and loaded again.

    `from_dict` does not only parse, it repairs: a service with no label gets its name,
    a category a service refers to is created if it is missing. So `to_dict` and
    `from_dict` are not inverses, and hashing the raw document made a config that had
    merely been *sent* look different from the same config *received* — a client
    handing back exactly what it was given would have been told it had a conflict.

    Hashing the repaired form is also the honest thing to identify: what two clients
    are racing over is the config that ends up on disk, not the spelling of the
    request.
    """
    return cfg_mod.to_dict(cfg_mod.from_dict(cfg_mod.to_dict(cfg)))


def etag(cfg) -> str:
    """A short hash of the config as it would be saved.

    Two clients editing at once is the case this exists for: the second save is
    refused rather than silently winning, which is how a machine somebody added
    disappears. Key order cannot matter (`sort_keys`), but the *order of services*
    must, because that order is what the flyout shows.
    """
    raw = json.dumps(normalised(cfg), sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def config_payload(cfg) -> dict:
    return {"protocol": PROTOCOL, "etag": etag(cfg), "config": cfg_mod.to_dict(cfg)}


def config_from_payload(payload: dict):
    """(Config, etag). The etag is the sender's; the receiver compares it with its
    own before applying anything."""
    cfg = cfg_mod.from_dict((payload or {}).get("config") or {})
    return cfg, str((payload or {}).get("etag") or "")


# ---------------------------------------------------------------------------
# the snapshot
# ---------------------------------------------------------------------------
def snapshot(engine) -> dict:
    """Everything a client needs to draw its first frame, in one answer.

    One request rather than a request per page: a client that opened with six calls
    would show six different moments, and the panel's own lists have to agree with
    each other more than they have to be fresh.
    """
    cfg = engine.config()
    store = engine.store
    return {
        "protocol": PROTOCOL,
        "version": version.short(),
        "at": time.time(),
        "config_etag": etag(cfg),
        "services": [service_row(s, store) for s in cfg.services],
        "machines": [machine_row(m, store) for m in cfg.machines],
        "stacks": [{"name": s.name, "steps": len(s.steps)} for s in cfg.stacks],
    }


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------
def event(kind: str, **facts) -> dict:
    """One thing that happened. `kind` is what a client switches on."""
    return {"protocol": PROTOCOL, "kind": kind, "at": time.time(), **facts}


def event_from_state(state_event) -> dict:
    """An `st.Event` as a wire event.

    Everything the local store's subscribers use has to survive, because the client
    rebuilds an `st.Event` from this and hands it to the same handlers — `previous`
    included, since "it is stopped" and "it has just stopped" are different rows in
    the history and different notifications on screen.
    """
    return event("status",
                 service=state_event.name,
                 machine=state_event.state.machine,
                 status=state_event.status,
                 previous=state_event.previous,
                 exit_code=state_event.state.exit_code,
                 pid=state_event.state.pid,
                 source=state_event.source)


def state_from_event(raw: dict):
    """The other direction: a wire event back into an `st.Event`.

    `since` is deliberately not carried. It is a monotonic clock reading, and one
    machine's monotonic clock means nothing on another — the client's own arrival
    time is the honest answer, which is what ServiceState's default gives it.
    """
    state = st.ServiceState(name=raw.get("service", ""),
                            machine=raw.get("machine", "") or "",
                            status=raw.get("status", st.UNKNOWN),
                            exit_code=int(raw.get("exit_code") or 0),
                            pid=int(raw.get("pid") or 0))
    return st.Event(state=state, previous=raw.get("previous"),
                    source=raw.get("source", st.SRC_SCM))
