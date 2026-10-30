from __future__ import annotations

import json
import os
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import core
import gif
import reel


def _daemon_status() -> dict | None:
    path = os.path.join(core.DATA_DIR, "daemon_status.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    pid = int(data.get("pid") or 0)
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return data


def _settings_payload() -> dict:
    import hotkeys
    import syskeys

    settings = core.load_settings()
    try:
        system = syskeys.status()
    except Exception as exc:
        system = {"keys": [], "all_disabled": False, "error": str(exc)}
    labels = {k: hotkeys.display(v) if v else "" for k, v in (settings.get("hotkeys") or {}).items()}
    return {
        "settings": settings,
        "library": core.library_dir(settings),
        "hotkey_labels": labels,
        "system_shortcuts": system,
        "daemon": _daemon_status(),
        "app_dir": core.APP_DIR,
        "data_dir": core.DATA_DIR,
        "daemon_log": os.path.join(core.APP_DIR, ".fused", "cache"),
        "stamp": core.STAMP_PATH,
    }


def main(op: str = "list", id: str = "", q: str = "", kind: str = "all", app: str = "", days: int = 0,
         limit: int = 200, offset: int = 0, shared: bool = False, name: str = "", tags: str = "",
         note: str = "", text: str = "", data: str = "", style: str = "", copy: bool = False,
         patch: str = "", level: str = "", spec: str = "", zoom: str = "", request: str = "") -> dict:
    core.ensure_dirs()
    if op == "list":
        out = core.list_items(q=q, kind=kind, app=app, days=days, limit=limit, offset=offset, shared=shared)
        out["daemon"] = _daemon_status()
        out["stamp"] = core.STAMP_PATH
        return out
    if op == "item":
        item = core.get_item(id)
        if not item:
            return {"error": "This capture is not in the library."}
        children = core.connect().execute(
            "SELECT id FROM items WHERE parent=? AND deleted IS NULL ORDER BY created DESC", (id,)).fetchall()
        return {"item": item, "events": core.events(limit=60, item=id), "children": [core.get_item(r[0]) for r in children]}
    if op == "events":
        return {"events": core.events(limit=limit or 300, level=level, kind=kind if kind != "all" else "", q=q)}
    if op == "settings":
        return _settings_payload()
    if op == "save_settings":
        incoming = json.loads(patch or "{}")
        if "hotkeys" in incoming:
            import hotkeys

            for action, value in incoming["hotkeys"].items():
                if value:
                    incoming["hotkeys"][action] = hotkeys.canonical(value)
        if "library_dir" in incoming:
            path = os.path.abspath(os.path.expanduser(str(incoming["library_dir"]).strip()))
            os.makedirs(path, exist_ok=True)
            if not os.access(path, os.W_OK):
                raise PermissionError(f"Hagaki cannot write to {path}")
            incoming["library_dir"] = path
        core.save_settings(incoming)
        core.log("info", "settings", "Settings saved: " + ", ".join(sorted(incoming)))
        return _settings_payload()
    if op == "preview_name":
        settings = core.load_settings()
        sample = {"app": "Safari", "title": "Kyoto Travel Guide", "width": 2880, "height": 1800, "kind": "image", "source": "region"}
        return {"name": core.format_name(text or settings["name_pattern"], sample) + ".png"}
    if op == "rename":
        return {"item": core.rename(id, name, "manual")}
    if op == "meta":
        return {"item": core.set_meta(id, tags=tags, note=note)}
    if op == "trash":
        return core.trash(id)
    if op == "verify":
        return {"item": core.verify(id)}
    if op == "verify_all":
        core.spawn_job("verify_all")
        return {"started": True}
    if op == "copy":
        return core.copy_item(id)
    if op == "paste":
        return {"items": core.paste_clipboard()}
    if op == "copy_text":
        return {"ok": core.copy_text(text)}
    if op == "ocr":
        return {"item": core.run_ocr(id)}
    if op == "ai_rename":
        return {"item": core.ai_name(id)}
    if op == "share":
        if not core.fused_signed_in():
            raise RuntimeError(core.SIGN_IN_FIRST)
        core.update_item(id, share_state="uploading")
        core.spawn_job("share", id)
        return {"item": core.get_item(id)}
    if op == "share_status":
        return core.share_status(id)
    if op == "save_styled":
        return {"item": core.save_styled(id, data, json.loads(style) if style else None, copy=copy)}
    if op == "video_info":
        return reel.video_details(id)
    if op == "video_plan":
        return reel.video_plan(id, json.loads(zoom or "{}"))
    if op == "style_video":
        return reel.start_style(id, json.loads(request or "{}"))
    if op == "detect_clicks":
        return reel.start_detect(id)
    if op == "make_gif":
        return gif.start(id)
    if op == "video_job":
        return reel.job_state(kind, id)
    if op == "reveal":
        item = core.get_item(id)
        subprocess.run(["open", "-R", item["path"]], check=False)
        return {"ok": True}
    if op == "open_file":
        item = core.get_item(id)
        subprocess.run(["open", item["path"]], check=False)
        return {"ok": True}
    if op == "open_folder":
        subprocess.run(["open", core.library_dir()], check=False)
        return {"ok": True}
    if op == "open_log":
        log = os.path.join(core.APP_DIR, ".fused", "cache")
        subprocess.run(["open", log], check=False)
        return {"ok": True}
    if op == "rescan":
        core.spawn_job("rescan")
        return {"started": True}
    if op == "check_spec":
        import hotkeys

        try:
            return {"ok": True, "spec": hotkeys.canonical(spec), "label": hotkeys.display(spec)}
        except hotkeys.SpecError as exc:
            return {"ok": False, "error": str(exc)}
    if op == "daemon":
        return {"daemon": _daemon_status()}
    raise ValueError(f"unknown op {op!r}")
