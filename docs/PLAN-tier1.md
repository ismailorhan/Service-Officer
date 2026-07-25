# Plan — Watchdog, Ordered Stacks, History

Detailed build plan for roadmap features 1–3, plus the settings screen they all
need. Read `ROADMAP.md` for why these three come first.

---

## 1. Config model

`%APPDATA%\ServiceOfficer\services.json` grows from a flat list into this.
`config.load_services()` already promotes legacy plain strings to dicts, so old
files keep working; add the same tolerance for missing sections.

```jsonc
{
  "version": 2,
  "services": [
    {
      "name": "AppEngine",
      "label": "CompuTec AppEngine",
      "recovery": {
        "enabled": true,
        "max_attempts": 3,          // 0 = keep trying
        "delay_seconds": 10,        // wait before attempt 1
        "backoff": 2.0,             // delay *= backoff each further attempt
        "restart_on_clean_stop": false,
        "flap_threshold": 5,        // stops...
        "flap_window_minutes": 30   // ...within this window ⇒ give up
      }
    }
  ],
  "stacks": [
    {
      "name": "SAP B1 stack",
      "steps": [
        { "service": "MSSQLSERVER",     "wait": "running", "timeout_seconds": 120 },
        { "service": "CTLicenseServer", "wait": "running", "timeout_seconds": 60  },
        { "service": "AppEngine",       "wait": "delay",   "delay_seconds": 15    },
        { "service": "WMSServer",       "wait": "running", "timeout_seconds": 60  }
      ]
    }
  ],
  "history": { "enabled": true, "retention_days": 30 },
  "notifications": { "on_crash": true, "on_recovery": true, "on_give_up": true },
  "auto_start": true
}
```

Defaults live in one place (`config.RECOVERY_DEFAULTS`, `config.HISTORY_DEFAULTS`)
and are merged on load, so a service written by an older version gains sane
recovery settings without a migration step.

---

## 2. Feature 1 — Watchdog (`watchdog.py`)

### Deciding what counts as a crash

The interesting question isn't "did it stop" but "did it stop *on purpose*".
Three sources, three answers:

| Stop came from | How we know | Default |
|---|---|---|
| Our own panel / stack run | `_action_active` counter, already exists | never restart |
| A crash | `SERVICE_STATUS_PROCESS.dwWin32ExitCode != 0` | restart |
| A clean external stop (services.msc) | exit code `0`, no action of ours in flight | leave alone, unless `restart_on_clean_stop` |

That third row is the one that saves us from fighting an administrator who
deliberately stopped a service. It needs `scm_notify` to pass the exit code
along, which is already in the struct we parse — only the payload widens:

```python
on_change(name, status, exit_code, process_id)
```

### Attempt loop

- On a qualifying stop: schedule attempt 1 after `delay_seconds`.
- Attempt *n* delay = `delay_seconds * backoff**(n-1)`, capped at 5 minutes.
- After `max_attempts`, stop and raise a "gave up" notification.
- Counter resets once the service has been `Running` continuously for 60 s —
  otherwise a service that starts and dies repeatedly would look healthy each
  time it briefly reaches Running.
- Flap guard: keep stop timestamps per service; `flap_threshold` stops inside
  `flap_window_minutes` ⇒ suspend recovery for that service, notify, and show it
  in the panel as *suspended* rather than silently doing nothing.

### Notifications

Windows toast via `winrt`/`win10toast` pulls in a dependency; simpler and
dependency-free is `Shell_NotifyIcon` with `NIF_INFO` (balloon → toast on
Windows 10/11), which we already have a handle for through pystray. One helper
`notify(title, text)` in `service_officer.py`.

---

## 3. Feature 2 — Ordered stacks (`stacks.py`)

```python
def run(stack, action, on_step, cancel) -> StackResult
```

- `action` ∈ `start` | `stop` | `restart`. `stop` walks the steps in reverse;
  `restart` is a reverse stop followed by a forward start.
- Per step, after issuing the control:
  - `wait: running` → poll until the service reports `Running`, giving up at
    `timeout_seconds` (poll every 250 ms; SCM notifications also arrive, but a
    poll keeps the runner self-contained and testable).
  - `wait: delay` → sleep `delay_seconds`. This exists because several services
    report `Running` before they are actually ready to serve, and a fixed pause
    is the honest way to model that.
- A failed step aborts the run and reports which step failed and why; nothing
  further is attempted.
- The watchdog is suppressed for services taking part in a run, so a stack stop
  isn't immediately undone.
- Progress surfaces as a step list inside the panel (current step highlighted,
  done steps ticked) and a toast at the end.

