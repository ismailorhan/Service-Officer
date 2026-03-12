import math
import os
import sys
import subprocess
import threading
import time

import pystray
from PIL import Image, ImageDraw

import config
import service_control
import settings_dialog

# Shared status cache: {service_name: status_string}
# Updated by the background poller; read by menu item lambdas.
_status_cache: dict = {}
_cache_lock = threading.Lock()


def _refresh_cache() -> None:
    """Re-query all services and update the shared cache keyed by service name."""
    services = config.load_services()
    new_cache = {}
    for svc in services:
        new_cache[svc["name"]] = service_control.query_status(svc["name"])
    with _cache_lock:
        _status_cache.clear()
        _status_cache.update(new_cache)


def _get_status(svc_name: str) -> str:
    with _cache_lock:
        return _status_cache.get(svc_name, "Unknown")


# Icon color rules:
#   no services  → white
#   all running  → green
#   all stopped  → red
#   mixed        → yellow
_ICON_COLORS = {
    "none":    ((220, 220, 220, 240), (140, 140, 140, 240)),  # white  / grey hub
    "green":   ((50,  200,  80, 240), ( 20, 110,  40, 240)),  # green  / dark-green hub
    "red":     ((220,  60,  60, 240), (120,  20,  20, 240)),  # red    / dark-red hub
    "yellow":  ((230, 190,   0, 240), (140, 110,   0, 240)),  # yellow / dark-yellow hub
}


def _icon_color_key() -> str:
    """Determine which colour to use based on the current status cache."""
    with _cache_lock:
        statuses = list(_status_cache.values())
    if not statuses:
        return "none"
    running = sum(1 for s in statuses if s == "Running")
    stopped = sum(1 for s in statuses if s == "Stopped")
    if running == len(statuses):
        return "green"
    if stopped == len(statuses):
        return "red"
    return "yellow"


def create_icon_image(color_key: str = "none") -> Image.Image:
    gear_color, hub_color = _ICON_COLORS.get(color_key, _ICON_COLORS["none"])

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx, cy = 32, 32
    teeth = 8
    r_outer, r_inner = 27, 20

    # Gear teeth polygon
    points = []
    for i in range(teeth * 2):
        angle = math.pi * i / teeth - math.pi / 2
        r = r_outer if i % 2 == 0 else r_inner
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(points, fill=gear_color)

    # Hub circle
    hub = 10
    draw.ellipse([cx - hub, cy - hub, cx + hub, cy + hub], fill=hub_color)

    return img


_STATUS_DOTS = {
    "Running":  "🟢",
    "Stopped":  "🔴",
    "Starting": "🟡",
    "Stopping": "🟡",
    "Paused":   "🟠",
    "Pausing":  "🟠",
    "Resuming": "🟡",
    "Not Found":"⚪",
}


def _build_service_items(icon: pystray.Icon) -> list:
    """Service rows only — used for the left-click menu."""
    services = config.load_services()
    items = []

    if not services:
        items.append(pystray.MenuItem("No services configured", None, enabled=False))
        return items

    for svc in services:
        _svc_name  = svc["name"]
        _svc_label = svc.get("label") or svc["name"]
        status     = _get_status(_svc_name)
        dot        = _STATUS_DOTS.get(status, "⚪")
        label      = f"{dot} {_svc_label}  ({status})"

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
            pystray.MenuItem("Start",   make_start(_svc_name),  visible=not is_running),
            pystray.MenuItem("Stop",    make_stop(_svc_name),   visible=not is_stopped),
            pystray.MenuItem("Restart", make_restart(_svc_name)),
        )
        items.append(pystray.MenuItem(label, sub))

    return items


def _build_left_menu(icon: pystray.Icon) -> pystray.Menu:
    """Left-click menu: services only."""
    return pystray.Menu(*_build_service_items(icon))


