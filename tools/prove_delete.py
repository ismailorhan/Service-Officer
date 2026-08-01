"""Does an open watch handle stop Windows from finishing a service delete?

    Run as administrator:  python tools\\prove_delete.py

Creates a throwaway service, holds a handle to it exactly the way core/scm.py's Watcher did,
deletes it, and then tries to create it again — which is what an uninstall followed by a
reinstall does. Then repeats the whole thing with nothing holding it, so the difference is the
handle and only the handle.

This is here because the field report ("uninstall AppEngine, install it again, and it does not
come up until Windows restarts") is a claim about *Windows*, and a claim about Windows should
be demonstrated rather than quoted. Nothing but the throwaway service is touched, and it is
deleted on every path out.
"""

import ctypes
import ctypes.wintypes
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import scm as scm_mod                                    # noqa: E402

#: A name nothing else can own. Not a real-looking one: somebody reading services.msc while
#: this runs should be able to tell at a glance that it is a probe.
NAME = "SvcOfficerDeleteProbe"

adv = ctypes.windll.advapi32
adv.OpenSCManagerW.restype = ctypes.c_void_p
adv.OpenSCManagerW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.wintypes.DWORD]
adv.CreateServiceW.restype = ctypes.c_void_p
adv.CreateServiceW.argtypes = ([ctypes.c_void_p] + [ctypes.c_wchar_p] * 2
                               + [ctypes.wintypes.DWORD] * 4 + [ctypes.c_wchar_p] * 2
                               + [ctypes.c_void_p] + [ctypes.c_wchar_p] * 3)
adv.OpenServiceW.restype = ctypes.c_void_p
adv.OpenServiceW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.wintypes.DWORD]
adv.DeleteService.argtypes = [ctypes.c_void_p]
adv.CloseServiceHandle.argtypes = [ctypes.c_void_p]

SC_MANAGER_ALL = 0xF003F
SERVICE_ALL = 0xF01FF
WIN32_OWN_PROCESS, DEMAND_START, ERROR_IGNORE = 0x10, 0x3, 0x0
ERROR_SERVICE_EXISTS = 1073


def _create(scm):
    return adv.CreateServiceW(scm, NAME, NAME, SERVICE_ALL, WIN32_OWN_PROCESS,
                              DEMAND_START, ERROR_IGNORE,
                              r"C:\Windows\System32\cmd.exe /c exit",
                              None, None, None, None, None)


def _remove(scm):
    """Delete it if it is there, so a previous run cannot colour this one."""
    handle = adv.OpenServiceW(scm, NAME, SERVICE_ALL)
    if handle:
        adv.DeleteService(handle)
        adv.CloseServiceHandle(handle)


def _round(scm, holding: bool) -> str:
    handle = _create(scm)
    if not handle:
        return f"could not create the probe service: error {ctypes.GetLastError()}"
    adv.CloseServiceHandle(handle)

    # Precisely what Watcher._sync did, and kept doing for the life of the process.
    watch = adv.OpenServiceW(scm, NAME, scm_mod.SERVICE_QUERY_STATUS) if holding else None

    victim = adv.OpenServiceW(scm, NAME, SERVICE_ALL)
    deleted = bool(adv.DeleteService(victim))
    adv.CloseServiceHandle(victim)

    again = _create(scm)
    err = 0 if again else ctypes.GetLastError()
    if again:
        adv.CloseServiceHandle(again)
    if watch:
        adv.CloseServiceHandle(watch)
    _remove(scm)

    named = {scm_mod.ERROR_SERVICE_MARKED_FOR_DELETE: " (ERROR_SERVICE_MARKED_FOR_DELETE)",
             ERROR_SERVICE_EXISTS: " (ERROR_SERVICE_EXISTS)"}.get(err, "")
    return (f"   DeleteService said {'OK' if deleted else 'FAILED'}\n"
            f"   creating it again: "
            f"{'OK' if again else f'FAILED, error {err}{named}'}")


def main() -> int:
    scm = adv.OpenSCManagerW(None, None, SC_MANAGER_ALL)
    if not scm:
        print(f"Cannot open the service manager (error {ctypes.GetLastError()}). "
              "This has to run as an administrator.")
        return 2
    try:
        _remove(scm)
        for holding, label in ((True, "with a watch handle open, as the hub service held one"),
                               (False, "with nothing holding it")):
            print(f"{label}:")
            print(_round(scm, holding))
            time.sleep(0.3)
    finally:
        _remove(scm)
        adv.CloseServiceHandle(scm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
