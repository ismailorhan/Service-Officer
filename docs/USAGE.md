# Service Officer — Usage Guide

## Overview

Service Officer lives in the Windows system tray (notification area, bottom-right corner). It shows a **gear icon** whose colour reflects the current state of all monitored services.

---

## Tray Icon Colors

| Icon color | Meaning |
|---|---|
| 🟢 Green | All monitored services are **Running** |
| 🔴 Red | All monitored services are **Stopped** |
| 🟡 Yellow | **Mixed** — at least one service is running and at least one is stopped |
| ⚪ White / grey | No services are configured yet |

The icon color updates automatically every **10 seconds** in the background.

---

## Tooltip

Hover over the tray icon to see a tooltip listing every monitored service and its current status:

```
Service Officer
  🟢 Print Spooler: Running
  🔴 W3SVC: Stopped
  🟡 MySQL: Starting
```

> Windows limits tooltips to 127 characters. If you have many services the tooltip is truncated with `...`.

---

## Left Click — Service Menu

**Left-click** the gear icon to open the service control menu.

Each service is shown with a coloured status dot:

| Dot | Status |
|---|---|
| 🟢 | Running |
| 🔴 | Stopped |
| 🟡 | Starting / Stopping / Resuming |
| 🟠 | Paused / Pausing |
| ⚪ | Not Found (service name invalid or not installed) |

Clicking a service name opens its sub-menu:

| Action | When visible |
|---|---|
| **Start** | Only when the service is **not** Running |
| **Stop** | Only when the service is **not** Stopped |
| **Restart** | Always |

After an action completes the menu refreshes automatically to reflect the new state.

---

## Right Click — Management Menu

**Right-click** the gear icon to open the management menu.

| Item | Description |
|---|---|
| **Open Services** | Opens the Windows Services console (`services.msc`) |
| **Refresh** | Forces an immediate status re-query and icon/menu update |
| **Settings** | Opens the Settings window to add, edit, or remove services |
| *(separator)* | |
| **Restart App** | Quits and relaunches Service Officer with administrator rights |
| **Quit** | Exits Service Officer |

---

## Settings Window

Open via **Right-click → Settings**.

### Adding a service

1. In the **Service name** field enter the internal Windows service name (e.g. `Spooler`, `W3SVC`, `MySQL80`).
   - This is the short name shown in the "Name" column of `services.msc`, **not** the "Display Name".
2. In the **Display label** field enter a friendly name that will appear in the tray menu (e.g. `Print Spooler`, `IIS`, `MySQL`).
   - If left blank, the service name is used as the label.
3. Click **Add**.
   - If the service is not found on this machine you will be asked to confirm before adding it.

### Editing an existing service

1. Click a service row in the list — the fields fill with its current values.
2. Change the name and/or label.
3. Click **Update**.
4. Click **Cancel Edit** to discard changes.

### Removing a service

1. Click a service row to select it.
2. Click **Remove Selected** and confirm.

### Saving

Click **Save** to persist changes and close the window.
Click **Cancel** to discard all unsaved changes.

> Settings are stored in `%APPDATA%\ServiceOfficer\services.json`.

---

## Finding the Windows Service Name

If you are unsure of the internal service name:

1. Open `services.msc` (Start → search "Services", or Right-click tray → **Open Services**).
2. Find the service you want.
3. Right-click it → **Properties**.
4. The **Service name** field at the top of the General tab is what you enter in Settings.

Example: "Print Spooler" has the service name `Spooler`.

---

## Administrator Rights

Service Officer **requires administrator rights** to start and stop services.

- The compiled `ServiceOfficer.exe` automatically requests elevation via a Windows manifest — a UAC prompt appears once when you launch it.
- The startup shortcut created by `install.bat` also has the "Run as administrator" flag set, so it prompts at login.
- If a service action fails (e.g. "Access Denied") it means the app is not running elevated. Use **Right-click → Restart App** to relaunch with full admin rights.

---

## Task Manager

The app appears as **`ServiceOfficer.exe`** in Task Manager (not `pythonw.exe`) when the compiled executable is used.

---

## Configuration File

Services are stored as JSON at:

```
%APPDATA%\ServiceOfficer\services.json
```

Example:

```json
{
  "services": [
    { "name": "Spooler",  "label": "Print Spooler" },
    { "name": "W3SVC",    "label": "IIS" },
    { "name": "MySQL80",  "label": "MySQL 8" }
  ]
}
```

You can edit this file directly in a text editor. Changes take effect after the next automatic refresh (up to 10 seconds) or after **Right-click → Refresh**.
