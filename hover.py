"""Hover flyout for the tray icon.

Windows' own tray tooltip (szTip) is capped at 128 UTF-16 units, which can't
hold more than a couple of service names. This draws our own compact dark
window instead: every configured service with its live status, shown while the
pointer rests on the tray icon.

It never takes focus (WS_EX_NOACTIVATE), so hovering doesn't disturb whatever
you were typing in, and it hides as soon as the pointer leaves both the icon
and the window. The Tk root is created once and reused, so showing is instant.
"""

import ctypes
import ctypes.wintypes
import queue
import threading
import tkinter as tk

from panel import BG, BG2, FG, FG2, FG3, LINE

# status -> (dot colour, label colour)
_DOT = {
    "Running":   "#40c463",
    "Stopped":   "#e5534b",
    "Paused":    "#f2c188",
    "Starting":  "#e3b341",
    "Stopping":  "#e3b341",
    "Resuming":  "#e3b341",
    "Pausing":   "#e3b341",
    "Not Found": "#8b8b8b",
}

SHOW_DELAY = 0.35   # seconds the pointer must rest on the icon
ROW_H = 20
MARGIN = 12

_q: "queue.Queue" = queue.Queue()
_thread = None
_lock = threading.Lock()
_visible = threading.Event()
_pending = threading.Event()


# ---------------------------------------------------------------------------
# Public API (called from the tray thread)
# ---------------------------------------------------------------------------
def request(items_provider, icon_rect):
    """Pointer is over the tray icon: show the flyout after a short rest.

    items_provider: called only if we actually show — returns [(label, status)].
    It's a callable rather than a list because WM_MOUSEMOVE fires many times a
    second and gathering the statuses reads the config from disk.
    icon_rect: (l, t, r, b) or None.
    """
    _ensure_thread()
    if _visible.is_set() or _pending.is_set():
        return
    _pending.set()

    def _later():
        try:
            if icon_rect and not _cursor_in(icon_rect):
                return          # pointer moved on before the delay elapsed
            _q.put(("show", (items_provider(), icon_rect)))
        finally:
            _pending.clear()

    t = threading.Timer(SHOW_DELAY, _later)
    t.daemon = True
    t.start()


def hide():
    """Hide it now (a click happened, or the app is shutting down)."""
    if _thread is not None:
        _q.put(("hide", None))


def _ensure_thread():
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, daemon=True)
            _thread.start()


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def _cursor_pos():
    pt = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _cursor_in(rect, pad=0):
    x, y = _cursor_pos()
    l, t, r, b = rect
    return (l - pad) <= x <= (r + pad) and (t - pad) <= y <= (b + pad)


def _no_activate(win):
    """Mark the window as a non-activating tool window so it never steals focus."""
    try:
        hwnd = ctypes.windll.user32.GetAncestor(win.winfo_id(), 2)  # GA_ROOT
        GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = -20, 0x08000000, 0x00000080
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# The Tk thread — owns the window for the process's lifetime
# ---------------------------------------------------------------------------
def _loop():
    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)

    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.configure(bg="#3a3a3a")   # 1px frame so it reads as a window on any wall
    win.attributes("-topmost", True)
    win.withdraw()

    body = tk.Frame(win, bg=BG, padx=12, pady=8)
    body.pack(fill="both", expand=True, padx=1, pady=1)

    title = tk.Label(body, text="Service Officer", bg=BG, fg=FG,
                     font=("Segoe UI", 9, "bold"), anchor="w")
    title.pack(fill="x")
    tk.Frame(body, bg=LINE, height=1).pack(fill="x", pady=(5, 4))
    rows_holder = tk.Frame(body, bg=BG)
    rows_holder.pack(fill="both", expand=True)

    state = {"rect": None}

    def _render(items):
        for child in rows_holder.winfo_children():
            child.destroy()
        running = sum(1 for _, s in items if s == "Running")
        title.config(text=f"Service Officer  —  {running}/{len(items)} running")

        # Cap at what fits on screen, in case of a very long list.
        avail = win.winfo_screenheight() - 160
        max_rows = max(4, avail // ROW_H)
        shown = items[:max_rows]
        for label, status in shown:
            r = tk.Frame(rows_holder, bg=BG)
            r.pack(fill="x")
            tk.Canvas(r, width=8, height=8, bg=BG, highlightthickness=0, bd=0
                      ).pack(side="left", padx=(0, 7))
            dot = r.winfo_children()[-1]
            dot.create_oval(1, 1, 7, 7, fill=_DOT.get(status, "#8b8b8b"), outline="")
            tk.Label(r, text=label, bg=BG, fg=FG, font=("Segoe UI", 9),
                     anchor="w").pack(side="left")
            tk.Label(r, text=status, bg=BG, fg=FG2, font=("Segoe UI", 8),
                     anchor="e").pack(side="right", padx=(14, 0))
        if len(items) > len(shown):
            tk.Label(rows_holder, text=f"+{len(items) - len(shown)} more", bg=BG,
                     fg=FG3, font=("Segoe UI", 8), anchor="w").pack(fill="x")

    def _place():
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        work = ctypes.wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0):
            x, y = work.right - w - MARGIN, work.bottom - h - MARGIN
        else:
            x = win.winfo_screenwidth() - w - MARGIN
            y = win.winfo_screenheight() - h - MARGIN
        win.geometry(f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")

    def _show(items, icon_rect):
        state["rect"] = icon_rect
        _render(items)
        _place()
        win.deiconify()
        win.lift()
        _no_activate(win)
        _visible.set()

    def _hide():
        win.withdraw()
        _visible.clear()

    def _watch():
        # Keep it up while the pointer is on the icon or on the window itself.
        if _visible.is_set():
            try:
                wr = (win.winfo_rootx(), win.winfo_rooty(),
                      win.winfo_rootx() + win.winfo_width(),
                      win.winfo_rooty() + win.winfo_height())
                on_win = _cursor_in(wr, pad=6)
                on_icon = bool(state["rect"]) and _cursor_in(state["rect"], pad=4)
                if not on_win and not on_icon:
                    _hide()
            except Exception:
                pass
        root.after(200, _watch)

    def _drain():
        while True:
            try:
                cmd, payload = _q.get_nowait()
            except queue.Empty:
                break
            try:
                if cmd == "show":
                    _show(*payload)
                elif cmd == "hide":
                    _hide()
            except Exception:
                pass
        root.after(60, _drain)

    _drain()
    _watch()
    root.mainloop()
