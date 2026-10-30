from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import time

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import core
import gif
import reel

RECORDINGS = os.path.join(core.DATA_DIR, "agent_recordings.json")
WINDOW_KEYS = ("id", "app", "title", "x", "y", "w", "h")
SUMMARY_KEYS = ("id", "kind", "source", "name", "path", "width", "height", "bytes", "duration", "app", "title",
                "share_url", "verify", "tags", "note", "parent")


def _iso(ts):
    return dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds") if ts else None


def _summary(item: dict, text_chars: int = 0) -> dict:
    out = {k: item.get(k) for k in SUMMARY_KEYS if item.get(k) not in (None, "")}
    out["captured_at"] = _iso(item.get("created"))
    if item.get("snippet"):
        out["match"] = item["snippet"]
    if text_chars and item.get("ocr"):
        out["text"] = item["ocr"][:text_chars]
    return out


def _windows() -> tuple[list[dict], list[dict]]:
    try:
        status = core.engine()
        if status.get("running"):
            res = core.daemon_call("/windows", start=False, timeout=15)
            return res.get("windows") or [], res.get("displays") or core.displays()
    except Exception:
        pass
    return core.window_list(exclude_pid=os.getpid()), core.displays()


def _pick_display(displays: list[dict], display: int) -> dict:
    if display:
        for d in displays:
            if d["id"] == int(display):
                return d
        raise ValueError(f"no display {display}; use list_displays")
    return next((d for d in displays if d["main"]), displays[0])


def _match_window(windows: list[dict], app: str, title: str, window_id: int) -> dict:
    if window_id:
        for w in windows:
            if w["id"] == int(window_id):
                return w
        raise ValueError(f"no visible window with id {window_id}; use list_windows")
    a, t = (app or "").lower(), (title or "").lower()
    cands = [w for w in windows if w["layer"] == 0 and w["w"] >= 120 and w["h"] >= 80]
    if a or t:
        cands = [w for w in cands if a in w["app"].lower() and t in w["title"].lower()]
        if not cands:
            raise ValueError(f"no visible window matches app={app!r} title={title!r}; use list_windows")
        titled = [w for w in cands if w["title"]]
        return (titled or cands)[0]
    front = core.front_app()
    mine = [w for w in cands if w["pid"] == front["pid"]]
    titled = [w for w in mine if w["title"]]
    pick = (titled or mine or cands or [None])[0]
    if pick is None:
        raise ValueError("no window to capture")
    return pick


def _clip_to_display(win: dict, displays: list[dict]) -> tuple[dict, list[float]]:
    cx, cy = win["x"] + win["w"] / 2, win["y"] + win["h"] / 2
    d = next((d for d in displays if d["x"] <= cx < d["x"] + d["width"] and d["y"] <= cy < d["y"] + d["height"]), None)
    d = d or next((d for d in displays if d["main"]), displays[0])
    x0, y0 = max(win["x"], d["x"]), max(win["y"], d["y"])
    x1, y1 = min(win["x"] + win["w"], d["x"] + d["width"]), min(win["y"] + win["h"], d["y"] + d["height"])
    return d, [round(x0 - d["x"]), round(y0 - d["y"]), round(x1 - x0), round(y1 - y0)]


def status() -> dict:
    """Hagaki state for an agent: background service, shortcuts, library folder, counts and capture permission."""
    settings = core.load_settings()
    out = {"library": core.library_dir(settings), "shortcuts": settings.get("hotkeys"),
           "use_macos_screenshot_keys": settings.get("override_system"), "counts": core.facets().get("kinds"),
           "screen_locked": core.screen_locked()}
    try:
        eng = core.engine()
        out["background_service"] = {"running": bool(eng.get("running")), "starts_with_fused_render": bool(eng.get("autostart"))}
        sources = core.server_call("/api/capture", method="GET", timeout=10).get("sources") or {}
        out["screenshot"] = sources.get("screenshot")
        out["screen_recording"] = sources.get("video")
        out["fused_render"] = "running"
    except Exception as exc:
        out["fused_render"] = f"not reachable: {exc}"
    return out


