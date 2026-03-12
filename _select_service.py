"""
Helper script launched WITHOUT elevation to select a service row in services.msc.
Must run non-elevated because MMC (services.msc) runs non-elevated, and
VirtualAllocEx is blocked across different Windows integrity levels.

Usage: pythonw _select_service.py <display_name>
"""
import sys
import time
import ctypes
import ctypes.wintypes

import win32gui
import win32con
import win32api

def main():
    if len(sys.argv) < 2:
        return
    # Join all args in case display name with spaces was split by the shell
    display_name = " ".join(sys.argv[1:])

    # Give services.msc a moment to start before we start looking
    time.sleep(2)

    # Wait for MMC Services window to appear (up to 10 s)
    hwnd = None
    for _ in range(25):
        hwnd = win32gui.FindWindow("MMCMainFrame", "Services")
        if hwnd:
            break
        time.sleep(0.4)

    if not hwnd:
        return

    # Bring to foreground
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.3)

    # Find SysListView32 inside MMC
    list_hwnd = None

    def _find_lv(h, _):
        nonlocal list_hwnd
        if win32gui.GetClassName(h) == "SysListView32" and list_hwnd is None:
            list_hwnd = h

    win32gui.EnumChildWindows(hwnd, _find_lv, None)
    if not list_hwnd:
        return

    # ----------------------------------------------------------------
    # Cross-process ListView text read via VirtualAllocEx.
    # This works here because both this helper and MMC run at medium IL.
    # ----------------------------------------------------------------
    LVM_GETITEMCOUNT  = 0x1004
    LVM_GETITEMTEXTW  = 0x1073
    LVM_SETITEMSTATE  = 0x102B
    LVM_ENSUREVISIBLE = 0x1013
    LVIS_SELECTED     = 0x0002
    LVIS_FOCUSED      = 0x0001
    LVIF_STATE        = 0x0001

    pid = ctypes.c_ulong(0)
    ctypes.windll.user32.GetWindowThreadProcessId(list_hwnd, ctypes.byref(pid))
    h_proc = ctypes.windll.kernel32.OpenProcess(0x001F0FFF, False, pid.value)
    if not h_proc:
        return

    BUF = 1024
    remote = ctypes.windll.kernel32.VirtualAllocEx(h_proc, None, BUF, 0x3000, 0x4)
    if not remote:
        ctypes.windll.kernel32.CloseHandle(h_proc)
        return

    class LVITEMW(ctypes.Structure):
        _fields_ = [
            ("mask",       ctypes.c_uint),
            ("iItem",      ctypes.c_int),
            ("iSubItem",   ctypes.c_int),
            ("state",      ctypes.c_uint),
            ("stateMask",  ctypes.c_uint),
            ("pszText",    ctypes.c_void_p),
            ("cchTextMax", ctypes.c_int),
        ]

    try:
        text_ptr   = remote
        struct_ptr = remote + BUF // 2
        written    = ctypes.c_size_t(0)

        item_count = ctypes.windll.user32.SendMessageW(list_hwnd, LVM_GETITEMCOUNT, 0, 0)
        dn_lower   = display_name.lower().strip()
        found      = -1

        for i in range(item_count):
            lvi = LVITEMW()
            lvi.iSubItem   = 0
            lvi.pszText    = text_ptr
            lvi.cchTextMax = (BUF // 2) // 2  # unicode chars

            ctypes.windll.kernel32.WriteProcessMemory(
                h_proc, struct_ptr, ctypes.byref(lvi), ctypes.sizeof(lvi), ctypes.byref(written))

            ctypes.windll.user32.SendMessageW(list_hwnd, LVM_GETITEMTEXTW, i, struct_ptr)

            local = ctypes.create_unicode_buffer((BUF // 2) // 2)
            ctypes.windll.kernel32.ReadProcessMemory(
                h_proc, text_ptr, local, ctypes.sizeof(local), ctypes.byref(written))

            if local.value.strip().lower() == dn_lower:
                found = i
                break

        if found >= 0:
            # Build state struct for LVM_SETITEMSTATE
            class LVITEMW_S(ctypes.Structure):
                _fields_ = [
                    ("mask",      ctypes.c_uint),
                    ("iItem",     ctypes.c_int),
                    ("iSubItem",  ctypes.c_int),
                    ("state",     ctypes.c_uint),
                    ("stateMask", ctypes.c_uint),
                ]

            # Deselect all
            s = LVITEMW_S()
            s.mask      = LVIF_STATE
            s.iItem     = -1
            s.stateMask = LVIS_SELECTED
            s.state     = 0
            ctypes.windll.kernel32.WriteProcessMemory(
                h_proc, remote, ctypes.byref(s), ctypes.sizeof(s), ctypes.byref(written))
            ctypes.windll.user32.SendMessageW(list_hwnd, LVM_SETITEMSTATE, ctypes.c_size_t(-1), remote)

            # Select + focus target row
            s.iItem     = found
            s.stateMask = LVIS_SELECTED | LVIS_FOCUSED
            s.state     = LVIS_SELECTED | LVIS_FOCUSED
            ctypes.windll.kernel32.WriteProcessMemory(
                h_proc, remote, ctypes.byref(s), ctypes.sizeof(s), ctypes.byref(written))
            ctypes.windll.user32.SendMessageW(list_hwnd, LVM_SETITEMSTATE, found, remote)

            # Scroll into view
            ctypes.windll.user32.SendMessageW(list_hwnd, LVM_ENSUREVISIBLE, found, 0)

    finally:
        ctypes.windll.kernel32.VirtualFreeEx(h_proc, remote, 0, 0x8000)
        ctypes.windll.kernel32.CloseHandle(h_proc)


if __name__ == "__main__":
    main()
