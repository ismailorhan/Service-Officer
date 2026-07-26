# Decisions

Answers to the design questions that came up, with the reasoning, so the next
person (or the next session) doesn't have to re-derive them. Dated, because some
of these depend on where the product is rather than on first principles.

---

## Where data lives, and in what format — decided 2026-07-25

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
