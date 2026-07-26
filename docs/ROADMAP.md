# Service Officer — Roadmap

Candidate features, kept here so each can be reviewed on its own. Ordered by
what changes the product most per unit of work, not by ease.

> **Status, 2026-07-26.** v2.0.0 shipped. Tiers 1–3 are built: watchdog, ordered
> stacks, remote-capable core, scheduler, categories, dashboard, history with the
> Windows event log merged in. Data moved to `%ProgramData%`. **Health checks
> (feature 5) are built** — five kinds, a master switch per service, a visible
> "last / next check" summary, and a restart cooldown that used to be a hidden
> constant.
>
> The UI layer was then standardised, in three commits tagged `[ui-std]`:
> `ui/theme.py` owns every colour, metric and glyph; no widget carries a palette
> value of its own; and the panel's eight pages moved out of a 2,415-line
> `ui/panel.py` into `ui/pages/`. Both steps were verified pixel-identical
> (36 screenshots, both themes) rather than asserted. Converting the
> sentence-style settings to labelled fields was reviewed and **deferred**: it
> changes what people see, unlike the rest.
>
> **Agreed next**, in order: maintenance windows (9), post-reboot verification
> (7), then mail notifications.
>
> **Designed but not built** — reasoning in [DECISIONS.md](DECISIONS.md):
> auto-update, how remote machines are reached, the agent-then-hub path to a web
> console, and mail.
>
> **Parked deliberately:** code signing (needs a purchased certificate), profile
> export / silent deploy (waits on the hub), uptime & SLA reporting, resource
> sparklines.

**Positioning.** This isn't a generic "Windows services" utility. Its centre of
gravity is the *ERP server*: a SAP Business One box running SQL Server, a
licence service, add-on services (CompuTec AppEngine / Label / WMS) that must
come up in a particular order and that nobody notices are down until a user
calls. Enterprise monitoring (Zabbix, Nagios) is too heavy for one customer
server; tray launchers are too dumb. The gap in between is this tool.

Legend — **Value**: how much it changes daily work. **Effort**: rough build
size. **Ready**: what already exists in the codebase that makes it cheap.

---

## Tier 1 — turns a viewer into a guardian

### 1. Watchdog + automatic recovery
**Value: very high · Effort: medium**

When a service stops without us asking, notify and (if enabled for that
service) start it again.

- Per-service: *keep running* on/off, **max attempts**, delay between attempts,
  optional backoff, and what to do when attempts are exhausted (notify only).
- Flapping detection: N stop events within a window ⇒ stop retrying and say so,
  instead of fighting a service that cannot stay up.
- Windows toast on crash, on recovery, and on giving up.
- Never fight a *deliberate* stop: a stop issued from our own panel must not be
  undone by the watchdog.

**Ready:** SCM push notifications already report a stop within ~30 ms
(`scm_notify.py`), and `_action_active` already distinguishes our own actions.

### 2. Ordered stacks (dependency-aware start/stop)
**Value: very high in the SAP B1 niche · Effort: medium**

A named, ordered group of services — e.g. `SQL Server → Licence Manager →
Server Tools → AppEngine → WMS` — with one-click **Start / Stop / Restart
stack**.

- Each step either waits until the previous service reports *Running* (with a
  timeout) or waits a fixed number of seconds; both are useful, because some
  services report Running before they are actually ready.
- Stop runs the order in reverse.
- Progress is visible per step, and a failed step stops the run and says which
  step failed and why.
- Fixes the classic post-Windows-Update problem: services set to Automatic come
  up in the wrong order and the add-ons fail against a licence service that
  wasn't ready.

### 3. State history / timeline
**Value: high · Effort: small**

A persisted, exportable log of state changes: *"AppEngine stopped 03:12:41,
auto-restarted 03:12:45, reached Running after 18 s."*

- Evidence to paste into a customer ticket.
- Makes flapping visible instead of anecdotal.
- Feeds an uptime summary ("AppEngine 99.8 % this month").

**Ready:** SCM notifications already deliver exact transitions with timestamps —
this is mostly storage and a viewer.

---

## Tier 2 — the multiplier

### 4. Remote machines
**Value: high · Effort: medium-high**

Manage services on other servers you already have admin rights on — the SAP
app server, the SQL box, the WMS server — in one panel.

