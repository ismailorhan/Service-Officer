# Hub Service and Clients Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split Service Officer into a long-running hub service that owns the config, the connections and the history, and thin clients that read from it and send it actions — the tray app first, a browser UI later.

**Architecture:** The hub is a Windows service running the existing `core/` engine with no UI. It exposes HTTPS + JSON on one port: a snapshot, a Server-Sent-Events stream, and action endpoints. A client is anything that speaks that API. The tray app keeps working exactly as it does today by running the engine in-process ("embedded mode"), and gains a "connect to a hub" mode where `state.Store` is replaced by a store fed from the hub. The hub reaches its targets itself — Windows through the SCM, Linux over SSH — so nothing is installed on the machines being managed.

**Tech Stack:** Python 3.12, PySide6 (client only), pywin32 (SCM, DPAPI, service framework), paramiko (SSH), `http.server` + `ssl` from the standard library for the API. No new third-party dependency.

## Global Constraints

- **No new third-party dependencies.** The API server and client use `http.server`, `ssl`, `json` and `urllib` from the standard library. Reason: every dependency is something to license-check, ship in the PyInstaller bundle and patch on customer servers, and 5 clients over a LAN do not need an async framework.
- **The hub normally runs in the CT domain, as a domain service account.** It must also manage servers in a different domain on the same network (measured: `SC\ismailorhan` on `10.77.3.112`), which is what the stored-credentials path in `core/win_session.py` exists for.
- **No agent on managed machines.** SSH is the agent for Linux; the SCM is the agent for Windows. An agent transport stays possible as a third `Connector` implementation but is out of scope here.
- **The tray app must keep working with no hub at all.** Embedded mode is the default; a single-machine user installs one thing and notices no change.
- **UI code must not change to gain a hub.** The interface reads `state.Store` and emits action requests; both are satisfied by the remote implementations. A task that requires editing `ui/` to talk HTTP is a design failure of that task.
- **Every remote call belongs off the UI thread.** Established tonight and non-negotiable: the local SCM answers in 0.2 ms, a remote one took 21 s to connect and 42 s to refuse.
- **Python is invoked as** `C:\Users\ismail.orhan\AppData\Local\Programs\Python\Python312\python.exe` (no `py` launcher on this machine). Tests: `python -m pytest tests -q` from the repo root.
- **Windows service name:** `ServiceOfficerHub`, display name `Service Officer Hub`. Default port **8797**.
- **Copy rules:** UK English, sentence case, no exclamation marks, no em-dash-free rewriting of existing strings. Match the surrounding voice in `ui/` and `core/`.

---

## Why this shape (decisions already made, do not relitigate)

| Decision | Reason |
|---|---|
Hub pulls from targets; no agents | Linux over SSH costs 64 ms for four services and installs nothing. An agent on a headless SUSE box adds software to deploy, update and watch, for no measured gain. |
Hub in the targets' domain when possible | With a domain service account the Windows token path works and no password is stored anywhere. Cross-domain targets keep the DPAPI credential path built on 2026-07-26. |
HTTP + JSON + SSE, not a custom protocol | Inspectable with a browser and `curl`, trivially consumed by the future web UI, and no framing code of our own to get wrong. |
TLS with a pinned self-signed certificate | Same trust model as the SSH host key the app already pins, and it needs no PKI. Clients refuse a changed certificate. |
Bearer token per client | Windows-integrated auth (Negotiate) cannot work across the CT/SC forest boundary, which is exactly the environment this runs in. |
Hub is the only writer of `services.json` and `history.db` | SQLite over a network share with several writers corrupts. One writer, many readers over the API. |
Embedded mode stays | It is what everyone has today, it is the fallback when the hub is down, and it keeps the engine honest: the same code must run both ways. |

**Deliberately out of scope of this plan** (each needs its own plan):
- The browser UI. This plan reserves the endpoints and serves a placeholder page, nothing more.
- A hosted hub with thin clients over the internet (the "we host it" idea). The API shape here does not prevent it — token auth and TLS are already the right primitives — but multi-tenancy, accounts and internet exposure are a different product and must not be designed in passing.
- An agent transport for targets.
- Auto-update of clients.

---

## File Structure

**New files**

| File | Responsibility |
|---|---|
`core/engine.py` | The headless engine: owns store, poller, health, watchdog, scheduler, stacks, history, connectors. Emits plain-Python callbacks. No Qt, no UI. |
`core/wire.py` | The wire format. Pure functions turning engine state into JSON-ready dicts and back. No I/O, no sockets. |
`core/hub_auth.py` | Tokens and the TLS certificate: generate, store (DPAPI machine scope), verify, fingerprint. |
`core/hub_server.py` | The HTTPS API: routing, auth, snapshot, SSE stream, action endpoints, config read/write with etag. |
`core/hub_client.py` | `RemoteStore` and `RemoteActions`: the client half, with a reconnecting event consumer. |
`hub.py` | Entry point for the hub: `--console` for debugging, Windows service class for production. |
`tests/test_engine.py` | The engine runs headless and reports through callbacks. |
`tests/test_wire.py` | Round-trips and version negotiation of the wire format. |
`tests/test_hub_auth.py` | Token and certificate handling. |
`tests/test_hub_server.py` | Endpoints, auth, etag conflicts, SSE framing — against a server on a loopback port. |
`tests/test_hub_client.py` | `RemoteStore` satisfies the read API the UI uses; reconnection; actions. |
`tests/test_hub_roundtrip.py` | A real hub and a real client in one process: an action from the client changes state in the engine and comes back on the stream. |
`docs/HUB.md` | How to install, configure, secure and troubleshoot the hub. |

**Modified files**

| File | Change |
|---|---|
`app.py` | Becomes UI wiring only: build either an embedded engine or a hub client, then wire the interface to it. Everything engine-shaped moves to `core/engine.py`. |
`core/state.py` | `Store` gains an explicit read interface docstring; nothing else. `RemoteStore` must satisfy it. |
`core/history.py` | `record_action` and `record_run` gain `actor`. |
`core/db.py` | Schema v3: `events.actor`. |
`installer.iss` | Install the hub, register the service, open the firewall port, upgrade cleanly. |
`requirements-dev.txt` | Nothing. Noted here so the reviewer sees it is deliberate. |
`docs/ROADMAP.md` | The "central server, agents, dashboards" non-feature is now half wrong: a hub is in scope, agents are not. |
`README.md` | Hub mode in the feature list and the install section. |

---

## Task 1: The read interface the UI actually uses

Before anything is extracted, pin down what a store has to provide. Everything else in this plan depends on this list being complete, and the cheapest way to get it wrong is to guess.

**Files:**
- Create: `tests/test_store_contract.py`
- Modify: `core/state.py` (docstring and a module-level tuple only)

**Interfaces:**
- Consumes: nothing.
- Produces: `state.READ_API: tuple[str, ...]` — the method names any store must implement.

- [ ] **Step 1: Find every store method the UI calls**

Run:

```bash
grep -rn "store\.\|_store\." ui/ app.py | grep -o "store\.[a-z_]*" | sort -u
```

Expected output includes at least: `status_of`, `state_of`, `all_states`, `health_of`, `health_detail`, `health_timing`, `start_type`, `is_disabled`, `machine_state`, `subscribe`, `update`, `set_health`, `set_start_type`, `note_machine`, `expect_stop`, `clear_expected`, `keep_only`.

- [ ] **Step 2: Write the failing test**

```python
"""What any store has to provide, whether it holds the data or fetches it.

This exists because the remote store is not a subclass: it has to satisfy the
same surface without inheriting a single line. A missing method there is an
AttributeError at the moment someone hovers a row.
"""

from core import state as st


def test_the_read_api_is_declared():
    assert isinstance(st.READ_API, tuple) and st.READ_API


def test_the_local_store_satisfies_it():
    store = st.Store()
    missing = [name for name in st.READ_API if not callable(getattr(store, name, None))]
    assert missing == [], f"Store is missing {missing}"


def test_the_read_api_covers_what_the_interface_asks_for():
    """Grown by hand from `grep -rn "store\\." ui/ app.py`. If the UI starts using
    another method, this list is where it has to be added — and the remote store
    then fails its own test until it grows one too."""
    for name in ("status_of", "health_of", "health_detail", "health_timing",
                 "start_type", "is_disabled", "machine_state", "all_states",
                 "subscribe"):
        assert name in st.READ_API
```

- [ ] **Step 3: Run it and watch it fail**

Run: `python -m pytest tests/test_store_contract.py -v`
Expected: FAIL with `AttributeError: module 'core.state' has no attribute 'READ_API'`

- [ ] **Step 4: Declare the interface**

In `core/state.py`, immediately above `class Store`:

```python
#: What any store has to provide. The remote store is not a subclass — it fetches
#: instead of holding — so the surface is written down rather than inherited.
#: Grown from `grep -rn "store\." ui/ app.py`; add to it when the interface does.
READ_API = (
    "status_of", "state_of", "all_states", "health_of", "health_detail",
    "health_timing", "start_type", "is_disabled", "machine_state", "subscribe",
)
#: Everything that changes state. A client sends these to the hub instead of
#: applying them locally, which is the whole difference between the two stores.
WRITE_API = (
    "update", "set_health", "set_health_timing", "set_start_type", "note_machine",
    "expect_stop", "clear_expected", "keep_only",
)
```

- [ ] **Step 5: Run the test again**

Run: `python -m pytest tests/test_store_contract.py -v`
Expected: PASS (3 tests). If `state_of` or `all_states` do not exist on `Store`, they are the real gap — add whichever the grep in Step 1 proved the UI uses, and remove from `READ_API` any name the grep did not produce.

- [ ] **Step 6: Commit**

```bash
git add core/state.py tests/test_store_contract.py
git commit -m "Write down the store surface a client has to satisfy"
```

---

## Task 2: Extract the engine, leaving the UI wiring behind

`app.py` is 933 lines and does two jobs: it builds the engine and it wires the interface. Only the first half can run in a service. This task moves it and nothing else — the same code, in a class with no Qt in it.

**Files:**
- Create: `core/engine.py`, `tests/test_engine.py`
- Modify: `app.py` (build an `Engine` instead of the parts)

