# Service Officer — Installation Guide

## Overview

Service Officer is a Windows system-tray application that lets you monitor and
control Windows services directly from the taskbar notification area. It ships
as a single executable with the `requireAdministrator` manifest embedded — a
UAC prompt appears once at launch.

---

## End-user Install (recommended)

Run the installer produced by Inno Setup:

```
dist\ServiceOfficerSetup.exe
```

The wizard offers two optional tasks:

| Task | Default | Effect |
|---|---|---|
| **Windows başladığında otomatik başlat** | ✅ checked | Creates a per-user shortcut in the Startup folder (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Service Officer.lnk`). Because the exe is admin-manifested, Windows shows the UAC prompt at each logon. |
| **Masaüstü kısayolu oluştur** | ☐ unchecked | Creates `Service Officer.lnk` on the public desktop. |

The auto-start option can also be toggled later from **Settings** in the tray
menu — checkbox **"Windows başladığında otomatik başlat"**.

Install location: `C:\Program Files\Service Officer\`.

---

## Uninstall

Use **Settings → Apps → Service Officer → Uninstall**, or run the uninstaller
from the install folder. This removes:

- `C:\Program Files\Service Officer\`
- Start Menu / Desktop / Startup shortcuts

The configuration file at `%APPDATA%\ServiceOfficer\services.json` is **not**
deleted by the uninstaller; delete it manually if you also want to clear your
service list and auto-start preference.

---

## Building from Source

### Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Windows | 10 / 11 | 64-bit |
| Python | 3.10 or newer | Check **"Add Python to PATH"** during install |
| Inno Setup | 6.x | https://jrsoftware.org/isdl.php — only needed to build the installer |

### Step 1 — Build the executable

From the project root:

```bat
build.bat
```

This will:

1. Install/upgrade `pip`, the runtime dependencies in `requirements.txt`, and
   PyInstaller.
2. Wipe any previous `build\`, `dist\`, and `.spec` artefacts.
3. Compile `service_officer.py` into a single-file `ServiceOfficer.exe` with
   `--uac-admin` so Windows requests elevation automatically.
4. Copy the finished `ServiceOfficer.exe` to the project root (the installer
   script picks it up from there).

### Step 2 — Build the installer

Compile `installer.iss` either with the Inno Setup IDE (open and press F9) or
from the command line:

```bat
iscc installer.iss
```

Output: `dist\ServiceOfficerSetup.exe`.

---

## Running from Source (dev mode)

```bat
py -m pip install -r requirements.txt
pythonw service_officer.py
```

In dev mode the settings window is launched via `pythonw.exe`. In a frozen
build it is launched by re-running the exe with the `--settings` flag, so the
target machine does **not** need Python installed.

---

## File Layout (after install)

```
C:\Program Files\Service Officer\
├── ServiceOfficer.exe   ← admin-manifested, single-file build
├── icon.ico
├── README.md
└── unins000.exe         ← Inno Setup uninstaller
```

Config (created on first save):

```
%APPDATA%\ServiceOfficer\services.json
```

---

## Source Layout

```
Service Officer\
├── service_officer.py    Main application + tray icon
├── service_control.py    Windows SCM (start/stop/restart/query)
├── settings_dialog.py    Settings GUI (tkinter)
├── autostart.py          Startup-folder shortcut management
├── config.py             JSON config read/write
├── _icon_data.py         Embedded gear-icon PNGs (base64)
├── requirements.txt      Python dependencies
├── build.bat             Build ServiceOfficer.exe (PyInstaller --uac-admin)
├── installer.iss         Inno Setup script
├── icon.ico              Gear icon used by exe and installer
└── docs\
    ├── INSTALLATION.md   This file
    └── USAGE.md          User guide
```
