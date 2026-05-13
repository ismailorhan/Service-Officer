"""
Manage the Service Officer auto-start shortcut in the user's Startup folder.

The shortcut points to ServiceOfficer.exe. Because the exe ships with a
manifest declaring requireAdministrator, Windows shows the UAC prompt at
logon when the shortcut runs the exe.
"""
import os
import sys

SHORTCUT_NAME = "Service Officer.lnk"


def _startup_dir() -> str:
    return os.path.join(
        os.environ["APPDATA"],
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
    )


def _shortcut_path() -> str:
    return os.path.join(_startup_dir(), SHORTCUT_NAME)


def _target_exe() -> str:
    """Path to ServiceOfficer.exe — the file we want launched at logon."""
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "ServiceOfficer.exe")


def is_enabled() -> bool:
    return os.path.exists(_shortcut_path())


def enable() -> None:
    """Create (or refresh) the Startup shortcut pointing at the current exe."""
    target = _target_exe()
    if not os.path.exists(target):
        # No exe to launch — leave the shortcut alone rather than create a dead link.
        return

    os.makedirs(_startup_dir(), exist_ok=True)

    # Use the Windows Shell COM object via pywin32 to author a proper .lnk.
    from win32com.client import Dispatch  # type: ignore

    shell = Dispatch("WScript.Shell")
    sc = shell.CreateShortCut(_shortcut_path())
    sc.TargetPath = target
    sc.WorkingDirectory = os.path.dirname(target)
    sc.IconLocation = target
    sc.Description = "Service Officer — Windows service tray manager"
    sc.WindowStyle = 7  # minimized; tray app has no window anyway
    sc.Save()


def disable() -> None:
    path = _shortcut_path()
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def apply(enabled: bool) -> None:
    if enabled:
        enable()
    else:
        disable()