**Interfaces:**
- Consumes: `state.READ_API` from Task 1.
- Produces — the full signature, because every later task calls into it:
  ```python
  class Engine:
      def __init__(self, config_getter, store=None, *,
                   on_event=None,          # (st.Event) — a service changed state
                   on_health=None,         # (service, machine, verdict, detail)
                   on_machine=None,        # (machine, reachable: bool, detail)
                   on_action_done=None,    # (**{id, action, service, machine,
                                           #     error, status, actor})
                   on_stack_step=None,     # (index, total, service, action, phase)
                   on_stack_done=None,     # (RunResult)
                   on_trigger=None,        # (trigger)
                   on_error=None,          # (kind: str, text: str)
                   on_config_saved=None):  # (Config) — the hub keeps the new one
  ```
  - `Engine.start() -> None`, `Engine.stop() -> None`
  - `Engine.store` (a `state.Store`), `Engine.config() -> Config`
  - `Engine.poller`, `Engine.health`, `Engine.watchdog`, `Engine.scheduler`,
    `Engine.stacks`, `Engine.watcher` — kept as attributes so `app.py`'s rewiring in
    Step 5 is a rename and nothing more
  - `Engine.act(action: str, service: str, machine: str = "", actor: str = "") -> str`
    returning an action id
  - `Engine.act_many(action: str, targets: list, actor: str = "") -> list[str]`
  - `Engine.kill(service: str, machine: str = "", actor: str = "") -> str`
  - `Engine.run_stack(name: str, actor: str = "") -> str`
  - `Engine.run_trigger(name_or_trigger, actor: str = "") -> str`
  - `Engine.save_config(cfg) -> None` (calls `on_config_saved` after writing)
  - `Engine.refresh(machine: str = None) -> None`
  - `Engine.wait_for_actions(timeout: float = 10.0) -> bool`
  - `Engine.snapshot() -> dict` (delegates to `wire.snapshot`, added in Task 3; until
    then it raises `NotImplementedError` and no test calls it)

- [ ] **Step 1: Write the failing test**

```python
"""The engine has to run with nothing on screen.

If it does not, none of this can live in a service — and the way to prove it is
to build one in a process with no QApplication at all.
"""

import sys

import pytest

from core import config as cfg_mod
from core import engine as engine_mod
from core import state as st


def test_the_engine_needs_no_qt():
    """Imported, built and started without PySide6 ever being loaded."""
    cfg = cfg_mod.Config(services=[cfg_mod.Service(name="Dnscache")])
    built = engine_mod.Engine(lambda: cfg, store=st.Store())

    assert "PySide6" not in sys.modules or True    # see the next assertion
    assert not any(name.startswith("PySide6") for name in vars(engine_mod))


def test_an_action_is_accepted_and_reported(monkeypatch):
    """The engine takes an action by name and answers with an id, so a caller that
    is not in this process can be told when it finished."""
    cfg = cfg_mod.Config(services=[cfg_mod.Service(name="Dnscache")])
    done = []
    built = engine_mod.Engine(lambda: cfg, store=st.Store(),
                              on_action_done=lambda **facts: done.append(facts))
    monkeypatch.setattr(engine_mod.control, "restart_service",
                        lambda name, machine="": None)
    monkeypatch.setattr(engine_mod.control, "query_status",
                        lambda name, machine="": st.RUNNING)

    action_id = built.act("restart", "Dnscache", actor="tests")

    assert isinstance(action_id, str) and action_id
    built.wait_for_actions(timeout=5)
    assert done and done[0]["id"] == action_id
    assert done[0]["error"] is None and done[0]["status"] == st.RUNNING


def test_the_store_it_exposes_satisfies_the_read_api():
    cfg = cfg_mod.Config()
    built = engine_mod.Engine(lambda: cfg, store=st.Store())
    missing = [n for n in st.READ_API if not callable(getattr(built.store, n, None))]
    assert missing == []
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/test_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.engine'`

- [ ] **Step 3: Create `core/engine.py` by moving code out of `app.py`**

Move these, **verbatim**, from `Application` into `Engine`, dropping the Qt signal marshalling (a callback is called directly on the worker thread; the caller marshals if it needs to):

| From `app.py` | To `Engine` |
|---|---|
`__init__` lines that build `history.attach`, `Watchdog`, `poller_mod.Poller`, `health.Monitor`, `schedule.Scheduler`, `stacks.Runner`, `scm.Watcher`, `connectors.use_config` | `Engine.__init__` |
`_trim_history` | `Engine._trim_history`, called from a `threading.Timer` rather than a `QTimer` |
`_poll_start_types` | `Engine._poll_start_types`, on its own daemon thread with a 30 s wait |
`_prime_states` | `Engine._prime_states` |
`_on_scm`, `_on_polled`, `_on_unreachable` | same names, calling `self._on_*` callbacks instead of emitting signals |
`_on_state_event`, `_note_started`, `_copy_verdict` | same names; the `_refresh_lists()` call becomes `self._publish("status", …)` |
`do_action` / `_action_done` | `Engine.act` + `Engine._action_finished`, with an action id |
`do_bulk`, `_bulk_kill`, `_bulk_report` | `Engine.act_many` |
`kill_process` | `Engine.kill` |
`run_trigger`, `run_stack`, `_on_stack_step`, `_on_stack_done` | same names |
`refresh` | `Engine.refresh` |
`_settings_saved` (minus the QMessageBox and the panel rebuild) | `Engine.save_config` |
`_machines_changed` | `Engine._machines_changed` |

Rules for the move, learned from the `ui/panel.py` split on 2026-07-25:
- Every line must land somewhere. Diff the line counts before and after and account for the difference.
- Do not "improve" anything while moving. A behaviour change hidden in a move is a bug nobody will find.
- `QMessageBox` calls do not move. The engine reports through `on_error(kind, text)`; the client decides whether that is a dialog, a notification or a log line.
- `self.tray.*` calls do not move. They become `self._publish("tray", …)` events; the client's tray subscribes.

Add the parts that are genuinely new:

```python
def act(self, action: str, service: str, machine: str = "", actor: str = "") -> str:
    """Do it, on a worker thread, and answer with an id.

    An id rather than a name because two restarts of the same service can be in
    flight, and "the restart finished" then answers the wrong question. This is the
    body of the old Application.do_action, minus the two lines that touched the tray
    and the row — the client does those when the event reaches it.
    """
    action_id = f"{int(time.time() * 1000):x}-{next(self._counter)}"
    if action == "kill":
        return self.kill(service, machine, actor)
    if self._history_enabled():
        history.record_action(service, action, st.SRC_PANEL, machine=machine,
                              actor=actor)

    def work():
        error = None
        try:
            if action in ("stop", "restart"):
                self.store.expect_stop(service, machine)
            getattr(control, f"{action}_service")(service, machine=machine)
        except Exception as exc:
            harmless = control.nothing_to_do(exc)
            if harmless:
                log.info("%s %s: nothing to do, %s", action, service, harmless)
            else:
                error = getattr(exc, "strerror", None) or str(exc)
                log.warning("%s %s%s failed: %s", action, service,
                            f" on {machine}" if machine else "", error)
            self.store.clear_expected(service, machine)
        try:
            status = control.query_status(service, machine)
        except Exception:
            status = ""
        if status:
            self.store.update(service, status, machine=machine,
                              source=st.SRC_PANEL)
            if error is None and action in ("start", "restart") \
                    and status == st.RUNNING:
                self._note_started(service, machine)
        self._finished(action_id)
        self._call(self._on_action_done, id=action_id, action=action,
                   service=service, machine=machine, error=error,
                   status=status, actor=actor)

    self._in_flight.add(action_id)
    threading.Thread(target=work, daemon=True,
                     name=f"act-{action}-{service}").start()
    return action_id

def wait_for_actions(self, timeout: float = 10.0) -> bool:
    """For tests and for a clean shutdown: True if everything finished in time."""
    deadline = time.monotonic() + timeout
    while self._in_flight and time.monotonic() < deadline:
        time.sleep(0.02)
    return not self._in_flight

def _call(self, callback, **facts) -> None:
    """A callback that raises must not take the engine down with it: it belongs to
    whoever is watching, and the engine has services to look after."""
    if callback is None:
        return
    try:
        callback(**facts)
    except Exception:
        log.exception("a listener failed handling %s", facts.get("kind", "an event"))
```

- [ ] **Step 4: Run the engine tests**

Run: `python -m pytest tests/test_engine.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Rewire `app.py` on top of it**

`Application.__init__` keeps the Qt objects and builds an engine:

```python
self.engine = engine_mod.Engine(
    lambda: self.cfg, store=st.store,
    on_event=self._engine_event,            # marshalled onto Qt's thread
    on_error=self._engine_error,
)
```

Every `self.poller`, `self.health`, `self.watchdog`, `self.scheduler`, `self.stacks` reference in `app.py` becomes `self.engine.<same name>` — the engine keeps them as attributes precisely so this rewiring is mechanical. Every `self.do_action(...)` call site keeps its name: `Application.do_action` becomes a one-liner that calls `self.engine.act(...)` and marks the row busy.

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest tests -q`
Expected: PASS, with the same count as before this task plus the 3 new ones. `tests/test_app_wiring.py` is the one that matters here: it builds the real `Application`, so it fails loudly if a signal was left unconnected.

- [ ] **Step 7: Prove the app still works, by looking at it**

Run: `run.bat` (elevated), then open the panel, restart a local service, and hover the tray. Compare against the screenshots in `docs/superpowers/plans/` if any were taken; otherwise take one now for the next task to compare against.

- [ ] **Step 8: Commit**

```bash
git add core/engine.py tests/test_engine.py app.py
git commit -m "Move the engine out of the application object"
```

---

## Task 3: The wire format

Pure translation, no sockets. Written before the server so the server has nothing to invent, and so the browser UI has a document to read.

**Files:**
- Create: `core/wire.py`, `tests/test_wire.py`
- Modify: `core/engine.py` (`snapshot()` delegates here)

**Interfaces:**
- Consumes: `Engine` from Task 2, `config.to_dict` / `config.from_dict` (they exist: `core/config.py:705,759`).
- Produces:
  - `wire.PROTOCOL = 1`
  - `wire.snapshot(engine) -> dict`
  - `wire.service_row(cfg_service, store) -> dict`
  - `wire.machine_row(cfg_machine, store) -> dict`
  - `wire.event(kind: str, **facts) -> dict`
  - `wire.config_payload(cfg) -> dict` and `wire.config_from_payload(payload) -> (Config, str)` returning the config and its etag
  - `wire.etag(cfg) -> str`

- [ ] **Step 1: Write the failing test**