def list_displays() -> dict:
    """Displays with id, origin and size in points (global top-left coordinates) and pixel scale."""
    return {"displays": core.displays()}


def list_windows(app: str = "", title: str = "", limit: int = 40) -> dict:
    """Visible windows, front to back: id, app, title, bounds in global points."""
    windows, _ = _windows()
    a, t = (app or "").lower(), (title or "").lower()
    rows = [w for w in windows if w["layer"] == 0 and w["w"] >= 80 and w["h"] >= 60
            and a in w["app"].lower() and t in w["title"].lower()]
    return {"windows": [{k: w[k] for k in WINDOW_KEYS} for w in rows[: max(1, int(limit))]]}


def capture_screen(display: int = 0, cursor: bool = False, copy: bool = False, name: str = "",
                   read_text: bool = True) -> dict:
    """Capture a full display into the library and return the saved capture."""
    windows, displays = _windows()
    d = _pick_display(displays, display)
    front = core.front_app()
    main = next((w for w in windows if w["pid"] == front["pid"] and w["layer"] == 0 and w["title"]), None)
    info = {"app": front["app"], "title": main["title"] if main else "", "bundle": front["bundle"]}
    item = core.capture_to_library(d, None, info, "screen", copy=copy, name=name, cursor=cursor, ocr=read_text)
    return {"capture": _summary(item, 4000)}


def capture_window(app: str = "", title: str = "", window_id: int = 0, copy: bool = False, name: str = "",
                   read_text: bool = True) -> dict:
    """Capture one window (by id, or app and title text; default the front window)."""
    windows, displays = _windows()
    win = _match_window(windows, app, title, window_id)
    d, rect = _clip_to_display(win, displays)
    info = {"app": win["app"], "title": win["title"], "bundle": core.bundle_for_pid(win["pid"])}
    item = core.capture_to_library(d, rect, info, "window", copy=copy, name=name, ocr=read_text)
    return {"capture": _summary(item, 4000), "window": {k: win[k] for k in WINDOW_KEYS}}


def capture_area(x: float, y: float, width: float, height: float, display: int = 0, copy: bool = False,
                 name: str = "", read_text: bool = True) -> dict:
    """Capture a rectangle in points. With display 0, x and y are global; else they are local to that display."""
    windows, displays = _windows()
    if display:
        d = _pick_display(displays, display)
        gx, gy = d["x"] + float(x), d["y"] + float(y)
    else:
        gx, gy = float(x), float(y)
        d = next((d for d in displays if d["x"] <= gx + width / 2 < d["x"] + d["width"]
                  and d["y"] <= gy + height / 2 < d["y"] + d["height"]), None) or _pick_display(displays, 0)
    x0, y0 = max(gx, d["x"]), max(gy, d["y"])
    x1, y1 = min(gx + float(width), d["x"] + d["width"]), min(gy + float(height), d["y"] + d["height"])
    if x1 - x0 < 2 or y1 - y0 < 2:
        raise ValueError("the area is not on a display")
    rect = [round(x0 - d["x"]), round(y0 - d["y"]), round(x1 - x0), round(y1 - y0)]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    win = next((w for w in windows if w["layer"] == 0 and w["x"] <= cx <= w["x"] + w["w"] and w["y"] <= cy <= w["y"] + w["h"]), None)
    info = {"app": win["app"], "title": win["title"], "bundle": core.bundle_for_pid(win["pid"])} if win else {}
    item = core.capture_to_library(d, rect, info, "region", copy=copy, name=name, ocr=read_text)
    return {"capture": _summary(item, 4000)}


