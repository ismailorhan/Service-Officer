"""This client's own settings — which are not the landscape's.

Once there is a hub, two kinds of setting exist and keeping them apart is the whole
point of this file:

| | Lives where | Examples |
|---|---|---|
| the landscape | the hub, one copy, shared | services, machines, stacks, triggers, health checks, retention |
| **this client** | the machine it runs on | which hub, its token, the pinned certificate, the theme, whether the tray starts with Windows, whether *this* screen shows notifications |

Mixing them means five people each believing they can change the theme for everyone, or
that history retention is somebody else's problem.

`client.json` is **not a second config**: it holds
nothing about any service, and losing it costs a re-pairing and nothing else. The token
is not in it — that goes in the DPAPI store with every other secret, because this file is
a document somebody may open and a bearer token in a text file is a password in a text
file.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields

from . import applog, config as cfg_mod
from . import secrets

log = applog.get("local")

#: This user's copy — the one that is written. A module-level default rather than a
#: call, so a test can point it somewhere else in one line.
PATH = os.path.join(cfg_mod.USER_DIR, "client.json")
#: The machine-wide copy, which is *read* when this user has none of their own. It is what
#: `ServiceOfficerHub.exe client pair --local` leaves behind at install time, and what a
#: deployment tool can drop next to services.json: a second person logging into the same
#: server finds the hub already configured instead of being asked to pair again.
#:
#: Two paths rather than one because the two have different owners. APP_DIR belongs to the
#: machine and, with a hub installed, to a LocalSystem service — readable by everyone,
#: writable only by administrators — and the tray application no longer runs elevated.
MACHINE_PATH = os.path.join(cfg_mod.APP_DIR, "client.json")


@dataclass
class Settings:
    """Everything about this installation that is nobody else's business."""
    #: "" means this client runs the engine itself — which is what everybody has today,
    #: and what a single-machine install keeps.
    hub_url: str = ""
    #: The hub's certificate, pinned on the first connection. A change after that is
    #: refused: the same rule as an SSH host key, for the same reason.
    hub_fingerprint: str = ""
    theme: str = "system"
    auto_start: bool = True
    #: Whether *this* screen shows notifications. Five clients all announcing the same
    #: crash is five toasts for one event, and it should be each person's choice.
    notify: bool = True


def _token_ref(url: str) -> str:
    """One entry per hub. Somebody with a test hub and a real one must not have the
    two overwrite each other."""
    return f"hub-token/{(url or '').rstrip('/')}"


def load() -> Settings:
    """The settings, or defaults.

    This user's file first, then the machine-wide one: a fresh user on a machine that was
    installed with a hub inherits the pairing rather than being asked for a token nobody
    has. Once they change anything it is theirs (see `save`), and the machine copy stops
    mattering to them.

    A missing file is a fresh install; a broken one is a crash mid-write or a bad hand
    edit. Neither stops the app — a client that will not start because it cannot parse
    its own preferences is worse than one that forgot them.
    """
    raw = None
    for where in (PATH, MACHINE_PATH):
        try:
            with open(where, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            break
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            log.warning("%s could not be read (%s); using defaults", where, exc)
            return Settings()
    if not isinstance(raw, dict):
        return Settings()
    # Unknown keys are dropped rather than raising — a newer client may have written
    # them, and that is not this one's problem. The same rule services.json follows.
    known = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in raw.items() if k in known})


def save(settings: Settings, machine: bool = False) -> bool:
    """Write it whole and move it into place, so a crash mid-write cannot leave a client
    that cannot read its own settings.

    `machine=True` writes the machine-wide copy instead, which needs the rights to write
    the data folder. Exactly one caller does that — `hub.exe client pair --local`, running
    from the installer — and it is the whole point of that command: the pairing has to be
    there for *whoever* logs into the server next, not only for the person who happened to
    run the installer.
    """
    where = MACHINE_PATH if machine else PATH
    try:
        os.makedirs(os.path.dirname(where) or ".", exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            dir=os.path.dirname(where) or ".", prefix="client-", suffix=".json")
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(asdict(settings), fh, indent=2)
        os.replace(temporary, where)
        return True
    except OSError as exc:
        log.warning("could not save %s: %s", where, exc)
        return False


# ---------------------------------------------------------------------------
# the token, which does not live in the file
# ---------------------------------------------------------------------------
def token(url: str) -> str:
    """This user's token for that hub, or the machine's.

    The machine's is what `client pair --local` writes at install time, as SYSTEM. A user
    can read it and cannot write it, which is the right way round: they inherit a working
    pairing and cannot quietly replace anybody else's.
    """
    ref = _token_ref(url)
    return (secrets.get(ref, path=secrets.USER_SECRETS_PATH)
            or secrets.get(ref))


def tokens(url: str) -> list:
    """Every token this computer holds for that hub, best guess first.

    There can be two — this user's and the machine's — and only the hub knows which it
    still accepts. A stale one used to be the end of it: on 2026-07-28 an upgrade had
    replaced the machine's token, the user's copy was refused, and a panel that had been
    working could not reconnect. So the client tries the other rather than giving up.
    """
    ref = _token_ref(url)
    found = []
    for path in (secrets.USER_SECRETS_PATH, None):
        value = secrets.get(ref, path=path) if path else secrets.get(ref)
        if value and value not in found:
            found.append(value)
    return found


def set_token(url: str, value: str, machine: bool = False) -> bool:
    """This user's own store — the machine's is not writable without elevation, and one
    person pairing must not repoint everybody who logs into this computer.

    `machine=True` is for `client pair --local` at install time, for the same reason as
    `save(machine=True)`: a token only that one person can read is a token the next person
    to sign in does not have.
    """
    return secrets.put(_token_ref(url), value,
                       path=None if machine else secrets.USER_SECRETS_PATH)


def forget_token(url: str) -> bool:
    """Only this user's. The machine-wide one belongs to whoever installed it, and
    removing it needs the rights that put it there — `hub.exe client revoke` is the way to
    make a token stop working for everybody."""
    return secrets.forget(_token_ref(url), path=secrets.USER_SECRETS_PATH)