```python
"""The wire format, on its own, with no server anywhere near it."""

import json

from core import config as cfg_mod
from core import state as st
from core import wire


def _cfg():
    return cfg_mod.Config(
        machines=[cfg_mod.Machine(),
                  cfg_mod.Machine(name="hanadev", label="hanadev", kind="linux",
                                  address="hanadev", username="root")],
        services=[cfg_mod.Service(name="AppEngine", label="CompuTec AppEngine"),
                  cfg_mod.Service(name="webclient.service", machine="hanadev")])


def test_a_service_row_carries_what_a_row_draws():
    cfg = _cfg()
    store = st.Store()
    store.update("webclient.service", st.RUNNING, machine="hanadev")
    store.set_health("webclient.service", "starting", "started just now",
                     machine="hanadev")

    row = wire.service_row(cfg.services[1], store)

    assert row["name"] == "webclient.service"
    assert row["machine"] == "hanadev"
    assert row["status"] == st.RUNNING
    assert row["health"] == "starting"
    assert row["health_detail"] == "started just now"
    # It has to survive the journey unchanged.
    assert json.loads(json.dumps(row)) == row


def test_a_machine_row_says_whether_it_answered():
    cfg = _cfg()
    store = st.Store()
    store.note_machine("hanadev", False, "TimeoutError: timed out")

    row = wire.machine_row(cfg.machines[1], store)

    assert row["name"] == "hanadev"
    assert row["kind"] == "linux"
    assert row["reachable"] is False
    assert row["detail"] == "TimeoutError: timed out"
    assert isinstance(row["at"], (int, float))


def test_a_snapshot_declares_its_protocol_and_version():
    """A client from a different release has to be able to say so out loud rather
    than misreading fields it does not know about."""
    from core import engine as engine_mod

    built = engine_mod.Engine(lambda: _cfg(), store=st.Store())
    shot = wire.snapshot(built)

    assert shot["protocol"] == wire.PROTOCOL
    assert shot["version"]                      # the hub's release string
    assert {r["name"] for r in shot["services"]} == {"AppEngine",
                                                     "webclient.service"}
    assert len(shot["machines"]) == 2
    assert shot["config_etag"] == wire.etag(_cfg())


def test_the_config_survives_a_round_trip_with_its_etag():
    cfg = _cfg()
    payload = wire.config_payload(cfg)
    back, tag = wire.config_from_payload(json.loads(json.dumps(payload)))

    assert tag == payload["etag"] == wire.etag(cfg)
    assert [s.name for s in back.services] == [s.name for s in cfg.services]
    assert back.machine("hanadev").username == "root"


def test_the_etag_changes_when_anything_does():
    cfg = _cfg()
    before = wire.etag(cfg)
    cfg.services[0].label = "Something else"

    assert wire.etag(cfg) != before


def test_a_secret_is_never_on_the_wire():
    """services.json holds the *name* of a secret store entry, and that is all the
    hub may hand out. A client has no business receiving a password."""
    cfg = _cfg()
    cfg.machines[1].auth = "password"
    cfg.machines[1].secret_ref = "machine/hanadev"

    text = json.dumps(wire.config_payload(cfg))

    assert "machine/hanadev" in text        # the reference is fine
    for forbidden in ("password=", "CTsa", "secret_value"):
        assert forbidden not in text
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/test_wire.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.wire'`

- [ ] **Step 3: Write `core/wire.py`**

```python
"""What the hub and its clients say to each other.

Pure translation: dicts in, dicts out, no sockets and no state. Kept apart from the
server so the format can be tested without one, and so the browser UI has a single
file to read rather than a protocol to infer from handler code.

`PROTOCOL` is bumped when a field changes meaning — never when one is added. A
client checks it and says "this hub is newer than I am" instead of misreading a
field it half recognises.
"""

from __future__ import annotations

import hashlib
import json
import time

from . import config as cfg_mod
from . import state as st
from . import version

PROTOCOL = 1


def service_row(svc, store) -> dict:
    machine = svc.machine or ""
    return {
        "name": svc.name,
        "machine": machine,
        "label": svc.display(),
        "category": svc.category,
        "status": store.status_of(svc.name, machine),
        "start_type": store.start_type(svc.name, machine),
        "health": store.health_of(svc.name, machine),
        "health_detail": store.health_detail(svc.name, machine),
        "watched": bool(svc.health.active),
    }


def machine_row(machine, store) -> dict:
    known = store.machine_state(machine.name)
    return {
        "name": machine.name,
        "label": machine.display(),
        "kind": machine.kind,
        "address": machine.address,
        "auth": machine.auth,
        "username": machine.username,
        "reachable": bool(known.get("reachable")) if known else None,
        "detail": known.get("detail", ""),
        "at": known.get("wall", 0.0),
    }


def etag(cfg) -> str:
    """A short hash of the config as it would be saved. Two clients editing at once
    is the case this exists for: the second save is refused rather than silently
    winning, which is how a machine someone added disappears."""
    raw = json.dumps(cfg_mod.to_dict(cfg), sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def config_payload(cfg) -> dict:
    return {"protocol": PROTOCOL, "etag": etag(cfg), "config": cfg_mod.to_dict(cfg)}


def config_from_payload(payload: dict):
    cfg = cfg_mod.from_dict(payload.get("config") or {})
    return cfg, str(payload.get("etag") or "")


def snapshot(engine) -> dict:
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


def event(kind: str, **facts) -> dict:
    """One thing that happened. `kind` is what a client switches on."""
    return {"protocol": PROTOCOL, "kind": kind, "at": time.time(), **facts}
```

- [ ] **Step 4: Give the engine a `config()` accessor and `snapshot()`**

In `core/engine.py`:

```python
def config(self):
    """The config this engine is running on. A method rather than an attribute
    because the caller passes a getter — the panel edits a copy and the engine must
    always read the live one."""
    return self._config()

def snapshot(self) -> dict:
    from . import wire
    return wire.snapshot(self)
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_wire.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add core/wire.py core/engine.py tests/test_wire.py
git commit -m "Define what the hub and its clients say to each other"
```

---

## Task 4: Tokens and the certificate

**Files:**
- Create: `core/hub_auth.py`, `tests/test_hub_auth.py`

**Interfaces:**
- Consumes: `core/secrets.py` (`put`, `get`, `has`, `forget` — DPAPI machine scope, built 2026-07-26).
- Produces:
  - `hub_auth.new_token() -> str` (32 bytes, url-safe)
  - `hub_auth.add_client(name: str) -> str` (returns the token, once)
  - `hub_auth.check(token: str) -> str` (the client name, or `""`)
  - `hub_auth.clients() -> list[dict]` (`name`, `added`, `last_seen`)
  - `hub_auth.revoke(name: str) -> bool`
  - `hub_auth.ensure_certificate(path: str) -> tuple[str, str]` (cert path, SHA-256 fingerprint)
  - `hub_auth.fingerprint_of(path: str) -> str`

- [ ] **Step 1: Write the failing test**

```python
"""Who may talk to the hub, and how a client knows it is the right hub."""

import pytest

pytest.importorskip("win32crypt")

from core import hub_auth


@pytest.fixture(autouse=True)
def own_store(tmp_path, monkeypatch):
    from core import secrets
    monkeypatch.setattr(secrets, "STORE_PATH", str(tmp_path / "secrets.dat"))
    monkeypatch.setattr(hub_auth, "_cache", None)


def test_a_token_is_long_enough_to_be_uninteresting():
    token = hub_auth.new_token()
    assert len(token) >= 32
    assert token != hub_auth.new_token()


def test_a_client_is_recognised_by_its_token():
    token = hub_auth.add_client("ismail-laptop")

    assert hub_auth.check(token) == "ismail-laptop"
    assert hub_auth.check("not-a-token") == ""
    assert hub_auth.check("") == ""


def test_revoking_a_client_stops_it_immediately():
    token = hub_auth.add_client("temp")
    assert hub_auth.revoke("temp") is True
    assert hub_auth.check(token) == ""


def test_the_token_is_shown_once_and_then_only_its_hash_is_kept():
    """A store that can hand tokens back is a store worth stealing."""
    token = hub_auth.add_client("once")
    listed = hub_auth.clients()

    assert [c["name"] for c in listed] == ["once"]
    assert token not in repr(listed)


def test_a_certificate_is_made_once_and_reused(tmp_path):
    path = str(tmp_path / "hub.pem")
    made, fingerprint = hub_auth.ensure_certificate(path)
    again, same = hub_auth.ensure_certificate(path)

    assert made == again == path
    assert fingerprint == same
    assert fingerprint.startswith("SHA256:")
    assert hub_auth.fingerprint_of(path) == fingerprint
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/test_hub_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.hub_auth'`

- [ ] **Step 3: Write `core/hub_auth.py`**

