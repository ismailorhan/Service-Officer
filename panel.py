"""Left-click flyout panel for Service Officer.

A dark, ShooApp-style flyout anchored to the tray. Each configured service is
shown on one row with its live status and inline Start / Stop / Restart buttons —
no nested submenus.

Runs on a dedicated daemon thread inside the tray process (not a subprocess):
that makes it open instantly and, because the tray process owns the foreground
after the click, lets the window take focus so clicking away closes it. All Tk
calls stay on that one thread; worker results marshal back through a queue.
"""

import base64
import ctypes
import ctypes.wintypes
import io
import queue
import threading
import tkinter as tk
from tkinter import messagebox

from PIL import Image, ImageTk

import _icon_data
import config
import service_control

# ── palette (lifted verbatim from ShooApp's _show_panel) ──────────────────────
BG     = "#1e1e1e"
BG2    = "#2a2a2a"
BG3    = "#242424"
FG     = "#ffffff"
FG2    = "#aaaaaa"
FG3    = "#7a7a7a"
LINE   = "#333333"
BTN    = "#2d2d2d"
BTN_HV = "#3d3d3d"
ROW_HV = "#242424"

W = 466  # panel width, matching ShooApp

# Keep every glyph inside the BMP. Drawing an astral (>U+FFFF) emoji such as
# 🔍 or 🗂 makes Windows load the big colour-emoji font: measured ~600-680 ms
# on first use in each Tk interpreter, regardless of the family named — that
# was the panel's entire open latency. BMP symbols cost 10-20 ms, and Windows'
# icon font (BMP range) supplies proper search/folder shapes.
SYMBOL_FONT = "Segoe UI Symbol"     # ✕ ⚙ — BMP symbols
# Measurements below are identical on every open, so cache them process-wide
# and skip a full Tk layout pass (~100 ms once a couple of dozen rows exist).
_ROW_H_CACHE = [None]
_CHROME_CACHE = [None]
_LOGO_IMG_CACHE = [None]  # decoded+resized PIL image (Tk conversion is per-open)

# Set by service_officer to a callable taking "start"/"end". The tray uses it to
# spin its icon for exactly as long as an action is in flight — polling alone
# misses short transitions (a restart can finish between two poll ticks).
ACTION_HOOK = [None]


def _notify(phase: str) -> None:
    hook = ACTION_HOOK[0]
    if hook:
        try:
            hook(phase)
        except Exception:
            pass
ICON_FONT   = "Segoe MDL2 Assets"   # Windows icon font, BMP, cheap
ICON_SEARCH = ""              # magnifier

# ── status model ──────────────────────────────────────────────────────────────
# Categories drive both the pill colour and which buttons are enabled.
_RUNNING = {"Running"}
_STOPPED = {"Stopped"}
_PAUSED  = {"Paused"}
_PENDING = {"Starting", "Stopping", "Resuming", "Pausing"}  # transient — wait it out

# category -> (chip background, chip foreground)
_CHIP = {
    "running": ("#193a26", "#8ff0ad"),
    "stopped": ("#3a2222", "#ff9b95"),
    "paused":  ("#3a2c1c", "#f2c188"),
    "pending": ("#3a3320", "#f2d489"),
    "none":    ("#2c2c2c", "#c2c2c2"),
}

# per-action hover accent
_ACCENT = {"start": "#40c463", "stop": "#e5534b", "restart": "#4aa3ff"}


def _category(status: str) -> str:
    if status in _RUNNING:
        return "running"
    if status in _STOPPED:
        return "stopped"
    if status in _PAUSED:
        return "paused"
    if status in _PENDING:
        return "pending"
    return "none"  # Not Found / Unknown


def _button_states(status: str) -> dict:
    """Which actions are valid for a given status -> {action: enabled}."""
    cat = _category(status)
    if cat == "running":
        return {"start": False, "stop": True,  "restart": True}
    if cat == "stopped":
        return {"start": True,  "stop": False, "restart": True}
    if cat == "paused":
        return {"start": False, "stop": True,  "restart": True}
    # pending / none: nothing safe to do until it settles
    return {"start": False, "stop": False, "restart": False}


