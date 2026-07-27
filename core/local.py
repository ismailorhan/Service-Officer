"""This client's own settings — which are not the landscape's.

Once there is a hub, two kinds of setting exist and keeping them apart is the whole
point of this file:

| | Lives where | Examples |
|---|---|---|
| the landscape | the hub, one copy, shared | services, machines, stacks, triggers, health checks, retention |
| **this client** | the machine it runs on | which hub, its token, the pinned certificate, the theme, whether the tray starts with Windows, whether *this* screen shows notifications |

Mixing them means five people each believing they can change the theme for everyone, or
that history retention is somebody else's problem.

`client.json` sits beside `services.json` and is **not a second config**: it holds
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

#: Beside services.json. A module-level default rather than a call, so a test can point
#: it somewhere else in one line.
PATH = os.path.join(cfg_mod.APP_DIR, "client.json")


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

    A missing file is a fresh install; a broken one is a crash mid-write or a bad hand
    edit. Neither stops the app — a client that will not start because it cannot parse
    its own preferences is worse than one that forgot them.
    """
    try:
        with open(PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return Settings()
    except (OSError, ValueError) as exc:
        log.warning("%s could not be read (%s); using defaults", PATH, exc)
        return Settings()
    if not isinstance(raw, dict):
        return Settings()
    # Unknown keys are dropped rather than raising — a newer client may have written
    # them, and that is not this one's problem. The same rule services.json follows.
    known = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in raw.items() if k in known})


def save(settings: Settings) -> bool:
    """Write it whole and move it into place, so a crash mid-write cannot leave a client
    that cannot read its own settings."""
    try:
        os.makedirs(os.path.dirname(PATH) or ".", exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            dir=os.path.dirname(PATH) or ".", prefix="client-", suffix=".json")
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(asdict(settings), fh, indent=2)
        os.replace(temporary, PATH)
        return True
    except OSError as exc:
        log.warning("could not save %s: %s", PATH, exc)
        return False


# ---------------------------------------------------------------------------
# the token, which does not live in the file
# ---------------------------------------------------------------------------
def token(url: str) -> str:
    return secrets.get(_token_ref(url))


def set_token(url: str, value: str) -> bool:
    return secrets.put(_token_ref(url), value)


def forget_token(url: str) -> bool:
    return secrets.forget(_token_ref(url))
