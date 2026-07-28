# What it cannot do

Version 2.2.0, checked against the code on 2026-07-28 rather than written from memory —
three entries here were found to be *lies* while this page was first compiled, and were
fixed instead of documented.

The rule this page exists to enforce: **a limit that is visible is a limit; a limit that
answers with the wrong machine's data is a bug.**

---

## By target

| Target | Watch | Control | Kill | Event log | Command check | File check | Instant |
|---|---|---|---|---|---|---|---|
| This computer | yes | yes | yes | yes | yes | yes | **yes** |
| Another Windows machine | yes | yes | **with WinRM** | **with WinRM** | **with WinRM** | yes | no, polled |
| A Linux machine over SSH | yes | yes | yes | journal | yes | yes | no, polled |

**"With WinRM" is a switch on that machine, off by default.** Kill, reading the event log
and running a Command health check need a transport that accepts a user name and password:
`sc`, `schtasks` and the admin shares ride the session on IPC$ and cross a forest boundary,
but `taskkill`, `tasklist` and WMI authenticate themselves and are refused. WinRM
authenticates itself too, and takes credentials.

What it needs, measured 2026-07-28:

| Where | What |
|---|---|
| a Windows **Server** target | nothing — 5985 is on by default |
| a Windows **10/11** target | `winrm quickconfig` on that machine |
| the machine doing the watching | the WinRM service, and the target in TrustedHosts. Done for you, and it needs administrator rights — which the hub has. |

**Why it is off by default, and stays a decision.** Every WinRM call authenticates, so it
writes a logon record to that machine's Security log. A Command health check every minute is
1,440 records a day, and somebody's SIEM will ask about it. **Test connection** tries and
sets the switch, so nobody has to know what WinRM is to get it right — and with the switch
off, not one PowerShell process starts.

**Still not possible on another Windows machine, with or without WinRM:** instant
notification. Its service manager has no doorbell to ring, so status is polled — 5 seconds
by default, per machine.

## By feature

### Only one operator, only one machine — **fixed in 2.2.0, if you install the hub**
This used to be the largest limit on the page: close the tray app, log off or shut down and
nothing watched anything.

With the hub installed, the engine is a Windows service — it watches, restarts, runs the
schedule and records history whether or not anybody is logged in, and every client reads
the same one. See [HUB.md](HUB.md).

**Without** the hub, the limit is unchanged and deliberately so: a single-machine install
is still a tray application, and closing it still stops everything. That is the right shape
for one administrator on one machine, and it is what an upgrade keeps.

Still true either way: **there is no clustering**. One hub is one point of failure. Windows
restarts it if it dies (`sc failure`, 5 s / 10 s / 30 s) and it comes back at boot, but a
hub whose machine is off is a landscape nobody is watching — and the clients say so rather
than showing stale rows.

### No agent, so no unreachable machines
A machine is manageable only if this computer can reach its service manager (RPC) or its
SSH port. A machine behind NAT, in a DMZ with no inbound rule, or on a network that only
allows outbound connections cannot be managed. An agent would solve it and is deliberately
not planned — see the reasoning in `ROADMAP.md`.

### No permissions
Whoever can run the app can control every service in it, and **every client of a hub can do
everything any other client can** — this was asked for explicitly and is not an oversight.
There is no read-only mode and no per-service rights.

What 2.2.0 adds is the *record*: every action carries the name of whoever asked
(`events.actor`, the History page's "Asked by" column, and a line in the hub's log). So
"who restarted this at 03:00" is answerable, and "who was allowed to" still is not.

A read-only token is the obvious next step and is not built. Revoking a client is the only
control there is today — from the panel's **Clients** page, or:

```bat
ServiceOfficerHub.exe client revoke ismail-laptop
```

Issuing tokens is on that page too, from any client. That follows from the same fact rather
than being a separate decision: a client that can put a `command` health check into the
configuration can already run anything on the hub as LocalSystem, so there is no boundary
for a hidden button to defend.

### Notifications
Windows toasts only, on the machine the app runs on. No mail, no Teams, no webhook.
`smtplib` is the intended route and the design is in `DECISIONS.md`; nothing is built.

### Nothing at all for
- **Maintenance windows.** The health monitor has the hook (`in_maintenance`) and nothing
  fills it, so a planned outage looks like a failure and may be restarted by the watchdog.
- **Uptime or SLA reporting.** The `uptime_daily` table exists in the schema; nothing
  writes it.
- **Resource graphs.** No CPU, no memory, no history of either.
- **Auto-update.** Releases are published on GitHub; the app never looks.
- **Code signing.** The installer is unsigned, so SmartScreen warns on a customer's
  machine.
- **A web interface.** The hub serves one read-only page at `https://<hub>:8797/` — the
  services, their states, and live updates. That is a proof that the API can carry a
  browser UI, not a browser UI: nothing on it acts, configures or filters.
- **Service dependencies.** Windows knows which services depend on which; this does not
  read them. A stack is the manual answer.
- **Changing a service's start type**, installing or removing a service, or editing
  anything about a service other than how we watch it.

### Scheduling
Triggers are: a time of day (optionally on chosen days), a repeat interval from that time,
or once per Windows boot. There is no cron expression, no "last Friday of the month", and
no calendar. A startup trigger fires once per **boot**, not once per app start — which is
what stops three installs in an evening from restarting a working stack three times.

### Stacks
Steps run in order, one at a time, on one thread. No parallel steps, no branching, no
"stop on failure vs continue" choice beyond what each step waits for.

### The service list
Windows: `SERVICE_WIN32` only, so drivers are not offered. Linux: `.service` units only, so
timers, sockets and targets are not offered.

### Health checks
- Five kinds, and the two most powerful of them (File, Command) are unavailable on a remote
  Windows machine — see the table above.
- Every check runs **from wherever the app runs**. `https://hanadev/...` answers the
  question "can this machine reach it", which is usually the one you want and is not always
  the same as "can a user reach it".
- A service with no checks is judged only by whether the service manager says Running,
  which is the "running but dead" gap the checks exist to close.

### Data
- `services.json`, `history.db` and the log live in `%ProgramData%\Service Officer` on the
  machine the app runs on. Nothing is central, nothing is backed up, nothing is replicated.
- One writer. Two copies of the app on one machine would fight over the database.
- Passwords are encrypted with DPAPI **machine** scope, so **any administrator on this
  computer can decrypt them**. That is stated in the panel where a password is entered. The
  alternative — user scope — cannot be read by a service, which is where the engine is
  going.
- History retention is by age only. There is no size cap.

### Platform
Windows 10/11 or Server for the app itself, and administrator rights to run it. Linux and
macOS are targets, never hosts — and only Linux, only over SSH, only systemd.

---

## Three things this page found that were bugs, not limits

Written down because they are the same mistake three times, and the fourth will be too:

1. **File and Command checks ignored the service's machine** and ran locally. A heartbeat
   check on a Linux service measured a path on the Windows box, and passed if a file of
   that name existed there. Fixed: they go through the transport, and are not offered where
   the transport cannot do them.
2. **A remote machine's "logs" were this computer's event log**, matched by service name.
   Fixed: `logs` is false for a remote Windows machine and the call says why.
3. **The history's timeline merged this computer's event log into a remote service's
   history**, so our events appeared under that machine's name. Fixed: the caller has to
   say which services are local, and a caller that does not gets no event-log rows.

The pattern: a feature that cannot work for a target must **fail loudly or not be offered**.
Answering from the wrong machine is the one outcome that is worse than not answering.
