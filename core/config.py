"""Configuration: a typed model over %ProgramData%\\ServiceOfficer\\services.json.

Version 2 adds per-service recovery rules, ordered stacks, history and
notification preferences. A version 1 file (a plain list of services, possibly
even a list of bare strings) loads without complaint and gains defaults, so an
existing install keeps working after an upgrade.

Saving is atomic: the document is written to a temporary file in the same
directory and then moved over the original, so a crash or a full disk can never
leave a customer's machine with a half-written config.

Machine-scoped, not per-user. What this app describes belongs to the computer:
the services installed on it, when they should restart, and what happened to
them. Under %APPDATA% a second administrator logging into the same server saw an
empty install, the history a ticket needs was in whoever's profile happened to be
active, and running as a Windows service would put it in
%WINDIR%\\System32\\config\\systemprofile — a different place again. There is a
one-time move from the old per-user location so nothing is lost.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field, asdict, replace

APP_DIR = os.path.join(
    os.environ.get("ProgramData", r"C:\ProgramData"), "ServiceOfficer")
#: where it used to live, per user; kept only to move data out of
LEGACY_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                          "ServiceOfficer")
CONFIG_PATH = os.path.join(APP_DIR, "services.json")
#: dropped next to the moved files so a second run doesn't try again
MIGRATION_MARK = ".moved-from-appdata"

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


LOCAL_MACHINE = ""                 # empty name means this computer


@dataclass
class Machine:
    """A computer whose services we manage. The local one always exists."""
    name: str = LOCAL_MACHINE      # "" = this computer, else a host name
    label: str = "This computer"

    @property
    def is_local(self) -> bool:
        return not self.name

    def display(self) -> str:
        return self.label or self.name or "This computer"


NO_CATEGORY = ""                   # a service that hasn't been filed anywhere
NO_CATEGORY_TITLE = "No category"

#: How to tell whether a service is doing its job, as opposed to merely being
#: Running. The SCM only knows whether a process exists, which is why a wedged
#: service can sit there reporting Running while nobody can use it.
#:
#: Five kinds, chosen to need no knowledge of any particular product:
#:   tcp      — something is accepting connections on a port
#:   http     — a URL answers, with an expected status and optionally some text
#:   process  — Running, but with no process behind it at all
#:   file     — a heartbeat or log file has been touched recently
#:   command  — anything else: exit code 0 means healthy
CHECK_KINDS = ("tcp", "http", "process", "file", "command")


@dataclass
class HealthCheck:
    kind: str = "tcp"
    #: tcp
    host: str = ""                 # blank = the machine the service runs on
    port: int = 0
    #: http
    url: str = ""
    expect_status: int = 0         # 0 = any 2xx or 3xx
    expect_text: str = ""          # must appear in the body when set
    insecure: bool = False         # internal servers often have their own certs
    #: file
    path: str = ""
    max_age_seconds: int = 300
    #: command
    command: str = ""
    expect_exit: int = 0
    #: all kinds
    timeout_seconds: int = 5
    enabled: bool = True

    def describe(self) -> str:
        """One line, in the words someone would use to explain the check."""
        if self.kind == "tcp":
            where = f"{self.host or 'the service’s machine'}:{self.port}"
            return f"something answers on {where}"
        if self.kind == "http":
            want = (f"HTTP {self.expect_status}" if self.expect_status
                    else "a success response")
            text = f" containing “{self.expect_text}”" if self.expect_text else ""
            return f"{self.url} returns {want}{text}"
        if self.kind == "process":
            return "it actually has a process"
        if self.kind == "file":
            return (f"{self.path} was written in the last "
                    f"{self.max_age_seconds}s")
        if self.kind == "command":
            exit_note = ("" if self.expect_exit == 0
                         else f" (exit {self.expect_exit})")
            return f"“{self.command}” succeeds{exit_note}"
        return self.kind


@dataclass
class Health:
    """When and how hard to ask, and what to do about the answer.

    checks are ANDed: a service with a port *and* a URL has to satisfy both,
    because either one failing means somebody can't use it.
    """
    checks: list = field(default_factory=list)     # list[HealthCheck]
    interval_seconds: int = 60
    #: after it reaches Running, how long before its answers count. A service
    #: that has just started has not opened its port yet, and judging it
    #: immediately would report every single restart as a failure.
    grace_seconds: int = 60
    #: one bad answer is a blip; this many in a row is a problem
    failures_before_acting: int = 3
    action: str = "notify"                         # notify | restart

    @property
    def enabled(self) -> bool:
        return any(c.enabled for c in self.checks)


@dataclass
class Category:
    """A heading services are grouped under in the panel and the dashboard.

    Purely how the lists are organised — nothing acts on a category — so it
    carries a name and nothing else.
    """
    name: str

    def display(self) -> str:
        return self.name or NO_CATEGORY_TITLE


@dataclass
class Service:
    name: str                      # Windows short name
    label: str = ""                # what the user sees
    machine: str = LOCAL_MACHINE   # which machine it belongs to
    category: str = NO_CATEGORY    # which heading it is listed under
    recovery: Recovery = field(default_factory=Recovery)
    health: Health = field(default_factory=Health)

    def display(self) -> str:
        return self.label or self.name

    @property
    def key(self) -> tuple:
        """Identity across machines — a name alone isn't unique once remote
        machines exist."""
        return (self.machine or "", self.name)


@dataclass
class Step:
    """One service in a stack.

    The wait fields describe the *gap after this step*, before the next one
    starts — which is why the last step in a stack has nothing to configure.

    wait == "running": wait until this service reports Running, then pause
        `grace_seconds` more; abandon the run if it hasn't started within
        `timeout_seconds`.
    wait == "delay": ignore the status and pause exactly `delay_seconds`.
    """
    service: str
    action: str = "start"          # "start" | "stop" | "restart"
    wait: str = "applied"          # "applied" (reach the target state) | "delay"
    timeout_seconds: int = 60      # give up waiting for the target state
    grace_seconds: int = 0         # extra pause once it reached the target
    delay_seconds: int = 10        # used when wait == "delay"

    @property
    def target_state(self) -> str:
        """The state this step is trying to produce."""
        return "Stopped" if self.action == "stop" else "Running"


@dataclass
class Stack:
    name: str
    steps: list = field(default_factory=list)   # list[Step]
    show_in_flyout: bool = True                 # offer it in the tray panel

    def summary(self, services=None) -> str:
        labels = {s.name: s.display() for s in (services or [])}
        return " → ".join(labels.get(st.service, st.service) for st in self.steps)

    def describe(self, services=None) -> str:
        """Per-step detail, spelling out what happens between the steps."""
        labels = {s.name: s.display() for s in (services or [])}
        out = []
        last = len(self.steps)
        for i, st in enumerate(self.steps, start=1):
            name = labels.get(st.service, st.service)
            head = f"{i}. {st.action} {name}"
            if i == last:
                out.append(f"{head} — verified {st.target_state.lower()} "
                           f"(up to {st.timeout_seconds}s)")
            elif st.wait == "applied":
                extra = f" + {st.grace_seconds}s" if st.grace_seconds else ""
                out.append(f"{head} — then wait until applied{extra}, "
                           f"timeout {st.timeout_seconds}s")
            else:
                out.append(f"{head} — then wait {st.delay_seconds}s, always")
        return "\n".join(out)


@dataclass
class Trigger:
    """When something should happen, and what.

    Deliberately two halves so the list of "whens" and the list of "actions" can
    grow independently — notifications and mail are the obvious next actions, a
    maintenance window the obvious next when.
    """
    name: str
    enabled: bool = True

    # -- when ----------------------------------------------------------------
    when: str = "startup"          # "startup" | "time"
    delay_seconds: int = 30        # startup: settle time before firing
    time_of_day: str = "03:00"     # time: local HH:MM
    days: list = field(default_factory=list)   # empty = every day, else 0=Mon…6=Sun
    repeat_seconds: int = 0        # 0 = once a day; else repeat from that time

    # -- action --------------------------------------------------------------
    action: str = "stack"          # "stack" | "service"
    stack: str = ""
    service: str = ""
    service_action: str = "start"  # start | stop | restart
    machine: str = LOCAL_MACHINE

    #: when to tell the user: never | success | failed | both | all (incl. skipped)
    notify: str = "failed"

    DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

    def repeat_text(self) -> str:
        if not self.repeat_seconds:
            return ""
        seconds = self.repeat_seconds
        if seconds % 3600 == 0:
            every = f"{seconds // 3600}h"
        elif seconds % 60 == 0:
            every = f"{seconds // 60}m"
        else:
            every = f"{seconds}s"
        return f", then every {every}"

    def when_text(self) -> str:
        if self.when == "startup":
            if self.delay_seconds:
                return f"when Windows starts, after {self.delay_seconds}s"
            return "when Windows starts"
        if self.days and len(self.days) < 7:
            days = ", ".join(self.DAY_NAMES[d] for d in sorted(self.days))
            return f"{days} at {self.time_of_day}{self.repeat_text()}"
        return f"every day at {self.time_of_day}{self.repeat_text()}"

    #: what "tell me" can be set to; the combinations exist because the runs
    #: worth hearing about are the ones that didn't simply work.
    NOTIFY_CHOICES = ("never", "success", "failed", "skipped",
                      "failed_skipped", "both", "all")

    def wants_notice(self, outcome: str) -> bool:
        """outcome is "success", "failed" or "skipped"."""
        if self.notify == "never":
            return False
        if self.notify == "all":
            return True
        if self.notify == "both":
            return outcome in ("success", "failed")
        if self.notify == "failed_skipped":
            return outcome in ("failed", "skipped")
        return self.notify == outcome

    def action_text(self, services=None) -> str:
        if self.action == "stack":
            return f"run “{self.stack}”" if self.stack else "run a stack (none chosen)"
        label = self.service
        for s in (services or []):
            if s.name == self.service:
                label = s.display()
                break
        return f"{self.service_action} {label}" if self.service else "no service chosen"

    def summary(self, services=None) -> str:
        return f"{self.when_text()} → {self.action_text(services)}"


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
    triggers: list = field(default_factory=list)      # list[Trigger]
    machines: list = field(default_factory=lambda: [Machine()])
    categories: list = field(default_factory=list)     # list[Category]
    history: History = field(default_factory=History)
    notifications: Notifications = field(default_factory=Notifications)
    auto_start: bool = True
    theme: str = "system"          # "system" follows Windows; else "dark"/"light"
    version: int = CURRENT_VERSION

    # -- lookup helpers ---------------------------------------------------
    def service(self, name: str, machine: str = "") -> Service | None:
        for s in self.services:
            if s.name == name and (s.machine or "") == (machine or ""):
                return s
        return None

    def stack(self, name: str) -> Stack | None:
        return next((s for s in self.stacks if s.name == name), None)

    def trigger(self, name: str) -> Trigger | None:
        return next((t for t in self.triggers if t.name == name), None)

    def machine(self, name: str) -> Machine | None:
        return next((m for m in self.machines if m.name == (name or "")), None)

    def machine_label(self, name: str) -> str:
        found = self.machine(name)
        return found.display() if found else (name or "This computer")

    def service_names(self) -> list:
        return [s.name for s in self.services]

    def category(self, name: str) -> Category | None:
        return next((c for c in self.categories if c.name == (name or "")), None)

    def grouped_services(self, include_empty: bool = False) -> list:
        """[(category name, title, [services])] for the panel and the dashboard.

        Categories keep the order they were defined in; services keep theirs
        inside each one. Uncategorised services come last under their own
        heading rather than being hidden.

        include_empty is for the editor, where a category with nothing in it is
        somewhere to drag a service *to*; those come last, after everything that
        has contents, and "No category" is always offered so a service can be
        dragged back out. In the lists you only read, an empty heading is noise.
        """
        groups = []
        for cat in self.categories:
            members = [s for s in self.services if (s.category or "") == cat.name]
            if members:
                groups.append((cat.name, cat.display(), members))
        loose = [s for s in self.services
                 if (s.category or "") == NO_CATEGORY
                 or not self.category(s.category)]
        if loose or include_empty:
            groups.append((NO_CATEGORY, NO_CATEGORY_TITLE, loose))
        if include_empty:
            filed = {name for name, _t, _m in groups}
            groups.extend((cat.name, cat.display(), [])
                          for cat in self.categories if cat.name not in filed)
        return groups


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
        category=str(raw.get("category") or NO_CATEGORY),
        recovery=_recovery_from(raw.get("recovery")),
        health=_health_from(raw.get("health")),
    )


def _check_from(raw) -> HealthCheck | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind", "tcp")
    if kind not in CHECK_KINDS:
        return None                     # an unknown kind is silently useless
    check = HealthCheck(
        kind=kind,
        host=str(raw.get("host") or ""),
        port=max(0, min(65535, _as_int(raw.get("port"), 0))),
        url=str(raw.get("url") or ""),
        expect_status=max(0, _as_int(raw.get("expect_status"), 0)),
        expect_text=str(raw.get("expect_text") or ""),
        insecure=bool(raw.get("insecure", False)),
        path=str(raw.get("path") or ""),
        max_age_seconds=max(1, _as_int(raw.get("max_age_seconds"), 300)),
        command=str(raw.get("command") or ""),
        expect_exit=_as_int(raw.get("expect_exit"), 0),
        timeout_seconds=max(1, min(120, _as_int(raw.get("timeout_seconds"), 5))),
        enabled=bool(raw.get("enabled", True)),
    )
    # A check with nothing to check would always pass, which is worse than
    # having no check at all — it would read as a guarantee.
    if kind == "tcp" and not check.port:
        return None
    if kind == "http" and not check.url:
        return None
    if kind == "file" and not check.path:
        return None
    if kind == "command" and not check.command:
        return None
    return check


def _health_from(raw) -> Health:
    if not isinstance(raw, dict):
        return Health()
    checks = [c for c in (_check_from(x) for x in raw.get("checks", [])) if c]
    action = raw.get("action", "notify")
    return Health(
        checks=checks,
        interval_seconds=max(5, _as_int(raw.get("interval_seconds"), 60)),
        grace_seconds=max(0, _as_int(raw.get("grace_seconds"), 60)),
        failures_before_acting=max(1, _as_int(
            raw.get("failures_before_acting"), 3)),
        action=action if action in ("notify", "restart") else "notify",
    )


def _step_from(raw) -> Step | None:
    if isinstance(raw, str):
        return Step(service=raw)
    if not isinstance(raw, dict) or not raw.get("service"):
        return None
    # "running" was the old name for "applied", and "auto" used to mean "follow
    # the stack-level action" before a step always carried its own.
    wait = raw.get("wait", "applied")
    if wait == "running":
        wait = "applied"
    if wait not in ("applied", "delay"):
        wait = "applied"
    action = raw.get("action", "start")
    if action in ("auto", "") or action not in ("start", "stop", "restart"):
        action = "start"
    return Step(
        service=str(raw["service"]),
        action=action,
        wait=wait,
        timeout_seconds=max(1, _as_int(raw.get("timeout_seconds"), 60)),
        grace_seconds=max(0, _as_int(raw.get("grace_seconds"), 0)),
        delay_seconds=max(0, _as_int(raw.get("delay_seconds"), 10)),
    )


def _stack_from(raw) -> Stack | None:
    if not isinstance(raw, dict) or not raw.get("name"):
        return None
    steps = [s for s in (_step_from(x) for x in raw.get("steps", [])) if s]
    return Stack(name=str(raw["name"]), steps=steps,
                 show_in_flyout=bool(raw.get("show_in_flyout", True)))


def _machine_from(raw) -> Machine | None:
    if isinstance(raw, str):
        return Machine(name=raw, label=raw or "This computer")
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "")
    return Machine(name=name, label=str(raw.get("label") or name or "This computer"))


def _category_from(raw) -> Category | None:
    name = raw if isinstance(raw, str) else (raw or {}).get("name")
    name = str(name or "").strip()
    return Category(name=name) if name else None


def _trigger_from(raw) -> Trigger | None:
    if not isinstance(raw, dict) or not raw.get("name"):
        return None
    when = raw.get("when", "startup")
    if when not in ("startup", "time"):
        when = "startup"
    action = raw.get("action", "stack")
    if action not in ("stack", "service"):
        action = "stack"
    svc_action = raw.get("service_action", "start")
    if svc_action not in ("start", "stop", "restart"):
        svc_action = "start"
    days = [d for d in raw.get("days", []) if isinstance(d, int) and 0 <= d <= 6]
    time_of_day = str(raw.get("time_of_day") or "03:00")
    if not _valid_hhmm(time_of_day):
        time_of_day = "03:00"
    notify = raw.get("notify", "failed")
    if notify not in Trigger.NOTIFY_CHOICES:
        notify = "failed"
    return Trigger(
        name=str(raw["name"]),
        enabled=bool(raw.get("enabled", True)),
        when=when,
        delay_seconds=max(0, _as_int(raw.get("delay_seconds"), 30)),
        time_of_day=time_of_day,
        days=sorted(set(days)),
        repeat_seconds=max(0, _as_int(raw.get("repeat_seconds"), 0)),
        action=action,
        stack=str(raw.get("stack") or ""),
        service=str(raw.get("service") or ""),
        service_action=svc_action,
        machine=str(raw.get("machine") or ""),
        notify=notify,
    )


def _valid_hhmm(text: str) -> bool:
    parts = str(text).split(":")
    if len(parts) != 2:
        return False
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return 0 <= hour <= 23 and 0 <= minute <= 59


def from_dict(data: dict) -> Config:
    """Build a Config from raw JSON, filling in anything missing."""
    data = data if isinstance(data, dict) else {}
    services = [s for s in (_service_from(x) for x in data.get("services", [])) if s]
    stacks = [s for s in (_stack_from(x) for x in data.get("stacks", [])) if s]
    triggers = [t for t in (_trigger_from(x) for x in data.get("triggers", [])) if t]

    # Every service belongs to a machine, and this computer is always one of
    # them — an install with no machines section still gets it.
    machines = [m for m in (_machine_from(x) for x in data.get("machines", [])) if m]
    if not any(m.is_local for m in machines):
        machines.insert(0, Machine())
    known = {m.name for m in machines}
    for svc in services:
        if svc.machine not in known:
            machines.append(Machine(name=svc.machine, label=svc.machine))
            known.add(svc.machine)

    # Categories are grouping only, so one a service refers to but that isn't
    # listed is kept rather than dropped — losing the grouping silently would be
    # worse than an extra heading.
    categories = [c for c in (_category_from(x)
                              for x in data.get("categories", [])) if c]
    seen = {c.name for c in categories}
    for svc in services:
        if svc.category and svc.category not in seen:
            categories.append(Category(name=svc.category))
            seen.add(svc.category)

    h = data.get("history") if isinstance(data.get("history"), dict) else {}
    n = data.get("notifications") if isinstance(data.get("notifications"), dict) else {}

    return Config(
        services=services,
        stacks=stacks,
        triggers=triggers,
        machines=machines,
        categories=categories,
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
        theme=(data.get("theme") if data.get("theme") in ("system", "dark", "light")
               else "system"),
        version=CURRENT_VERSION,
    )


def to_dict(cfg: Config) -> dict:
    return {
        "version": CURRENT_VERSION,
        "services": [asdict(s) for s in cfg.services],
        "stacks": [asdict(s) for s in cfg.stacks],
        "triggers": [asdict(t) for t in cfg.triggers],
        "machines": [asdict(m) for m in cfg.machines],
        "categories": [asdict(c) for c in cfg.categories],
        "history": asdict(cfg.history),
        "notifications": asdict(cfg.notifications),
        "auto_start": cfg.auto_start,
        "theme": cfg.theme,
    }


#: files worth carrying over from the old per-user directory
MIGRATED_FILES = ("services.json", "history.jsonl", "service-officer.log")


def migrate_from_legacy(app_dir: str = None, legacy_dir: str = None) -> list:
    """Move a per-user install's data to the machine-wide directory, once.

    Copied rather than moved, and only when the destination has nothing: an
    upgrade must never be the reason someone's service list disappears. Returns
    what it brought over, for the log.
    """
    app_dir = app_dir or APP_DIR
    legacy_dir = legacy_dir or LEGACY_DIR
    if os.path.abspath(app_dir) == os.path.abspath(legacy_dir):
        return []
    if not os.path.isdir(legacy_dir):
        return []
    if os.path.exists(os.path.join(legacy_dir, MIGRATION_MARK)):
        return []                       # already done on a previous run

    brought = []
    try:
        os.makedirs(app_dir, exist_ok=True)
        for name in MIGRATED_FILES:
            source = os.path.join(legacy_dir, name)
            target = os.path.join(app_dir, name)
            if os.path.exists(source) and not os.path.exists(target):
                shutil.copy2(source, target)
                brought.append(name)
        # The old copies stay where they are; the mark stops us looking again.
        with open(os.path.join(legacy_dir, MIGRATION_MARK), "w",
                  encoding="utf-8") as f:
            f.write(f"moved to {app_dir}\n")
    except OSError:
        return brought                  # partial is fine; nothing was destroyed
    return brought


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
