"""Settings window for Service Officer.

Dark, matching the flyout panel's visual language. Lets the user pick which
Windows services to monitor — choosing them from a searchable list of every
installed service (no more typing short names by hand) — and toggle the
Windows-startup auto-launch.

Runs in a separate process (see open_settings) so tkinter always gets a clean
main thread; the tray process's message loop belongs to pystray.
"""

import ctypes
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox

import autostart
import config
import service_control
from panel import (BG, BG2, BG3, FG, FG2, FG3, LINE, BTN, BTN_HV,
                   ICON_FONT, ICON_SEARCH)

_ACCENT = "#40c463"
_DANGER = "#e5534b"

_settings_open = False
_lock = threading.Lock()


def open_settings():
    """Launch the settings window in a separate process so it always gets a
    proper main thread for tkinter.  A guard flag prevents multiple windows."""
    global _settings_open
    with _lock:
        if _settings_open:
            return
        _settings_open = True

    def _run():
        global _settings_open
        try:
            if getattr(sys, "frozen", False):
                exe = os.path.abspath(sys.executable)
                args = [exe, "--settings"]
                cwd = os.path.dirname(exe)
            else:
                here = os.path.dirname(os.path.abspath(__file__))
                script = os.path.join(here, "settings_dialog.py")
                pythonw = _find_pythonw()
                args = [pythonw, script, "--standalone"]
                cwd = here
            subprocess.Popen(args, cwd=cwd).wait()
        finally:
            _settings_open = False

    threading.Thread(target=_run, daemon=True).start()


def _find_pythonw() -> str:
    """Return path to pythonw.exe — dev-mode only (frozen exe uses --settings)."""
    import shutil

    candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if os.path.exists(candidate):
        return candidate

    found = shutil.which("pythonw")
    if found:
        return found

    found = shutil.which("py")
    if found:
        return found

    import glob
    for pattern in [
        r"C:\Python3*\pythonw.exe",
        r"C:\Users\*\AppData\Local\Python\*\pythonw.exe",
        r"C:\Users\*\AppData\Local\Programs\Python\*\pythonw.exe",
    ]:
        matches = sorted(glob.glob(pattern), reverse=True)
        if matches:
            return matches[0]

    return "py"


# ---------------------------------------------------------------------------
# Shared dark-UI helpers
# ---------------------------------------------------------------------------
def _apply_dark_titlebar(win) -> None:
    """Paint the native title bar dark on Windows 10/11 (best-effort)."""
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetAncestor(win.winfo_id(), 2)  # GA_ROOT
        val = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)
            )
    except Exception:
        pass


def _dark_button(parent, text, command, accent=None, width=None):
    b = tk.Button(parent, text=text, command=command, bg=BTN, fg=FG2,
                  activebackground=BTN_HV, activeforeground=FG, relief="flat",
                  bd=0, font=("Segoe UI", 9), padx=12, pady=6, cursor="hand2",
                  takefocus=False)
    if width:
        b.config(width=width)
    hover_fg = accent or FG
    b.bind("<Enter>", lambda e: b.config(bg=BTN_HV, fg=hover_fg))
    b.bind("<Leave>", lambda e: b.config(bg=BTN, fg=FG2))
    return b


def _dark_listbox(parent, height=8, selectmode="browse"):
    return tk.Listbox(parent, bg=BG2, fg=FG, selectbackground=BTN_HV,
                      selectforeground=FG, relief="flat", bd=0,
                      highlightthickness=0, activestyle="none", selectmode=selectmode,
                      font=("Segoe UI", 10), height=height)


def _center_over(child, parent) -> None:
    child.update_idletasks()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    cw, ch = child.winfo_reqwidth(), child.winfo_reqheight()
    x = px + (pw - cw) // 2
    y = py + (ph - ch) // 3
    child.geometry(f"+{max(x, 0)}+{max(y, 0)}")