def _build_right_menu(icon: pystray.Icon) -> pystray.Menu:
    """Right-click menu: Open Services, Refresh, Settings, Restart App, Quit."""
    def refresh_action(icon, item):
        threading.Thread(target=_force_refresh, args=(icon,), daemon=True).start()

    return pystray.Menu(
        pystray.MenuItem("Open Services", lambda icon, item: threading.Thread(target=_open_in_services, args=(None,), daemon=True).start()),
        pystray.MenuItem("Refresh",       refresh_action),
        pystray.MenuItem("Settings",      lambda icon, item: settings_dialog.open_settings()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Restart App",   lambda icon, item: threading.Thread(target=_restart_app, args=(icon,), daemon=True).start()),
        pystray.MenuItem("Quit",          lambda icon, item: icon.stop()),
    )


def _build_menu(icon: pystray.Icon) -> pystray.Menu:
    """Default menu assigned to icon.menu (used as fallback / right-click base)."""
    return _build_right_menu(icon)


def _force_refresh(icon: pystray.Icon) -> None:
    """Re-query services, rebuild both menus, and update icon colour."""
    _refresh_cache()
    icon._left_menu  = _build_left_menu(icon)
    icon._right_menu = _build_right_menu(icon)
    icon.menu        = icon._right_menu   # pystray needs icon.menu set
    icon.icon        = create_icon_image(_icon_color_key())
    _update_tooltip(icon)


def _update_tooltip(icon: pystray.Icon) -> None:
    services = config.load_services()
    with _cache_lock:
        snapshot = dict(_status_cache)
    if not snapshot:
        icon.title = "Service Officer — No services configured"
        return
    _symbols = {
        "Running":  "🟢",
        "Stopped":  "🔴",
        "Starting": "🟡",
        "Stopping": "🟡",
        "Paused":   "🟠",
        "Pausing":  "🟠",
        "Resuming": "🟡",
        "Not Found":"⚪",
    }
    # Build a name->label lookup so tooltip shows the friendly label
    label_map = {svc["name"]: svc.get("label") or svc["name"] for svc in services}
    lines = ["Service Officer"]
    for svc_name, status in snapshot.items():
        symbol = _symbols.get(status, "?")
        friendly = label_map.get(svc_name, svc_name)
        lines.append(f"  {symbol} {friendly}: {status}")
    # Windows tooltip max is 127 chars — truncate gracefully if needed
    tooltip = "\n".join(lines)
    if len(tooltip) > 127:
        tooltip = tooltip[:124] + "..."
    icon.title = tooltip


def _poll_loop(icon: pystray.Icon) -> None:
    """
    Background thread: refresh status every 10 seconds and rebuild the menu.
    10 s is fast enough to catch transitions (Starting → Running) quickly.
    """
    while True:
        time.sleep(10)
        _force_refresh(icon)


def main() -> None:
    # Initial cache population before first menu build
    _refresh_cache()

    icon = pystray.Icon(
        name="ServiceOfficer",
        icon=create_icon_image(_icon_color_key()),
        title="Service Officer",
    )

    # Build initial menus
    icon._left_menu  = _build_left_menu(icon)
    icon._right_menu = _build_right_menu(icon)
    icon.menu        = icon._right_menu
    _update_tooltip(icon)

    # Background polling thread
    poll_thread = threading.Thread(target=_poll_loop, args=(icon,), daemon=True)
    poll_thread.start()

    def setup(icon):
        icon.visible = True

        import ctypes
        import ctypes.wintypes

        WM_LBUTTONUP = 0x0202
        WM_RBUTTONUP = 0x0205
        NIN_SELECT   = 0x0400
        TPM_RIGHTALIGN  = 0x0008
        TPM_BOTTOMALIGN = 0x0020
        TPM_RETURNCMD   = 0x0100

        def _show_menu_for(menu):
            """Synchronously build and show a popup menu for the given pystray.Menu."""
            # Temporarily swap icon.menu, rebuild the HMENU, then show it
            icon.menu = menu
            # _update_menu builds icon._menu_handle synchronously in the
            # pystray Windows backend when called from the same thread
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
            if lparam == WM_LBUTTONUP or lparam == NIN_SELECT:
                _show_menu_for(icon._left_menu)
            elif lparam == WM_RBUTTONUP:
                _show_menu_for(icon._right_menu)

        icon._on_notify = _patched_on_notify
        # pystray stores the handler by reference in _message_handlers at __init__,
        # so we must update the dict entry directly to make the patch take effect
        from pystray._util import win32 as _win32
        icon._message_handlers[_win32.WM_NOTIFY] = _patched_on_notify

    icon.run(setup=setup)


def _run_action(fn, service_name: str, icon: pystray.Icon) -> None:
    """
    Execute a service control action. On success, immediately refresh so the
    menu reflects the new state (e.g. Start disappears after starting).
    On failure, show an error dialog.
    """
    import pywintypes
    try:
        fn(service_name)
        # Brief wait for SCM to settle, then refresh
        time.sleep(1.5)
        _force_refresh(icon)
    except pywintypes.error as e:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showerror(
            "Service Officer",
            f"Could not perform action on '{service_name}':\n{e.strerror}",
            parent=root,
        )
        root.destroy()


def _open_in_services(_service_name: str) -> None:
    """Open services.msc."""
    import ctypes
    ctypes.windll.shell32.ShellExecuteW(None, "open", "services.msc", None, None, 1)


def _restart_app(icon: pystray.Icon) -> None:
    """Stop the tray icon then relaunch elevated via ShellExecute runas."""
    import ctypes
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Prefer the compiled exe if it exists next to this script
    exe = os.path.join(script_dir, "ServiceOfficer.exe")
    if os.path.exists(exe):
        program = exe
        args    = ""
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


if __name__ == "__main__":
    main()
