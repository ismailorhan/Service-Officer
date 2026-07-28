# Is this codebase ready to grow?

An honest assessment before building Tier 1, because the answer changes what we
build on. Short version: **the domain layer is worth keeping and the UI layer is
not.** Keep the Python core, move the interface to a real widget toolkit, and do
it through the process boundary that already exists.

---

## What the code looks like today

| Layer | Files | Lines | Verdict |
|---|---|---:|---|
| UI | `panel.py`, `settings_dialog.py`, `hover.py` | 1364 (61 %) | the expensive part |
| Domain | `scm_notify.py`, `service_control.py`, `config.py`, `autostart.py` | 439 (20 %) | keep |
| Mixed | `service_officer.py` (tray + icon rendering + status cache + spinner + wiring) | 427 | split |

## The evidence against tkinter

Not theory — every one of these cost real debugging time in a single day of work,
and every one is an accident of the toolkit rather than a problem with the
product:

- An astral emoji (`🔍`) cost **~600 ms per window open** in font fallback. The
  fix was to ban non-BMP glyphs from the UI.
- The native scrollbar renders light grey on Windows and ignores its colour
  options, so it had to be hand-drawn.
- An `overrideredirect` window ignores geometry set before it is mapped, so both
  the panel and the flyout first appeared at 0,0. Two separate bugs, same cause.
- `WS_EX_NOACTIVATE` wasn't enough to stop the flyout stealing focus, because
  Tk's `deiconify()`/`lift()` activate explicitly. Visibility had to be driven
  through raw `ShowWindow`.
- tkinter isn't thread-safe, so status updates needed a hand-built queue to
  marshal onto the UI thread.
- The spinner had to be twelve pre-rendered PNG frames; there is no animation
  primitive.

What Tier 1–2 needs next is exactly where tkinter is weakest: a **sortable,
filterable history table**, a **tree of machines and their services**, **per-step
stack editors**, and **sparklines**. tkinter has no real data grid (`Treeview` is
limited and hard to theme), no charting, and no reliable mixed-DPI story — and
mixed DPI matters, because this tool is used over RDP on customer servers.

## What is genuinely good and should survive any rewrite

- `scm_notify.py` — SCM push notifications, 32 ms end-to-end, with the APC/
  re-arm semantics understood and the repeat-notification filtering in place.
- `service_control.py` — thin, correct, and already remote-capable
  (`machine=` on every call).
- The behavioural knowledge encoded in this session: crash vs deliberate stop via
  exit code, action suppression, work-area anchoring, the 128-unit tray tooltip
  limit.

That is the product's real intellectual property, and it is all UI-agnostic.

---

## Recommendation

**Keep Python. Replace the UI with PySide6 (Qt). Migrate through the existing
process boundary.**

Why Qt over the alternatives:

| Option | Verdict |
|---|---|
| Stay on tkinter | Cheapest today, but every roadmap screen (grids, trees, charts) is priced at a premium, and the result keeps looking hand-made. |
| **PySide6 (Qt)** | **Chosen.** Real dark theming (QSS), `QTableView` with sort/filter models, `QTreeView` for machines, charts, correct per-monitor DPI, thread-safe signals instead of our queue, `QSystemTrayIcon`, and frameless non-activating popups as a supported flag rather than a `ShowWindow` workaround. LGPL, so a commercial product is fine. |
| PyQt6 | Same toolkit, GPL/commercial licence — worse for a product we may sell. |
| customtkinter | Prettier tkinter; the grid, DPI and threading limits are unchanged. |
| Web UI (Tauri/Electron/pywebview) | Great for dashboards, wrong for a tray utility that lives on RDP sessions, and a second runtime to ship. |
| Rewrite in C#/WPF | Arguably the best long-term home for a Windows-only ops tool — native toasts, MSI/MSIX, small AOT binary. But it throws away a working, tested core and doubles time-to-features. Revisit only if this becomes a funded product with a team. |

