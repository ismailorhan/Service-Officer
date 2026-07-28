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

---

## Kill, File and Command on another Windows machine — researched 2026-07-27

Asked whether the three "no"s for a remote Windows target could be made yes. Probed the
mechanisms against the real SAP/SQL server (10.77.3.112, in the SC domain from CT.CORP),
each riding — or not — the IPC$ session core/win_session.py already holds:

| Wanted | Mechanism | Rides IPC$? | Result |
|---|---|---|---|
File | admin share `\host\C$\path` | yes | **18 ms**, and through the real health path 9 ms |
control | `sc \host`, held connection | yes | works (the 21 s first-open, then ms) |
Command | `schtasks /Create /S` | writes; needs Task Scheduler rights | *Access denied* |
Command/Kill | WMI / CIM over DCOM | no — dynamic RPC + WinRM | TrustedHosts / firewalled |
Kill | `taskkill /S`, `tasklist /S` | **no — authenticates itself** | *user name or password is incorrect* |
Command/Kill | WinRM `Invoke-Command` | separate service, 5985 | open on this server; cross-forest needs config |

The clean line: **anything that travels over the SMB named pipe uses the session we
already established; anything that does its own RPC authentication does not**, and across
a forest boundary its own attempt fails. `sc`, `schtasks` and the admin shares are on the
first side; `taskkill`, `tasklist` and WMI are on the second.

So **File is built** — a path translation, `C:\x` → `\host\C$\x`, nothing installed on
the target, and `abilities.file_check` is true for a remote Windows machine so the editor
offers it. `command_check` and `kill` stay false.

**Command and Kill are one transport away, and it is WinRM.** It was open on the server
tested (5985), it is on by default on Windows Server, one fixed port with no dynamic RPC
range, and it would slot in as a third `Connector` implementation exactly as SSH did —
`Invoke-Command` for a command, `Stop-Process -Id` for a kill (the SCM already gives us the
pid). The cross-forest case needs TrustedHosts or HTTPS with explicit credentials, which is
the same DPAPI-stored password already in play. schtasks was the other candidate and is
worse: it writes, it needs Task Scheduler rights the account did not have, and the
create-run-read-delete round trip measured ~77 s against File's 9 ms.

**Not built now** because it is a transport, not a tweak, and the thing that makes it
matter most — a machine where these need to work — is the same machine you would put the
hub on, where they already do. A WinRM connector is the right next step *after* the hub, or
for a Windows target that genuinely cannot host one.

---

## The hub: what was decided while building it, 2026-07-28

The plan is
[docs/superpowers/plans/2026-07-27-hub-service-and-clients.md](superpowers/plans/2026-07-27-hub-service-and-clients.md).
These are the places where building it taught something the plan did not know, or where
the plan was wrong and was overruled.

### The client's administrator manifest: not moved, made conditional

The plan said move `--uac-admin` from the tray application to the hub, because "the tray
application no longer touches a service manager". That is true of a client of a hub and
**false of the single-machine install everybody has today**, which drives this computer's
SCM directly. Shipping it as written would have turned every button in that install into
*access denied* — silently, because a query needs no rights and a control does.

So the manifest is gone and the question is asked at run time (`app.needs_elevation`):
elevate only when this launch will do the work itself, which means no hub address
configured and none being passed. A client never prompts; an embedded install prompts
exactly as it always did. Four tests, one per branch, because this is the kind of change
that appears to work.

### `Config.hub.enabled` removed before it could lie

It existed, defaulted to False, and **nothing ever read it** — `hub.py` serves whenever
the service runs. A second switch would have made "the service is running and nothing
answers" possible, which is the worst kind of configuration. Installing the hub component
is the decision to serve; there is no other.

### A client added while the hub was running was refused

Found by pasting a real token into the real page. The client list is cached to avoid a
DPAPI decrypt per request — 13.2 ms, measured, against 0.27 ms to build a snapshot — and
the cache was invalidated *on write*. But `client add` is a console command in a **different
process** from the service, so the hub never did the write and never dropped the cache. The
only cure would have been restarting the hub in the middle of pairing a client.

Now the cache carries the store file's `(mtime_ns, size)` and re-reads when it changes:

| | Cost |
|---|---|
`os.stat` on the store | 55 µs |
warm token check, including the stat | 59 µs |
cold check (DPAPI decrypt) | 13.2 ms |

240× cheaper than what it saves, so it is taken on every request. Proved with a real
second interpreter writing the same store, not with a monkeypatch.

### A token on a URL, and out of the log

`EventSource` cannot send an `Authorization` header, so the browser page's stream carries
its token as a query parameter — **only** that endpoint; a token on any other path 401s,
which is a test. And `hub_server.log_message` redacts `token=` before writing, because the
log is the thing people paste into tickets.

### The verdict goes on the wire, not into each UI

