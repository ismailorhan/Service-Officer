import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox

import config
import service_control

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
            # When frozen (compiled exe), sys.executable is ServiceOfficer.exe
            # and __file__ is an internal path inside the archive.
            # The .py source files live next to the exe on disk.
            if getattr(sys, "frozen", False):
                app_dir = os.path.dirname(os.path.abspath(sys.executable))
            else:
                app_dir = os.path.dirname(os.path.abspath(__file__))

            script = os.path.join(app_dir, "settings_dialog.py")
            pythonw = _find_pythonw()
            proc = subprocess.Popen(
                [pythonw, script, "--standalone"],
                cwd=app_dir,
            )
            proc.wait()
        finally:
            _settings_open = False

    t = threading.Thread(target=_run, daemon=True)
    t.start()


def _find_pythonw() -> str:
    """Return path to pythonw.exe (no console window).

    When running as a compiled exe, sys.executable is ServiceOfficer.exe so we
    cannot rely on its directory.  Search multiple locations in order.
    """
    import shutil

    # 1. Alongside the real Python interpreter (works when running as .py)
    candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if os.path.exists(candidate):
        return candidate

    # 2. Search PATH
    found = shutil.which("pythonw")
    if found:
        return found

    # 3. Try py.exe launcher (always available on Windows with Python installed)
    found = shutil.which("py")
    if found:
        return found

    # 4. Common install locations for Python 3.x
    import glob
    for pattern in [
        r"C:\Python3*\pythonw.exe",
        r"C:\Users\*\AppData\Local\Python\*\pythonw.exe",
        r"C:\Users\*\AppData\Local\Programs\Python\*\pythonw.exe",
    ]:
        matches = sorted(glob.glob(pattern), reverse=True)  # newest first
        if matches:
            return matches[0]

    # 5. Last resort: use py launcher without -W (may flash a console briefly)
    return "py"


# ---------------------------------------------------------------------------
# Standalone entry point — called when this script is run directly as a
# subprocess (either by open_settings() above or for dev testing).
# ---------------------------------------------------------------------------