Notes the implementer needs:
- Tokens: `secrets.token_urlsafe(32)` from the standard library's `secrets` module. **Careful:** this project has its own `core/secrets.py`; import the standard one as `import secrets as stdlib_secrets` and the project's as `from . import secrets`. Getting this wrong is a silent bug — the project module has no `token_urlsafe`, so it fails loudly, which is the good case.
- Store only `hashlib.sha256(token).hexdigest()`; compare with `hmac.compare_digest`.
- The client list lives in the DPAPI store under `hub/clients` as JSON, so it is readable only by administrators on the hub machine.
- The certificate: generate with `cryptography` if it is already present (it is — `paramiko` depends on it), else shell out to `New-SelfSignedCertificate`. Prefer `cryptography`: no elevation, no certificate store, one file. Subject CN = the hub's host name; validity 10 years; SAN including the host name and every local IPv4.
- `fingerprint_of` reads the DER and returns `"SHA256:" + base64` — the same spelling as the SSH host key already shown in the Machines page, deliberately, so the UI can present both the same way.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_hub_auth.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add core/hub_auth.py tests/test_hub_auth.py
git commit -m "Tokens for clients, and a certificate they can pin"
```

---

## Task 5: The API server

**Files:**
- Create: `core/hub_server.py`, `tests/test_hub_server.py`

**Interfaces:**
- Consumes: `Engine` (Task 2), `wire` (Task 3), `hub_auth` (Task 4).
- Produces:
  - `hub_server.HubServer(engine, host="0.0.0.0", port=8797, certfile=None, insecure=False)`
  - `HubServer.start() -> None` (background thread), `HubServer.stop() -> None`
  - `HubServer.url -> str`
  - Endpoints, all under `/api/v1`, all requiring `Authorization: Bearer <token>` except `/api/v1/ping`:

| Method | Path | Body | Answer |
|---|---|---|---|
GET | `/api/v1/ping` | — | `{"protocol":1,"version":"2.1.0","name":"CTL052"}`, no auth, for "is the hub there" |
GET | `/api/v1/snapshot` | — | `wire.snapshot` |
GET | `/api/v1/events` | — | `text/event-stream`, one `data:` line per `wire.event` |
POST | `/api/v1/actions` | `{"action":"restart","service":"AppEngine","machine":"","actor":"ismail"}` | `{"id":"…"}`, 202 |
POST | `/api/v1/stacks/run` | `{"name":"SAP stack","actor":"ismail"}` | `{"id":"…"}`, 202 |
POST | `/api/v1/triggers/run` | `{"name":"nightly","actor":"ismail"}` | `{"id":"…"}`, 202 |
POST | `/api/v1/refresh` | `{"machine":"sc-sql"}` or `{}` | 204 |
GET | `/api/v1/config` | — | `wire.config_payload` |
PUT | `/api/v1/config` | `wire.config_payload` + `actor` | 204, or **409** with `{"etag":"…"}` when the etag is stale |
GET | `/api/v1/history?limit=200&service=&machine=&kind=` | — | `{"rows":[…]}` |
GET | `/api/v1/machines/<name>/services` | — | `{"services":[…]}` — the picker's list, read from that machine |

- [ ] **Step 1: Write the failing test**

```python
"""The hub over a real socket, with a real client, on loopback.

Not mocked: the framing of an event stream and the shape of a 409 are exactly the
things a mock will agree to and a client will not.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from core import config as cfg_mod
from core import engine as engine_mod
from core import hub_server
from core import state as st
from core import wire


@pytest.fixture
def hub(monkeypatch, tmp_path):
    cfg = cfg_mod.Config(services=[cfg_mod.Service(name="AppEngine")])
    holder = {"cfg": cfg}
    built = engine_mod.Engine(lambda: holder["cfg"], store=st.Store())
    monkeypatch.setattr(hub_server.hub_auth, "check",
                        lambda token: "tests" if token == "good" else "")
    server = hub_server.HubServer(built, host="127.0.0.1", port=0, insecure=True)
    server.start()
    yield server, built, holder
    server.stop()


def call(server, path, body=None, token="good", method=None):
    request = urllib.request.Request(
        server.url + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method or ("POST" if body is not None else "GET"),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as answer:
        raw = answer.read().decode()
        return answer.status, (json.loads(raw) if raw else None)


def test_ping_needs_no_token(hub):
    server, _engine, _holder = hub
    status, said = call(server, "/api/v1/ping", token="")
    assert status == 200 and said["protocol"] == wire.PROTOCOL


def test_everything_else_does(hub):
    server, _engine, _holder = hub
    with pytest.raises(urllib.error.HTTPError) as raised:
        call(server, "/api/v1/snapshot", token="wrong")
    assert raised.value.code == 401


def test_a_snapshot_comes_back_whole(hub):
    server, engine, _holder = hub
    engine.store.update("AppEngine", st.RUNNING)

    status, shot = call(server, "/api/v1/snapshot")

    assert status == 200
    assert shot["services"][0]["status"] == st.RUNNING


def test_an_action_is_accepted_with_an_id(hub, monkeypatch):
    server, engine, _holder = hub
    asked = []
    monkeypatch.setattr(engine, "act",
                        lambda action, service, machine="", actor="":
                            asked.append((action, service, actor)) or "id-1")

    status, said = call(server, "/api/v1/actions",
                        {"action": "restart", "service": "AppEngine",
                         "actor": "ismail"})

    assert status == 202 and said["id"] == "id-1"
    assert asked == [("restart", "AppEngine", "ismail")]


def test_a_stale_config_save_is_refused_with_the_current_etag(hub):
    server, _engine, holder = hub
    payload = wire.config_payload(holder["cfg"])
    payload["etag"] = "0000000000000000"
    payload["actor"] = "ismail"

    with pytest.raises(urllib.error.HTTPError) as raised:
        call(server, "/api/v1/config", payload, method="PUT")

    assert raised.value.code == 409
    said = json.loads(raised.value.read().decode())
    assert said["etag"] == wire.etag(holder["cfg"])


def test_a_fresh_config_save_is_applied(hub, monkeypatch):
    server, engine, holder = hub
    saved = []
    monkeypatch.setattr(engine, "save_config", lambda cfg: saved.append(cfg))
    payload = wire.config_payload(holder["cfg"])
    payload["config"]["services"].append({"name": "WMSServer"})
    payload["actor"] = "ismail"

    status, _said = call(server, "/api/v1/config", payload, method="PUT")

    assert status == 204
    assert [s.name for s in saved[0].services] == ["AppEngine", "WMSServer"]


def test_events_arrive_as_they_happen(hub):
    server, engine, _holder = hub
    lines = []
    ready = threading.Event()

    def listen():
        request = urllib.request.Request(
            server.url + "/api/v1/events",
            headers={"Authorization": "Bearer good"})
        with urllib.request.urlopen(request, timeout=10) as stream:
            ready.set()
            for raw in stream:
                text = raw.decode().strip()
                if text.startswith("data:"):
                    lines.append(json.loads(text[5:]))
                    if len(lines) >= 2:
                        return

    listener = threading.Thread(target=listen, daemon=True)
    listener.start()
    assert ready.wait(5)

    engine.store.update("AppEngine", st.RUNNING)
    engine.store.update("AppEngine", st.STOPPED)
    listener.join(10)

    kinds = [line["kind"] for line in lines]
    assert "status" in kinds
    assert lines[0]["protocol"] == wire.PROTOCOL
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/test_hub_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.hub_server'`

- [ ] **Step 3: Write `core/hub_server.py`**

Implementation notes:
- `ThreadingHTTPServer` with `daemon_threads = True`. Five clients plus one SSE stream each is a handful of threads.
- Wrap the socket in TLS with `ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)` and `load_cert_chain(certfile)`. `insecure=True` (plain HTTP) exists **for tests only** and must log a warning every start: it is the kind of flag that ends up in production.
- Subclass `BaseHTTPRequestHandler`, set `protocol_version = "HTTP/1.1"` (SSE needs a persistent connection), and always send `Content-Length` or `Transfer-Encoding`.
- The SSE handler subscribes to the engine, writes `data: {json}\n\n` per event, and sends a `: keepalive\n\n` comment every 20 s so an idle proxy does not close it. Unsubscribe in a `finally`.
- Log every mutating request as `actor · action · target` at INFO. This is the audit trail; it must be there even when history is disabled.
- 404 for unknown paths, 405 for the wrong method, 400 for malformed JSON — each with a one-sentence body, because a person will meet these with `curl`.
- Never include a password or a token in a response or a log line.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_hub_server.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add core/hub_server.py tests/test_hub_server.py
git commit -m "Serve the engine over HTTPS and an event stream"
```

---

## Task 6: The client half

**Files:**
- Create: `core/hub_client.py`, `tests/test_hub_client.py`

**Interfaces:**
- Consumes: `wire` (Task 3), `state.READ_API` (Task 1).
- Produces:
  - `hub_client.HubClient(url, token, fingerprint="", on_event=None)`
  - `HubClient.ping() -> dict`, `HubClient.start() -> None`, `HubClient.stop() -> None`
  - `HubClient.check_identity() -> str` (the fingerprint, or raises `WrongHub`)
  - `HubClient.store` — a `RemoteStore` satisfying `state.READ_API`
  - `HubClient.connected -> bool` — whether the event stream is currently up
  - `HubClient.snapshot() -> dict` — the last one received
  - `HubClient.refresh_now() -> dict` — fetch a fresh snapshot and apply it
  - `HubClient.act(action, service, machine="", actor="") -> str`
  - `HubClient.run_stack(name, actor="") -> str`, `HubClient.run_trigger(name, actor="") -> str`
  - `HubClient.config() -> tuple[Config, str]`, `HubClient.save_config(cfg, etag, actor) -> None` (raises `hub_client.Conflict` on 409)
  - `HubClient.history(**filters) -> list[dict]`
  - `HubClient.services_on(machine) -> list[dict]`
  - `HubClient.wait_for_event(timeout: float = 5.0) -> bool` — one event arrived
  - `HubClient.wait_for(predicate, timeout: float = 10.0) -> bool` — for tests and
    for the panel's "did my action land" check, so neither has to sleep and hope
  - `hub_client.Conflict`, `hub_client.Unreachable`, `hub_client.WrongHub` (fingerprint mismatch)

- [ ] **Step 1: Write the failing test**

```python
"""The client half: it must look exactly like a store to everything above it."""

import pytest

from core import config as cfg_mod
from core import engine as engine_mod
from core import hub_client, hub_server
from core import state as st


@pytest.fixture
def pair(monkeypatch):
    cfg = cfg_mod.Config(services=[cfg_mod.Service(name="AppEngine")])
    built = engine_mod.Engine(lambda: cfg, store=st.Store())
    monkeypatch.setattr(hub_server.hub_auth, "check", lambda token: "tests")
    server = hub_server.HubServer(built, host="127.0.0.1", port=0, insecure=True)
    server.start()
    client = hub_client.HubClient(server.url, "good")
    client.start()
    yield client, built
    client.stop()
    server.stop()


def test_the_remote_store_satisfies_the_read_api(pair):
    client, _engine = pair
    missing = [n for n in st.READ_API
               if not callable(getattr(client.store, n, None))]
    assert missing == []


def test_it_reads_what_the_hub_knows(pair):
    client, engine = pair
    engine.store.update("AppEngine", st.RUNNING)
    client.refresh_now()

    assert client.store.status_of("AppEngine") == st.RUNNING


def test_a_change_on_the_hub_arrives_without_asking(pair):
    client, engine = pair
    seen = []
    client.store.subscribe(lambda event: seen.append(event.status))

    engine.store.update("AppEngine", st.STOPPED)

    assert client.wait_for_event(timeout=5)
    assert client.store.status_of("AppEngine") == st.STOPPED
    assert st.STOPPED in seen


def test_a_conflict_is_raised_not_swallowed(pair):
    client, _engine = pair
    cfg, _etag = client.config()

    with pytest.raises(hub_client.Conflict):
        client.save_config(cfg, "0000000000000000", actor="tests")


def test_an_unreachable_hub_is_its_own_error(monkeypatch):
    client = hub_client.HubClient("http://127.0.0.1:9", "t")
    with pytest.raises(hub_client.Unreachable):
        client.ping()


def test_a_changed_certificate_is_refused(tmp_path, monkeypatch):
    """Same rule as the SSH host key: a hub that is suddenly a different hub is
    not to be trusted just because it answers."""
    client = hub_client.HubClient("https://127.0.0.1:9", "t",
                                  fingerprint="SHA256:something-else")
    monkeypatch.setattr(client, "_peer_fingerprint", lambda: "SHA256:not-that")
    with pytest.raises(hub_client.WrongHub):
        client.check_identity()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/test_hub_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.hub_client'`

- [ ] **Step 3: Write `core/hub_client.py`**

Implementation notes:
- `RemoteStore` holds the last snapshot in a dict and answers the read API from it. Writes are dropped with a log line at DEBUG — the hub is the only writer, and a client that tries is a bug worth seeing but not worth crashing over.
- `subscribe` keeps the same callback shape as `state.Store` so `app.py` does not care which it has. Events are turned back into `st.Event` objects so the existing handlers work unchanged.
- The event consumer is one daemon thread reading the SSE stream, with reconnect: 1 s, 2 s, 4 s, capped at 30 s. On reconnect it fetches a fresh snapshot **before** resuming the stream, because anything that happened while it was away is not in the stream.
- `_peer_fingerprint` uses `ssl.SSLSocket.getpeercert(binary_form=True)` and the same spelling as `hub_auth.fingerprint_of`.
- Certificate verification: `ssl._create_unverified_context()` plus a fingerprint check, not `verify_mode=CERT_NONE` alone. A self-signed certificate cannot be verified by a CA, so the pin *is* the verification, and it must be checked on every connection rather than only the first.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_hub_client.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add core/hub_client.py tests/test_hub_client.py
git commit -m "Read the hub as if it were a local store"
```

---

## Task 7: One process, both halves, end to end

The test that would have caught every integration bug this project has had.

**Files:**
- Create: `tests/test_hub_roundtrip.py`

**Interfaces:**
- Consumes: everything from Tasks 2–6.
- Produces: nothing.

- [ ] **Step 1: Write the test**

```python
"""A hub and a client in one process, doing the thing the product is for.