`st.effective()` exists because the flyout, the hover card and the tray icon each worked
out "Running but failing its checks means *Not responding*" separately, and disagreed with
each other about the same service at the same moment. The browser page was about to be the
fourth: it had a lookup table of its own. `wire.service_row` now sends `state_label` and
`state_category`, and the page draws what it is told — including counting "N of M running"
from the category, so the count cannot contradict the row beside it. The same bug, in the
same shape, as the one fixed in the panel on 2026-07-26.

### `events.actor`, and what stays blank

Schema v3 adds one column by `ALTER TABLE`, so an existing history keeps its rows. The
watchdog, the scheduler and a trigger write it **empty**: `source` already says what kind
of thing asked, and inventing a name for something nobody asked for would be worse than
the gap. The History page hides the column when nothing in view fills it — so the
single-machine install never sees it — while the CSV export always carries it, because a
file in a ticket is read by something that cannot cope with columns that come and go.

### Two bugs in one flaky test, 2026-07-28

The build failed on `test_the_client_survives_the_hub_going_away_and_coming_back`, which
had passed twenty minutes earlier. Under load it was slower, and slower exposed both of
these. Neither was flakiness.

**The handshake had a gap.** The client fetched a snapshot, announced itself connected,
*then* opened the event stream. A change published in that window went to nobody — and
because a store publishes only *changes*, the client did not show a stale value for a
moment, it showed the wrong one until the same service happened to change again. The order
is now stream → snapshot → events: the hub queues per listener from the moment a stream
opens, so anything during the snapshot arrives after it, in order.

**`stop()` did not stop.** It set a flag the reader only looks at between lines, and an
idle SSE stream says nothing for a keepalive interval — **20.1 s measured**. Worse, `start()`
refuses to run while the old thread is alive, so a stop that had not finished made the next
start a silent no-op. It now shuts the socket down, which is the only thing that interrupts
a blocked read:

| | Before | After |
|---|---|---|
`stop()` | 20.1 s | under 0.1 s |
the client test file | 328 s | 30 s (the teardowns were the whole difference) |
`start()` after `stop()` | silently did nothing | connects |

Holding the socket meant using `http.client` for that one request instead of `urlopen`:
an SSE response has no length and no chunked framing, so `getresponse()` hands the socket
to the response and forgets it, and `urlopen` never exposed it in the first place.

It also runs on the way out of the application, where twenty seconds is a window that does
not close.

### The service that would not have started, 2026-07-28

`hub.py` had no test file, and the one path nothing exercised was the only path a service
ever really takes. The SCM launches the exe with **no arguments** and expects
`StartServiceCtrlDispatcher` within about thirty seconds; `main()` fell through to
pywin32's `HandleCommandLine`, which found no command, printed usage and exited. Windows
calls that error 1053, "the service did not respond in a timely fashion".

Nothing that had been run could have found it: `--console` takes a different branch, and
the tests never ran `main()` at all. `tests/test_hub_service.py` now covers every way of
being started, one test each, and the fix was checked by removing it and watching the
right test fail.

The general lesson, which has now cost twice: **an entry point is code.** `app.main()` and
`hub.main()` are where the arguments are interpreted, and an interpretation nothing asserts
is a guess.

### Three things the first real install taught, 2026-07-28

2.2.0 was installed on this machine with both components. The hub came up correctly —
delayed automatic start, recovery policy 5 s / 10 s / 30 s, the firewall rule on the
domain profile only, `{"protocol": 1, "version": "2.2.0", "name": "CTL052"}` on the port,
the SUSE box's `webclient.service` reported healthy and `10.77.3.112`'s service manager
connected. **The tray application crashed on startup.**

`RemoteStore.counts()` answered three values where `Store.counts()` answers two, and the
tray unpacks two. Every contract test passed because they asked whether the method
*existed* — and `test_client_mode.py`'s fake hub carried a *local* Store, so the real
widgets had never met a remote one. Both gaps are closed: the contract test now compares
what the two stores actually answer given the same facts, and three tests drive the tray,
the hover card and the whole of `_refresh_lists` against a real `RemoteStore`.

**Every connection to the hub by name cost two seconds.**

| Connecting to | Before | After |
|---|---|---|
| `https://CTL052:8797` | 2073 ms | 2 ms |
| `https://10.77.3.50:8797` | 0 ms | 0 ms |
| `check_identity` | 2040 ms | 2 ms |
| `refresh_now` | 2060 ms | 12 ms |
| first frame in a client | ~4.1 s | ~14 ms |

`CTL052` resolves to eight addresses and the first is a link-local IPv6 one; the hub
listened on `0.0.0.0`, so every connection waited for that attempt to give up before
falling back to IPv4. Nothing was misconfigured. One socket bound to `::` with
`IPV6_V6ONLY` off answers both families — which is what Python's own `http.server` does,
for the same reason. A `bind` that names a specific address is still taken literally.

