import base64
import ctypes
import ctypes.wintypes
import io
import os
import subprocess
import sys
import threading
import time

import pystray
from PIL import Image, ImageDraw

import config
import panel
import service_control
import settings_dialog
import _icon_data

# ---------------------------------------------------------------------------
# Status cache — updated by the background poller; drives the tray icon
# colour and the hover tooltip.
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
# Spinner — an amber arc sweeping around the tray icon while the SCM is still
# starting/stopping something, so a transition is visible without opening the
# panel. Frames are pre-rendered once and then cycled.
# ---------------------------------------------------------------------------
_SPIN_FRAMES: list = []
_spin_stop = threading.Event()
_spin_thread = None
_spin_lock = threading.Lock()


def _spin_frames() -> list:
    """Pre-render the rotating-arc frames (lazily, once)."""
    if _SPIN_FRAMES:
        return _SPIN_FRAMES
    base = create_icon_image("yellow")
    w, h = base.size
    inset = max(1, w // 32)
    box = (inset, inset, w - inset - 1, h - inset - 1)
    width = max(2, w // 12)
    for angle in range(0, 360, 30):
        frame = base.copy()
        draw = ImageDraw.Draw(frame)
        # A 100-degree arc; sweeping its start angle reads as a spinner.
        draw.arc(box, start=angle, end=angle + 100, fill=(227, 179, 65, 255),
                 width=width)
        _SPIN_FRAMES.append(frame)
    return _SPIN_FRAMES


def _pending_count() -> int:
    with _cache_lock:
        return sum(1 for s in _status_cache.values() if s in _PENDING_STATUSES)


def _spin_loop(icon: pystray.Icon) -> None:
    frames = _spin_frames()
    i = 0
    while not _spin_stop.is_set():
        try:
            icon.icon = frames[i % len(frames)]
        except Exception:
            break
        i += 1
        _spin_stop.wait(0.12)
    # Settled — hand the icon back to the steady state.
    try:
        icon.icon = create_icon_image(_icon_color_key())
    except Exception:
        pass


def _spinning() -> bool:
    return _spin_thread is not None and _spin_thread.is_alive()


def _sync_spinner(icon: pystray.Icon) -> None:
    """Start the spinner while anything is pending; stop it once settled."""
    global _spin_thread
    with _spin_lock:
        if _pending_count():
            if not _spinning():
                _spin_stop.clear()
                _spin_thread = threading.Thread(target=_spin_loop, args=(icon,),
                                                daemon=True)
                _spin_thread.start()
        elif _spinning():
            _spin_stop.set()


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

# Statuses that mean "the SCM is still working on it".
_PENDING_STATUSES = {"Starting", "Stopping", "Resuming", "Pausing"}


# ---------------------------------------------------------------------------
# Menu builders
# ---------------------------------------------------------------------------
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
    """Re-query services, rebuild the menu, and update tray icon colour."""
    _refresh_cache()
    icon._right_menu = _build_right_menu(icon)
    icon.menu        = icon._right_menu
    _sync_spinner(icon)
    if not _spinning():          # don't fight the spinner for the icon
        icon.icon = create_icon_image(_icon_color_key())
    _update_tooltip(icon)


def _utf16_len(text: str) -> int:
    """Length in UTF-16 code units — what the Windows szTip buffer counts."""
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


def _clip_utf16(text: str, max_units: int = 120) -> str:
    """Clip to at most max_units UTF-16 code units without splitting a
    surrogate pair. The Windows tray tooltip (szTip) is a 128-WCHAR buffer, and
    status emoji outside the BMP each take TWO units — so counting Python chars
    (len) undercounts and can overflow Shell_NotifyIcon. Count units instead."""
    out, units = [], 0
    for ch in text:
        n = 2 if ord(ch) > 0xFFFF else 1
        if units + n > max_units:
            out.append("…")  # …
            break
        out.append(ch)
        units += n
    return "".join(out)


def _update_tooltip(icon: pystray.Icon) -> None:
    services = config.load_services()
    with _cache_lock:
        snapshot = dict(_status_cache)

    if not snapshot:
        icon.title = "Service Officer — No services configured"
        return

    label_map = {svc["name"]: svc.get("label") or svc["name"] for svc in services}
    total   = len(snapshot)
    running = sum(1 for s in snapshot.values() if s == "Running")

    # szTip is only 128 UTF-16 units, so a full list can't fit once there are
    # more than a handful of services. Lead with a summary, then spend the
    # remaining room on the services that need attention (anything not Running)
    # — those are what you actually want from a hover.
    head = f"Service Officer — {running}/{total} running"
    attention = [(label_map.get(n, n), s) for n, s in snapshot.items() if s != "Running"]

    if not attention:
        icon.title = _clip_utf16(head + "\nAll services running")
        return

    lines, shown = [head], 0
    for friendly, status in attention:
        # "•" is one UTF-16 unit; the status emoji are astral (two each).
        candidate = "\n".join(lines + [f"• {friendly}: {status}"])
        if _utf16_len(candidate) > 116:   # leave room for the "+N more" line
            break
        lines.append(f"• {friendly}: {status}")
        shown += 1

    remaining = len(attention) - shown
    if remaining:
        lines.append(f"+{remaining} more")
    icon.title = _clip_utf16("\n".join(lines))


def _poll_loop(icon: pystray.Icon) -> None:
    """Background thread: refresh every 10s, or every 1.5s while a service is
    mid-transition so the spinner tracks it and stops promptly once settled."""
    while True:
        time.sleep(1.5 if _pending_count() else 10)
        _force_refresh(icon)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
def _open_services() -> None:
    """Open services.msc."""
    ctypes.windll.shell32.ShellExecuteW(None, "open", "services.msc", None, None, 1)


def _restart_app(icon: pystray.Icon) -> None:
    """Stop the tray icon then relaunch elevated via ShellExecute runas."""
    if getattr(sys, "frozen", False):
        program = os.path.abspath(sys.executable)
        args = ""
        script_dir = os.path.dirname(program)
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
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
    # Frozen exe re-launches itself with --settings to open the settings dialog
    # (avoids needing a separate Python install on the target machine).
    if "--settings" in sys.argv[1:]:
        settings_dialog._run_settings_dialog()
        return

    _refresh_cache()

    icon = pystray.Icon(
        name="ServiceOfficer",
        icon=create_icon_image(_icon_color_key()),
        title="Service Officer",
    )

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
                panel.open_panel()
            elif lparam == WM_RBUTTONUP:
                _show_menu_for(icon._right_menu)

        icon._on_notify = _patched_on_notify
        from pystray._util import win32 as _win32
        icon._message_handlers[_win32.WM_NOTIFY] = _patched_on_notify

    icon.run(setup=setup)


if __name__ == "__main__":
    main()
