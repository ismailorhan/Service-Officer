import base64
import ctypes
import ctypes.wintypes
import io
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

import pystray
import pywintypes
from PIL import Image

import config
import service_control
import settings_dialog
import _icon_data

# ---------------------------------------------------------------------------
# Status cache — updated by the background poller; read by menu lambdas
# ---------------------------------------------------------------------------
_status_cache: dict = {}
_cache_lock = threading.Lock()


def _refresh_cache() -> None:
    """Re-query all configured services and update the shared cache."""
    services = config.load_services()
    new_cache = {svc["name"]: service_control.query_status(svc["name"]) for svc in services}
    with _cache_lock:
        _status_cache.clear()
        _status_cache.update(new_cache)


def _get_status(svc_name: str) -> str:
    with _cache_lock:
        return _status_cache.get(svc_name, "Unknown")


# ---------------------------------------------------------------------------
# Icons
# ---------------------------------------------------------------------------
_ICON_B64 = {
    "green":  _icon_data.ICON_GREEN,   # all services running
    "yellow": _icon_data.ICON_YELLOW,  # mixed / some stopped
    "red":    _icon_data.ICON_RED,     # all services stopped
}


def _icon_color_key() -> str:
    """Return which icon colour to use based on the current status cache."""
    with _cache_lock:
        statuses = list(_status_cache.values())
    if not statuses:
        return "green"
    running = sum(1 for s in statuses if s == "Running")
    if running == len(statuses):
        return "green"
    if running == 0:
        return "red"
    return "yellow"


def create_icon_image(color_key: str = "green") -> Image.Image:
    b64 = _ICON_B64.get(color_key, _ICON_B64["green"])
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")


# ---------------------------------------------------------------------------
# Status symbols — shared by menu items and tooltip
# ---------------------------------------------------------------------------
_STATUS_SYMBOLS = {
    "Running":  "🟢",
    "Stopped":  "🔴",
    "Starting": "🟡",
    "Stopping": "🟡",
    "Paused":   "🟠",
    "Pausing":  "🟠",
    "Resuming": "🟡",
    "Not Found":"⚪",
}


# ---------------------------------------------------------------------------
# Menu builders
# ---------------------------------------------------------------------------
def _build_service_items(icon: pystray.Icon) -> list:
    """Service rows used for the left-click menu."""
    services = config.load_services()

    if not services:
        return [pystray.MenuItem("No services configured", None, enabled=False)]

    items = []
    for svc in services:
        svc_name  = svc["name"]
        svc_label = svc.get("label") or svc["name"]
        status    = _get_status(svc_name)
        dot       = _STATUS_SYMBOLS.get(status, "⚪")
        label     = f"{dot} {svc_label}  ({status})"

        def make_start(s):
            def _fn(icon, item):
                threading.Thread(target=_run_action, args=(service_control.start_service, s, icon), daemon=True).start()
            return _fn

        def make_stop(s):
            def _fn(icon, item):
                threading.Thread(target=_run_action, args=(service_control.stop_service, s, icon), daemon=True).start()
            return _fn

        def make_restart(s):
            def _fn(icon, item):
                threading.Thread(target=_run_action, args=(service_control.restart_service, s, icon), daemon=True).start()
            return _fn

        is_running = status == "Running"
        is_stopped = status == "Stopped"

        sub = pystray.Menu(
            pystray.MenuItem("Start",   make_start(svc_name), visible=not is_running),
            pystray.MenuItem("Stop",    make_stop(svc_name),  visible=not is_stopped),
            pystray.MenuItem("Restart", make_restart(svc_name)),
        )
        items.append(pystray.MenuItem(label, sub))

    return items


def _build_left_menu(icon: pystray.Icon) -> pystray.Menu:
    """Left-click menu: service rows only."""
    return pystray.Menu(*_build_service_items(icon))


