# Service Officer — Roadmap

Candidate features, kept here so each can be reviewed on its own. Ordered by
what changes the product most per unit of work, not by ease.

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

### 5. "Running but dead" health checks
**Value: high · Effort: medium**

The real ERP failure mode isn't a stopped service — it's a service that reports
Running while nothing answers. Optional per-service check:

- TCP port listening
- HTTP(S) endpoint returns expected status
- SQL Server connection succeeds

A failing check shows an amber "unhealthy" state distinct from Stopped, and can
feed the same recovery rules as Tier 1.

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

- Central server, agents, dashboards — that's the monitoring-suite market, and
  the point of this tool is that it needs none of that.
- Cross-platform. The whole value is Win32/SCM depth.

---

## Shipping concerns

- **Code signing.** Unsigned installers trigger SmartScreen on customer
  machines. Needed before any wider distribution.
- **Unit tests.** The behaviour is currently proven by scripted smoke tests run
  during development; a committed test suite should come with Tier 1.
- **Auto-update.** Versioned releases exist (CI builds on a `v*` tag); an
  in-app update check is the missing half.