**Verified feasible:** every call we use takes a machine parameter —
`win32serviceutil.QueryServiceStatus(name, machine=...)`, `StartService`,
`StopService`, `RestartService`, `ControlService` — and
`OpenSCManagerW(machine, ...)` means even push notifications work remotely.

Real constraints to design for: admin rights on the target, RPC/SMB reachable
through the firewall, credentials (simplest first version: the account the app
already runs as, i.e. same domain), and a per-machine offline/unreachable state
in the UI.

### 5. "Running but dead" health checks — **built, 2026-07-26**
**Value: high · Effort: medium**

The real ERP failure mode isn't a stopped service — it's a service that reports
Running while nothing answers. Built generically rather than for one stack: TCP,
HTTP(S), process alive, file exists/fresh, and command exits 0. A service failing
its checks shows **Not responding** instead of Running, in the tray icon, the
hover card, the flyout and the dashboard, and can be restarted automatically.

What the build taught, kept because it will come up again:

- An unfinished check must not be *run*. A URL check with no URL was being
  executed and failing, so a half-typed setting looked like an outage;
  `is_configured()` now skips it and says "No URL set yet".
- `socket.getaddrinfo` returns link-local IPv6 first for a Windows hostname, so a
  TCP check against a name cost 2.05 s before trying the address that works.
  Addresses are ordered with link-local last, and the failure reported is the
  first address's, not the last.
- A restart cooldown is essential and must be visible. It existed as a hidden
  `COOLDOWN_SECONDS = 300`, which is why a 1-minute check interval looked like it
  was firing every 6–7 minutes.
- `time.monotonic()` is uptime, so "now − 0" comparisons mean a freshly booted
  machine skips its first restart.

---

## Tier 3 — polish that sells

### 6. Panic button
One click: restart the whole stack in dependency order, then produce a
copy-pasteable summary of what was done and what the states were — for the
support ticket.

### 7. Post-reboot verification
After a Windows Update restart, confirm the stack came up in the right order and
report anything that didn't, instead of discovering it from a phone call.

### 8. Per-service resource graphs
CPU / memory / uptime sparkline per service. **Ready:** the SCM notification
already carries `dwProcessId`, so the process is identified for free.

### 9. Maintenance windows
Scheduled restarts, and alert suppression during a planned window.

### 10. One-click logs
Open Event Viewer filtered to that service, or the service's own log file.

### 11. Profile export / import + silent deployment
`services.json` is already the profile. Add export/import and a
`/config=stack.json /silent` install flag so one profile can be pushed to fifty
customer servers. A "detect stack" scan could pre-suggest SQL / SAP B1 /
CompuTec groupings.

### 12. Uptime & SLA summary
Monthly per-service availability, derived from the history in feature 3.

---

## Non-features (deliberately out of scope)

- **Agents on managed machines.** Measured on 2026-07-26: SSH reads four services
  on the SUSE box in 64 ms and installs nothing, and a held connection to a remote
  Windows service manager answers in 9 ms. An agent on a headless server is
  software to deploy, update, secure and watch, in exchange for tens of
  milliseconds. It stays possible — an agent would be a third `Connector`
  implementation and nothing above `control.py` would know — but it is not the
  plan.
- **Dashboards, alerting pipelines, metric stores.** That is the monitoring-suite
  market. This tool tells one team about one landscape.
- Cross-platform *clients*. The whole value is Win32/SCM depth; Linux is a
  **target**, not a place this runs.

### No longer a non-feature: a central service

The line above used to read "central server, agents, dashboards — the point of
this tool is that it needs none of that", and half of that stopped being true the
moment a second person wanted to see the same landscape. A hub is now planned:
one Windows service owning the config, the connections and the history, with the
tray app and later a browser page reading it. Agents are still out; the hub
reaches its targets itself.

The reasoning and the task-by-task plan:
[docs/superpowers/plans/2026-07-27-hub-service-and-clients.md](superpowers/plans/2026-07-27-hub-service-and-clients.md).

---

## Shipping concerns

- **Code signing.** Unsigned installers trigger SmartScreen on customer
  machines. Needed before any wider distribution.
- **Unit tests.** The behaviour is currently proven by scripted smoke tests run
  during development; a committed test suite should come with Tier 1.
- **Auto-update.** Versioned releases exist (CI builds on a `v*` tag); an
  in-app update check is the missing half.