# ── launcher (in-process daemon thread) ───────────────────────────────────────
_panel_thread = None
_lock = threading.Lock()


def open_panel():
    """Open the flyout on a dedicated thread. A guard prevents duplicates."""
    global _panel_thread
    with _lock:
        if _panel_thread is not None and _panel_thread.is_alive():
            return
        _panel_thread = threading.Thread(target=_run_panel, daemon=True)
        _panel_thread.start()


# ── the flyout ────────────────────────────────────────────────────────────────
def _run_panel():
    services = config.load_services()

    def _sig(lst):
        """Identity of the configured list — name + label per service."""
        return [(s["name"], s.get("label") or s["name"]) for s in lst]

    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)

    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.configure(bg=BG)
    win.attributes("-topmost", True)
    win.withdraw()  # stay hidden until positioned, so it never flashes at 0,0

    # suppress the auto-close while we own a modal dialog
    _suppress_close = [False]
    _poll_id = [None]
    _drain_id = [None]
    _watch_id = [None]
    _seen_fg = [False]   # has the panel ever held the foreground?

    # Worker threads must never touch tkinter directly (it isn't thread-safe).
    # They push a zero-arg callable here; _drain runs it on the main thread.
    _q: "queue.Queue" = queue.Queue()

    def _drain():
        while True:
            try:
                fn = _q.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                pass
        _drain_id[0] = root.after(80, _drain)

    def _close(*_):
        for after_id in (_poll_id[0], _drain_id[0], _watch_id[0]):
            try:
                if after_id:
                    root.after_cancel(after_id)
            except Exception:
                pass
        try:
            win.destroy()
            root.destroy()
        except Exception:
            pass

    # ── header ────────────────────────────────────────────────────────────────
    hdr = tk.Frame(win, bg=BG, padx=14, pady=10)
    hdr.pack(fill="x")
    try:
        if _LOGO_IMG_CACHE[0] is None:
            _LOGO_IMG_CACHE[0] = (
                Image.open(io.BytesIO(base64.b64decode(_icon_data.ICON_GREEN)))
                .convert("RGBA").resize((20, 20), Image.LANCZOS))
        _logo_img = ImageTk.PhotoImage(_LOGO_IMG_CACHE[0])
        _logo = tk.Label(hdr, image=_logo_img, bg=BG)
        _logo.image = _logo_img  # keep a reference so it isn't garbage-collected
    except Exception:
        # Fall back to a glyph if the image backend is unavailable.
        _logo = tk.Label(hdr, text="⚙", bg=BG, fg="#8ff0ad", font=(SYMBOL_FONT, 13))
    _logo.pack(side="left", padx=(0, 7))
    tk.Label(hdr, text="Service Officer", bg=BG, fg=FG,
             font=("Segoe UI", 12, "bold")).pack(side="left")

    close_btn = tk.Label(hdr, text="✕", bg=BG, fg=FG2,
                         font=(SYMBOL_FONT, 11), padx=8, pady=2, cursor="hand2")
    close_btn.pack(side="right")
    close_btn.bind("<Button-1>", _close)
    close_btn.bind("<Enter>", lambda e: close_btn.config(fg="#ff6b6b", bg=BG2))
    close_btn.bind("<Leave>", lambda e: close_btn.config(fg=FG2, bg=BG))

    badge_var = tk.StringVar(value="")
    badge = tk.Label(hdr, textvariable=badge_var, bg="#193a26", fg="#bfeccd",
                     font=("Segoe UI", 8), padx=8, pady=1)
    badge.pack(side="right", padx=(0, 8))

    tk.Frame(win, bg=LINE, height=1).pack(fill="x")

    # ── summary line ──────────────────────────────────────────────────────────
    summary_var = tk.StringVar(value="")
    tk.Label(win, textvariable=summary_var, bg=BG, fg=FG2,
             font=("Segoe UI", 9), padx=14, pady=4).pack(anchor="w")

    # ── search ────────────────────────────────────────────────────────────────
    sf = tk.Frame(win, bg=BG2, padx=10, pady=6)
    sf.pack(fill="x", padx=10, pady=(0, 4))
    tk.Label(sf, text=ICON_SEARCH, bg=BG2, fg=FG2, font=(ICON_FONT, 9)).pack(side="left")
    search_var = tk.StringVar()
    entry = tk.Entry(sf, textvariable=search_var, bg=BG2, fg=FG,
                     insertbackground=FG, relief="flat", font=("Segoe UI", 10), bd=0)
    entry.pack(side="left", fill="x", expand=True, padx=(4, 0))

    # ── column header ─────────────────────────────────────────────────────────
    cols = tk.Frame(win, bg=BG2)
    cols.pack(fill="x", padx=10)
    tk.Label(cols, text="SERVICE", bg=BG2, fg=FG2, font=("Segoe UI", 8, "bold"),
             anchor="w", padx=6, pady=3).pack(side="left", fill="x", expand=True)
    tk.Label(cols, text="STATUS", bg=BG2, fg=FG2, font=("Segoe UI", 8, "bold"),
             width=10, anchor="center", pady=3).pack(side="left")
    tk.Label(cols, text="ACTIONS", bg=BG2, fg=FG2, font=("Segoe UI", 8, "bold"),
             width=12, anchor="e", padx=6, pady=3).pack(side="left")

    # ── scrollable list ───────────────────────────────────────────────────────
    list_wrap = tk.Frame(win, bg=BG)
    list_wrap.pack(fill="both", expand=True, padx=2)

    canvas = tk.Canvas(list_wrap, bg=BG, highlightthickness=0, bd=0)
    inner = tk.Frame(canvas, bg=BG)
    inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.pack(side="left", fill="both", expand=True)

    # Custom dark scrollbar — the native tk.Scrollbar renders light grey on
    # Windows and ignores its colour options. This is a thin overlaid thumb,
    # sized to the visible fraction and draggable.
    _SB_COL, _SB_HOVER = "#5a5a5a", "#7a7a7a"
    sb = tk.Frame(list_wrap, bg=_SB_COL, width=7, cursor="hand2")

    def _sb_update(first, last):
        first, last = float(first), float(last)
        if first <= 0.0 and last >= 1.0:
            sb.place_forget()          # everything fits — no scrollbar
            return
        ch = canvas.winfo_height()
        top = int(first * ch)
        height = max(30, int((last - first) * ch))
        sb.place(in_=list_wrap, relx=1.0, x=-9, y=top, width=7, height=height, anchor="nw")

    canvas.configure(yscrollcommand=_sb_update)

    def _sb_drag(event):
        ch = canvas.winfo_height() or 1
        y = event.y_root - canvas.winfo_rooty()
        canvas.yview_moveto(min(1.0, max(0.0, y / ch)))
    sb.bind("<B1-Motion>", _sb_drag)
    sb.bind("<Button-1>", _sb_drag)
    sb.bind("<Enter>", lambda e: sb.config(bg=_SB_HOVER))
    sb.bind("<Leave>", lambda e: sb.config(bg=_SB_COL))

    def _on_inner_config(_):
        canvas.configure(scrollregion=canvas.bbox("all"))
    inner.bind("<Configure>", _on_inner_config)
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(inner_id, width=e.width))

    def _on_wheel(e):
        canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
    canvas.bind_all("<MouseWheel>", _on_wheel)

    # ── build one row per service ─────────────────────────────────────────────
    rows = []  # list of dicts

    def _make_action(fn, svc_name, verb, row):
        def _fn(_=None):
            # optimistic: lock the row and show the transient state
            _suppress_close[0] = True
            for b in row["btns"].values():
                b.config(state="disabled", fg="#4a4a4a")
            _set_chip(row["chip"], "pending", verb + "…")
            _notify("start")   # tray spins while this runs

            def work():
                err = None
                try:
                    fn(svc_name)
                except Exception as e:  # pywintypes.error and friends
                    err = e
                _notify("end")
                def done():
                    _suppress_close[0] = False
                    if err is not None:
                        strerror = getattr(err, "strerror", None) or str(err)
                        _suppress_close[0] = True
                        messagebox.showerror(
                            "Service Officer",
                            f"Could not {verb.lower()} '{svc_name}':\n{strerror}",
                            parent=win,
                        )
                        _suppress_close[0] = False
                    _poll()
                _q.put(done)
            threading.Thread(target=work, daemon=True).start()
        return _fn

    def _hover(btn, action, enabled_getter):
        def enter(_):
            if enabled_getter():
                btn.config(bg=BTN_HV, fg=_ACCENT[action])
        def leave(_):
            if enabled_getter():
                btn.config(bg=BTN, fg=FG2)
        btn.bind("<Enter>", enter)
        btn.bind("<Leave>", leave)

    SYMBOL = {"start": "▶", "stop": "■", "restart": "↻"}
    ACTION_FN = {
        "start":   service_control.start_service,
        "stop":    service_control.stop_service,
        "restart": service_control.restart_service,
    }
    VERB = {"start": "Start", "stop": "Stop", "restart": "Restart"}

    ROW_H = 56  # fallback row height until a real row can be measured
    MIN_ROWS = 3
    _chrome = [None]  # measured height of everything except the scrolling list

    def _list_height(row_h):
        """List height: at least MIN_ROWS, growing with the service count up to
        as many rows as fit on screen above the tray (then it scrolls)."""
        n = len(services)
        if not n:
            return 140
        work = ctypes.wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0):
            avail = work.bottom - work.top
        else:
            avail = win.winfo_screenheight()
        chrome = _chrome[0] if _chrome[0] else 230
        TOP_GAP, BOTTOM_GAP = 40, 14   # keep a gap from the screen top and tray
        usable = avail - chrome - TOP_GAP - BOTTOM_GAP
        max_rows = max(MIN_ROWS, usable // row_h)
        visible = max(MIN_ROWS, min(n, max_rows))
        return int(visible * row_h)

    def _build_rows():
        """(Re)build one row per configured service. Called on open and again
        whenever the configured list changes (add / remove / rename in Settings)."""
        for child in inner.winfo_children():
            child.destroy()
        rows.clear()

        for svc in services:
            svc_name  = svc["name"]
            svc_label = svc.get("label") or svc_name

            rf = tk.Frame(inner, bg=BG)
            rf.pack(fill="x")
            rf.bind("<Enter>", lambda e, f=rf: f.config(bg=ROW_HV))
            rf.bind("<Leave>", lambda e, f=rf: f.config(bg=BG))

            inner_pad = tk.Frame(rf, bg=BG)
            inner_pad.pack(fill="x", padx=14, pady=9)

            # name + service id
            meta = tk.Frame(inner_pad, bg=BG)
            meta.pack(side="left", fill="x", expand=True)
            tk.Label(meta, text=svc_label, bg=BG, fg=FG, font=("Segoe UI", 10),
                     anchor="w").pack(fill="x")
            tk.Label(meta, text=svc_name, bg=BG, fg=FG3, font=("Consolas", 8),
                     anchor="w").pack(fill="x")

            # actions (packed right-to-left so order reads start/stop/restart)
            actions = tk.Frame(inner_pad, bg=BG)
            actions.pack(side="right")
            chip = tk.Label(inner_pad, text="…", font=("Segoe UI", 8, "bold"),
                            bg=_CHIP["none"][0], fg=_CHIP["none"][1],
                            padx=8, pady=2, width=9)
            chip.pack(side="right", padx=(0, 10))

            row = {"name": svc_name, "chip": chip, "btns": {}, "frame": rf,
                   "meta": meta, "label": svc_label, "status": None}

            for action in ("start", "stop", "restart"):
                # start disabled/dim; the first poll enables the valid actions,
                # so there's no misleading "all actions available" flash on open.
                b = tk.Button(actions, text=SYMBOL[action], bg=BTN, fg="#4a4a4a",
                              activebackground=BTN_HV, activeforeground=FG,
                              relief="flat", bd=0, font=("Segoe UI", 10), width=2,
                              cursor="hand2", takefocus=False, state="disabled")
                b.pack(side="left", padx=2)
                b.config(command=_make_action(ACTION_FN[action], svc_name,
                                              VERB[action], row))
                row["btns"][action] = b
                _hover(b, action, (lambda a=action, r=row:
                                   _button_states(r["status"] or "Unknown")[a]))

            rows.append(row)

        if not services:
            tk.Label(inner, text="No services configured.\nAdd some from Settings.",
                     bg=BG, fg=FG2, font=("Segoe UI", 10), justify="center",
                     pady=24).pack(fill="x")

        # Measure a real row (two-line content + padding) instead of guessing,
        # so the list caps at whole rows and nothing is half-clipped. The height
        # never changes, so measure once per process and reuse it after that.
        if _ROW_H_CACHE[0]:
            row_h = _ROW_H_CACHE[0]
        else:
            inner.update_idletasks()
            row_h = rows[0]["frame"].winfo_reqheight() if rows else ROW_H
            if row_h < 30:        # not laid out yet — fall back
                row_h = ROW_H
            elif rows:
                _ROW_H_CACHE[0] = row_h
        canvas.config(height=_list_height(row_h))
        canvas.yview_moveto(0)

    # ── status rendering ──────────────────────────────────────────────────────
    def _set_chip(chip, category, text):
        bg, fg = _CHIP[category]
        chip.config(text=text, bg=bg, fg=fg)

    def _apply(status_map: dict):
        running = stopped = other = 0
        for row in rows:
            status = status_map.get(row["name"], "Unknown")
            row["status"] = status
            cat = _category(status)
            _set_chip(row["chip"], cat, status)
            states = _button_states(status)
            for action, b in row["btns"].items():
                if states[action]:
                    b.config(state="normal", fg=FG2)
                else:
                    b.config(state="disabled", fg="#4a4a4a")
            if cat == "running":
                running += 1
            elif cat == "stopped":
                stopped += 1
            else:
                other += 1

        total = len(rows)
        badge_var.set(f"{running} of {total} running" if total else "no services")
        parts = [f"{total} service{'s' if total != 1 else ''}"]
        parts.append(f"{running} running")
        parts.append(f"{stopped} stopped")
        if other:
            parts.append(f"{other} other")
        summary_var.set("  ·  ".join(parts))

    def _poll():
        def work():
            # Re-read config each cycle so Settings changes (add/remove/rename)
            # show up live without reopening the panel.
            fresh = config.load_services()
            result = {s["name"]: service_control.query_status(s["name"])
                      for s in fresh}

            def done():
                if _sig(fresh) != _sig(services):
                    services[:] = fresh
                    _build_rows()
                    _filter()      # keep the current search applied
                    _anchor()      # window height may have changed
                _apply(result)
            _q.put(done)
        threading.Thread(target=work, daemon=True).start()
        # Reschedule a single recurring poll (cancel any pending one first, so
        # action-triggered polls don't stack up extra timers).
        try:
            if _poll_id[0]:
                root.after_cancel(_poll_id[0])
            _poll_id[0] = root.after(2500, _poll)
        except Exception:
            pass

    # ── search filtering ──────────────────────────────────────────────────────
    def _filter(*_):
        q = search_var.get().lower().strip()
        # Re-pack in original order so filtering never scrambles the list.
        for row in rows:
            row["frame"].pack_forget()
        for row in rows:
            if (q in row["label"].lower()) or (q in row["name"].lower()):
                row["frame"].pack(fill="x")
    search_var.trace_add("write", _filter)

    # ── footer ────────────────────────────────────────────────────────────────
    footer = tk.Frame(win, bg=BG3)
    footer.pack(fill="x")
    tk.Frame(footer, bg=LINE, height=1).pack(fill="x")
    fbar = tk.Frame(footer, bg=BG3, padx=10, pady=10)
    fbar.pack(fill="x")

    def _fbtn(text, cmd, font_family="Segoe UI"):
        b = tk.Button(fbar, text=text, bg=BTN, fg=FG2, activebackground=BTN_HV,
                      activeforeground=FG, relief="flat", bd=0,
                      font=(font_family, 9), padx=10, pady=6, cursor="hand2",
                      takefocus=False, command=cmd)
        b.pack(side="left", fill="x", expand=True, padx=3)
        b.bind("<Enter>", lambda e: b.config(bg=BTN_HV, fg=FG))
        b.bind("<Leave>", lambda e: b.config(bg=BTN, fg=FG2))
        return b

    def _open_services():
        ctypes.windll.shell32.ShellExecuteW(None, "open", "services.msc", None, None, 1)
        _close()  # the panel's job is done; close it so it isn't left stuck open

    def _open_settings():
        import settings_dialog
        settings_dialog.open_settings()
        _close()

    _fbtn("↻  Refresh", _poll)
    _fbtn("▤  Services", _open_services)
    _fbtn("⚙  Settings", _open_settings, SYMBOL_FONT)

    # Measure the fixed chrome (everything except the scrolling list) now that
    # the footer exists, so _list_height can size the list to fit the screen.
    # Cached process-wide — it's the same on every open.
    if _CHROME_CACHE[0]:
        _chrome[0] = _CHROME_CACHE[0]
    else:
        canvas.config(height=200)
        win.update_idletasks()
        _chrome[0] = _CHROME_CACHE[0] = win.winfo_reqheight() - 200
    _build_rows()

    # ── anchor to work-area bottom-right (like a native tray flyout) ───────────
    def _anchor():
        win.update_idletasks()
        h = win.winfo_reqheight()
        MARGIN = 12
        work = ctypes.wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0):
            x = work.right - W - MARGIN
            y = work.bottom - h - MARGIN
        else:
            x = win.winfo_screenwidth() - W - MARGIN
            y = win.winfo_screenheight() - h - MARGIN
        geo = f"{W}x{h}+{max(x, 0)}+{max(y, 0)}"
        win.geometry(geo)
        return geo

    _anchor()
    win.deiconify()  # positioned while hidden — appears in place, no 0,0 flash
    win.after(60, lambda: _sb_update(*canvas.yview()))  # sync the scrollbar thumb

    # ── take the foreground reliably ──────────────────────────────────────────
    # Opening from a background thread, Windows' foreground lock often makes a
    # plain SetForegroundWindow silently fail — then the window never holds
    # focus, <FocusOut> never fires, and clicking away wouldn't close it.
    # AttachThreadInput to the current foreground thread bypasses that lock.
    def _force_foreground():
        u = ctypes.windll.user32
        try:
            hwnd = u.GetAncestor(win.winfo_id(), 2) or win.winfo_id()  # GA_ROOT
            fg = u.GetForegroundWindow()
            fg_tid = u.GetWindowThreadProcessId(fg, None)
            cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
            if fg_tid and fg_tid != cur_tid:
                u.AttachThreadInput(fg_tid, cur_tid, True)
                u.BringWindowToTop(hwnd)
                u.SetForegroundWindow(hwnd)
                u.AttachThreadInput(fg_tid, cur_tid, False)
            else:
                u.SetForegroundWindow(hwnd)
        except Exception:
            pass

    # ── close when another window takes the foreground ────────────────────────
    # Poll the foreground window instead of relying on <FocusOut>: the event
    # only fires if the panel currently holds focus, so returning from Settings
    # (or never focusing the panel) would leave it stuck open. Polling closes it
    # whenever the foreground moves elsewhere — but only after the panel has
    # actually been foreground once (so a failed foregrounding can't insta-close).
    def _watch():
        try:
            fg = ctypes.windll.user32.GetForegroundWindow()
            pid = ctypes.wintypes.DWORD(0)
            ctypes.windll.user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
            ours = ctypes.windll.kernel32.GetCurrentProcessId()
            # Compare by process, not HWND: an override-redirect window's
            # winfo_id() is a child window, not the top-level GetForegroundWindow
            # returns, so an HWND match never held and it never closed.
            if pid.value == ours:
                _seen_fg[0] = True
            elif _seen_fg[0] and not _suppress_close[0] and not search_var.get().strip():
                _close()
                return
        except Exception:
            pass
        _watch_id[0] = root.after(300, _watch)

    win.bind("<Escape>", _close)
    win.lift()
    win.attributes("-topmost", True)
    win.focus_force()
    _force_foreground()
    entry.focus_set()
    _watch_id[0] = root.after(500, _watch)  # small grace so foregrounding settles

    _drain()   # start the main-thread work queue
    _poll()    # first status query
    root.mainloop()


if __name__ == "__main__":
    _run_panel()
