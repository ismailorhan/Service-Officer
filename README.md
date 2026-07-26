# Service Officer

A Windows tray app for the servers an ERP runs on. It watches services, restarts
them when they fail, runs them in the right order, and tells you when one is
running but not actually answering — without opening Services MMC and guessing.

Version 2.1.0. Python 3.12 + PySide6, packaged with PyInstaller and Inno Setup.

## What it does

**Tray icon** — one glance: green all running, red all stopped, amber mixed,
grey nothing configured. Hover for a card listing every service and its state;
the Windows tooltip holds 128 characters, which isn't enough for four services.

**Flyout** (left-click) — the list, grouped by category, with Start / Stop /
Restart per service and Kill for the ones that wedge. Tick several rows and act
on all of them at once.

**Service Management Panel** (double-click, or right-click → Manage) — the same
controls with room to work, plus everything that is configuration:

| Page | What it is for |
|---|---|
| Dashboard | Live status and controls, the flyout without the hurry |
| Services | What to watch, per service: recovery, health checks, machine |
| Categories | The headings the flyout and dashboard group by |
| Stacks | An ordered run — SQL Server, then AppEngine, then WMS |
| Schedule | Triggers: a time of day, a repeat, or at startup |
| History | What the app did, filterable, exportable to Excel |
| Machines | The servers whose services this panel can reach |
| Settings | Theme, auto-start, and the handful of app-wide choices |

**Recovery** — when a service stops on its own, start it again: up to N attempts,
with a wait, and a flap window so a service that dies in a loop stops being
restarted instead of being restarted for ever.

**Health checks** — "running" is not the same as "working". A service can hold a
port open and answer nothing. Five kinds of check, per service:

| Kind | Passes when |
|---|---|
| TCP | a port accepts a connection |
| HTTP | a URL answers with an acceptable status |
| Process | a named process is alive |
| File | a file exists, and optionally was written recently |
| Command | a command exits 0 |

A service that fails its checks for long enough shows **Not responding** rather
than "Running", and can be restarted automatically — with a cooldown, so a
restart loop is not the cure.

**Stacks** — start or stop several services in order, each step waiting for the
one before it: for a delay, or until the service reports the state you asked for.

**Other machines** — the panel manages more than the computer it runs on, and how
each one is reached is a property of that machine rather than a mode of the app:

| Target | Reached by | Needs on that machine |
|---|---|---|
| This computer | the service manager directly | nothing |
| Another Windows machine | the service manager over RPC, as the signed-in account or as a named one (`DOMAIN\account` and a password kept encrypted here) | the *Remote Service Management* and *File and Printer Sharing (SMB-In)* firewall rules, in the domain profile |
| A Linux machine | `systemctl` and `journalctl` over SSH, by key or password, with the host key pinned | nothing installed — an account, and `sudo` without a password if you want to control rather than only watch |

A Linux service is not a second-class one: the same rows, the same recovery, the
same health checks, the same history. `sapb1servertools.service` and
`CompuTec AppEngine` sit in one list.

## Requirements

- Windows 10 / 11
- Administrator rights. Starting and stopping services needs them, and the data
  directory under `%ProgramData%` only lets administrators write. The exe ships
  with a `requireAdministrator` manifest, so UAC prompts on launch.

## Install

```bat
dist\ServiceOfficerSetup.exe
```

Two optional tasks in the wizard: start automatically when Windows starts
(checked), and a desktop shortcut (unchecked). Auto-start can be changed later
in the panel's Settings page.

## Run from source

A build takes about ninety seconds. This takes two, and it is the same code —
only the version line reads "running from source". Run it elevated.

```bat
run.bat
```

```bat
run.bat -t
```

```bat
run.bat -p
```

Bare runs the app, `-t` runs the tests, `-p` opens the panel on its own with no
tray — the quick loop for UI work.

## Build

```bat
build.bat
```

Stamps the version, runs the tests, and produces a one-dir PyInstaller build
with the admin manifest embedded. Then compile the installer:

```bat
iscc installer.iss
```

Output: `dist\ServiceOfficerSetup.exe`.

Versions are `2.1.0` for a release and `2.1.0.N` for internal builds, where N is
a build counter in `.build-number` (not committed). `stamp_version.py` refuses to
build if `core/version.py`, `installer.iss` and the git tag disagree.

## Where things are written

Everything lives in one directory, because this is a machine-level tool: which
services matter on a server is a property of the server, not of whoever happens
to be logged in.

```
C:\ProgramData\Service Officer\
  services.json          config, rewritten whole and atomically
  history.db             what happened — SQLite, one immutable row per event
  service-officer.log    application log, rotating 1 MB × 3
```

Three files, three formats, on purpose. `services.json` is a document read and
rewritten as a whole, and hand-editable in an emergency. `history.db` is queried,
filtered, aggregated and exported by the app itself — and it has to survive being
written by one process while another reads it, which a file that gets rewritten to
apply retention cannot. The `.log` is prose for a human reading a tail when the
*app itself* misbehaves.

Times are stored in UTC and shown in local time — see `core/clock.py` for why
(the short answer is daylight saving, and that a hub will interleave several
machines' events).

Data from earlier versions is carried forward on first run and nothing is
deleted: files from `%ProgramData%\ServiceOfficer` or `%APPDATA%\ServiceOfficer`
are copied across, newest first, leaving the originals alone; and a
`history.jsonl` from before the SQLite move is imported into `history.db` and then
renamed `.migrated`.

## Layout

```
app.py             wiring: tray, timers, and who hears what
core/              no UI imports at all
  config.py        the typed model, atomic save, migration
  control.py       one door to every target; picks the transport and nothing more
  connectors.py    what a transport has to be able to do, and the registry of them
  scm_windows.py   transport: the Windows service manager, local or remote
  win_session.py   signing in to another Windows machine as a named account
  ssh_linux.py     transport: systemctl and journalctl over SSH
  poller.py        statuses for the machines that cannot tell us themselves
  scm.py           push notifications, for the one machine that can
  state.py         status cache and event bus
  health.py        the five checks, and the monitor that acts on them
  watchdog.py      recovery rules
  stacks.py        the ordered runner
  schedule.py      triggers
  history.py       append, query, export
  db.py            the SQLite event store and its migrations
  clock.py         one rule about time: store UTC, show local
  secrets.py       passwords, encrypted per machine, never in services.json
  applog.py        the rotating log
  version.py       one place that knows the version
ui/                Qt only
  theme.py         every colour, metric and glyph — the single source of truth
  tray.py  flyout.py  hover.py  dashboard.py  rows.py  widgets.py  icons.py
  panel.py         the window: sidebar, current page, Save
  pages/           one module per section of the panel's menu
tests/             pytest, headless where it can be
```

`ui/theme.py` owns the stylesheet; no widget carries a colour of its own, so a
theme change is one call rather than a tour of the tree. `docs/` holds the
decisions, the roadmap and the architecture assessment.