# ---------------------------------------------------------------------------
# Service picker — choose from every installed service
# ---------------------------------------------------------------------------
def _pick_service(parent, taken_names):
    """Modal picker. Returns a list of {"name","display"} (one per selected
    service) or None if cancelled. Excludes taken_names."""
    result = {"value": None}

    dlg = tk.Toplevel(parent)
    dlg.title("Add a service")
    dlg.configure(bg=BG)
    dlg.geometry("460x520")
    dlg.resizable(False, False)
    dlg.transient(parent)
    _apply_dark_titlebar(dlg)

    tk.Label(dlg, text="Pick a Windows service to monitor", bg=BG, fg=FG,
             font=("Segoe UI", 11, "bold"), anchor="w", padx=14
             ).pack(fill="x", pady=(12, 2))
    tk.Label(dlg, text="Search by display name or service name.", bg=BG, fg=FG2,
             font=("Segoe UI", 9), anchor="w", padx=14).pack(fill="x")

    sf = tk.Frame(dlg, bg=BG2)
    sf.pack(fill="x", padx=14, pady=(8, 6))
    tk.Label(sf, text=ICON_SEARCH, bg=BG2, fg=FG2, font=(ICON_FONT, 9),
             padx=8, pady=6).pack(side="left")
    search_var = tk.StringVar()
    entry = tk.Entry(sf, textvariable=search_var, bg=BG2, fg=FG,
                     insertbackground=FG, relief="flat", bd=0,
                     font=("Segoe UI", 10))
    entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

    count_var = tk.StringVar(value="")
    tk.Label(dlg, textvariable=count_var, bg=BG, fg=FG3, font=("Segoe UI", 8),
             anchor="w", padx=14).pack(fill="x")

    list_frame = tk.Frame(dlg, bg=BG)
    list_frame.pack(fill="both", expand=True, padx=14, pady=(2, 6))
    scrollbar = tk.Scrollbar(list_frame, relief="flat", bd=0, width=10,
                             bg=BG2, troughcolor=BG, activebackground=BTN_HV)
    listbox = _dark_listbox(list_frame, height=14, selectmode="extended")
    listbox.config(yscrollcommand=scrollbar.set)
    scrollbar.config(command=listbox.yview)
    scrollbar.pack(side="right", fill="y")
    listbox.pack(side="left", fill="both", expand=True)

    status = tk.Label(dlg, text="Loading services…", bg=BG, fg=FG2,
                      font=("Segoe UI", 9), anchor="w", padx=14)
    status.pack(fill="x")

    all_services = []   # list of dicts {name, display, status}
    shown = []          # filtered subset parallel to listbox rows

    def _populate(*_):
        q = search_var.get().lower().strip()
        listbox.delete(0, tk.END)
        shown.clear()
        for s in all_services:
            if s["name"] in taken_names:
                continue
            if q and q not in s["display"].lower() and q not in s["name"].lower():
                continue
            shown.append(s)
            listbox.insert(tk.END, f"  {s['display']}   ·   {s['name']}   ({s['status']})")
        count_var.set(f"{len(shown)} services")

    def _confirm(*_):
        sel = listbox.curselection()
        if not sel:
            return
        result["value"] = [{"name": shown[i]["name"], "display": shown[i]["display"]}
                           for i in sel]
        dlg.destroy()

    search_var.trace_add("write", _populate)
    listbox.bind("<Double-Button-1>", _confirm)
    entry.bind("<Return>", lambda e: _confirm())

    btn_row = tk.Frame(dlg, bg=BG)
    btn_row.pack(fill="x", padx=14, pady=(0, 12))
    _dark_button(btn_row, "Cancel", dlg.destroy).pack(side="right")
    _dark_button(btn_row, "Add", _confirm, accent=_ACCENT).pack(side="right", padx=(0, 6))

    # Enumerate synchronously (~200ms for a few hundred services) — safe and
    # simple; avoids marshaling a worker thread's result back into tkinter.
    try:
        all_services.extend(service_control.list_all_services())
        status.config(text="Double-click one, or select several (Ctrl/Shift-click) and click Add.")
        _populate()
    except Exception as e:
        status.config(text=f"Could not list services: {e}", fg=_DANGER)

    _center_over(dlg, parent)
    dlg.attributes("-topmost", True)
    dlg.focus_force()
    entry.focus_set()
    dlg.grab_set()
    parent.wait_window(dlg)
    return result["value"]


