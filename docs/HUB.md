# The hub

One machine does the work; every Service Officer on the network reads it.

Without a hub, Service Officer is a tray application that manages services itself. That
is still the right shape for one administrator on one machine, and nothing here is
required. This document is about the other case.

## When you want one

- **Five people, one truth.** Two administrators each running their own copy poll the
  same servers twice, and each sees a slightly different moment. With a hub there is one
  answer and everybody is reading it.
- **The configuration lives on a server.** Add a service once, on the hub, and every
  client has it. No file to copy, no two lists that disagree.
- **State survives a workstation reboot.** The watchdog, the schedule and the health
  checks keep running when the last person logs out — because they are running in a
  service, not in somebody's session.
- **Who did what.** Every action is recorded with the name of the person who asked for
  it. With one operator that was answerable from memory; with five it is only answerable
  from the record, and the History page has an "Asked by" column for it.

## Where to install it

**In the same domain as the servers it manages** — that is the whole decision, and it is
worth being blunt about why.

Measured on 2026-07-26, on this network: a workstation in domain `CT` cannot open a
service manager on a server in domain `SC`, no matter what rights the account holds at
home. `SC` and `taskkill` authenticate themselves and are refused. So:

| The hub is | What it can manage | What you have to store |
|---|---|---|
| In the targets' domain, running as a domain service account | everything, with no credentials anywhere | nothing — Windows does it |
| In another domain | everything, using per-machine credentials | one username and password per machine, in the DPAPI store on the hub |

The second row works — it is what the Machines page's **User** and **Password** fields
are for, and it was built and measured on 2026-07-26 — but it means secrets on disk.
The first row means none. Prefer the first row.

The client machines can be anywhere. They talk to the hub over one TCP port and never
touch a service manager themselves.

## Installing

One installer, with components:

```bat
ServiceOfficerSetup.exe
```

- **Client only** — the tray application. What a workstation gets.
- **Hub only** — the Windows service. What a server nobody logs into gets.
- **Both** — this machine serves and reads. Comes out of the installer already paired,
  with no token to carry anywhere.

Silently:

```bat
:: a server: the hub, no tray icon
ServiceOfficerSetup.exe /SILENT /NORESTART /TYPE=hub

:: a workstation, already pointed at its hub
ServiceOfficerSetup.exe /SILENT /NORESTART /TYPE=client /HUBURL=https://ctl052:8797 /HUBTOKEN=xxxxxxxx

:: this machine, both
ServiceOfficerSetup.exe /SILENT /NORESTART /TYPE=full
```

An **upgrade needs no `/TYPE`** — Inno Setup preselects whatever is already installed, so
`ServiceOfficerSetup.exe /SILENT /NORESTART` upgrades a hub as a hub and a client as a
client.

A token on an installer command line is visible in the process list while the installer
runs, and in whatever log a deployment tool keeps. The alternative is to install without
it and pass `--token` once on the first launch. Neither is worse than the other; what
matters is that nobody is surprised by it.

### The service account

The hub is registered as **LocalSystem**. Change it afterwards:

```bat
sc.exe config ServiceOfficerHub obj= "CT\svc-officer" password= "the password"
```

Typed by a person in a console, once. The installer does not ask for it on purpose: Inno
Setup would hold it in memory and in its own log, an unattended install would need it on
a command line, and the entire point of running as a domain account is to *stop* storing
credentials.

The account needs **Log on as a service** (`secpol.msc` → Local Policies → User Rights
Assignment). This is the single most common reason a service account fails to start, and
the message Windows gives for it is unhelpful.

Then:

```bat
sc.exe stop ServiceOfficerHub && sc.exe start ServiceOfficerHub
```

### The firewall

The installer adds one rule, **domain profile only**:

```powershell
Get-NetFirewallRule -DisplayName "Service Officer Hub" | Select-Object Enabled, Profile, Direction
```

Domain only is deliberate. A laptop or a server can find itself on a network it did not
expect, and a management port has no business being open there.

### What Windows does when it dies

Set at install, and the layer that matters most because it survives the process being
gone entirely:

```powershell
sc.exe qfailure ServiceOfficerHub
```

Restart after 5 s, then 10 s, then 30 s for anything further, count forgotten after a
day. Start type is **delayed automatic**: at boot the services this hub manages are
starting too, and asking about them while they do produces a page of "not responding"
that nobody needs.

## Adding a client

On the hub:

```bat
"C:\Program Files\Service Officer\ServiceOfficerHub\ServiceOfficerHub.exe" client add ismail-laptop
```

It prints the token **once**, with the exact command to run on the client and the
certificate fingerprint that client should see. On the client:

```bat
ServiceOfficer.exe --connect https://ctl052:8797 --token <the token>
```

