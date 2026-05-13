import json
import os

APP_DATA_DIR = os.path.join(os.environ["APPDATA"], "ServiceOfficer")
CONFIG_PATH = os.path.join(APP_DATA_DIR, "services.json")

AUTO_START_DEFAULT = True


def _load_raw() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_raw(data: dict) -> None:
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_services() -> list:
    """
    Returns a list of dicts: [{"name": "Spooler", "label": "Print Spooler"}, ...]
    Backwards-compatible: plain strings from old config are promoted to dicts.
    """
    data = _load_raw()
    services = data.get("services", [])
    result = []
    for svc in services:
        if isinstance(svc, str):
            result.append({"name": svc, "label": svc})
        else:
            result.append(svc)
    return result


def save_services(services: list) -> None:
    """Accepts a list of dicts: [{"name": ..., "label": ...}, ...]"""
    data = _load_raw()
    data["services"] = services
    _save_raw(data)


def load_auto_start() -> bool:
    data = _load_raw()
    if "auto_start" in data:
        return bool(data["auto_start"])
    # First run: defer to the actual Startup-folder state so the checkbox
    # reflects whatever the installer (or the user) put on disk.
    try:
        import autostart
        return autostart.is_enabled()
    except Exception:
        return AUTO_START_DEFAULT


def save_auto_start(enabled: bool) -> None:
    data = _load_raw()
    data["auto_start"] = bool(enabled)
    _save_raw(data)
