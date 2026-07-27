# Decisions

Answers to the design questions that came up, with the reasoning, so the next
person (or the next session) doesn't have to re-derive them. Dated, because some
of these depend on where the product is rather than on first principles.

---

## Where data lives, and in what format — decided 2026-07-25

> **The history's format was superseded on 2026-07-26** — see "The event
> store" below. The reasoning here about *where* still stands; the
> reasoning about JSON Lines is kept because the argument that replaced it
> is only legible next to it.

`%ProgramData%\Service Officer\`, not `%APPDATA%`. This describes the *machine*:
which services it runs, when they should restart, what happened to them. Per-user
storage meant a second administrator saw an empty install, the history a ticket
needs sat in whichever profile happened to be logged in, and running as a Windows
service later would have put it in
`%WINDIR%\System32\config\systemprofile\AppData\Roaming` — a third location. A
roaming profile would also carry one server's service list to another server,
which is precisely wrong.

The installer grants `admins-modify` on the folder, because the default ACL on a
ProgramData subfolder only lets the creating user write. Existing installs are
migrated once, by copy, and only into an empty destination — see
`config.migrate_from_legacy`.

**Two files, two formats, on purpose:**

| File | Format | Why |
|---|---|---|
| `services.json` | JSON | A document read and rewritten whole. Human-editable in an emergency. |
| `history.jsonl` | JSON Lines | Appended to constantly, queried by service and time, exported, and soon aggregated for uptime. One self-describing record per line: appending never rewrites the file, a torn write costs the last line rather than the file, and adding a field doesn't break old readers. A plain `.log` would mean parsing prose with regexes and would break the first time a field was added. |
| `service-officer.log` | plain text, rotating | Read by a human when the *app itself* misbehaves. Sequential prose with timestamps; JSON here would make it harder to skim, not easier. Rotates at 1 MB × 3. |

**When to move history to SQLite:** when either (a) a single machine's history
passes a few hundred thousand rows and `query()` reading the whole file starts to
show, or (b) uptime/SLA reporting needs indexed aggregation, or (c) a central hub
holds several machines' history at once. Today it is 35 KB / ~250 rows and reading
it costs milliseconds, so a database would be cost without benefit.

---

## Auto-update — designed 2026-07-25, not built

GitHub Releases is already the publishing channel, the app already runs elevated,
and Inno Setup already supports a silent reinstall over the top. So:

1. **Feed.** A `latest.json` published with each release: version, installer URL,
   SHA-256, release notes, and a `minimum` field so a broken version can be
   skipped. Read over HTTPS.
2. **Check.** On start, then every 24 h. Never more often — this is a server tool,
   not a browser.
3. **Verify before running anything.** Download to temp, check the SHA-256 against
   the feed, and refuse if it differs. We are about to run an installer as
   administrator; the hash is not optional. Once the app is signed (below), check
   the Authenticode signature too.
4. **Install.** `ServiceOfficerSetup.exe /SILENT /NORESTART`, then exit and let
   the installer relaunch.
5. **Never at a bad moment.** Not while a stack run or a scheduled action is in
   flight, and not inside a maintenance window. An update that restarts the
   watchdog mid-recovery is worse than being a version behind.
6. **Default to asking.** On a production server the default is "tell me, don't
   install" — a badge in the panel footer, not a nag. "Install automatically" is a
   setting, off by default.

Deliberately **not** chosen: winget (public repo, and we want private control),
MSI + Group Policy (heavy, and the customer's AD isn't ours), and any
self-updating-binary scheme (replacing a running Qt install in place is fragile
where a full installer is not).

---

## Reaching other machines — decided 2026-07-25

**Keep the native SCM RPC we already use.** `core/control.py` passes `machine=` to
pywin32, which is the same mechanism as `sc.exe \\server query`: RPC over the SMB
named pipe `\\server\pipe\svcctl`.

What it needs on the target:
- The account running Service Officer must be an administrator there (or hold
  `SC_MANAGER_CONNECT` plus per-service rights).
- **TCP 445** open, and the **"Remote Service Management"** firewall rule group
  enabled. Some operations also touch the RPC endpoint mapper on **135**.
- No agent, no install, no extra service on the target — this is why it wins.

What we ask the user for: the **machine name or IP**, and nothing else. Windows
authenticates with the caller's existing token; there is no password field because
there is nowhere honest to put one. Where different credentials are needed, the
correct answer is to run Service Officer as a domain account that is an
administrator on the targets.

If explicit credentials become a real requirement, they go in **Windows
Credential Manager** (`win32cred.CredWrite`) and a session is established with
`WNetAddConnection2` before the SCM calls — never a password in `services.json`.

> **Built on 2026-07-26**, and the requirement was real within a day: the machines
> to manage are in the `SC` domain and this workstation is in `CT.CORP`, so the
> caller's token is refused with "access denied" no matter how many rights it has
> at home. The `WNetAddConnection2` half is exactly as predicted. The store is
> **DPAPI machine scope** (`core/secrets.py`) rather than Credential Manager,
> because a credential written to a user's vault cannot be read by a LocalSystem
> service, and that service is where this engine is going. `services.json` holds
> the name of the store entry and never the password, as promised.
>
> The **known gap** below was closed the same week: `core/poller.py` polls every
> machine without push, and 2026-07-27 established that no remote transport has
> push — see "The two lies a transport can tell".

Alternatives considered and rejected for now:
- **WinRM (5985/5986)** — cleaner credential handling and friendlier to modern
  firewalls, but needs enabling on every target, is slower per call, and gives up
  `NotifyServiceStatusChangeW`, so status would have to be polled.
- **SSH (OpenSSH for Windows)** — explicit key auth, but we would be shelling out
  to `sc.exe`/PowerShell and parsing text, and again lose push notifications.
- **Our own agent** — the right answer eventually, but that is the hub design
  below, not a way to reach a machine today.

**Known gap:** `scm.Watcher` only subscribes to local services
(`if not s.machine`), so remote services get no push updates. Remote support needs
a polling loop for them — a few seconds' interval, since remote queries measured
~0.2 ms each locally but cross-network they are not free.

---

## Four servers, and a central hub — designed 2026-07-25, not built

> **Superseded on 2026-07-27, in the part that matters.** Stage 1 stands and is
> what gets built: the engine as a Windows service, the panel as its client.
> Stage 2 does not — **there are no agents on managed machines, so there is
> nothing for a hub to receive.** The hub *is* Stage 1's service, and it reaches
> its targets itself: the SCM for Windows, SSH for Linux.
>
> What changed was evidence rather than taste. Between them, 2026-07-26 and
> 2026-07-27 measured SSH reading four services on a real SUSE box in 64 ms with
> nothing installed there, and a held connection to a remote Windows service
> manager answering in 9 ms. An agent buys tens of milliseconds and costs a
> deployment, an update path and a second thing to monitor on every server — and
> the Linux targets are headless boxes where the owner does not want our software
> at all, which is a reasonable position and also the technically correct one.
>
> Agent→hub's real prize was **not storing credentials for targets**. That is
> answered more cheaply: run the hub as a domain service account in the targets'
> domain and there is nothing to store. Where a target is in another forest — the
> `SC` domain reached from `CT.CORP`, measured on 2026-07-26 — a per-machine
> credential in the DPAPI store covers it.
>
> The plan that replaces Stage 2:
> [superpowers/plans/2026-07-27-hub-service-and-clients.md](superpowers/plans/2026-07-27-hub-service-and-clients.md).
> An agent transport remains possible as a third `Connector` implementation, per
> target, if a machine ever turns out to be genuinely unreachable by pull.

Yes, a centre is needed, but the *first* step is not centralisation.

**Stage 0 — today.** One operator's desktop app reaching N machines over SCM RPC.
Fine for a handful of servers, one LAN, one administrator. Each install has its
own config; there is no shared truth.

**Stage 1 — the agent. This is the important one.** Service Officer installable as
a **Windows service** (LocalSystem, no UI) that runs the watchdog, the scheduler
and the history locally. Today, if the operator logs off, the tray app dies and
*nothing recovers* — which undercuts the product's whole promise. `core/` is
already UI-free precisely so this is possible: the service hosts `core`, and the
desktop panel becomes a client that talks to the local agent instead of doing the
work itself.

**Stage 2 — the hub.** A small central service that agents report to over
**HTTPS, outbound only** (agent → hub). No inbound holes in any server's firewall,
which is what makes it deployable across sites and VPNs. Per-machine bearer token
for authentication. The hub keeps events in a real database (SQLite first,
PostgreSQL when it outgrows that), serves the **web UI**, and can queue commands
for an agent to pick up on its next poll. The desktop panel stays as one client
among several.

Why this order: the agent is worth building even if the hub never happens, and the
hub is a thin addition once the agent already has an event stream and a command
loop. Building the hub first would mean a central console over machines that
cannot recover themselves.

---

## Mail notifications — designed 2026-07-25, not built

Standard library only: `smtplib` + `email.message.EmailMessage`. No dependency.

- **Settings**: host, port, encryption (STARTTLS / SSL / none), username, from,
  one or more recipients, and a **Send test mail** button that reports the real
  SMTP error rather than "failed".
- **The password does not go in `services.json`.** Windows Credential Manager,
  keyed `ServiceOfficer/smtp`. The JSON holds everything else.
- **Sending is off the GUI thread**, with a timeout, a small retry queue, and a
  cap — a mail server hiccup must not lose an alert, and must not hang the panel.
- **What triggers it**: the notification switches that already exist (crash /
  recovered / gave up) and each trigger's own `notify` setting
  (never/success/failed/skipped/…). Each gains a channel: tray balloon, mail, or
  both.
- **Content is the differentiator.** Subject `[CTL052] AppEngine stopped
  unexpectedly (exit 1067)`; body carries the last few history rows *and* the
  Windows event-log lines we already read. The mail arrives with the reason in it,
  not just the fact.
- **Rate limit.** A flapping service must not send fifty mails: at most one per
  service per N minutes, with a digest option.
- **Microsoft 365 caveat**: basic-auth SMTP is being retired, so an internal relay
  or an app password is the realistic route; OAuth2 is future work.

---

## Later, deliberately

- **Code signing.** Unsigned, SmartScreen calls it an unknown publisher, which
  makes installing on a customer's server harder than it should be. Needs a
  purchased certificate; parked until then.
- **Profile export / silent deploy.** Export a configuration and apply it to
  twenty servers. Waits on the hub, which changes what "a profile" means.
- **Uptime / SLA summary.** The data is already in the history; only the
  aggregation is missing. Waits on the SQLite move if it is to be quick.
- **Resource sparklines.** CPU and memory per service in the row, to show
  "running but bloated".

---

## How the UI layer is kept from drifting — decided 2026-07-26

An audit of `ui/` found the same value written in several places: 27 per-widget
`setStyleSheet` calls carrying palette colours, paddings as literals in each row,
and eight pages sharing one 2,415-line module. None of it was broken; all of it
made the next change cost more than it should.

Three rules now, in priority order:

1. **`ui/theme.py` is the only file that names a colour, a spacing or a glyph.**
   Widgets carry `objectName`s and properties (`role="hint"`, `kind="destructive"`,
   `chip="true"`), and the sheet decides what those look like. A theme switch is
   one `setStyleSheet(theme.sheet())`, not a walk of the widget tree — which is
   why `MainPanel.restyle()` now only redraws icons.
2. **An inline stylesheet may carry geometry, never a palette value.** Three
   remain, all on `FlatEdit`: padding and a hover underline. QSS cannot
   hover-underline a `QLabel` reliably (a synthetic `QEnterEvent` doesn't trigger
   `:hover`; it needs real pointer tracking), so that one stays inline — but it
   holds no colour, so no mode switch can invalidate it.
3. **One page, one module.** `ui/panel.py` keeps the window; `ui/pages/*` holds
   the pages. `ui.panel` re-exports them, so nothing outside had to change.

**Refactors of this layer are verified, not asserted.** A harness records 36
screenshots across both themes and every page, then compares them pixel for
pixel after the change; both `[ui-std]` commits came out 36/36 identical. It paid
for itself twice in one afternoon:

- Deleting an inline `color: FG3` from the fold chevron would have silently
  darkened it to `FG`. Moving a colour to the sheet means *adding the rule*, not
  just removing the old line.
- The harness itself lied at first. It grabs windows shown on the real screen, so
  a mouse resting on a row put that row in `:hover` and baked `BG_HOVER` into the
  baseline — a 17,461-pixel "regression" that was a pointer. Worse, moving the
  window away is not enough: Qt only clears `WA_UnderMouse` when a leave event
  arrives, so a stale hover survived the move. The harness now moves each window
  to the corner farthest from the pointer *and* delivers the leave itself.

**Deferred on purpose:** rewriting the sentence-style settings ("Try up to 3
times, waiting 10 seconds first…") as labelled fields. Everything above is
invisible to the user by design; that one is a change to what people read, so it
is a product decision rather than a cleanup.

---

## The event store: SQLite — decided 2026-07-26

The history moved from append-only JSON Lines to a SQLite file, `history.db`. The
reason is not row counts, and it matters to be precise about that, because "JSON
doesn't scale" would have been the wrong reason — reading the newest 200 rows had
already been made cost what it displays (54 ms → 0.6 ms) by reading the file
backwards.

**The reason is that a second process is coming.** The next step on the roadmap is
Service Officer installed as a Windows service, running the watchdog and the
scheduler while the desktop panel becomes a client. Appending from two processes
is safe. Retention was not: `trim()` rewrote the whole file through a temporary
copy and `os.replace`, so any append landing during a trim went into the
abandoned inode and was **lost without trace**. On a product whose promise is
"here is what happened at 03:12", silently losing events is the worst class of
bug there is. Retention is now a `DELETE`, and the file is never replaced — there
is a test that asserts exactly that.

Two more things follow from it:

- **WAL mode.** One writer and many readers, neither blocking the other, with
  each reader seeing a consistent snapshot. That is the agent-writes /
  panel-reads split, without a locking protocol of our own.
- **Aggregation becomes possible.** "AppEngine 99.8% this month" over indexed
  rows is a query; over a text file it is a full parse every time. A daily
  per-service rollup table exists for that (`uptime_daily`, schema version 2),
  derived and rebuildable — `events` remains the only source of truth.

**Measured** on 20,000 rows (2.8 MB of JSONL became a 3.4 MB database — larger,
because of the indexes that are the point):

| | JSONL | SQLite |
|---|---:|---:|
| `query(limit=200)` | 0.60 ms | 0.75 ms |
| `query(service=…)` | 3.40 ms | 2.07 ms |
| `runs(limit=200)`, no runs in the file | full scan | 0.01 ms |
| one event written | ~0.05 ms | 0.04 ms |
| retention pass dropping 15,954 rows | rewrite the file | 21 ms |
| a day-by-service aggregate | not feasible | 1.2 ms |

**Choices inside the choice**, so they are not re-litigated:

- `sqlite3` from the standard library — no new dependency, which is a project
  rule. No ORM: this is ten queries, and SQLAlchemy would cost a dependency and
  build size for nothing.
- Not PostgreSQL or SQL Server locally — installing a database server on a
  customer's ERP box to hold a service log is absurd. Not a time-series database
  either; that is the monitoring-suite market the product deliberately isn't in.
- **One store, not two.** JSONL and CSV remain *export* formats. Two sources of
  truth is how they disagree.
- `ts` stays TEXT, ISO-8601 UTC: it sorts correctly as text, and someone opening
  the file in any SQLite browser can read it.
- An `extra` JSON column keeps the property that made JSON Lines attractive —
  a new field doesn't need a schema change or break an older reader.
- Migrations are numbered steps keyed by the version they produce, recorded in
  `PRAGMA user_version`. A step, once shipped, is never edited: a customer has a
  file at that version.

**What went wrong while building it**, kept because it will happen again:
`sqlite3.connect()` on a corrupt file *succeeds* — the first statement is what
fails — so the error path left an open handle. On Windows that locks the file, and
`set_aside()` could then never move a damaged history out of the way: recovery was
impossible in precisely the case it exists for. The connection is closed on the
error path now, and a test scribbles over a database header to prove it.

Retention also now runs **daily** rather than only 3 seconds after start. A server
runs for weeks without this app restarting, and retention that only ran at startup
was retention that never ran.

---

## Choosing a health check: measured, not guessed — 2026-07-26

Two real services on one SUSE box, restarted while a probe watched them every half
second. Both are the "running but dead" case the health feature exists for, and
both show that the obvious check is the wrong one.

**SAP Business One Web Client** (`webclient.service`, `Type=oneshot` with
`RemainAfterExit`):

| moment | after the restart |
|---|---:|
| systemd, and so the panel, said Running | ~1 s |
| the port accepted again | ~9 s |
| `/webx/index.html` answered 200 | ~9 s |
| its own API stopped returning 500 | later still |

The static page is served long before the application works — the browser showed
"Internal Server Error" while the root URL answered 200. A check on the root URL
goes green while the thing is unusable.

**SAP Business One Server Tools** (`sapb1servertools.service`, SLD on port 40000):

| moment | after the restart |
|---|---:|
| stopped answering | 45.2 s into the probe |
| **the port never closed at all** | — |
| `/ControlCenter` answered 200 again | 24.1 s later |

A TCP check on 40000 would have reported healthy through the entire outage: Tomcat
holds the listener while the deployed application is gone. Twenty-four seconds of
downtime, invisible.

**So the rule for picking a check: probe the chain, not the port.** The right URL is
the one the application itself depends on — found by watching which request fails
during a restart and succeeds when it is ready. For the web client that is
`/tcli/dbtype/get.svc`, and the readiness signal is a **401**: 500 while starting,
401 once up and correctly refusing an anonymous caller. Expecting an error status is
legitimate and needs no credentials. For Server Tools it is `/ControlCenter`, 200,
with "System Landscape Directory" as the marker.

Two consequences already built:

- `grace_seconds` must exceed the *application's* readiness time, not the port's.
  Measured: ~25 s for Server Tools, and around 30 s for the web client's login
  screen. A short grace plus automatic restart would restart a service that is
  still coming up, and it would never finish.
- The grace window asks the question and ignores only the *answer*, so a service
  ready in nine seconds shows as ready in nine seconds instead of sitting at
  "Starting…" until the window expires. While it is starting the question is asked
  every five seconds whatever the service's own interval is — at "every 60s" the
  row would have sat at "Starting…" for the full minute regardless, which is the
  same wrong answer arrived at more slowly.

Verified through the interface rather than in a unit test, because the unit tests
all passed while the screen was wrong: the whole application restarting Server
Tools for real, photographed every two seconds. The row said "Starting…" from the
tenth second to the thirty-fourth and Running from the thirty-sixth — twenty-six
seconds of warm-up against 24.1 seconds of measured downtime — with the dot amber
and the tray gear turning in every one of those frames.

---

## Managing another Windows machine: three numbers that shaped the code — 2026-07-27

All three were bugs before they were measurements, and each one is invisible until
somebody points a stopwatch at it.

**Opening a connection costs everything; using it costs nothing.** Against a Windows
box in another domain (`10.77.3.112`, ours is `CT.CORP`, its is `SC`):

| | |
|---|---:|
`OpenSCManager`, three times over | 21032 ms, 21022 ms, 21033 ms |
`EnumServicesStatus` on a held handle | 28 ms, 18 ms, 19 ms |
`OpenService` + `QueryServiceStatus` on a held handle | 7 ms, 7 ms, 6 ms |

The code opened afresh for every question — status, start type and pid are three —
so reading one service's state cost 63 seconds. The connection is now held per
machine and reopened only when a handle goes stale: 21 seconds once per run, then
9 ms a poll.

**Where the 21 seconds goes.** A TCP connect to any high port on that machine takes
21036 ms to fail, which is Windows' SYN retry budget. RPC tries a dynamic TCP port
first, that port is firewalled, and it falls back to named pipes over SMB — which
works, twenty-one seconds later. `sc.exe` pays exactly the same 21.1 s, so this is
Windows' behaviour and not ours. Enabling the *Remote Service Management (RPC)* rule
on the target removes it.

**A Tomcat takes a minute to stop.** SAP's Server Tools, timed through the code that
restarts it:

    stop asked           0.0s  ->  Stopping
    Stopped             61.0s
    running again       62.8s

The budget was 30 seconds. When it ran out the code started the service anyway, and
Windows answers a start request with 1056, "already running", for a service in *any*
state other than Stopped — Stopping included. 1056 was on the list of errors meaning
"nothing to do", so the refusal was swallowed, the restart was recorded as a success,
and the service finished stopping in peace. The budget is two minutes now, the wait is
checked rather than assumed, and a service that will not stop raises an error that
"nothing to do" cannot forgive.

## The two lies a transport can tell — 2026-07-27

Both cost an evening, and both were a component claiming an ability it did not have.

**`push` meant "can read the journal".** The SSH connector set it from whether
`journalctl` worked, and a machine that claims push is excluded from polling. Nothing
in this app follows a journal — there is no `journalctl -f` anywhere — and the SUSE
box signs in as root, which can always read it. So every service on it sat at
"Unknown" for a whole session. It had been hidden by the startup priming pass asking
each service directly: the state was right once and then quietly frozen, which is the
failure the poller exists to prevent. Reading logs and being told about changes are
different abilities, and only the first is true over SSH.

**"Unknown" on a service had several explanations and no way to choose between them.**
So a machine's row now says whether it is answering — "connected · answered 3s ago",
"no answer, last tried 12s ago", or "not asked yet", which is a state of its own and
was the one in play. With that on screen the diagnosis is one glance instead of an
evening.

## Nothing that repeats is slow — 2026-07-27

Measured rather than assumed, on a nine-service, three-machine configuration, because
the honest answer to "should we optimise the UI" turned out to be no:

| Path | Median |
|---|---:|
`flyout.apply_states()` — runs on every event | 0.05 ms |
`hover._render()` — rebuilt on every refresh | 1.09 ms |
`machines_page.refresh()` — every 3 s while open | 3.17 ms |
`dashboard.apply_states()` | 0.05 ms |
`store.update()` with no change | under 0.01 ms |

The one to watch is the machines list, and 3 ms every three seconds is a thousandth of
a core. The expensive things in this application are all on the other side of a
network, which is why the rule that came out of tonight is about threads and not about
algorithms: **the UI thread only ever asks this computer.**

---

## A check has to measure the machine the service is on — 2026-07-27

Found while answering "why does it matter where the hub runs", and it was live in 2.1.0:
`_file` called `os.path.getmtime` and `_command` called `subprocess.Popen`, both ignoring
the service's machine entirely. So a heartbeat check on a Linux service measured a path on
the Windows box the app happened to be running on — and **passed**, if a file of that name
existed locally. The connectors had `run()` and `stat()` for exactly this from the day the
Linux transport was written; health never called them.

Both now go through the transport. Verified against the real machines:

| Check | Machine | Answer |
|---|---|---|
`systemctl is-active webclient.service` | hanadev, over SSH | ok, `active`, 891 ms |
`su - hdbadm -c "HDB info"` | hanadev | ok — which is what makes HANA checkable at all |
`/usr/sap/SAPBusinessOne/WebClient/startup.sh` exists | hanadev | ok |
`/no/such/heartbeat` | hanadev | fails: *not on sd* |
`C:\Windows\win.ini` **as if it were on hanadev** | hanadev | **fails** — it passed before |
`sc query MSSQLSERVER` | sc-sql, remote Windows | fails: *running a command on another computer is not supported yet* |
`C:\Windows\win.ini`, `cmd /c exit 0` | this computer | unchanged |

The last two rows are the point. A remote Windows machine cannot run a command, and saying
so is the whole job: the alternative is running it here and calling the answer that
machine's. And the local paths deliberately do **not** route through a connector — they are
a few lines of `os` and `subprocess` that have worked for months, and a registry lookup in
front of every check on the machine we are already standing on buys nothing.

**Why this decides where the hub goes.** Whatever machine the hub runs on is `machine=""`:
it gets push notifications (32 ms) instead of polling (5 s), needs no firewall rule, no
credentials, and is the only Windows machine where Kill, Command and File checks work at
all. Everything else is a remote target. So the hub belongs on the machine whose services
matter most — or the landscape has to accept polling and RPC rules for them.