Entry points: a **Stacks** row group in the panel footer or a submenu on the
tray's right-click menu (`Start stack ▸ SAP B1 stack`).

---

## 4. Feature 3 — History (`history.py`)

- Append-only JSON Lines at `%APPDATA%\ServiceOfficer\history.jsonl`:
  `{"ts": "2026-07-25T03:12:41.213+03:00", "service": "AppEngine",
    "from": "Running", "to": "Stopped", "exit_code": 1067, "source": "scm"}`
- `source` ∈ `scm` | `panel` | `watchdog` | `stack`, so the timeline reads as a
  story: crash → watchdog attempt → running.
- Written from the single point where state changes already converge
  (`_on_scm_change`), so nothing else needs to know history exists.
- Trimmed on startup and daily: drop entries older than `retention_days`, and
  hard-cap the file (e.g. 5 MB) to stay well-behaved on a customer server.
- Viewer: a **History** section in settings — newest first, filterable by
  service, with **Export…** writing CSV for a ticket.
- Uptime per service for a period is derived from the same data (feature 12).

---

## 5. Settings screen redesign

Today's settings window is one flat panel: a service list plus a startup
checkbox. Three features with per-service options and their own sections need
real structure.

### Shape

A 760×620 window, sidebar on the left, content on the right — matching the
flyout's palette (`#1e1e1e` / `#2a2a2a`, Segoe UI, BMP glyphs only, dark title
bar via DWM, as in the current dialog).

```
┌──────────────┬──────────────────────────────────────────────┐
│ Services     │  (master–detail: list left, settings right)  │
│ Stacks       │                                              │
│ History      │                                              │
│ General      │                                              │
└──────────────┴──────────────────────────────────────────────┘
```

- **Services** — the existing list (add from the picker, rename, remove,
  reorder, multi-select) on the left; selecting one shows its detail on the
  right: display label, and a **Recovery** group with *keep this service
  running*, **max attempts**, delay, backoff, "also restart after a clean stop",
  and the flap guard. Number fields are spinboxes with sane bounds, and the
  whole group greys out when recovery is off.
- **Stacks** — stack list on the left; on the right the ordered steps with
  ↑/↓, each step showing service, wait mode (*until running* / *fixed delay*)
  and the timeout or delay. A **Test run** button executes it with live progress,
  which is how you find out that a service needs a delay rather than a wait.
- **History** — enable, retention, the timeline table, **Export…**.
- **General** — start with Windows, which notifications to show, and (later)
  remote machines.

### Implementation notes

- Keep using plain `tk` widgets: `ttk` is painful to theme dark, and the current
  dialog already proves the hand-styled approach works.
- Reuse the existing helpers (`_dark_button`, `_dark_listbox`,
  `_apply_dark_titlebar`) and add `_dark_spinbox`, `_dark_check`,
  `_dark_dropdown`, `_section` (labelled group).
- Only BMP glyphs — an astral emoji costs ~600 ms of font fallback per Tk
  interpreter, which is what made the panel slow.
- Settings still runs as its own process (`--settings`), so nothing here can
  block the tray.
- Save writes the whole document atomically (temp file + replace) so a crash
  mid-write can't leave a truncated config on a customer machine.

---

## 6. Order of work

1. **Config v2 + defaults merge** — everything else builds on it. Ship with
   loader tolerance and atomic save.
2. **History** — smallest, and it immediately makes the next two observable.
3. **Watchdog** — needs the exit-code payload from `scm_notify` and the
   suppression rules.
4. **Settings redesign** — sidebar shell, then Services/Recovery detail, then
   History view.
5. **Stacks** — runner first (testable headless), then the editor and the
   progress UI.

## 7. How each part gets proven

- **Config**: round-trip old and new files; a v1 file must load, gain defaults,
  and save as v2 without losing anything.
- **History**: synthetic transitions produce the expected lines; retention drops
  the right entries; export opens as CSV.
- **Watchdog**: the crash / clean-stop / our-own-stop distinction is the thing
  most likely to be wrong, so drive each with a real service (`PrintNotify` was
  used for the SCM latency test) — a crash simulated by killing the service
  process, a clean stop via `sc stop`, and our own stop through the panel. Assert
  a restart in the first case only. Then flap guard: repeated kills must give up
  after the threshold.
- **Stacks**: a stack over real on-demand services must start in order and stop
  in reverse; a step whose service can't reach Running must abort at its timeout
  with a clear reason.
- **Settings**: build every section headless (as the current smoke tests do),
  and assert edits round-trip to disk.