def _prompt_label(parent, initial):
    """Modal single-field prompt for a display label. Returns str or None."""
    result = {"value": None}

    dlg = tk.Toplevel(parent)
    dlg.title("Display label")
    dlg.configure(bg=BG)
    dlg.resizable(False, False)
    dlg.transient(parent)
    _apply_dark_titlebar(dlg)

    tk.Label(dlg, text="Display label", bg=BG, fg=FG,
             font=("Segoe UI", 10, "bold"), anchor="w", padx=14
             ).pack(fill="x", pady=(12, 4))
    var = tk.StringVar(value=initial)
    entry = tk.Entry(dlg, textvariable=var, bg=BG2, fg=FG, insertbackground=FG,
                     relief="flat", bd=0, font=("Segoe UI", 10), width=36)
    entry.pack(fill="x", padx=14, ipady=5)

    def _ok(*_):
        v = var.get().strip()
        if v:
            result["value"] = v
        dlg.destroy()

    entry.bind("<Return>", _ok)
    row = tk.Frame(dlg, bg=BG)
    row.pack(fill="x", padx=14, pady=12)
    _dark_button(row, "Cancel", dlg.destroy).pack(side="right")
    _dark_button(row, "Save", _ok, accent=_ACCENT).pack(side="right", padx=(0, 6))

    _center_over(dlg, parent)
    dlg.attributes("-topmost", True)
    dlg.focus_force()
    entry.focus_set()
    entry.selection_range(0, "end")
    dlg.grab_set()
    parent.wait_window(dlg)
    return result["value"]


