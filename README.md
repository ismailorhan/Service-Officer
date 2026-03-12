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
- Admin rights (required to start/stop services)

## Run (from source)

```bash
pip install -r requirements.txt
pythonw service_officer.py
```

## Build EXE

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name ServiceOfficer --icon=icon.ico service_officer.py
```

Output: `ServiceOfficer.exe` — no installation needed, runs standalone.

## Configuration

Use **Settings** from the right-click menu to add or remove services to monitor.
