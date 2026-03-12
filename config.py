import json
import os

APP_DATA_DIR = os.path.join(os.environ["APPDATA"], "ServiceOfficer")
CONFIG_PATH = os.path.join(APP_DATA_DIR, "services.json")


def load_services() -> list:
    """
    Returns a list of dicts: [{"name": "Spooler", "label": "Print Spooler"}, ...]
    Backwards-compatible: plain strings from old config are promoted to dicts.
    """
    if not os.path.exists(CONFIG_PATH):
        return []
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        services = data.get("services", [])
        # Migrate old plain-string format
        result = []
        for svc in services:
            if isinstance(svc, str):
                result.append({"name": svc, "label": svc})
            else:
                result.append(svc)
        return result
    except (json.JSONDecodeError, OSError):
        return []


def save_services(services: list) -> None:
    """Accepts a list of dicts: [{"name": ..., "label": ...}, ...]"""
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"services": services}, f, indent=2)
