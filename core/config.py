"""Configuration: a typed model over %APPDATA%\\ServiceOfficer\\services.json.

Version 2 adds per-service recovery rules, ordered stacks, history and
notification preferences. A version 1 file (a plain list of services, possibly
even a list of bare strings) loads without complaint and gains defaults, so an
existing install keeps working after an upgrade.

Saving is atomic: the document is written to a temporary file in the same
directory and then moved over the original, so a crash or a full disk can never
leave a customer's machine with a half-written config.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict, replace

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                       "ServiceOfficer")
CONFIG_PATH = os.path.join(APP_DIR, "services.json")

CURRENT_VERSION = 2


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
@dataclass
class Recovery:
    """What to do when a service stops without us asking."""
    enabled: bool = False
    max_attempts: int = 3          # 0 = keep trying
    delay_seconds: int = 10        # wait before the first attempt
    backoff: float = 2.0           # delay multiplier for each further attempt
    restart_on_clean_stop: bool = False
    flap_threshold: int = 5        # this many stops...
    flap_window_minutes: int = 30  # ...within this window and we give up

    def delay_for(self, attempt: int, cap_seconds: int = 300) -> float:
        """Delay before `attempt` (1-based), with backoff, capped."""
        if attempt <= 1:
            return float(self.delay_seconds)
        d = self.delay_seconds * (self.backoff ** (attempt - 1))
        return float(min(d, cap_seconds))


@dataclass
class Service:
    name: str                      # Windows short name
    label: str = ""                # what the user sees
    machine: str = ""              # "" = this computer (roadmap #4)
    recovery: Recovery = field(default_factory=Recovery)

    def display(self) -> str:
        return self.label or self.name

    @property
    def key(self) -> tuple:
        """Identity across machines — a name alone isn't unique once remote
        machines exist."""
        return (self.machine or "", self.name)


@dataclass
class Step:
    service: str
    wait: str = "running"          # "running" | "delay"
    timeout_seconds: int = 60      # used when wait == "running"
    delay_seconds: int = 10        # used when wait == "delay"


@dataclass
class Stack:
    name: str
    steps: list = field(default_factory=list)   # list[Step]

    def summary(self, services=None) -> str:
        labels = {s.name: s.display() for s in (services or [])}
        return " → ".join(labels.get(st.service, st.service) for st in self.steps)


@dataclass
class History:
    enabled: bool = True
    retention_days: int = 30


@dataclass
class Notifications:
    on_crash: bool = True
    on_recovery: bool = True
    on_give_up: bool = True


@dataclass
class Config:
    services: list = field(default_factory=list)      # list[Service]
    stacks: list = field(default_factory=list)        # list[Stack]
    history: History = field(default_factory=History)
    notifications: Notifications = field(default_factory=Notifications)
    auto_start: bool = True
    version: int = CURRENT_VERSION

    # -- lookup helpers ---------------------------------------------------
    def service(self, name: str, machine: str = "") -> Service | None:
        for s in self.services:
            if s.name == name and (s.machine or "") == (machine or ""):
                return s
        return None

    def stack(self, name: str) -> Stack | None:
        return next((s for s in self.stacks if s.name == name), None)

    def service_names(self) -> list:
        return [s.name for s in self.services]


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------
def _as_int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _recovery_from(raw) -> Recovery:
    d = raw if isinstance(raw, dict) else {}
    base = Recovery()
    return Recovery(
        enabled=bool(d.get("enabled", base.enabled)),
        max_attempts=max(0, _as_int(d.get("max_attempts"), base.max_attempts)),
        delay_seconds=max(0, _as_int(d.get("delay_seconds"), base.delay_seconds)),
        backoff=max(1.0, _as_float(d.get("backoff"), base.backoff)),
        restart_on_clean_stop=bool(d.get("restart_on_clean_stop",
                                         base.restart_on_clean_stop)),
        flap_threshold=max(2, _as_int(d.get("flap_threshold"), base.flap_threshold)),
        flap_window_minutes=max(1, _as_int(d.get("flap_window_minutes"),
                                          base.flap_window_minutes)),
    )


def _service_from(raw) -> Service | None:
    # v1 tolerance: a bare string was a service name.
    if isinstance(raw, str):
        return Service(name=raw, label=raw)
    if not isinstance(raw, dict) or not raw.get("name"):
        return None
    return Service(
        name=str(raw["name"]),
        label=str(raw.get("label") or raw["name"]),
        machine=str(raw.get("machine") or ""),
        recovery=_recovery_from(raw.get("recovery")),
    )


def _step_from(raw) -> Step | None:
    if isinstance(raw, str):
        return Step(service=raw)
    if not isinstance(raw, dict) or not raw.get("service"):
        return None
    wait = raw.get("wait", "running")
    if wait not in ("running", "delay"):
        wait = "running"
    return Step(
        service=str(raw["service"]),
        wait=wait,
        timeout_seconds=max(1, _as_int(raw.get("timeout_seconds"), 60)),
        delay_seconds=max(0, _as_int(raw.get("delay_seconds"), 10)),
    )


def _stack_from(raw) -> Stack | None:
    if not isinstance(raw, dict) or not raw.get("name"):
        return None
    steps = [s for s in (_step_from(x) for x in raw.get("steps", [])) if s]
    return Stack(name=str(raw["name"]), steps=steps)


def from_dict(data: dict) -> Config:
    """Build a Config from raw JSON, filling in anything missing."""
    data = data if isinstance(data, dict) else {}
    services = [s for s in (_service_from(x) for x in data.get("services", [])) if s]
    stacks = [s for s in (_stack_from(x) for x in data.get("stacks", [])) if s]

    h = data.get("history") if isinstance(data.get("history"), dict) else {}
    n = data.get("notifications") if isinstance(data.get("notifications"), dict) else {}

    return Config(
        services=services,
        stacks=stacks,
        history=History(
            enabled=bool(h.get("enabled", True)),
            retention_days=max(1, _as_int(h.get("retention_days"), 30)),
        ),
        notifications=Notifications(
            on_crash=bool(n.get("on_crash", True)),
            on_recovery=bool(n.get("on_recovery", True)),
            on_give_up=bool(n.get("on_give_up", True)),
        ),
        auto_start=bool(data.get("auto_start", True)),
        version=CURRENT_VERSION,
    )


def to_dict(cfg: Config) -> dict:
    return {
        "version": CURRENT_VERSION,
        "services": [asdict(s) for s in cfg.services],
        "stacks": [asdict(s) for s in cfg.stacks],
        "history": asdict(cfg.history),
        "notifications": asdict(cfg.notifications),
        "auto_start": cfg.auto_start,
    }


def load(path: str = None) -> Config:
    path = path or CONFIG_PATH
    if not os.path.exists(path):
        return Config()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return from_dict(json.load(f))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        # A corrupt file must not stop the app from starting; the user still
        # gets a tray icon and can fix things in Settings.
        return Config()


def save(cfg: Config, path: str = None) -> None:
    path = path or CONFIG_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".cfg-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(to_dict(cfg), f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)       # atomic on Windows for same-volume moves
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def copy_service(svc: Service, **changes) -> Service:
    """Shallow copy with overrides, for editors that shouldn't mutate live state."""
    return replace(svc, **changes)