**Closing the app logged an ERROR with a stack.** `stop()` shuts the socket down on
purpose, the reader's read fails with WinError 10053, and that was reported as "the hub
connection failed" every single time anybody closed the tray app normally. The log is what
somebody reads when something has actually gone wrong, so a deliberate close is one line
at INFO now.

### The data folder was writable by everyone, and the hub made that dangerous

Found on 2026-07-28 by reading the ACL of the install that had just been made, while
working out why a client had written nothing to the log:

    BUILTIN\Users   Write   Allow        <- inherited from ProgramData

Inno Setup's `Permissions: admins-modify` **adds** rights; it does not remove what
ProgramData hands down. `core/secrets.py` had claimed for months that "the file's ACL
(Administrators and SYSTEM, from the installer) is what keeps everyone else out". That was
written down before it was true.

Before 2.2.0 it was untidiness. The app ran elevated, as the administrator sitting in
front of it, so a user who edited `services.json` gained nothing they did not already
have. **With a hub it is a local privilege escalation:** the hub runs as LocalSystem, and
a health check of kind `command` is a shell command line stored in that file
(`subprocess.Popen(check.command, shell=True)`), so anyone who could write the file could
run anything as SYSTEM.

The installer now strips inheritance:

    icacls "%ProgramData%\Service Officer" /inheritance:r
        /grant:r *S-1-5-18:(OI)(CI)F         SYSTEM              full
        /grant:r *S-1-5-32-544:(OI)(CI)F     Administrators      full
        /grant:r *S-1-5-11:(OI)(CI)RX        Authenticated Users read

SIDs rather than names because this runs on Turkish Windows, where the Users group is
"Kullanicilar" and an English name matches nothing at all — silently. Verified by applying
it to a directory and then being refused a write as an ordinary user.

**What that broke, and where it went instead.** A tray application that is no longer
elevated cannot write there either, and it has three things of its own to keep:

| | Was | Now |
|---|---|---|
| `client.json` (pairing, theme, notifications) | the machine's data folder | `%LOCALAPPDATA%\Service Officer`, with the machine-wide copy still *read* as a fallback |
| the client's token | the machine's `secrets.dat` | the user's own; the machine's is still read |
| the client's log | the machine's, or nowhere | the machine's if writable, else the user's |

Reading the machine-wide copies is what keeps `client pair --local` working for a second
person who logs into the same server: they inherit a working pairing and cannot overwrite
anybody else's. Writing is always their own.

### WinRM: what it cost to open three abilities, 2026-07-28

Researched on 2026-07-27 and deferred as "a transport, not a tweak". Built now, after
measuring rather than assuming.

**What was measured first.**

| | |
|---|---|
`sc-sap-sql` (Windows Server), WinRM identify | **OK** — 5985 open, nothing configured |
`10.77.3.110` (Windows 11) | no answer: `winrm quickconfig` needed there |
Kerberos, by name, CT to SC | `0x80090311` — no authenticating authority, i.e. no forest trust |
NTLM, by IP or by name | needs the target in this computer's TrustedHosts |
this computer's WinRM service | Stopped, and TrustedHosts cannot even be *read* while it is |
`powershell.exe` start, trivial script | **103 ms** best, 124 ms average, against 9 ms for a held SCM query |

So: nothing to configure on a Windows Server target, one command on a client target, and two
settings on the machine doing the watching. The client-side settings are applied
automatically, one machine at a time, never `*`.

**Three implementation choices, each from something that went wrong the same afternoon.**

*A script file written as UTF-8 with a BOM, not `-EncodedCommand`.* Base64 of UTF-16LE
cannot be broken by quoting or by a code page, which is why it was the first choice — three
failures that day came from a `.ps1` read as ANSI, a here-string, and an em-dash. But base64
PowerShell is a malware indicator to most EDR products, and a management tool that trips the
customer's security team is a management tool the customer blocks. A BOM buys the same
correctness without looking like an attack.

*Failures reported on stdout behind a marker.* PowerShell run as a child process wraps
stderr in CLIXML — progress records and all — and the first probe reported an XML document
where an error message belonged. CLIXML that arrives anyway is decoded into the sentence
inside it rather than shown.

*Kill by process id, never by name.* The service manager has already said which process a
service is; killing by name on a machine running two of them is a different and worse thing.

**A switch, not a detection.** `Machine.winrm`, off by default. Every call authenticates, so
it writes a logon record to the target's Security log — a check every minute is 1,440 a day.
Test connection probes and sets the switch, which is how somebody who does not know what
WinRM is ends up with it right; and with the switch off, `abilities()` starts no process at
all. Off is off.

**Every refusal is an instruction.** The TrustedHosts command with the machine already in
it; "give it a user name and password" for a missing forest trust; "use its name" for
Kerberos against an IP; `winrm quickconfig` for a machine with it switched off; "an account
in its Administrators group" for a refusal. All five were collected from real attempts, and
each one is a test.

**What is still not possible:** instant notification from another Windows machine. WinRM
does not change that — there is no doorbell on a remote service manager.
