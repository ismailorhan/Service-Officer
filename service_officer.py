import base64
import ctypes
import ctypes.wintypes
import io
import math
import os
import subprocess
import sys
import threading
import time

import pystray
from PIL import Image, ImageDraw

import config
import hover
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


_GEAR_FILL    = (244, 246, 248, 255)
_GEAR_OUTLINE = (22, 38, 63, 255)
_GEAR_TEETH   = 8


def _draw_gear(size: int) -> Image.Image:
    """A badge-less gear, drawn supersampled so rotating it stays smooth.

    The shipped icons have the state badge composited over the gear, so it can't
    be masked off without biting a hole in the teeth — hence drawing our own.
    """
    S = 8
    n = size * S
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = n / 2
    r_out, r_root, r_hole = n * 0.46, n * 0.36, n * 0.145
    lw = max(1, int(n * 0.045))

    pts = []
    steps = _GEAR_TEETH * 24
    for i in range(steps):
        frac = i / steps
        a = 2 * math.pi * frac
        r = r_out if (frac * _GEAR_TEETH) % 1.0 < 0.45 else r_root
        pts.append((c + r * math.cos(a), c + r * math.sin(a)))
    d.polygon(pts, fill=_GEAR_FILL)
    d.line(pts + [pts[0]], fill=_GEAR_OUTLINE, width=lw, joint="curve")
    d.ellipse([c - r_hole, c - r_hole, c + r_hole, c + r_hole],
              fill=(0, 0, 0, 0), outline=_GEAR_OUTLINE, width=lw)
    return img


def _spin_frames() -> list:
    """Frames of the gear turning clockwise (lazily rendered, once).

    With 8 teeth the shape repeats every 45°, so a 45° sweep loops seamlessly.
    PIL rotates counter-clockwise for positive angles, hence the negative step.
    """
    if _SPIN_FRAMES:
        return _SPIN_FRAMES
    size = create_icon_image("green").size[0]
    base = _draw_gear(size)
    steps = 12
    for i in range(steps):
        angle = -(360 / _GEAR_TEETH) * (i / steps)      # clockwise
        _SPIN_FRAMES.append(
            base.rotate(angle, resample=Image.BICUBIC)
                .resize((size, size), Image.LANCZOS))
    return _SPIN_FRAMES


def _pending_count() -> int:
    with _cache_lock:
        return sum(1 for s in _status_cache.values() if s in _PENDING_STATUSES)


# Actions started from the panel raise this while they run. Polling alone can't
# be trusted to notice: a restart often finishes between two poll ticks, so the
# transition would never be seen and the icon would never spin.
_action_active = [0]


def _should_spin() -> bool:
    return _action_active[0] > 0 or _pending_count() > 0


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
    """Start the spinner while anything is in flight; stop it once settled."""
    global _spin_thread
    with _spin_lock:
        if _should_spin():
            if not _spinning():
                _spin_stop.clear()
                _spin_thread = threading.Thread(target=_spin_loop, args=(icon,),
                                                daemon=True)
                _spin_thread.start()
        elif _spinning():
            _spin_stop.set()


def _make_action_hook(icon: pystray.Icon):
    """Hook handed to the panel: spin for exactly as long as an action runs."""
    def hook(phase: str) -> None:
        if phase == "start":
            _action_active[0] += 1
            _sync_spinner(icon)
        else:
            _action_active[0] = max(0, _action_active[0] - 1)
            # Re-query now so the icon colour/tooltip reflect the new state,
            # then drop the spinner if nothing else is pending.
            threading.Thread(target=_force_refresh, args=(icon,),
                             daemon=True).start()
    return hook


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


def _poll_loop(icon: pystray.Icon) -> None:
    """Background thread: refresh every 10s, or every 1.5s while a service is
    mid-transition so the spinner tracks it and stops promptly once settled."""
    while True:
        time.sleep(1.5 if _should_spin() else 5)
        _force_refresh(icon)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
class _NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.DWORD),
                ("hWnd", ctypes.wintypes.HWND),
                ("uID", ctypes.wintypes.UINT),
                ("guidItem", ctypes.c_byte * 16)]


def _tray_icon_rect(icon: pystray.Icon):
    """Screen rect of our tray icon, so the hover flyout knows when the pointer
    has left it. Returns (l, t, r, b) or None."""
    try:
        nid = _NOTIFYICONIDENTIFIER()
        nid.cbSize = ctypes.sizeof(_NOTIFYICONIDENTIFIER)
        nid.hWnd = icon._hwnd   # pystray's message window
        # pystray builds NOTIFYICONDATAW with a bogus "hID" keyword, so the real
        # uID field is never assigned and stays 0. Verified: uID=0 returns S_OK
        # and the true rect, while any other value fails with E_FAIL.
        nid.uID = 0
        rect = ctypes.wintypes.RECT()
        if ctypes.windll.shell32.Shell_NotifyIconGetRect(
                ctypes.byref(nid), ctypes.byref(rect)) == 0:  # S_OK
            return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        pass
    return None


def _hover_items() -> list:
    """(label, status) for every configured service, in configured order."""
    services = config.load_services()
    with _cache_lock:
        snapshot = dict(_status_cache)
    return [(svc.get("label") or svc["name"],
             snapshot.get(svc["name"], "Unknown")) for svc in services]


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
        # Empty szTip: Windows then draws no native tooltip, leaving the hover
        # flyout (hover.py) as the only thing that appears on hover. The native
        # one is capped at 128 UTF-16 units and couldn't list the services.
        title="",
    )

    icon._right_menu = _build_right_menu(icon)
    icon.menu        = icon._right_menu
    panel.ACTION_HOOK[0] = _make_action_hook(icon)

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

        WM_MOUSEMOVE = 0x0200
        _icon_rect = [None]

        def _patched_on_notify(wparam, lparam):
            if lparam in (WM_LBUTTONUP, NIN_SELECT):
                hover.hide()
                panel.open_panel()
            elif lparam == WM_RBUTTONUP:
                hover.hide()
                _show_menu_for(icon._right_menu)
            elif lparam == WM_MOUSEMOVE:
                if _icon_rect[0] is None:
                    _icon_rect[0] = _tray_icon_rect(icon)
                hover.request(_hover_items, _icon_rect[0])

        icon._on_notify = _patched_on_notify
        from pystray._util import win32 as _win32
        icon._message_handlers[_win32.WM_NOTIFY] = _patched_on_notify

    icon.run(setup=setup)


if __name__ == "__main__":
    main()