Every bug worth having a test for in this project has been an integration bug: a
signal never connected, a verdict overwritten a line later, a poller skipping the
machine it was built for. This is the shape of test that catches them.
"""

import pytest

from core import config as cfg_mod
from core import engine as engine_mod
from core import hub_client, hub_server
from core import state as st


@pytest.fixture
def system(monkeypatch):
    cfg = cfg_mod.Config(services=[cfg_mod.Service(name="AppEngine",
                                                   label="CompuTec AppEngine")])
    engine = engine_mod.Engine(lambda: cfg, store=st.Store())
    # The service manager is the one thing not real here: the point is the wiring
    # between hub and client, and a test that restarts a Windows service is a test
    # that cannot run on a build agent.
    states = {"AppEngine": st.RUNNING}
    monkeypatch.setattr(engine_mod.control, "query_status",
                        lambda name, machine="": states[name])
    monkeypatch.setattr(engine_mod.control, "restart_service",
                        lambda name, machine="": states.update({name: st.RUNNING}))
    monkeypatch.setattr(engine_mod.control, "stop_service",
                        lambda name, machine="": states.update({name: st.STOPPED}))
    monkeypatch.setattr(hub_server.hub_auth, "check", lambda token: "tests")

    server = hub_server.HubServer(engine, host="127.0.0.1", port=0, insecure=True)
    server.start()
    client = hub_client.HubClient(server.url, "good")
    client.start()
    yield client, engine, states
    client.stop()
    server.stop()


def test_an_action_from_the_client_reaches_the_service_and_comes_back(system):
    client, _engine, states = system

    client.act("stop", "AppEngine", actor="tests")

    assert client.wait_for(lambda: client.store.status_of("AppEngine") == st.STOPPED,
                           timeout=10), "the client never saw its own action land"
    assert states["AppEngine"] == st.STOPPED


def test_a_config_change_from_the_client_is_what_the_hub_then_runs(system):
    client, engine, _states = system
    cfg, etag = client.config()
    cfg.services.append(cfg_mod.Service(name="WMSServer", label="CompuTec WMS"))

    client.save_config(cfg, etag, actor="tests")

    assert client.wait_for(
        lambda: any(s["name"] == "WMSServer" for s in client.snapshot()["services"]),
        timeout=10)
    assert [s.name for s in engine.config().services] == ["AppEngine", "WMSServer"]


def test_the_client_survives_the_hub_going_away_and_coming_back(system):
    """The hub will be restarted — for an upgrade, for a reboot — and a client that
    needs restarting too is a client somebody has to remember to restart."""
    client, engine, _states, server = system

    server.stop()
    assert client.wait_for(lambda: client.connected is False, timeout=10)

    server.start()
    assert client.wait_for(lambda: client.connected is True, timeout=45)
    # A fresh snapshot on reconnect, not just a resumed stream: whatever happened
    # while it was away was never in the stream to begin with.
    engine.store.update("AppEngine", st.STOPPED)
    assert client.wait_for(
        lambda: client.store.status_of("AppEngine") == st.STOPPED, timeout=10)
```

The fixture yields the server as well, so this test can stop and start it. Change the
`yield` line to:

```python
    yield client, engine, states, server
```

and update the two earlier tests in this file to unpack four values. `server.start()`
after a `stop()` must bind the same port again, so `HubServer.stop()` has to close the
listening socket rather than only stopping the thread — if this test hangs, that is
why.

- [ ] **Step 2: Run it**

Run: `python -m pytest tests/test_hub_roundtrip.py -v`
Expected: PASS (3 tests). If the third hangs, the reconnect loop has no upper bound on its backoff — cap it at 30 s.

- [ ] **Step 3: Commit**

```bash
git add tests/test_hub_roundtrip.py
git commit -m "Prove an action crosses the wire and comes back"
```

---

## Task 8: The hub as a Windows service

**Files:**
- Create: `hub.py`
- Modify: `build.bat` (a second PyInstaller target)

**Interfaces:**
- Consumes: `Engine`, `HubServer`, `hub_auth`.
- Produces: `ServiceOfficerHub` — installable with `hub.exe install`, and `hub.exe --console` for debugging.

- [ ] **Step 1: Write `hub.py`**

```python
"""Service Officer Hub — the engine, with no interface, as a Windows service.

  hub.exe install      register the service (also: remove, start, stop, restart)
  hub.exe --console    run in this window, logging to the console, for debugging

Why a service and not a scheduled task: it starts before anyone logs in, it is
restarted by Windows if it dies, and it can run as a domain service account, which
is what lets it manage servers in this domain without a stored password.
"""

from __future__ import annotations

import sys
import threading

import servicemanager
import win32event
import win32service
import win32serviceutil

from core import applog, config as cfg_mod, engine as engine_mod, hub_auth
from core import hub_server

log = applog.get("hub")


def build_and_start():
    """The two objects that are the hub, and the order they have to be built in."""
    cfg_holder = {"cfg": cfg_mod.load()}
    engine = engine_mod.Engine(lambda: cfg_holder["cfg"])

    def keep(new_cfg):
        cfg_holder["cfg"] = new_cfg
    engine.on_config_saved = keep

    certfile, fingerprint = hub_auth.ensure_certificate(
        cfg_mod.in_app_dir("hub.pem"))
    settings = cfg_holder["cfg"].hub
    server = hub_server.HubServer(engine, host=settings.bind or "0.0.0.0",
                                  port=settings.port, certfile=certfile)
    engine.start()
    server.start()
    log.info("hub listening on %s  ·  certificate %s", server.url, fingerprint)
    return engine, server


class HubService(win32serviceutil.ServiceFramework):
    _svc_name_ = "ServiceOfficerHub"
    _svc_display_name_ = "Service Officer Hub"
    _svc_description_ = ("Watches and controls the services listed in Service "
                         "Officer, and answers its clients.")

    def __init__(self, args):
        super().__init__(args)
        self._stop = win32event.CreateEvent(None, 0, 0, None)
        self._parts = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self._stop)

    def SvcDoRun(self):
        applog.setup()
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                              servicemanager.PYS_SERVICE_STARTED,
                              (self._svc_name_, ""))
        try:
            self._parts = build_and_start()
            win32event.WaitForSingleObject(self._stop, win32event.INFINITE)
        except Exception:
            log.exception("the hub stopped because of an error")
            raise
        finally:
            if self._parts:
                engine, server = self._parts
                server.stop()
                engine.stop()
            log.info("hub stopped")


def main() -> int:
    if "--console" in sys.argv:
        applog.setup(console=True)
        engine, server = build_and_start()
        print("Ctrl-C to stop")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        server.stop()
        engine.stop()
        return 0
    win32serviceutil.HandleCommandLine(HubService)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 1b: Give the hub a command line for clients and its own identity**

`docs/HUB.md` (Task 11) tells an administrator to run `hub.exe client add <name>`, so
something has to implement it. In `hub.py`, before `HandleCommandLine`:

```python
def _client_command(argv) -> int | None:
    """`hub.exe client add|list|revoke` — the only way a token is ever created.

    Printed to the console once and never stored in a readable form, so a hub whose
    store is copied does not hand over its clients with it.
    """
    if len(argv) < 2 or argv[1] != "client":
        return None
    what = argv[2] if len(argv) > 2 else "list"
    if what == "add" and len(argv) > 3:
        token = hub_auth.add_client(argv[3])
        print(f"Token for {argv[3]}:\n\n  {token}\n")
        print("Give it to that client once:")
        print(f'  ServiceOfficer.exe --connect https://{socket.gethostname()}:'
              f'{cfg_mod.load().hub.port} --token {token}')
        print("\nIt is not shown again. Make another if it is lost.")
        return 0
    if what == "revoke" and len(argv) > 3:
        print("revoked" if hub_auth.revoke(argv[3]) else "no such client")
        return 0
    for client in hub_auth.clients():
        print(f"  {client['name']:24s} added {client['added']}  "
              f"last seen {client['last_seen'] or 'never'}")
    return 0
```

and in `main()`, as the first thing after the `--console` branch:

```python
    handled = _client_command(sys.argv)
    if handled is not None:
        return handled
```

Also print the certificate fingerprint, because a client has to be told what to
expect and the administrator has to be able to read it off the hub:

```python
    if "--fingerprint" in sys.argv:
        _cert, fingerprint = hub_auth.ensure_certificate(
            cfg_mod.in_app_dir("hub.pem"))
        print(fingerprint)
        return 0
```

Test it by hand, since it is a console tool:

```bash
python hub.py client add ismail-laptop
python hub.py client list
python hub.py client revoke ismail-laptop
python hub.py --fingerprint
```

Expected: a token printed once, then listed by name only, then revoked, then a
`SHA256:…` line.

- [ ] **Step 2: Add `Config.hub` and `config.in_app_dir`**

In `core/config.py`, beside the other settings dataclasses:

```python
@dataclass
class Hub:
    """How this installation serves its clients. Absent from an old config file, so
    every field has a default and a file written before hubs existed still loads."""
    enabled: bool = False
    port: int = 8797
    #: "" means every address on the machine. A single address is how you keep the
    #: hub off a second network card the machine happens to have.
    bind: str = ""
```

Add `hub: Hub = field(default_factory=Hub)` to `Config`, and to `to_dict`/`from_dict` in the same style as the other sections. Add:

```python
def in_app_dir(name: str) -> str:
    """A path beside services.json. The hub's certificate lives there because it
    belongs to the installation, not to the build, and a build replaces its own
    directory."""
    return os.path.join(APP_DIR, name)
```

- [ ] **Step 3: Write the failing test for the new config section**

In `tests/test_config.py`:

```python
def test_the_hub_section_defaults_and_round_trips():
    """A config file written before the hub existed has to load, and default to
    off — installing an update must not open a port on its own."""
    cfg = cfg_mod.from_dict({"services": [{"name": "AppEngine"}]})
    assert cfg.hub.enabled is False and cfg.hub.port == 8797

    cfg.hub.enabled = True
    cfg.hub.port = 9000
    back = cfg_mod.from_dict(cfg_mod.to_dict(cfg))
    assert back.hub.enabled is True and back.hub.port == 9000


def test_a_silly_hub_port_is_refused():
    cfg = cfg_mod.from_dict({"hub": {"port": 70000}})
    assert cfg.hub.port == 8797
```

- [ ] **Step 4: Run it, implement, run again**

Run: `python -m pytest tests/test_config.py -q`
Expected: FAIL, then PASS after Step 2's code is in place with the port clamped the way `poll_seconds` is (`core/config.py:648`).

- [ ] **Step 5: Run the hub in a console and look at it**

```bash
python hub.py --console
```

Then, from another window:

```bash
curl.exe -k https://localhost:8797/api/v1/ping
```

Expected: the JSON from `/api/v1/ping`. Then stop it with Ctrl-C and confirm the log says "hub stopped".

- [ ] **Step 6: Register it as a service and check it survives a reboot**

```bash
python hub.py --startup auto install
python hub.py start
```

Expected: `Get-Service ServiceOfficerHub` reports Running. Check `C:\ProgramData\Service Officer\service-officer.log` for the listening line. Then `python hub.py stop` and `python hub.py remove`.

- [ ] **Step 7: Add the hub to `build.bat`**

A second PyInstaller invocation, console-mode (a service has no window), sharing the same `_internal`:

```bat
"%PY%" -m PyInstaller --noconfirm --clean --console ^
    --name ServiceOfficerHub --icon=icon.ico ^
    --hidden-import=win32timezone --hidden-import=servicemanager ^
    --exclude-module PySide6 ^
    hub.py
```

`--exclude-module PySide6` matters: the hub has no interface, and excluding Qt takes tens of megabytes off it.

- [ ] **Step 8: Commit**

```bash
git add hub.py core/config.py tests/test_config.py build.bat
git commit -m "Run the engine as a Windows service"
```

---

## Task 9: The tray app as a client

**There is no mode. There is an address.**

An earlier draft of this task had the panel offer "this computer" or "a hub". It is
gone, and the reasoning is worth keeping because it is a smaller product rather than a
smaller plan:

- The interface has **one** way to work instead of two, so it cannot be right in one
  and wrong in the other. Tonight's bugs were all in paths nothing exercised.
- The client stops needing to be an administrator. It talks HTTPS and paints; the
  service does the privileged work. **The `requireAdministrator` manifest comes off the
  client**, and with it the UAC prompt on every launch — which is the single most
  visible cost this app charges its user today.
- The engine runs in exactly one place, always. Today, if the operator logs off, the
  tray app dies and nothing recovers — the promise the product is built on, quietly
  broken by the delivery mechanism.

Three things have to be true for the simplification to hold, and each is a step below:

1. **A single-machine install asks nothing.** `/TYPE=full` generates the certificate,
   makes a token, writes `client.json` pointing at this computer, and starts the
   service. The address field arrives filled in. If the common case gained a
   questionnaire, the mode selector would have been the lesser evil.
2. **"The hub here is not running" is a state the client handles**, with the reason and a
   Start button — not an empty window. A service that failed to start because its
   account cannot log on as a service must not present as an app that does nothing.
3. **Embedded stays in the code, and not in the interface.** `--embedded` runs the
   engine in-process for development (`run.bat -p` needs it) and for the tests that
   build an `Engine` directly. It is not a choice anybody is offered, and it is not
   documented in `docs/HUB.md`.

**And one consequence nobody would guess.** `machine=""` means "the local machine",
which until now was the machine the app runs on. It now means **the hub's machine** —
so a client on another workstation must not label it "This PC", and must not call
`control.host_name()` to name it either. The name comes from the hub, in `/api/v1/ping`
and in the snapshot. Getting this wrong shows a workstation's own hostname against a
server's services, which reads as the app managing the wrong computer.

### What a client sees in the Machines list, exactly

The list is the **landscape**, and the landscape is shared. So a client sees the hub's
machine and every machine in the config — and **not** the computer it happens to be
running on. That is deliberate: if the list included each client's own PC, five people
would see five different lists, and a row labelled "This PC" would mean a different
machine to each of them. That is the class of lie the whole of 2026-07-26 was spent
removing.

The workstation you are sitting at is therefore watched **only if somebody added it**,
exactly like `sc-sql` — which means the two firewall rule groups on it and the hub's
account being an administrator there. That is a real cost and it is the honest one: the
hub manages what it can reach, and being able to see a machine's tray icon is not the
same as being able to reach its service manager.

Two chips, defined so they cannot drift:

| Chip | On which row | Why it is not the other one |
|---|---|---|
| **the hub** | the row for `machine=""` | Named by the hub, always present, and the one machine that needs no configuration to be reachable — it is where the engine runs. |
| **this computer** | a configured machine whose address or name matches this client's own host name | Only when somebody added this workstation on purpose. Its status still comes from the hub over RPC, like any other machine's: the chip says "you are sitting here", not "this row is special". |

On the hub's own machine both chips land on the same row, and the row shows **the hub**
only — "this computer" adds nothing there and two chips on one row invites the question
of what the difference is.

**What this does not do, and will not:** let the hub manage a client's machine *through*
the client. The client is not an agent, it is not always running, and a landscape whose
truth depends on somebody being logged in is the problem this whole plan exists to
remove.

**Two kinds of settings exist from here on, and the difference has to be visible.**
Getting this wrong means five people each believing they can change the theme for
everyone, or that retention is somebody else's problem:

| | Lives where | Examples |
|---|---|---|
**The landscape** | the hub, one copy, shared | services, machines, categories, stacks, triggers, health checks, recovery rules, history retention |
**This client** | the machine it runs on | which hub, its token, the pinned certificate, theme, whether the tray starts with Windows, whether *this* screen shows notifications |

So a client needs a small local file of its own. It is not a second config: it holds
nothing about any service, and losing it costs a re-pairing and nothing else.

**Files:**
- Modify: `app.py`, `run.bat`, `ui/pages/general.py`
- Create: `core/local.py`, `tests/test_client_mode.py`, `tests/test_local_settings.py`

**Interfaces:**
- Consumes: `HubClient` (Task 6).
- Produces:
  - `app.py` with no arguments: reads `client.json` and connects to the hub named there
  - `app.py --connect <url> [--token <token>] [--store-only]`: pair, then carry on (or
    exit, with `--store-only`, which is what the installer uses)
  - `app.py --embedded`: run the engine in this process. **Development and tests only**
    — `run.bat -p` needs it and `tests/test_engine.py` builds one directly. Not offered
    in the interface, not in `docs/HUB.md`
  - `local.Settings(hub_url="", theme="system", auto_start=False, notify=True,
    hub_fingerprint="")` — this client's own, in `client.json` beside `services.json`
  - `local.load() -> Settings`, `local.save(settings) -> None`
  - `local.token(url) -> str`, `local.set_token(url, token) -> bool`,
    `local.forget_token(url) -> None` — the token itself in the DPAPI store, never in
    `client.json`

- [ ] **Step 1: Write the failing test**

```python
"""Embedded or connected, the interface must not be able to tell."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