def open_url(url: str, browser: str = "", wait_seconds: float = 3) -> dict:
    """Open a web address in a browser on this Mac and return the browser's front window."""
    import subprocess

    url = core.web_url(url)
    res = subprocess.run(["open", "-a", browser, url] if browser else ["open", url], capture_output=True, text=True,
                         timeout=30)
    if res.returncode:
        raise RuntimeError(res.stderr.strip() or f"could not open {url}")
    time.sleep(max(0.0, min(30.0, float(wait_seconds))))
    name = os.path.splitext(os.path.basename(browser.rstrip("/")))[0] if browser else core.browser_for(url)
    windows, _ = _windows()
    try:
        win = _match_window(windows, name, "", 0) if name else None
    except ValueError:
        win = None
    if not win:
        return {"url": url, "browser": name, "window": None,
                "note": "The page opened, but no browser window is on screen: it may be on another Space or behind a "
                        "full-screen app. Use capture_web_page, or ask the person to show the window."}
    return {"url": url, "browser": win["app"], "window": {k: win[k] for k in WINDOW_KEYS}}


def capture_web_page(url: str, width: int = 1440, height: int = 900, full_page: bool = False,
                     wait_seconds: float = 2, dark: bool = False, scale: float = 1, copy: bool = False,
                     name: str = "", read_text: bool = True) -> dict:
    """Screenshot a web page in a hidden browser (no window, works when the screen is locked) into the library."""
    item = core.capture_web(url, width=width, height=height, full_page=full_page, wait=wait_seconds, dark=dark,
                            scale=scale, copy=copy, name=name, ocr=read_text)
    return {"capture": _summary(item, 4000), "url": item.get("note")}


def ask_user_to_capture(mode: str = "region", timeout: int = 120) -> dict:
    """Open Hagaki's selection overlay so the person picks an area, then wait for that capture."""
    if mode not in ("region", "window", "screen", "record", "gif"):
        raise ValueError("mode must be region, window, screen, record or gif")
    start = time.time()
    core.daemon_call("/capture", {"mode": mode}, start=True)
    want = "video" if mode == "record" else "image"
    only = "='gif'" if mode == "gif" else " NOT IN ('import', 'gif')"
    deadline = start + max(5, int(timeout))
    while time.time() < deadline:
        time.sleep(0.6)
        conn = core.connect()
        row = conn.execute(f"SELECT id FROM items WHERE created>=? AND kind=? AND source{only} AND deleted IS NULL "
                           "ORDER BY created DESC LIMIT 1", (start - 1, want)).fetchone()
        conn.close()
        if row:
            time.sleep(0.4)
            item = core.get_item(row[0])
            return {"capture": _summary(item, 4000)}
    try:
        core.daemon_call("/selection", {"action": "cancel"}, start=False, timeout=10)
    except Exception:
        pass
    return {"capture": None, "note": "No capture before the timeout. The person may have pressed Esc."}


