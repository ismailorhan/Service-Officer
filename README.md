# Service Officer

A Windows system tray app for monitoring and controlling Windows services — without opening Services MMC.

## What it does

- Sits in the system tray with a **color-coded gear icon**:
  - 🟢 Green — all services running
  - 🔴 Red — all services stopped
  - 🟡 Yellow — mixed state
  - ⚪ White — no services configured

- **Left-click** → see all configured services with live status, start/stop/restart each one
- **Right-click** → Refresh, Settings, Open Services MMC, Restart App, Quit
- Status auto-refreshes every **10 seconds** in the background
- Tooltip shows live status of all services at a glance

## Requirements

- Windows 10 / 11
- Admin rights (required to start/stop services). The compiled exe ships with
  a `requireAdministrator` manifest, so Windows triggers the UAC prompt
  automatically on launch.

## Install (end users)

Use the installer produced by `installer.iss`:

```
dist\ServiceOfficerSetup.exe
```

The wizard offers two optional tasks (both can be toggled at install time):

- **Start automatically when Windows starts** — creates a per-user Startup
  shortcut. Checked by default.
- **Create a desktop shortcut** — off by default.

Auto-start can also be toggled later from **Settings** in the tray menu.

## Run (from source — dev)

```bash
pip install -r requirements.txt
pythonw service_officer.py
```

## Build EXE

```bat
build.bat
```

`build.bat` installs PyInstaller, cleans previous artefacts, and produces
`ServiceOfficer.exe` with the admin manifest embedded (`--uac-admin`). The
finished exe is copied to the project root for the Inno Setup script to pick
up.

## Build Installer

1. Run `build.bat` to produce `ServiceOfficer.exe`.
2. Open `installer.iss` in Inno Setup Compiler (or run `iscc installer.iss`).
3. Output: `dist\ServiceOfficerSetup.exe`.

## Configuration

Use **Settings** from the right-click menu to add or remove services to
monitor, and to toggle the Windows-startup auto-launch.

Config file: `%APPDATA%\ServiceOfficer\services.json`