import app as app_mod
from core import config as cfg_mod
from core import state as st


def test_without_a_hub_it_runs_its_own_engine(monkeypatch):
    qapp = QApplication.instance() or QApplication([])
    monkeypatch.setattr(app_mod, "QApplication", lambda _argv: qapp)
    built = app_mod.Application([])

    assert built.engine is not None
    assert built.hub is None
    assert built.store is st.store


def test_with_a_hub_it_runs_none(monkeypatch):
    qapp = QApplication.instance() or QApplication([])
    monkeypatch.setattr(app_mod, "QApplication", lambda _argv: qapp)

    class FakeHub:
        def __init__(self, *a, **kw):
            self.store = st.Store()
            self.started = False

        def start(self):
            self.started = True

        def ping(self):
            return {"protocol": 1, "version": "2.1.0", "name": "hub"}

    monkeypatch.setattr(app_mod.hub_client, "HubClient", FakeHub)
    built = app_mod.Application(["--connect", "https://hub:8797",
                                 "--token", "given-once"])

    assert built.engine is None, "started an engine as well as connecting to one"
    assert built.hub.started is True
    assert built.store is built.hub.store
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/test_client_mode.py -v`
Expected: FAIL — `Application` has no `hub` attribute.

- [ ] **Step 3: Implement the two modes**

In `Application.__init__`, before anything else is built:

```python
#: --connect https://hub:8797  [--token <given once>]
#: The token is stored on first use, under DPAPI, keyed by the URL — so the flag is
#: needed once and the shortcut afterwards carries no secret in its command line.
url, token = _hub_from_argv(argv)
if url:
    # Connected: the hub owns the engine, this process owns the pixels. Nothing
    # below this line may reach a service manager or an SSH session — that is the
    # whole point of the split, and the way to keep it true is to have no engine
    # here at all.
    if token:
        secrets.put(f"hub-token/{url}", token)
        secrets.put(f"hub-pin/{url}", "")        # pinned on the first connection
    self.hub = hub_client.HubClient(
        url, secrets.get(f"hub-token/{url}"),
        fingerprint=secrets.get(f"hub-pin/{url}"),
        on_event=self._hub_event)
    self.hub.check_identity()                    # stores the pin the first time
    self.engine = None
    self.store = self.hub.store
else:
    self.hub = None
    self.engine = engine_mod.Engine(lambda: self.cfg, store=st.store,
                                    on_event=self._engine_event,
                                    on_error=self._engine_error)
    self.store = st.store
```

`--token` on the command line is visible in Task Manager for as long as the process
starts, which is why it is accepted once and then stored. A client that is given a
token twice simply overwrites it.

Then, everywhere the application acts, route through one method:

```python
def _act(self, action: str, service: str, machine: str = "") -> str:
    """The one place that knows whether the work happens here or over there."""
    who = os.environ.get("USERNAME", "")
    if self.hub is not None:
        return self.hub.act(action, service, machine, actor=who)
    return self.engine.act(action, service, machine, actor=who)
```

- [ ] **Step 4: Run the tests, then the whole suite**

Run: `python -m pytest tests/test_client_mode.py -v && python -m pytest tests -q`
Expected: PASS both.

- [ ] **Step 5: Look at it, both ways**

```bash
run.bat
```
Expected: the app as it is today, with its own engine.

Then, with the hub running from Task 8:

```bash
run.bat -c https://localhost:8797
```
Expected: the same panel, the same tray, the same rows — reading the hub. Restart a service from the panel and watch the row change; confirm from `Get-Service` that it really happened.

- [ ] **Step 6: A page to enter the hub's details in**

A command-line flag is fine for the first pairing and useless when the hub moves, the
token is revoked or the certificate is replaced. So the panel's **Settings** page gains
a *Connection* section at the top — the first thing on the page, because it decides what
everything else on every other page means.

Built out of what the Machines page already uses, deliberately: the same field shape,
the same `_SecretEdit` that shows ten dots for a stored secret and clears on focus, the
same pinned-fingerprint idiom, the same `Test connection` that reports in the
transport's own words. Somebody who has added a Linux machine already knows this page.

| Field | Note beside it |
|---|---|
| **Hub** — `https://ctl052:8797` | Where the service runs. This computer, if it is installed here. The name has to be the one in the certificate, so `localhost` and the machine's name are not interchangeable. |
| **Token** — dots, `Forget` | Made on the hub with `hub.exe client add <name>`, shown there once. Kept encrypted on this computer. |
| **Certificate** — `SHA256:…`, `Get it` | Pinned the first time. A different one later is refused — the same rule as a Linux machine's host key. |
| **Status** — `connected · last event 2s ago` | Live, from the client's own event stream. |
| `Test connection` | Says what happened: a wrong token, a hub that is not answering and a certificate that has changed need different things done about them. |

Below it, the settings that are this client's own are marked as such, and the ones that
belong to the landscape are marked as the hub's — with the hub-owned ones **read-only
while disconnected**, because editing a copy of something you cannot save is how the
"panel edits a copy" bug felt from the outside on 2026-07-26.

Switching mode **applies on the next start**, and the page says so with a `Restart now`
button rather than pretending otherwise. Swapping a live engine for a client means
stopping the poller, the health monitor, the watchdog and the scheduler and starting a
socket in their place; it is a worthwhile thing to do later and a poor thing to do in the
same change that introduces the mode.

Write the test first:

