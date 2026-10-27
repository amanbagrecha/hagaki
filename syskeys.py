from __future__ import annotations

import json
import os
import subprocess

DOMAIN = "com.apple.symbolichotkeys"
KEY = "AppleSymbolicHotKeys"
ACTIVATE = "/System/Library/PrivateFrameworks/SystemAdministration.framework/Resources/activateSettings"

SHIFT, CTRL, CMD = 131072, 262144, 1048576

SCREENSHOT_KEYS = {
    "28": {"label": "Save picture of screen as a file", "spec": "cmd+shift+3", "parameters": [51, 20, CMD | SHIFT]},
    "29": {"label": "Copy picture of screen to the clipboard", "spec": "ctrl+cmd+shift+3", "parameters": [51, 20, CMD | SHIFT | CTRL]},
    "30": {"label": "Save picture of selected area as a file", "spec": "cmd+shift+4", "parameters": [52, 21, CMD | SHIFT]},
    "31": {"label": "Copy picture of selected area to the clipboard", "spec": "ctrl+cmd+shift+4", "parameters": [52, 21, CMD | SHIFT | CTRL]},
    "184": {"label": "Screenshot and recording options", "spec": "cmd+shift+5", "parameters": [53, 23, CMD | SHIFT]},
}

REPLACEMENTS = {"screen": "cmd+shift+3", "region": "cmd+shift+4", "record": "cmd+shift+5"}


def _plain(value):
    if isinstance(value, dict) or hasattr(value, "keys"):
        return {str(k): _plain(value[k]) for k in value.keys()}
    if isinstance(value, (list, tuple)) or (hasattr(value, "count") and hasattr(value, "objectAtIndex_")):
        return [_plain(v) for v in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    return str(value)


def _read(domain: str) -> dict:
    from Foundation import CFPreferencesCopyAppValue

    value = CFPreferencesCopyAppValue(KEY, domain)
    return _plain(value) if value is not None else {}


def _write(domain: str, table: dict) -> None:
    from Foundation import CFPreferencesAppSynchronize, CFPreferencesSetAppValue

    CFPreferencesSetAppValue(KEY, table, domain)
    if not CFPreferencesAppSynchronize(domain):
        raise OSError(f"could not save {domain}")


def _activate(domain: str) -> None:
    if domain == DOMAIN and os.path.exists(ACTIVATE):
        subprocess.run([ACTIVATE, "-u"], timeout=20, capture_output=True)


def status(domain: str = DOMAIN) -> dict:
    table = _read(domain)
    rows = []
    for key, info in SCREENSHOT_KEYS.items():
        entry = table.get(key)
        enabled = True if entry is None else bool(entry.get("enabled", True))
        rows.append({"id": key, "label": info["label"], "spec": info["spec"], "enabled": enabled})
    return {"keys": rows, "all_disabled": all(not r["enabled"] for r in rows)}


def disable(backup_path: str, domain: str = DOMAIN) -> dict:
    table = _read(domain)
    if not os.path.exists(backup_path):
        backup = {key: table.get(key) for key in SCREENSHOT_KEYS}
        tmp = backup_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"domain": domain, "entries": backup}, fh, indent=2)
        os.replace(tmp, backup_path)
    for key, info in SCREENSHOT_KEYS.items():
        entry = table.get(key) or {"value": {"parameters": list(info["parameters"]), "type": "standard"}}
        entry = dict(entry)
        entry["enabled"] = False
        table[key] = entry
    _write(domain, table)
    _activate(domain)
    return status(domain)


def restore(backup_path: str, domain: str = DOMAIN) -> dict:
    table = _read(domain)
    backup = {}
    if os.path.exists(backup_path):
        with open(backup_path, encoding="utf-8") as fh:
            backup = (json.load(fh) or {}).get("entries") or {}
    for key in SCREENSHOT_KEYS:
        previous = backup.get(key)
        if previous is None:
            table.pop(key, None)
        else:
            table[key] = previous
    _write(domain, table)
    _activate(domain)
    if os.path.exists(backup_path):
        os.remove(backup_path)
    return status(domain)