def _load_recs() -> dict:
    try:
        with open(RECORDINGS, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_recs(recs: dict) -> None:
    core._atomic_write(RECORDINGS, json.dumps(recs, indent=2).encode())


def start_recording(x: float = 0, y: float = 0, width: float = 0, height: float = 0, display: int = 0,
                    app: str = "", title: str = "", audio: str = "none", max_seconds: int = 0, gif: bool = False) -> dict:
    """Start a screen recording of an area, a window (app/title) or a full display. Stop it with stop_recording."""
    if gif:
        audio = "none"
    if audio not in ("none", "mic", "system", "both"):
        raise ValueError("audio must be none, mic, system or both")
    windows, displays = _windows()
    info = {}
    if width and height:
        d = _pick_display(displays, display) if display else (core.display_for_point(float(x) + width / 2, float(y) + height / 2) or _pick_display(displays, 0))
        gx, gy = (d["x"] + float(x), d["y"] + float(y)) if display else (float(x), float(y))
        rect = [round(gx - d["x"]), round(gy - d["y"]), round(width), round(height)]
    elif app or title:
        win = _match_window(windows, app, title, 0)
        d, rect = _clip_to_display(win, displays)
        info = {"app": win["app"], "title": win["title"]}
    else:
        d = _pick_display(displays, display)
        rect = None
    if not info:
        front = core.front_app()
        info = {"app": front["app"], "title": ""}
    when = time.time()
    temp = os.path.join(core.WORK_DIR, f"agent-rec-{int(when * 1000)}.mov")
    gif_settings = core.load_settings().get("gif") or {}
    cursor = bool(gif_settings.get("cursor", True)) if gif else True
    limit = int(max_seconds) or (int(gif_settings.get("max_seconds") or 60) if gif else 300)
    body = {"mode": "screen", "display": d["id"], "audio": False if audio == "none" else audio, "cursor": cursor,
            "path": temp, "maxSeconds": max(1, min(3600, limit)), "title": "Hagaki recording"}
    if rect:
        body["rect"] = rect
    rec = core.server_call("/api/capture/start", body, timeout=40)
    began = time.time()
    recs = _load_recs()
    recs[rec["id"]] = {"temp": rec.get("path") or temp, "display": d["id"], "rect": rect, "info": info,
                       "started": when, "scale": d.get("scale", 1), "gif": bool(gif)}
    _save_recs(recs)
    core.log("info", "record", "Recording started by an agent", None, {"rect": rect, "display": d["id"]})
    try:
        if core.engine().get("running"):
            core.daemon_call("/show_recording", {"cid": rec["id"], "display": d["id"], "rect": rect, "started": began},
                             start=False, timeout=15)
    except Exception:
        pass
    return {"recording_id": rec["id"], "max_seconds": body["maxSeconds"], "display": d["id"], "rect": rect, "gif": bool(gif)}


def stop_recording(recording_id: str = "") -> dict:
    """Stop an agent recording (default the newest), save it to the library and return the capture."""
    recs = _load_recs()
    if not recs:
        raise ValueError("no agent recording is running")
    rid = recording_id or max(recs, key=lambda k: recs[k]["started"])
    rec = recs.get(rid)
    if rec is None:
        raise ValueError(f"unknown recording {rid}")
    try:
        res = core.server_call(f"/api/capture/{rid}/stop", {}, timeout=180)
    except RuntimeError as exc:
        res = {"error": str(exc)}
    try:
        track = core.daemon_call("/track", {"cid": rid}, start=False, timeout=15).get("track")
    except Exception:
        track = None
    path = res.get("path") or rec["temp"]
    if not os.path.exists(path):
        raise RuntimeError(f"the recording file is missing: {res.get('error') or path}")
    info = rec.get("info") or {}
    warning = res.get("warning")
    if rec.get("gif"):
        try:
            item = gif.store(path, info, rec["started"], rec.get("scale") or 1, display=rec.get("display"),
                             rect=rec.get("rect"))
        except Exception as exc:
            warning = f"The GIF failed, so Hagaki saved the video: {exc}"
        else:
            os.remove(path)
            recs = _load_recs()
            recs.pop(rid, None)
            _save_recs(recs)
            return {"capture": _summary(item)}
    settings = core.load_settings()
    meta = {"app": info.get("app"), "title": info.get("title"), "kind": "video", "source": "record"}
    stem = core.format_name(settings.get("name_pattern"), meta, rec["started"])
    final = core.unique_path(core.target_folder(settings, rec["started"]), stem, os.path.splitext(path)[1] or ".mov")
    shutil.move(path, final)
    ratio = core.fit_recording(final, rec.get("scale") or 1)
    item = core.ingest(final, kind="video", source="record", app=info.get("app") or "", title=info.get("title") or "",
                       display=rec.get("display"), rect=rec.get("rect"), pixel_ratio=ratio,
                       created=rec["started"])
    if track:
        reel.save_track(item["id"], track)
    recs = _load_recs()
    recs.pop(rid, None)
    _save_recs(recs)
    out = {"capture": _summary(item)}
    if warning:
        out["warning"] = warning
    return out


def search_captures(query: str = "", kind: str = "all", app: str = "", days: int = 0, limit: int = 20) -> dict:
    """Search the library by name, app, window title, tags, notes and text inside images."""
    if kind not in ("all", "image", "video", "styled"):
        raise ValueError("kind must be all, image, video or styled")
    res = core.list_items(q=query, kind=kind, app=app, days=days, limit=max(1, min(200, int(limit))))
    return {"total": res["total"], "captures": [_summary(i) for i in res["items"]]}


def get_capture(id: str, text_chars: int = 8000) -> dict:
    """One capture with all metadata, the text read from it, and its event history."""
    item = core.get_item(id)
    if not item:
        raise ValueError(f"no capture {id}")
    out = _summary(item, int(text_chars))
    out.update({"exists": item["exists"], "sha256": item.get("sha256"), "verified_at": _iso(item.get("verified")),
                "clipboard": bool(item.get("clipboard")), "name_source": item.get("name_source"),
                "pixel_ratio": item.get("pixel_ratio"), "thumbnail": item.get("thumb")})
    out["events"] = [{"at": _iso(e["ts"]), "level": e["level"], "kind": e["kind"], "message": e["message"]}
                     for e in core.events(limit=30, item=id)]
    return out


def latest_capture(kind: str = "all") -> dict:
    """The newest capture in the library."""
    res = core.list_items(kind=kind, limit=1)
    return {"capture": _summary(res["items"][0], 4000) if res["items"] else None}


def read_text(id: str, again: bool = False) -> dict:
    """The text in a screenshot (Apple Vision OCR on this Mac). Reads it now if needed."""
    item = core.get_item(id)
    if not item:
        raise ValueError(f"no capture {id}")
    if again or item.get("ocr_state") != "done":
        item = core.run_ocr(id)
    return {"id": id, "text": item.get("ocr") or ""}


def rename_capture(id: str, name: str = "", use_ai: bool = False) -> dict:
    """Rename a capture to `name`, or let the AI model name it from the image (use_ai true)."""
    if use_ai:
        item = core.ai_name(id)
    elif name.strip():
        item = core.rename(id, name, "agent")
    else:
        raise ValueError("give a name or set use_ai true")
    return {"capture": _summary(item)}


def tag_capture(id: str, tags: str | None = None, note: str | None = None) -> dict:
    """Set tags (comma-separated) and a note. Both are searchable. An empty string clears the value."""
    item = core.set_meta(id, tags=tags, note=note)
    return {"capture": _summary(item)}


def list_styles() -> dict:
    """Background ids, frame ids, shape (aspect) ids and default values for style_capture."""
    cat = core.studio_catalog()
    return {"backgrounds": [{"id": b["id"], "name": b["name"], "group": b.get("group")} for b in cat["backgrounds"]],
            "frames": cat["frames"], "aspects": cat["aspects"], "defaults": cat["defaults"]}


def style_capture(id: str, bg: str = "", frame: str = "", padding: float = -1, radius: float = -1,
                  shadow: float = -1, inset: float = -1, aspect: str = "", title: str = "", caption: str = "",
                  stamp: bool = False, scale: float = 1, copy: bool = False, zoom: bool = True,
                  zoom_amount: float = 1.8, follow: bool = True, rings: int = -1, find_clicks: bool = True) -> dict:
    """Make a styled copy of a screenshot or a recording (background, window frame, padding, corners, shadow,
    caption, stamp; for a recording also zoom on clicks)."""
    opts = {"bg": bg, "frame": frame, "aspect": aspect, "title": title, "caption": caption, "stamp": bool(stamp),
            "scale": scale}
    for key, value in (("padding", padding), ("radius", radius), ("shadow", shadow), ("inset", inset)):
        if value is not None and float(value) >= 0:
            opts[key] = float(value)
    item = core.get_item(id)
    if item and item["kind"] == "video":
        opts.pop("scale")
        options = {"auto": bool(zoom), "amount": zoom_amount, "follow": bool(follow),
                   "rings": None if int(rings) < 0 else bool(rings)}
        styled = reel.render_video(id, opts, options, copy=copy, find_clicks=find_clicks)
        return {"capture": _summary(styled), "clicks": reel.track_summary(reel.load_track(id)),
                "zoom": styled["style"]["zoom"]}
    item = core.render_style(id, opts, copy=copy)
    return {"capture": _summary(item)}


def make_gif(id: str, fps: int = 0, size: int = -1, colors: str = "", once: bool = False) -> dict:
    """Make an animated GIF from a recording (also a styled one) and add it to the library."""
    opts = {}
    if int(fps) > 0:
        opts["fps"] = int(fps)
    if int(size) >= 0:
        opts["size"] = int(size)
    if colors:
        if colors not in gif.COLORS:
            raise ValueError(f"colors must be one of: {', '.join(gif.COLORS)}")
        opts["colors"] = colors
    if once:
        opts["loop"] = False
    return {"capture": _summary(gif.make(id, opts))}


def copy_capture(id: str) -> dict:
    """Put a capture on the clipboard (image data, or the file for a recording)."""
    return core.copy_item(id)


def share_capture(id: str, mode: str = "public") -> dict:
    """Upload a capture to the person's Fused account and return a share link. mode: public or temporary."""
    if mode not in ("public", "temporary"):
        raise ValueError("mode must be public or temporary")
    item = core.share(id, mode)
    return {"capture": _summary(item), "url": item.get("share_url")}


def delete_capture(id: str) -> dict:
    """Move a capture file to the macOS Trash and remove it from the gallery."""
    return core.trash(id)


def verify_captures(id: str = "") -> dict:
    """Check SHA-256 of one capture (or all) against the value saved at capture time."""
    if id:
        item = core.verify(id)
        return {"id": id, "state": item["verify"]}
    return core.verify_all()


def open_in_hagaki(id: str = "") -> dict:
    """Open the Hagaki gallery (or one capture) for the person."""
    import subprocess

    url = core.app_url(id or None)
    subprocess.run(["open", url], check=False)
    return {"url": url}


def recent_events(limit: int = 40, level: str = "") -> dict:
    """Recent Hagaki log events (captures, clipboard, OCR, renames, uploads, errors)."""
    return {"events": [{"at": _iso(e["ts"]), "level": e["level"], "kind": e["kind"], "item": e["item"], "message": e["message"]}
                       for e in core.events(limit=max(1, min(500, int(limit))), level=level)]}


BOT_ACTIONS = ("status", "list_displays", "list_windows", "capture_screen", "capture_window", "capture_area",
               "open_url", "capture_web_page", "ask_user_to_capture", "start_recording", "stop_recording", "search_captures", "latest_capture",
               "get_capture", "read_text", "rename_capture", "tag_capture", "copy_capture", "list_styles", "style_capture", "make_gif",
               "verify_captures", "recent_events", "open_in_hagaki")


def main(action: str = "status", args: str = "{}") -> dict:
    if action not in BOT_ACTIONS:
        raise ValueError(f"action must be one of: {', '.join(BOT_ACTIONS)}")
    kwargs = json.loads(args) if isinstance(args, str) and args.strip() else (args or {})
    if action == "ask_user_to_capture":
        kwargs["timeout"] = min(int(kwargs.get("timeout", 50)), 50)
    if action in ("open_url", "capture_web_page"):
        kwargs["wait_seconds"] = min(float(kwargs.get("wait_seconds", 2)), 5)
    return globals()[action](**kwargs)


if __name__ == "__main__":
    fn = globals().get(sys.argv[1] if len(sys.argv) > 1 else "status")
    kwargs = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    print(json.dumps(fn(**kwargs), indent=2, default=str))