The token is stored (DPAPI, machine scope) and the certificate is pinned. After that the
flags are not needed again — the client remembers.

**Where it remembers.** In that user's own profile:

```
%LOCALAPPDATA%\Service Officer\client.json      which hub, its fingerprint, the theme
%LOCALAPPDATA%\Service Officer\secrets.dat      the token
```

Per user, because the tray application is not elevated and the machine's folder is not
writable by it — and because one person pairing should not repoint everybody who logs into
the same computer. A machine-wide `client.json` beside `services.json` is still **read**
when a user has none of their own, which is what makes `client pair --local` work for the
next person who signs in: they inherit the pairing and cannot overwrite anyone else's.

### The data folder, and who may write it

The installer locks it down, because the hub reads `services.json` as **LocalSystem** and a
health check of kind `command` is a shell command line in that file. If everybody could
write it, everybody could run code as SYSTEM.

```powershell
icacls "C:\ProgramData\Service Officer"
```

Expect SYSTEM and Administrators with full control, and Authenticated Users with read only.
If you see the built-in Users group with `(W)` — which is what ProgramData hands down by
default, and what installs made by 2.2.0 were left with — put it right in an elevated
console:

```bat
icacls "%ProgramData%\Service Officer" /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F /grant:r *S-1-5-32-544:(OI)(CI)F /grant:r *S-1-5-11:(OI)(CI)RX
```

Who is paired, and who is still talking:

```bat
ServiceOfficerHub.exe client list
ServiceOfficerHub.exe client revoke ismail-laptop
```

A revoke takes effect immediately, including on a hub that is already running.

## The certificate

Self-signed, made once, kept for ten years, at:

```
C:\ProgramData\Service Officer\hub.pem
```

It is **pinned by fingerprint**, not trusted by a chain — there is no CA here, and a pin
is stronger in this setting than a chain nobody checks. Read it off the hub with:

```bat
ServiceOfficerHub.exe --fingerprint
```

which prints something spelled exactly the way this app already spells an SSH host key,
because it is the same idea:

```
SHA256:hkAF3xB9uY0mZ1s4W7cQ+dEr8tG5jK2nP0vX6yL9aQc
```

**If it changes**, every client refuses to connect and says so — which is the point. It
changes when `hub.pem` is deleted or the machine is rebuilt. Fix it on each client by
connecting again with the new fingerprint accepted:

```bat
ServiceOfficer.exe --connect https://ctl052:8797 --token <a new token>
```

Do not work around a fingerprint change you cannot explain. That is the one thing the
pin is for.

## A browser

The hub serves one page at `https://<hub>:8797/`. It asks for a token, lists the services
and their states, and follows the same event stream the clients do. It is deliberately
small — a read-only view, not the tray application — and it exists to prove the API is
enough for a browser UI.

## Troubleshooting

| Symptom | Check | Fix |
|---|---|---|
| Client says "cannot reach the hub" | `Get-Service ServiceOfficerHub` | `sc.exe start ServiceOfficerHub`; if it stops again, the log below says why |
| ...and the service is running | `curl.exe -k https://<hub>:8797/api/v1/ping` from the client | if it fails from the client but works on the hub, it is the firewall rule or the profile it is on |
| Client says the certificate changed | `ServiceOfficerHub.exe --fingerprint` on the hub | if the hub was rebuilt, re-pair the client; if it was not, stop and find out why |
| "A valid token is needed" | `ServiceOfficerHub.exe client list` | issue a new one with `client add`; tokens are not recoverable, only replaceable |
| The service will not start as a domain account | Event Viewer → System, and the log below | almost always **Log on as a service** (`secpol.msc`) |
| The hub is up but a target machine says "no answer" | the Machines page — it names the reason on the machine's own row | that row is the answer: a closed port, a refused sign-in, a machine that is off |
| Something restarted and nobody knows why | the History page, "Asked by" column | a blank there with source *watchdog* means the watchdog did it, and the row above says what it saw |

The log, on the hub, for all of the above:

```
C:\ProgramData\Service Officer\service-officer.log
```

It rotates at 1 MB, keeps three, and holds everything the hub did including who asked.

A **client's** log is not in there. That folder belongs to the machine and to the hub
service — administrators and SYSTEM can write it, everybody else can only read — and the
tray application does not run elevated, so it keeps its own:

```
%LOCALAPPDATA%\Service Officer\service-officer.log
```

An install with no hub is unchanged: that app *is* elevated, so everything stays in one
file.

To watch the hub in a console instead of as a service — the fastest way to see a startup
failure:

```bat
sc.exe stop ServiceOfficerHub
ServiceOfficerHub.exe --console
```

It needs administrator rights, like the service, and for the same reason: it is the half
that talks to a service manager.