# ---------------------------------------------------------------------------
# Main settings window
# ---------------------------------------------------------------------------
def _run_settings_dialog():
    root = tk.Tk()
    root.title("Service Officer — Settings")
    root.configure(bg=BG)
    root.geometry("520x600")
    root.resizable(False, False)
    _apply_dark_titlebar(root)

    root.lift()
    root.attributes("-topmost", True)
    root.focus_force()
    root.after(200, lambda: root.attributes("-topmost", False))

    _services = config.load_services()  # working copy

    # ── header ────────────────────────────────────────────────────────────
    tk.Label(root, text="Monitored services", bg=BG, fg=FG,
             font=("Segoe UI", 13, "bold"), anchor="w", padx=16
             ).pack(fill="x", pady=(14, 0))
    tk.Label(root, text="These appear in the tray flyout with live status.",
             bg=BG, fg=FG2, font=("Segoe UI", 9), anchor="w", padx=16
             ).pack(fill="x", pady=(0, 8))

    # ── list ──────────────────────────────────────────────────────────────
    list_frame = tk.Frame(root, bg=BG)
    list_frame.pack(fill="both", expand=True, padx=16)
    scrollbar = tk.Scrollbar(list_frame, relief="flat", bd=0, width=10,
                             bg=BG2, troughcolor=BG, activebackground=BTN_HV)
    listbox = _dark_listbox(list_frame, height=9, selectmode="extended")
    listbox.config(yscrollcommand=scrollbar.set)
    scrollbar.config(command=listbox.yview)
    scrollbar.pack(side="right", fill="y")
    listbox.pack(side="left", fill="both", expand=True)

    def _refresh(select=None):
        listbox.delete(0, tk.END)
        for svc in _services:
            label = svc.get("label") or svc["name"]
            suffix = "" if label == svc["name"] else f"   ·   {svc['name']}"
            listbox.insert(tk.END, f"  {label}{suffix}")
        if select is not None and 0 <= select < len(_services):
            listbox.selection_set(select)
            listbox.see(select)
        _empty.pack_forget() if _services else _empty.pack(pady=8)

    _empty = tk.Label(root, text="No services yet — click “Add service”.",
                      bg=BG, fg=FG3, font=("Segoe UI", 9))

    # ── list action buttons ───────────────────────────────────────────────
    actions = tk.Frame(root, bg=BG)
    actions.pack(fill="x", padx=16, pady=10)

    def _add():
        taken = {s["name"] for s in _services}
        picked = _pick_service(root, taken)  # list of {name, display}
        if picked:
            for p in picked:
                _services.append({"name": p["name"], "label": p["display"]})
            _refresh(len(_services) - 1)

    def _rename():
        sel = listbox.curselection()
        if len(sel) != 1:
            messagebox.showinfo("Service Officer",
                                "Select one service to rename.", parent=root)
            return
        i = sel[0]
        new = _prompt_label(root, _services[i].get("label") or _services[i]["name"])
        if new:
            _services[i]["label"] = new
            _refresh(i)

    def _remove():
        sel = listbox.curselection()
        if not sel:
            messagebox.showinfo("Service Officer",
                                "Select a service in the list first.", parent=root)
            return
        if len(sel) == 1:
            svc = _services[sel[0]]
            msg = f'Stop monitoring "{svc.get("label") or svc["name"]}"?'
        else:
            msg = f"Stop monitoring these {len(sel)} services?"
        if messagebox.askyesno("Remove service", msg, parent=root):
            for i in sorted(sel, reverse=True):  # delete high indices first
                del _services[i]
            _refresh()

    def _move(delta):
        sel = listbox.curselection()
        if len(sel) != 1:  # reordering acts on a single row
            return
        i = sel[0]
        j = i + delta
        if 0 <= j < len(_services):
            _services[i], _services[j] = _services[j], _services[i]
            _refresh(j)  # keep the selection on the moved row

    _dark_button(actions, "  + Add service  ", _add, accent=_ACCENT).pack(side="left")
    _dark_button(actions, "Rename", _rename).pack(side="left", padx=(6, 0))
    _dark_button(actions, "Remove", _remove, accent=_DANGER).pack(side="left", padx=(6, 0))
    _dark_button(actions, "↓", lambda: _move(1)).pack(side="right")
    _dark_button(actions, "↑", lambda: _move(-1)).pack(side="right", padx=(0, 6))
    listbox.bind("<Double-Button-1>", lambda e: _rename())

    tk.Frame(root, bg=LINE, height=1).pack(fill="x", padx=16, pady=(2, 0))

    # ── startup option ────────────────────────────────────────────────────
    opt = tk.Frame(root, bg=BG)
    opt.pack(fill="x", padx=16, pady=12)
    auto_start_var = tk.BooleanVar(value=config.load_auto_start())
    tk.Checkbutton(
        opt, text="Start automatically when Windows starts",
        variable=auto_start_var, bg=BG, fg=FG, selectcolor=BG2,
        activebackground=BG, activeforeground=FG, font=("Segoe UI", 10),
        anchor="w", bd=0, highlightthickness=0, cursor="hand2",
    ).pack(fill="x")

    # ── save / cancel ─────────────────────────────────────────────────────
    footer = tk.Frame(root, bg=BG3)
    footer.pack(fill="x", side="bottom")
    tk.Frame(footer, bg=LINE, height=1).pack(fill="x")
    bar = tk.Frame(footer, bg=BG3)
    bar.pack(fill="x", padx=16, pady=12)

    def _save():
        config.save_services(_services)
        enabled = bool(auto_start_var.get())
        config.save_auto_start(enabled)
        try:
            autostart.apply(enabled)
        except Exception as e:
            messagebox.showwarning(
                "Service Officer",
                f"Could not apply the auto-start setting:\n{e}",
                parent=root,
            )
        root.destroy()

    _dark_button(bar, "Cancel", root.destroy, width=10).pack(side="right")
    save_btn = _dark_button(bar, "Save", _save, accent=_ACCENT, width=10)
    save_btn.pack(side="right", padx=(0, 8))

    _refresh()
    if _services:
        listbox.selection_set(0)  # so Remove/Rename have a target right away
    root.mainloop()


if __name__ == "__main__":
    _run_settings_dialog()