def _build_right_menu(icon: pystray.Icon) -> pystray.Menu:
    """Right-click menu: Open Services, Refresh, Settings, Restart App, Quit."""
    def refresh_action(icon, item):
        threading.Thread(target=_force_refresh, args=(icon,), daemon=True).start()

    return pystray.Menu(
        pystray.MenuItem("Open Services", lambda icon, item: threading.Thread(target=_open_services, daemon=True).start()),
        pystray.MenuItem("Refresh",       refresh_action),
        pystray.MenuItem("Settings",      lambda icon, item: settings_dialog.open_settings()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Restart App",   lambda icon, item: threading.Thread(target=_restart_app, args=(icon,), daemon=True).start()),
        pystray.MenuItem("Quit",          lambda icon, item: icon.stop()),
    )


# ---------------------------------------------------------------------------
# Refresh helpers
# ---------------------------------------------------------------------------
def _force_refresh(icon: pystray.Icon) -> None:
    """Re-query services, rebuild menus, and update tray icon colour."""
    _refresh_cache()
    icon._left_menu  = _build_left_menu(icon)
    icon._right_menu = _build_right_menu(icon)
    icon.menu        = icon._right_menu
    icon.icon        = create_icon_image(_icon_color_key())
    _update_tooltip(icon)


def _update_tooltip(icon: pystray.Icon) -> None:
    services = config.load_services()
    with _cache_lock:
        snapshot = dict(_status_cache)

    if not snapshot:
        icon.title = "Service Officer — No services configured"
        return

    label_map = {svc["name"]: svc.get("label") or svc["name"] for svc in services}
    lines = ["Service Officer"]
    for svc_name, status in snapshot.items():
        symbol   = _STATUS_SYMBOLS.get(status, "?")
        friendly = label_map.get(svc_name, svc_name)
        lines.append(f"  {symbol} {friendly}: {status}")

    # Windows tooltip max is 127 chars
    tooltip = "\n".join(lines)
    if len(tooltip) > 127:
        tooltip = tooltip[:124] + "..."
    icon.title = tooltip


def _poll_loop(icon: pystray.Icon) -> None:
    """Background thread: refresh every 10 seconds."""
    while True:
        time.sleep(10)
        _force_refresh(icon)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
def _run_action(fn, service_name: str, icon: pystray.Icon) -> None:
    """Execute a service control action, then refresh on success or show error."""
    try:
        fn(service_name)
        time.sleep(1.5)  # give SCM time to settle
        _force_refresh(icon)
    except pywintypes.error as e:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showerror(
            "Service Officer",
            f"Could not perform action on '{service_name}':\n{e.strerror}",
            parent=root,
        )
        root.destroy()


def _open_services() -> None:
    """Open services.msc."""
    ctypes.windll.shell32.ShellExecuteW(None, "open", "services.msc", None, None, 1)


def _restart_app(icon: pystray.Icon) -> None:
    """Stop the tray icon then relaunch elevated via ShellExecute runas."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    exe = os.path.join(script_dir, "ServiceOfficer.exe")

    if os.path.exists(exe):
        program, args = exe, ""
    else:
        program = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(program):
            program = sys.executable
        args = f'"{os.path.abspath(__file__)}"'

    ret = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", program, args or None, script_dir, 1
    )

    if ret <= 32:
        # UAC cancelled or failed — relaunch without elevation
        subprocess.Popen(
            [program] + ([args.strip('"')] if args else []),
            cwd=script_dir,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )

    icon.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    _refresh_cache()

    icon = pystray.Icon(
        name="ServiceOfficer",
        icon=create_icon_image(_icon_color_key()),
        title="Service Officer",
    )

    icon._left_menu  = _build_left_menu(icon)
    icon._right_menu = _build_right_menu(icon)
    icon.menu        = icon._right_menu
    _update_tooltip(icon)

    threading.Thread(target=_poll_loop, args=(icon,), daemon=True).start()

    def setup(icon):
        icon.visible = True

        WM_LBUTTONUP    = 0x0202
        WM_RBUTTONUP    = 0x0205
        NIN_SELECT      = 0x0400
        TPM_RIGHTALIGN  = 0x0008
        TPM_BOTTOMALIGN = 0x0020
        TPM_RETURNCMD   = 0x0100

        def _show_menu_for(menu):
            icon.menu = menu
            icon._update_menu()
            if not icon._menu_handle:
                return
            ctypes.windll.user32.SetForegroundWindow(icon._hwnd)
            point = ctypes.wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
            hmenu, descriptors = icon._menu_handle
            index = ctypes.windll.user32.TrackPopupMenuEx(
                hmenu,
                TPM_RIGHTALIGN | TPM_BOTTOMALIGN | TPM_RETURNCMD,
                point.x, point.y,
                icon._menu_hwnd,
                None,
            )
            if index > 0:
                descriptors[index - 1](icon)

        def _patched_on_notify(wparam, lparam):
            if lparam in (WM_LBUTTONUP, NIN_SELECT):
                _show_menu_for(icon._left_menu)
            elif lparam == WM_RBUTTONUP:
                _show_menu_for(icon._right_menu)

        icon._on_notify = _patched_on_notify
        from pystray._util import win32 as _win32
        icon._message_handlers[_win32.WM_NOTIFY] = _patched_on_notify

    icon.run(setup=setup)


if __name__ == "__main__":
    main()
