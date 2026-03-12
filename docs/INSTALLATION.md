# Service Officer — Installation Guide

## Overview

Service Officer is a Windows system-tray application that lets you monitor and control Windows services directly from the taskbar notification area.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Windows | 10 / 11 | 64-bit |
| Python | 3.10 or newer | https://www.python.org/downloads/ — check **"Add Python to PATH"** |
| pip | bundled with Python | updated automatically during install |

---

## Quick Install (recommended)

1. Double-click **`install.bat`** in the app folder.
2. Accept the UAC prompt when Windows asks for administrator permission.
3. The installer will:
   - Check that Python is available
   - Install all Python dependencies (`pystray`, `Pillow`, `pywin32`)
   - Create a **startup shortcut** in your Windows Startup folder so the app launches automatically at login with administrator rights
   - Launch the app immediately — look for the **gear icon** in your system tray

---

## Manual Install

If you prefer to install manually or `install.bat` fails:

```bat
REM 1. Open a command prompt in the app folder
cd "C:\...\Service Officer"

REM 2. Install dependencies
py -m pip install -r requirements.txt

REM 3. Run the app (Python script, no console window)
pythonw service_officer.py
```

---

## Building the Standalone Executable

The compiled `ServiceOfficer.exe` is self-contained and does not require Python to be installed on other machines.

1. Double-click **`build.bat`**.
2. The script will:
   - Install PyInstaller if it is not already present
   - Generate `icon.ico` (the gear icon at multiple resolutions)
   - Compile `service_officer.py` into `ServiceOfficer.exe` using PyInstaller
   - Copy the finished exe to the app root folder
3. Build time: approximately 1–2 minutes.

After a successful build the app folder will contain:

```
ServiceOfficer.exe    ← run this directly, or use run.bat
icon.ico              ← the gear icon used by the exe
```

> **Note:** `build.bat` must be run at least once to produce the exe. The exe is not included in the source files.

---

## Startup at Login

`install.bat` creates:

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ServiceOfficer.lnk
```

This shortcut points to `run.bat` and has the **"Run as administrator"** flag set, so the app starts elevated automatically after every login.

If you use the compiled exe directly (without `run.bat`), the exe itself also requests administrator rights via its embedded manifest — no shortcut flag needed.

---

## Running the App

| Method | Command / Action |
|---|---|
| Compiled exe | Double-click `ServiceOfficer.exe` |
| Via launcher | Double-click `run.bat` |
| Python (dev) | `pythonw service_officer.py` in a terminal |

`run.bat` automatically prefers `ServiceOfficer.exe` if it exists, and falls back to `pythonw` otherwise.

---

## Uninstall

1. Right-click the tray icon → **Quit**
2. Delete the startup shortcut:
   ```
   %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ServiceOfficer.lnk
   ```
3. Delete the configuration data (optional):
   ```
   %APPDATA%\ServiceOfficer\
   ```
4. Delete the app folder.

---

## File Structure

```
Service Officer\
├── service_officer.py        Main application
├── service_control.py        Windows SCM (start/stop/restart/query)
├── settings_dialog.py        Settings GUI (tkinter)
├── config.py                 JSON config read/write
├── generate_icon.py          Build helper: renders icon.ico
├── service_officer.manifest  Windows UAC manifest (requireAdministrator)
├── requirements.txt          Python dependencies
├── install.bat               Install + startup shortcut creator
├── build.bat                 Compile to ServiceOfficer.exe
├── run.bat                   Launcher (prefers exe, falls back to pythonw)
├── ServiceOfficer.spec       PyInstaller spec (auto-generated)
├── icon.ico                  Generated gear icon (created by build.bat)
├── ServiceOfficer.exe        Compiled executable (created by build.bat)
└── docs\
    ├── INSTALLATION.md       This file
    └── USAGE.md              User guide
```

Config file location: `%APPDATA%\ServiceOfficer\services.json`