def _run_settings_dialog():
    root = tk.Tk()
    root.title("Service Officer \u2014 Settings")
    root.geometry("480x460")
    root.resizable(False, False)

    # Bring to front reliably
    root.lift()
    root.attributes("-topmost", True)
    root.focus_force()
    # Allow other windows to come in front after initial focus
    root.after(200, lambda: root.attributes("-topmost", False))

    # -- Service list --
    top_frame = tk.Frame(root)
    top_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 4))

    tk.Label(top_frame, text="Monitored Services:", anchor="w").pack(fill=tk.X)

    list_frame = tk.Frame(top_frame)
    list_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

    scrollbar = tk.Scrollbar(list_frame, orient=tk.VERTICAL)
    listbox = tk.Listbox(
        list_frame,
        selectmode=tk.SINGLE,
        height=8,
        yscrollcommand=scrollbar.set,
        font=("Consolas", 9),
    )
    scrollbar.config(command=listbox.yview)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # Internal storage: parallel list of dicts matching listbox order
    _services = config.load_services()

    def refresh_listbox(select_index=None):
        listbox.delete(0, tk.END)
        for svc in _services:
            listbox.insert(tk.END, f"{svc['label']}  [{svc['name']}]")
        if select_index is not None:
            listbox.selection_set(select_index)
            listbox.see(select_index)

    refresh_listbox()

    # -- Status label --
    status_var = tk.StringVar(value="")
    tk.Label(root, textvariable=status_var, fg="gray", anchor="w").pack(
        fill=tk.X, padx=12
    )

    # -- Add / Edit fields --
    _editing_index = [None]  # None = adding, int = editing that index

    fields_frame = tk.LabelFrame(root, text="Add Service", padx=8, pady=6)
    fields_frame.pack(fill=tk.X, padx=12, pady=(4, 0))

    row1 = tk.Frame(fields_frame)
    row1.pack(fill=tk.X, pady=(0, 4))
    tk.Label(row1, text="Service name:", width=14, anchor="w").pack(side=tk.LEFT)
    name_entry = tk.Entry(row1)
    name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

    row2 = tk.Frame(fields_frame)
    row2.pack(fill=tk.X)
    tk.Label(row2, text="Display label:", width=14, anchor="w").pack(side=tk.LEFT)
    label_entry = tk.Entry(row2)
    label_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

    action_btn = tk.Button(fields_frame, text="Add", width=8)
    action_btn.pack(anchor="e", pady=(6, 0))

    def reset_form():
        _editing_index[0] = None
        name_entry.config(state=tk.NORMAL)
        name_entry.delete(0, tk.END)
        label_entry.delete(0, tk.END)
        fields_frame.config(text="Add Service")
        action_btn.config(text="Add")
        status_var.set("")
        listbox.selection_clear(0, tk.END)

    def on_action():
        name  = name_entry.get().strip()
        label = label_entry.get().strip() or name

        if not name:
            status_var.set("Service name is required.")
            return

        idx = _editing_index[0]

        if idx is None:
            # --- ADD ---
            existing_names = [s["name"] for s in _services]
            if name in existing_names:
                status_var.set(f'"{name}" is already in the list.')
                return
            st = service_control.query_status(name)
            if st == "Not Found":
                if not messagebox.askyesno(
                    "Service not found",
                    f'Windows service "{name}" was not found.\nAdd it anyway?',
                    parent=root,
                ):
                    return
            _services.append({"name": name, "label": label})
            refresh_listbox(len(_services) - 1)
        else:
            # --- UPDATE ---
            existing_names = [s["name"] for i, s in enumerate(_services) if i != idx]
            if name in existing_names:
                status_var.set(f'"{name}" is already used by another entry.')
                return
            st = service_control.query_status(name)
            if st == "Not Found":
                if not messagebox.askyesno(
                    "Service not found",
                    f'Windows service "{name}" was not found.\nSave anyway?',
                    parent=root,
                ):
                    return
            _services[idx] = {"name": name, "label": label}
            refresh_listbox(idx)

        reset_form()

    action_btn.config(command=on_action)
    name_entry.bind("<Return>", lambda _: label_entry.focus())
    label_entry.bind("<Return>", lambda _: on_action())

    # Clicking a row loads it into the fields for editing
    def on_select(event):
        sel = listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        svc = _services[idx]
        _editing_index[0] = idx
        fields_frame.config(text="Edit Service")
        action_btn.config(text="Update")
        name_entry.config(state=tk.NORMAL)
        name_entry.delete(0, tk.END)
        name_entry.insert(0, svc["name"])
        label_entry.delete(0, tk.END)
        label_entry.insert(0, svc["label"])
        status_var.set("Edit the fields above and click Update, or click Cancel Edit to discard.")

    listbox.bind("<<ListboxSelect>>", on_select)

    # -- Buttons row --
    btn_row = tk.Frame(root)
    btn_row.pack(fill=tk.X, padx=12, pady=(6, 0))

    def remove_selected():
        sel = listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        svc = _services[idx]
        if not messagebox.askyesno(
            "Remove service",
            f'Remove "{svc["label"]}" from the list?',
            parent=root,
        ):
            return
        del _services[idx]
        refresh_listbox()
        reset_form()

    tk.Button(btn_row, text="Remove Selected", command=remove_selected).pack(side=tk.LEFT)
    tk.Button(btn_row, text="Cancel Edit", command=reset_form).pack(side=tk.LEFT, padx=(6, 0))

    # -- Save / Cancel --
    save_frame = tk.Frame(root)
    save_frame.pack(pady=10)

    def save_and_close():
        config.save_services(_services)
        root.destroy()

    tk.Button(save_frame, text="Save", command=save_and_close, width=10).pack(
        side=tk.LEFT, padx=6
    )
    tk.Button(save_frame, text="Cancel", command=root.destroy, width=10).pack(
        side=tk.LEFT, padx=6
    )

    root.mainloop()


if __name__ == "__main__":
    _run_settings_dialog()