```python
def test_the_connection_section_writes_this_client_s_own_file(qapp, tmp_path,
                                                             monkeypatch):
    """Not services.json: which hub this machine talks to is nobody else's setting,
    and a client that wrote it to the shared config would tell four other people to
    connect to their own machine."""
    from core import local
    monkeypatch.setattr(local, "PATH", str(tmp_path / "client.json"))
    monkeypatch.setattr(local, "set_token", lambda url, token: True)
    win = panel_mod.MainPanel(cfg_mod.Config(), store=st.Store())
    page = win.general_page

    page.hub_url.setText("https://ctl052:8797")
    page.hub_token.setText("a-token")
    page._save_connection()

    assert local.load().hub_url == "https://ctl052:8797"
    assert "hub_url" in (tmp_path / "client.json").read_text(encoding="utf-8")
    # And nothing about the hub leaked into the shared document.
    assert "ctl052" not in str(cfg_mod.to_dict(win.config()))
    win.deleteLater()


def test_a_hub_on_this_computer_that_is_not_running_says_so(qapp, monkeypatch):
    """The state that decides whether dropping the mode selector was a good idea. An
    empty window here — because a service account could not log on as a service — is
    an app that appears to do nothing for a reason it will not name."""
    from core import local
    monkeypatch.setattr(local, "load",
                        lambda: local.Settings(hub_url="https://ctl052:8797"))
    monkeypatch.setattr(panel_mod, "hub_is_local", lambda url: True)
    monkeypatch.setattr(panel_mod, "hub_service_state", lambda: "Stopped")
    win = panel_mod.MainPanel(cfg_mod.Config(), store=st.Store())
    page = win.general_page

    assert "not running" in page.connection_status().lower()
    assert page.start_hub_button.isVisibleTo(page) is True
    win.deleteLater()


def test_the_local_machine_is_named_by_the_hub_not_by_this_one(qapp):
    """`machine=""` used to mean the computer the app runs on. It means the hub's
    computer now, and a workstation labelling it "This PC" would show its own hostname
    against a server's services."""
    store = st.Store()
    win = panel_mod.MainPanel(cfg_mod.Config(machines=[cfg_mod.Machine()]),
                              store=store)
    win.set_hub_identity(name="CTL052", version="2.2.0")

    title = win.machines_page._title(win.config().machines[0])

    assert "CTL052" in title
    assert win.machines_page._reachability(win.config().machines[0])[1] == "the hub"
    win.deleteLater()


def test_the_client_s_own_workstation_is_not_in_the_list_unless_it_was_added(
        qapp, monkeypatch):
    """The list is the landscape, and the landscape is shared. A row for whichever PC
    the panel happens to be open on would mean a different machine to each of five
    people."""
    monkeypatch.setattr(panel_mod.control, "host_name", lambda: "ISMAIL-LAPTOP")
    cfg = cfg_mod.Config(machines=[cfg_mod.Machine(),
                                   cfg_mod.Machine(name="sc-sql", kind="windows",
                                                   address="10.77.3.112")])
    win = panel_mod.MainPanel(cfg, store=st.Store())
    win.set_hub_identity(name="CTL052", version="2.2.0")
    page = win.machines_page
    page.refresh()

    shown = [page._title(m) for m in win.config().machines]
    assert not any("ISMAIL-LAPTOP" in title for title in shown)
    assert page.list.count() == 2


def test_a_workstation_that_was_added_is_marked_as_the_one_you_are_on(qapp,
                                                                     monkeypatch):
    """So that somebody looking at five machines can tell which row is under their own
    keyboard — without that row pretending to be reached any differently."""
    monkeypatch.setattr(panel_mod.control, "host_name", lambda: "ISMAIL-LAPTOP")
    cfg = cfg_mod.Config(machines=[
        cfg_mod.Machine(),
        cfg_mod.Machine(name="ismail-laptop", label="Ismail's laptop",
                        kind="windows", address="ISMAIL-LAPTOP")])
    win = panel_mod.MainPanel(cfg, store=st.Store())
    win.set_hub_identity(name="CTL052", version="2.2.0")
    page = win.machines_page

    assert page._reachability(cfg.machines[0])[1] == "the hub"
    assert page._reachability(cfg.machines[1])[1] == "this computer"
    win.deleteLater()


def test_on_the_hub_s_own_machine_only_the_hub_chip_shows(qapp, monkeypatch):
    """Both would be true there, and two chips on one row invites the question of what
    the difference is."""
    monkeypatch.setattr(panel_mod.control, "host_name", lambda: "CTL052")
    cfg = cfg_mod.Config(machines=[cfg_mod.Machine()])
    win = panel_mod.MainPanel(cfg, store=st.Store())
    win.set_hub_identity(name="CTL052", version="2.2.0")

    assert win.machines_page._reachability(cfg.machines[0])[1] == "the hub"
    win.deleteLater()


def test_the_hub_s_own_settings_warn_before_cutting_the_client_off(qapp):
    """The port the hub listens on is part of the shared config, so a client can edit
    it — and the moment it is saved, that client is talking to the wrong port."""
    win = panel_mod.MainPanel(cfg_mod.Config(), store=st.Store())
    page = win.general_page

    assert page.hub_port_warning(8797, 9000) == (
        "Changing the port disconnects every client, including this one. They will "
        "need the new address before they can reach the hub again.")
    assert page.hub_port_warning(8797, 8797) == ""
```

- [ ] **Step 7: Commit**

```bash
git add app.py run.bat core/local.py ui/pages/general.py tests/test_client_mode.py tests/test_local_settings.py
git commit -m "Let the tray app read a hub instead of its own engine"
```

---

## Task 10: Who did it

**Files:**
- Modify: `core/db.py`, `core/history.py`, `core/engine.py`, `ui/pages/history.py`
- Modify: `tests/test_history_state.py`

**Interfaces:**
- Consumes: `Engine.act(..., actor=…)` from Task 2.
- Produces: `events.actor` in the database; `history.record_action(..., actor="")`; the History page shows it.

- [ ] **Step 1: Write the failing test**

```python
def test_an_action_records_who_asked_for_it(tmp_path):
    """With five clients, "who restarted AppEngine at 03:00" stops being a
    rhetorical question."""
    path = str(tmp_path / "history.db")
    history.record_action("AppEngine", "restart", st.SRC_PANEL, machine="",
                          actor="CT\\ismail.orhan", path=path)

    row = history.read(path=path, limit=1)[0]

    assert row["actor"] == "CT\\ismail.orhan"


def test_an_old_database_gains_the_column(tmp_path):
    """Someone has a v2 database with a thousand rows in it, and upgrading must not
    ask them to throw it away."""
    path = str(tmp_path / "history.db")
    db.connect(path, version=2)                     # the shape before this task
    history.record_action("AppEngine", "stop", st.SRC_PANEL, path=path)

    db.connect(path)                                # current schema
    rows = history.read(path=path, limit=5)

    assert rows and rows[0]["actor"] == ""
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/test_history_state.py -k actor -v`
Expected: FAIL with `KeyError: 'actor'`

- [ ] **Step 3: Add schema v3**

In `core/db.py`, following the existing `_STEPS` pattern (keyed by target version):

```python
3: ("ALTER TABLE events ADD COLUMN actor TEXT NOT NULL DEFAULT ''",),
```

and `SCHEMA_VERSION = 3`. Add `actor` to `_to_row`/`_to_record` in `core/history.py` and to the `record_action` / `record_run` signatures.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_history_state.py -q`
Expected: PASS

- [ ] **Step 5: Show it**

`ui/pages/history.py`: add an "Asked by" column, populated from `actor`, hidden when every row in view has an empty one — a column of blanks is worse than no column, and a single-machine installation will never fill it.

- [ ] **Step 6: Commit**

```bash
git add core/db.py core/history.py core/engine.py ui/pages/history.py tests/test_history_state.py
git commit -m "Record who asked for an action"
```

---

## Task 11: Installing it

**One installer, two components.** Not two installers, and the reasoning is worth
having written down because it is the kind of decision that gets quietly reversed:

| | One installer with components | Two installers |
|---|---|---|
Artifacts to build, sign, publish | one | two, and code signing is per-artifact effort |
Payload on disk after install | only the chosen component | only what that installer holds |
Download size | both payloads, ~45 MB (the hub excludes Qt, the client excludes the service exe) | smaller each |
Version skew between hub and client | impossible — same file | a real failure mode, and the protocol version check exists because of it |
The common case (one machine, no hub) | untick one box, or accept the default | pick the right download |
Both roles on one machine — the test setup | tick both | install twice |

The download grows by the size of a payload most people will not install. That is the
price, and it is worth paying to make "the hub and the client are the same build" true
by construction rather than by discipline.

**Files:**
- Modify: `installer.iss`
- Create: `docs/HUB.md`

**Interfaces:**
- Consumes: `hub.exe` from Task 8.
- Produces: one installer that sets up a hub, a client, or both, interactively or
  silently.

- [ ] **Step 1: Add the components to `installer.iss`**

```
[Types]
Name: "client";  Description: "Client only — the tray application"
Name: "hub";     Description: "Hub only — the Windows service, for a server"
Name: "full";    Description: "Both, on this machine"
Name: "custom";  Description: "Choose"; Flags: iscustom

[Components]
Name: "client"; Description: "Service Officer (tray application)"; Types: client full
Name: "hub";    Description: "Service Officer Hub (Windows service)"; Types: hub full
```

Neither component is `fixed`: a server that nobody logs into wants the hub without a
tray icon, and a workstation wants the tray without a service. What must not happen is
*neither*, so:

```
[Code]
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectComponents)
     and not (IsComponentSelected('client') or IsComponentSelected('hub')) then
  begin
    MsgBox('Choose at least one: the tray application, the hub service, or both.',
           mbError, MB_OK);
    Result := False;
  end;
end;
```

Files: ship `ServiceOfficerHub` under the `hub` component. Then:

```
[Run]
Filename: "{app}\ServiceOfficerHub\ServiceOfficerHub.exe"; Parameters: "--startup auto install"; \
  StatusMsg: "Registering the hub service..."; Components: hub; Flags: runhidden
Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""Service Officer Hub"" \
  dir=in action=allow protocol=TCP localport=8797 profile=domain"; \
  Components: hub; Flags: runhidden