Cost to accept: the packaged app grows from ~18 MB to roughly 45–70 MB, and
startup is a little slower. For something that launches once at boot and lives in
the tray, that is the right trade. Install as one-dir rather than one-file so
launch stays quick; Inno Setup already handles that.

### Migration path (low risk, incremental)

1. **Settings first.** It already runs as its own process (`--settings`), so a Qt
   settings window can ship without touching the tray. This is also the biggest
   new UI surface, so it is where Qt pays off most.
2. **Flyout and hover next**, as a second Qt window in that same process model.
3. **Tray last** — `QSystemTrayIcon` replaces pystray, and with it go the
   hand-drawn scrollbar, the queue marshalling, the `ShowWindow` focus dance and
   the pre-rendered spinner frames.

Never run tk and Qt event loops in one process; the process split is what makes
this safe.

---

## Restructuring the core (needed regardless of toolkit)

`service_officer.py` has become the place where everything meets. Before adding
three features to it, split it:

```
core/            (no UI imports at all)
  state.py       status cache + event bus (subscribe/publish)
  scm_notify.py  unchanged
  control.py     service_control, now remote-aware
  config.py      typed model, defaults merge, atomic save, migrations
  history.py     append/query/trim
  watchdog.py    recovery rules
  stacks.py      ordered runner
ui/              (Qt)
  tray.py  flyout.py  hover.py  settings/  icons.py
app.py           wiring only
```

The single most important piece is the **event bus** in `state.py`. Today the
SCM handler calls the spinner, the icon and the hover flyout directly; the
watchdog, history and stack runner would each add another direct call. One
publish/subscribe stream means every consumer sees the same events, and it makes
the whole core testable without a UI.

## Two gaps to close before the codebase gets bigger

- **Tests aren't in the repo.** Everything so far was proven with throwaway
  scripts — a real suite (`tests/`, pytest, run in CI on push) belongs with Tier 1.
  The domain layer is pure Python, so most of it tests headlessly.
- **No application log.** On a customer server, "it didn't restart the service"
  needs a rotating log file to diagnose. Cheap now, invaluable later.

Then, for shipping outside your own machines: **code signing** (SmartScreen) and
an in-app update check (CI already publishes releases on a `v*` tag).

---

## Where the boundary went (2026-07-28, v2.2.0)

The plan above was carried out, and then one boundary was added that it did not
foresee: **the engine and the interface are now separate processes, optionally on
separate machines.**

| Layer | Files | Knows about |
|---|---|---|
| Engine | `core/engine.py` | the poller, health, watchdog, scheduler, stack runner, SCM watcher. **No Qt** — there is a test that reads the file and fails if `PySide6` appears in it, because a Windows service has no display. |
| Transport | `core/wire.py`, `core/hub_server.py`, `core/hub_client.py`, `core/hub_auth.py` | HTTPS + JSON, one event stream, tokens, a pinned self-signed certificate. Standard library only. |
| Interface | `app.py`, `ui/` | a store and a way to ask for an action. It cannot tell whether either is local. |
| Service host | `hub.py` | pywin32 service framework, `sc failure` recovery, the console commands |

The seam is deliberately narrow: `app.py` holds an engine **or** a hub client, and
one method (`_ask_for`) knows which. Everything else — the flyout, the dashboard,
the settings dialog, the watchdog's own path — goes through it and never learns.

Two rules that came out of building it, both from bugs:

- **The UI thread only ever asks this computer.** A synchronous call to a remote
  service manager on the paint thread froze the window for 21 seconds the first
  time and 15 seconds after that. Everything remote happens on the engine's
  threads, which is now trivially true when the engine is on another machine.
- **The store's writers are not the reader's business.** `RemoteStore` satisfies
  the same read API and refuses every write. It is deliberately *not* a subclass
  of `Store`: inheriting would make a missing method return an empty answer
  instead of failing where it was written.

The browser page (`core/hub_pages/index.html`) exists to prove the API is enough
for a UI that is not this one. It reads the same snapshot and the same event
stream a client does.