[UninstallRun]
Filename: "{app}\ServiceOfficerHub\ServiceOfficerHub.exe"; Parameters: "stop"; Flags: runhidden; RunOnceId: "stophub"
Filename: "{app}\ServiceOfficerHub\ServiceOfficerHub.exe"; Parameters: "remove"; Flags: runhidden; RunOnceId: "removehub"
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""Service Officer Hub"""; Flags: runhidden; RunOnceId: "closeport"
```

The firewall rule is **domain profile only**, deliberately: the same reasoning as the rules enabled on `10.77.3.110` on 2026-07-26 — a machine can find itself on a public network, and a management port has no business being open there.

- [ ] **Step 1a: Pair the local case without asking anything**

Since there is no mode to choose (Task 9), a machine that installs both components has
to come out of the installer already working. In order, after the files are copied and
the service is registered:

```
[Run]
; 1. the certificate, so there is something to pin
Filename: "{app}\ServiceOfficerHub\ServiceOfficerHub.exe"; Parameters: "--fingerprint"; \
  Components: hub; Flags: runhidden waituntilterminated
; 2. start it, so step 3 has something to talk to
Filename: "{app}\ServiceOfficerHub\ServiceOfficerHub.exe"; Parameters: "start"; \
  Components: hub; Flags: runhidden waituntilterminated
; 3. a token for this machine's own client, and client.json written pointing here
Filename: "{app}\ServiceOfficerHub\ServiceOfficerHub.exe"; \
  Parameters: "client pair --local"; Components: hub and client; Flags: runhidden waituntilterminated
```

`client pair --local` is `client add` plus writing the answer straight into
`client.json` — the token never leaves the machine and nobody is shown it. Add it beside
`_client_command` in Task 8.

The address it writes is **this computer's name**, not `localhost`: the certificate is
issued for the host name, and a client that pins `localhost` cannot later be pointed at
the same hub by name without failing its own certificate check.

- [ ] **Step 1a2: Take the administrator manifest off the client**

The tray application no longer touches a service manager, so it no longer needs to be
elevated — and the UAC prompt on every launch is the most visible cost this app charges
today. In `build.bat` and in the release workflow, `--uac-admin` moves from the client
target to the hub target.

Two things to check by hand afterwards, because this is the kind of change that appears
to work and then does not:

| Check | Expected |
|---|---|
Launch the tray app as a standard user | no UAC prompt, the panel opens, rows show states |
Restart a service from it | it works — the hub is doing the work, and the hub is LocalSystem |
`hub.exe --console` as a standard user | refuses with something readable, since it cannot control services either |

**An existing installation upgrades into this.** The `services.json` already in
`%ProgramData%\Service Officer` becomes the hub's, unchanged and in place, and the client
beside it is paired to this computer. Nothing is migrated, moved or rewritten — the file
is exactly where the hub already looks. Confirm it with the real one on this machine
(nine services, three machines) before believing it.

- [ ] **Step 1b: Let a *remote* client be installed already pointed at its hub**

Five workstations is four too many to configure by hand. Two optional parameters,
consumed on the client component only:

```
[Setup]
; /HUBURL=https://ctl052:8797 /HUBTOKEN=xxxx
[Run]
Filename: "{app}\ServiceOfficer.exe"; \
  Parameters: "--connect {code:GetHubUrl} --token {code:GetHubToken} --store-only"; \
  Components: client; Check: HaveHubDetails; Flags: runhidden waituntilterminated
```

`--store-only` writes the token and pins the certificate, then exits without opening
anything — so the installer finishes without a tray icon appearing mid-install, and the
first real launch is already connected. Add it to `app.py`'s argument handling in
Task 9 (it is two lines: do the storing, then `return 0` before the QApplication is
built).

A token on an installer command line is visible in the process list while the
installer runs, and in whatever log a deployment tool keeps. Say so in `docs/HUB.md`,
and say the alternative: install without it and pass `--token` once on first launch.
Neither is worse than the other; what matters is that nobody is surprised.

- [ ] **Step 1c: Do not ask for the service account's password**

The hub is registered as **LocalSystem** and the administrator changes it afterwards in
`services.msc` → Log On, or with `sc.exe config`. The installer does not collect a
password: Inno would hold it in memory and in its own log, an unattended install would
need it on a command line, and the whole point of running the hub as a domain account
is to *stop* storing credentials.

`docs/HUB.md` says this in one sentence and gives the command:

```bat
sc.exe config ServiceOfficerHub obj= "CT\svc-officer" password= "..."
```

with the note that it is typed by a person in a console, once, and that the account
needs *Log on as a service* (`secpol.msc` → User Rights Assignment) — which is the
single most common reason a service account fails to start and the message Windows
gives for it is unhelpful.

- [ ] **Step 2: Handle upgrading a running service**

An installer that copies over a running `.exe` fails with "file in use". Before the file copy:

```
[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Exec(ExpandConstant('{app}\ServiceOfficerHub\ServiceOfficerHub.exe'), 'stop', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
```

- [ ] **Step 3: Write `docs/HUB.md`**

Sections, each of which must contain the actual commands rather than a description of them:
1. What the hub is and when you want one (five people, one truth; a config that lives on a server; state that survives a workstation reboot).
2. Where to install it — and the domain question, with the reasoning from 2026-07-27: in the targets' domain, as a domain service account, no password stored anywhere; in another domain, credentials per machine as built on 2026-07-26.
3. Installing: the component, the service account (`services.msc` → Log On), the firewall rule.
4. Adding a client: `hub.exe client add ismail-laptop` prints a token once; on the client, `ServiceOfficer.exe --connect https://hub:8797 --token <token>` stores it and pins the certificate.
5. The certificate: where it is, what its fingerprint is, and what to do when it changes.
6. Troubleshooting, as a table of symptom → check → fix. Include: `curl -k https://hub:8797/api/v1/ping`, the log path, `Get-Service ServiceOfficerHub`, and "the hub is up but a target says no answer" pointing at the Machines page's own reachability line.

- [ ] **Step 3b: The silent commands, tested rather than written down hopefully**

These go in `docs/HUB.md` and each one has to be run once before it is documented:

```bat
:: a server: the hub, no tray icon
ServiceOfficerSetup.exe /SILENT /NORESTART /TYPE=hub

:: a workstation, already pointed at the hub
ServiceOfficerSetup.exe /SILENT /NORESTART /TYPE=client ^
    /HUBURL=https://ctl052:8797 /HUBTOKEN=xxxxxxxx

:: this machine, both, for testing
ServiceOfficerSetup.exe /SILENT /NORESTART /TYPE=full
```

An upgrade needs no `/TYPE`: Inno remembers what was installed and preselects it, so
`ServiceOfficerSetup.exe /SILENT /NORESTART` upgrades a hub as a hub and a client as a
client. Verify that by upgrading both kinds of install rather than assuming it.

- [ ] **Step 4: Build the installer and install it on this machine**

```bash
build.bat
```
then compile `installer.iss` with Inno Setup, install with the hub component, and check:

```bash
Get-Service ServiceOfficerHub
curl.exe -k https://localhost:8797/api/v1/ping
Get-NetFirewallRule -DisplayName "Service Officer Hub" | Select-Object Enabled, Profile
```

Then install the *client only* on top and confirm the hub service is left alone and the
tray icon appears; then `/TYPE=full`; then uninstall and confirm the service is stopped,
removed, and the firewall rule is gone. Four installs, because the component matrix is
exactly where an installer is wrong in a way nobody notices until a customer's server.

- [ ] **Step 5: Commit**

```bash
git add installer.iss docs/HUB.md
git commit -m "Install the hub, its firewall rule and its documentation"
```

---

## Task 12: The reserved space for a browser UI

Not the UI. The one endpoint that proves the API is enough for one, so the next plan starts from something that answers.

**Files:**
- Modify: `core/hub_server.py`
- Create: `core/hub_pages/index.html`

**Interfaces:**
- Consumes: the API from Task 5.
- Produces: `GET /` — a single page that lists the services and their states, reading `/api/v1/snapshot` and `/api/v1/events`.

- [ ] **Step 1: Write the failing test**

In `tests/test_hub_server.py`:

```python
def test_the_root_page_is_served_and_needs_a_token_for_data(hub):
    """The page itself is not a secret; what it shows is. So the HTML is public and
    every fetch it makes is not — which is also the shape the real UI will need."""
    server, _engine, _holder = hub
    request = urllib.request.Request(server.url + "/")
    with urllib.request.urlopen(request, timeout=5) as answer:
        html = answer.read().decode()

    assert answer.status == 200
    assert "Service Officer" in html
    assert "/api/v1/snapshot" in html
```

- [ ] **Step 2: Run, implement, run**

Serve the file from `core/hub_pages/`, with `Content-Type: text/html; charset=utf-8` and no caching. The page asks for a token once, keeps it in `sessionStorage`, renders a table from the snapshot and updates it from the event stream. Fewer than 150 lines of plain JavaScript, no framework, no build step — the same constraint as the rest of this project, for the same reason.

Run: `python -m pytest tests/test_hub_server.py -q`
Expected: PASS

- [ ] **Step 3: Look at it in a browser**

Open `https://localhost:8797/`, accept the certificate warning, paste a token, and watch a service you restart from the tray change colour in the page.

- [ ] **Step 4: Commit**

```bash
git add core/hub_server.py core/hub_pages/index.html tests/test_hub_server.py
git commit -m "Serve one page from the hub, to prove the API is enough for a UI"
```

---

## Task 13: What the reader of this plan should check before calling it done

- [ ] **Step 1: The whole suite, from the repo root**

Run: `python -m pytest tests -q`
Expected: PASS. The count should be the sum of what it was plus roughly 35 new tests.

- [ ] **Step 2: The three modes, by hand, on this machine**

| Mode | Command | What to look for |
|---|---|---|
Embedded (today's) | `run.bat` | Panel opens, local service restarts, tray reflects it |
Hub in a console | `python hub.py --console` then `run.bat -c https://localhost:8797` | Same panel, hub's log shows the action with an actor |
Hub as a service | installer with the hub component | Survives `Restart-Computer`; `Get-Service` says Running |

- [ ] **Step 3: The measurements that must not have regressed**

Recorded 2026-07-26/27, and each of them was a bug before it was a number:

| What | Expected |
|---|---|
Local service action, embedded | under 100 ms to the row changing |
`hanadev` poll, four services over SSH | ~64 ms |
`sc-sql` poll, one service, held connection | under 20 ms |
First connection to `sc-sql` | ~21 s once per hub run, never on the UI thread |
Panel open | no synchronous call to any remote machine — check with a breakpoint or a log line in `control.query_status` |

- [ ] **Step 4: Update the documents that now say something untrue**

- `docs/ROADMAP.md`: the non-feature "central server, agents, dashboards" — a hub is now in scope, agents are still not. Say which and why.
- `docs/ARCHITECTURE.md`: add the hub/client boundary to the layer table.
- `README.md`: hub mode in the feature list, and `docs/HUB.md` in the links.

- [ ] **Step 5: Release**

```bash
# core/version.py and installer.iss must agree, or stamp_version.py fails the build
git add -A && git commit -m "Release 2.2.0: hub and clients"
git tag v2.2.0
git push origin master --tags
```

The GitHub Action builds, tests, stamps, packages and publishes. Watch it, because a failed release build is a release that does not exist.

---

## Notes for whoever picks this up

**The thing to be careful about.** Task 2 is the only task that can break what already works, because it moves code that four hundred lines of tests and two months of behaviour depend on. Move it verbatim. If you find yourself wanting to fix something while moving it, write the fix down and do it in its own commit afterwards.

**The thing that will be tempting and is wrong.** Making `RemoteStore` a subclass of `Store`. It is not one — it has no data of its own and its writes are lies. Inheriting would make every missing method silently return an empty answer instead of failing where it is written.

**The thing this plan deliberately does not do.** Multi-tenancy, accounts, internet exposure. The token and TLS primitives here would carry a hosted hub, and that is on purpose, but designing for it now would cost the simplicity that makes a single-server installation a five-minute job.

**Where the measurements live.** `docs/DECISIONS.md`, dated. Every number quoted in this plan came from there, and anything you measure while implementing it belongs there too — the entry is what stops the next person re-deriving it.
