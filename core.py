from __future__ import annotations

import base64
import copy
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, ".fused", "data")
CACHE_DIR = os.path.join(APP_DIR, ".fused", "cache")
THUMB_DIR = os.path.join(CACHE_DIR, "thumbs")
WORK_DIR = os.path.join(CACHE_DIR, "work")
DB_PATH = os.path.join(DATA_DIR, "hagaki.db")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
STAMP_PATH = os.path.join(DATA_DIR, "changed.stamp")
SYSKEYS_BACKUP = os.path.join(DATA_DIR, "system_shortcuts_backup.json")
SCHEMA_VERSION = 1
FTS_WORD_RULES = "'unicode61 remove_diacritics 2'"
THUMB_VERSION = 1

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".tiff", ".bmp"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".webm"}

DEFAULT_SETTINGS = {
    "library_dir": "~/Fused/Screenshots",
    "name_pattern": "{date} {time} {app} - {title}",
    "month_folders": True,
    "image_format": "png",
    "jpeg_quality": 90,
    "hotkeys": {
        "region": "alt+shift+4",
        "screen": "alt+shift+3",
        "window": "alt+shift+2",
        "record": "alt+shift+5",
        "open": "alt+shift+h",
    },
    "override_system": False,
    "after_capture": {
        "clipboard": True,
        "toast": True,
        "sound": True,
        "ocr": True,
        "open_editor": False,
        "auto_style": False,
        "ai_rename": False,
    },
    "capture": {"cursor": False, "snap_windows": True, "magnifier": True, "delay": 0, "scale": 1, "annotate": True},
    "record": {"audio": "none", "cursor": True, "max_seconds": 1800, "scale": 1, "draw_fade": True},
    "annotate": {"tool": "arrow", "color": 0, "size": 1, "fill": False},
    "draw": {"tool": "pen", "color": 0, "size": 1, "fill": False},
    "share": {"mode": "public", "copy_link": True},
    "style": None,
    "onboarded": False,
}

SHARE_CANVASES = {"public": "hagaki_public", "temporary": "hagaki_temporary"}
SHARE_COLUMNS = ("share_canvas", "share_slug", "share_uri", "share_viewer", "share_remote")

_lock = threading.RLock()
_schema_ready = False


def ensure_dirs() -> None:
    for d in (DATA_DIR, CACHE_DIR, THUMB_DIR, WORK_DIR):
        os.makedirs(d, exist_ok=True)


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _atomic_write(path: str, data: bytes) -> None:
    tmp = f"{path}.{uuid.uuid4().hex[:6]}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def load_settings() -> dict:
    ensure_dirs()
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as fh:
            stored = json.load(fh)
    except (OSError, ValueError):
        stored = {}
    return _merge(DEFAULT_SETTINGS, stored if isinstance(stored, dict) else {})


def save_settings(patch: dict) -> dict:
    with _lock:
        current = load_settings()
        merged = _merge(current, patch or {})
        _atomic_write(SETTINGS_PATH, json.dumps(merged, indent=2).encode("utf-8"))
    touch()
    return merged


def library_dir(settings: dict | None = None) -> str:
    settings = settings or load_settings()
    path = os.path.abspath(os.path.expanduser(settings.get("library_dir") or DEFAULT_SETTINGS["library_dir"]))
    os.makedirs(path, exist_ok=True)
    return path


def touch() -> None:
    ensure_dirs()
    _atomic_write(STAMP_PATH, str(time.time()).encode())


def connect() -> sqlite3.Connection:
    global _schema_ready
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    if not _schema_ready:
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS items(
              id TEXT PRIMARY KEY, kind TEXT, source TEXT, path TEXT, name TEXT, ext TEXT,
              created REAL, width INTEGER, height INTEGER, bytes INTEGER, duration REAL,
              pixel_ratio REAL DEFAULT 1, app TEXT DEFAULT '', bundle TEXT DEFAULT '',
              title TEXT DEFAULT '', display INTEGER, rect TEXT, parent TEXT,
              sha256 TEXT, verify TEXT, verified REAL, ocr TEXT DEFAULT '', ocr_state TEXT,
              tags TEXT DEFAULT '', note TEXT DEFAULT '', name_source TEXT,
              share_url TEXT, share_state TEXT, shared REAL, style TEXT, clipboard INTEGER DEFAULT 0,
              thumb TEXT, deleted REAL);
            CREATE INDEX IF NOT EXISTS items_created ON items(created);
            CREATE INDEX IF NOT EXISTS items_path ON items(path);
            CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
              id UNINDEXED, name, app, title, ocr, tags, note,
              tokenize={FTS_WORD_RULES});
            CREATE TABLE IF NOT EXISTS events(
              id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, level TEXT, kind TEXT,
              item TEXT, message TEXT, data TEXT);
            CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
            """
        )
        conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema', ?)", (str(SCHEMA_VERSION),))
        have = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
        for col in SHARE_COLUMNS:
            if col not in have:
                conn.execute(f"ALTER TABLE items ADD COLUMN {col} TEXT")
        _schema_ready = True
    return conn


def log(level: str, kind: str, message: str, item: str | None = None, data: dict | None = None) -> None:
    try:
        conn = connect()
        conn.execute(
            "INSERT INTO events(ts, level, kind, item, message, data) VALUES(?,?,?,?,?,?)",
            (time.time(), level, kind, item, message, json.dumps(data) if data else None),
        )
        conn.close()
    except sqlite3.Error as exc:
        print(f"hagaki log failed: {exc}", file=sys.stderr)
    print(f"[{level}] {kind}: {message}" + (f" ({item})" if item else ""), file=sys.stderr, flush=True)
    touch()


KIND_LEAD = {"image": "Screenshot", "video": "Recording", "styled": "Styled"}


def fused_safe(name: str) -> bool:
    alnum = re.sub(r"[^A-Za-z0-9]+", "", name or "")
    return bool(alnum) and alnum[0].isalpha()


def _sanitize(name: str, limit: int = 120, lead: str = "Capture") -> str:
    name = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", " ", str(name or ""))
    name = re.sub(r"\s+", " ", name).strip(" .-_–·")
    name = re.sub(r"(\s-\s)+$", "", name).strip()
    if len(name) > limit:
        name = name[:limit].rstrip(" .-_–·")
    if lead and name and not fused_safe(name):
        name = f"{lead} {name}"
    name = re.sub(r"\s*[–—]+\s*|\s+-+\s+", "-", name)
    name = re.sub(r"\s+", "_", name)
    return name or lead or "Capture"


def format_name(pattern: str, info: dict, when: float | None = None) -> str:
    t = dt.datetime.fromtimestamp(when or time.time())
    app = (info.get("app") or "").strip()
    title = (info.get("title") or "").strip()
    if title and app and title.lower() == app.lower():
        title = ""
    values = {
        "date": t.strftime("%Y-%m-%d"),
        "time": t.strftime("%H.%M.%S"),
        "year": t.strftime("%Y"),
        "month": t.strftime("%m"),
        "day": t.strftime("%d"),
        "app": app,
        "title": title[:70],
        "w": str(info.get("width") or ""),
        "h": str(info.get("height") or ""),
        "kind": "Recording" if info.get("kind") == "video" else "Screenshot",
        "source": info.get("source") or "",
        "rand": uuid.uuid4().hex[:4],
    }
    out = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), pattern or DEFAULT_SETTINGS["name_pattern"])
    out = re.sub(r"\s+-\s*$", "", out.strip())
    out = re.sub(r"^\s*-\s+", "", out)
    out = re.sub(r"\s+-\s+-\s+", " - ", out)
    return _sanitize(out, lead=values["kind"])


def _same(a: str, b: str | None) -> bool:
    if not b:
        return False
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.abspath(a) == os.path.abspath(b)


def unique_path(folder: str, stem: str, ext: str, ignore: str | None = None) -> str:
    candidate = os.path.join(folder, stem + ext)
    n = 2
    while os.path.exists(candidate) and not _same(candidate, ignore):
        candidate = os.path.join(folder, f"{stem} ({n}){ext}")
        n += 1
    return candidate


def move_file(src: str, dst: str) -> str:
    import shutil

    try:
        os.replace(src, dst)
    except OSError:
        shutil.move(src, dst)
    return dst


def target_folder(settings: dict, when: float | None = None) -> str:
    folder = library_dir(settings)
    if settings.get("month_folders"):
        folder = os.path.join(folder, dt.datetime.fromtimestamp(when or time.time()).strftime("%Y-%m"))
    os.makedirs(folder, exist_ok=True)
    return folder


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _fts_upsert(conn: sqlite3.Connection, item_id: str) -> None:
    row = conn.execute("SELECT id, name, app, title, ocr, tags, note FROM items WHERE id=?", (item_id,)).fetchone()
    conn.execute("DELETE FROM items_fts WHERE id=?", (item_id,))
    if row is not None:
        conn.execute(
            "INSERT INTO items_fts(id, name, app, title, ocr, tags, note) VALUES(?,?,?,?,?,?,?)",
            (row["id"], row["name"] or "", row["app"] or "", row["title"] or "", row["ocr"] or "", row["tags"] or "", row["note"] or ""),
        )


def update_item(item_id: str, **fields) -> None:
    if not fields:
        return
    with _lock:
        conn = connect()
        cols = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE items SET {cols} WHERE id=?", (*fields.values(), item_id))
        if set(fields) & {"name", "app", "title", "ocr", "tags", "note"}:
            _fts_upsert(conn, item_id)
        conn.close()
    touch()


def image_size(path: str) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        return int(im.width), int(im.height)


def make_thumb(item_id: str, path: str, kind: str) -> str | None:
    ensure_dirs()
    out = os.path.join(THUMB_DIR, f"{item_id}-v{THUMB_VERSION}.jpg")
    try:
        if kind == "video":
            if not video_frame(path, out, 720):
                return None
        else:
            from PIL import Image

            with Image.open(path) as im:
                im.thumbnail((720, 720))
                if im.mode in ("RGBA", "LA", "P"):
                    im = im.convert("RGBA")
                    bg = Image.new("RGB", im.size, (246, 241, 232))
                    bg.paste(im, mask=im.split()[-1])
                    im = bg
                else:
                    im = im.convert("RGB")
                tmp = out + ".tmp.jpg"
                im.save(tmp, "JPEG", quality=84, optimize=True)
                os.replace(tmp, out)
        return out
    except Exception as exc:
        log("warn", "thumbnail", f"Could not make a thumbnail: {exc}", item_id)
        return None


def video_info(path: str) -> dict:
    try:
        import AVFoundation
        import CoreMedia
        from Foundation import NSURL

        asset = AVFoundation.AVURLAsset.URLAssetWithURL_options_(NSURL.fileURLWithPath_(path), None)
        seconds = float(CoreMedia.CMTimeGetSeconds(asset.duration()))
        tracks = asset.tracksWithMediaType_(AVFoundation.AVMediaTypeVideo)
        width = height = 0
        if tracks and len(tracks):
            size = tracks[0].naturalSize()
            width, height = int(size.width), int(size.height)
        return {"duration": seconds if seconds == seconds else None, "width": width, "height": height}
    except Exception:
        return {"duration": None, "width": 0, "height": 0}


def frame_generator(path: str, max_side: int, tolerance: float | None = None):
    import Quartz
    import AVFoundation
    import CoreMedia
    from Foundation import NSURL

    asset = AVFoundation.AVURLAsset.URLAssetWithURL_options_(NSURL.fileURLWithPath_(path), None)
    gen = AVFoundation.AVAssetImageGenerator.assetImageGeneratorWithAsset_(asset)
    gen.setAppliesPreferredTrackTransform_(True)
    gen.setMaximumSize_((max_side, max_side))
    if tolerance is not None:
        tol = CoreMedia.CMTimeMakeWithSeconds(tolerance, 600)
        gen.setRequestedTimeToleranceBefore_(tol)
        gen.setRequestedTimeToleranceAfter_(tol)

    def grab(seconds: float):
        result = gen.copyCGImageAtTime_actualTime_error_(CoreMedia.CMTimeMakeWithSeconds(seconds, 600), None, None)
        return result[0] if isinstance(result, tuple) else result

    return grab, float(CoreMedia.CMTimeGetSeconds(asset.duration()) or 0)


def video_frame(path: str, out: str, max_side: int = 720) -> bool:
    grab, seconds = frame_generator(path, max_side)
    image = grab(min(0.4, max(0.0, seconds / 2)))
    if image is None:
        return False
    write_cgimage(image, out, "public.jpeg", 0.84)
    return True


def run_export(asset, comp, out: str, file_type, timeout: float, progress=None) -> None:
    import AVFoundation
    from Foundation import NSURL

    tmp = out + ".part" + os.path.splitext(out)[1]
    if os.path.exists(tmp):
        os.remove(tmp)
    session = AVFoundation.AVAssetExportSession.alloc().initWithAsset_presetName_(
        asset, AVFoundation.AVAssetExportPresetHighestQuality)
    if session is None:
        raise RuntimeError("macOS could not start the video export")
    session.setVideoComposition_(comp)
    session.setOutputURL_(NSURL.fileURLWithPath_(tmp))
    session.setOutputFileType_(file_type)
    session.setShouldOptimizeForNetworkUse_(True)
    done = threading.Event()
    session.exportAsynchronouslyWithCompletionHandler_(done.set)
    deadline = time.time() + timeout
    while not done.wait(0.25):
        if progress:
            progress(float(session.progress()))
        if time.time() > deadline:
            session.cancelExport()
            done.wait(5)
            break
    if session.status() != AVFoundation.AVAssetExportSessionStatusCompleted:
        err = session.error()
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError(err.localizedDescription() if err else "the export stopped")
    os.replace(tmp, out)


def write_cgimage(image, out: str, utype: str = "public.png", quality: float | None = None) -> None:
    import Quartz
    from Foundation import NSURL

    tmp = out + ".part"
    dest = Quartz.CGImageDestinationCreateWithURL(NSURL.fileURLWithPath_(tmp), utype, 1, None)
    if dest is None:
        raise OSError(f"could not write {out}")
    props = {Quartz.kCGImageDestinationLossyCompressionQuality: quality} if quality else None
    Quartz.CGImageDestinationAddImage(dest, image, props)
    if not Quartz.CGImageDestinationFinalize(dest):
        raise OSError(f"could not encode {out}")
    os.replace(tmp, out)


def scale_factor(pixel_ratio: float, scale) -> float:
    try:
        scale, pixel_ratio = float(scale or 0), float(pixel_ratio or 1)
    except (TypeError, ValueError):
        return 1.0
    if scale <= 0 or pixel_ratio <= 0:
        return 1.0
    return min(1.0, scale / pixel_ratio)


def scale_cgimage(image, factor: float):
    import Quartz

    if factor >= 0.999:
        return image
    w = max(1, round(Quartz.CGImageGetWidth(image) * factor))
    h = max(1, round(Quartz.CGImageGetHeight(image) * factor))
    space = Quartz.CGImageGetColorSpace(image) or Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)
    ctx = Quartz.CGBitmapContextCreate(None, w, h, 8, 0, space, Quartz.kCGImageAlphaPremultipliedLast)
    if ctx is None:
        space = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)
        ctx = Quartz.CGBitmapContextCreate(None, w, h, 8, 0, space, Quartz.kCGImageAlphaPremultipliedLast)
    Quartz.CGContextSetInterpolationQuality(ctx, Quartz.kCGInterpolationHigh)
    Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, w, h), image)
    return Quartz.CGBitmapContextCreateImage(ctx)


def scale_image_file(path: str, factor: float) -> None:
    if factor >= 0.999:
        return
    from PIL import Image

    with Image.open(path) as im:
        im.load()
        size = (max(1, round(im.width * factor)), max(1, round(im.height * factor)))
        small = im.resize(size, Image.LANCZOS)
    small.save(path)


def scale_video(path: str, factor: float) -> bool:
    if factor >= 0.999:
        return False
    import AVFoundation
    import CoreMedia
    import Quartz
    from Foundation import NSURL

    asset = AVFoundation.AVURLAsset.URLAssetWithURL_options_(NSURL.fileURLWithPath_(path), None)
    tracks = asset.tracksWithMediaType_(AVFoundation.AVMediaTypeVideo)
    if not tracks or not len(tracks):
        return False
    track = tracks[0]
    size = track.naturalSize()
    w = max(2, int(size.width * factor) // 2 * 2)
    h = max(2, int(size.height * factor) // 2 * 2)
    fastest = float(CoreMedia.CMTimeGetSeconds(track.minFrameDuration()) or 0)
    fps = round(1 / fastest) if fastest > 0 else int(round(track.nominalFrameRate() or 0)) or 30
    comp = AVFoundation.AVMutableVideoComposition.videoComposition()
    comp.setRenderSize_((w, h))
    comp.setFrameDuration_(CoreMedia.CMTimeMake(1, max(24, min(60, fps))))
    step = AVFoundation.AVMutableVideoCompositionInstruction.videoCompositionInstruction()
    step.setTimeRange_(CoreMedia.CMTimeRangeMake(CoreMedia.kCMTimeZero, asset.duration()))
    layer = AVFoundation.AVMutableVideoCompositionLayerInstruction.videoCompositionLayerInstructionWithAssetTrack_(track)
    layer.setTransform_atTime_(Quartz.CGAffineTransformMakeScale(w / size.width, h / size.height), CoreMedia.kCMTimeZero)
    step.setLayerInstructions_([layer])
    comp.setInstructions_([step])
    try:
        run_export(asset, comp, path, AVFoundation.AVFileTypeQuickTimeMovie, 900)
    except RuntimeError as exc:
        log("warn", "record", f"Could not make the recording smaller: {exc}")
        return False
    return True


def fit_recording(path: str, pixel_ratio: float) -> float:
    factor = scale_factor(pixel_ratio, (load_settings().get("record") or {}).get("scale"))
    try:
        if scale_video(path, factor):
            return float(pixel_ratio or 1) * factor
    except Exception as exc:
        log("warn", "record", f"Could not make the recording smaller: {exc}")
    return float(pixel_ratio or 1)


def ingest(path: str, *, kind: str | None = None, source: str = "import", app: str = "", bundle: str = "",
           title: str = "", display: int | None = None, rect=None, parent: str | None = None,
           pixel_ratio: float = 1.0, created: float | None = None, name_source: str = "pattern",
           style: dict | None = None, clipboard: bool = False, note: str = "") -> dict:
    path = os.path.abspath(path)
    ext = os.path.splitext(path)[1].lower()
    if kind is None:
        kind = "video" if ext in VIDEO_EXTS else "image"
    item_id = new_id()
    created = created or time.time()
    size = os.path.getsize(path)
    digest = sha256_file(path)
    width = height = 0
    duration = None
    if kind == "video":
        info = video_info(path)
        width, height, duration = info["width"], info["height"], info["duration"]
    else:
        try:
            width, height = image_size(path)
        except Exception:
            width = height = 0
    row = {
        "id": item_id, "kind": kind, "source": source, "path": path,
        "name": os.path.splitext(os.path.basename(path))[0], "ext": ext, "created": created,
        "width": width, "height": height, "bytes": size, "duration": duration,
        "pixel_ratio": pixel_ratio, "app": app or "", "bundle": bundle or "", "title": title or "",
        "display": display, "rect": json.dumps(rect) if rect else None, "parent": parent,
        "sha256": digest, "verify": "ok", "verified": time.time(),
        "ocr": "", "ocr_state": "pending" if kind != "video" else "none",
        "name_source": name_source, "style": json.dumps(style) if style else None,
        "clipboard": 1 if clipboard else 0, "note": note,
    }
    with _lock:
        conn = connect()
        cols = ", ".join(row)
        conn.execute(f"INSERT INTO items({cols}) VALUES({', '.join('?' for _ in row)})", tuple(row.values()))
        _fts_upsert(conn, item_id)
        conn.close()
    thumb = make_thumb(item_id, path, kind)
    if thumb:
        update_item(item_id, thumb=thumb)
    label = {"image": "Screenshot", "video": "Recording", "styled": "Styled image"}.get(kind, "File")
    log("info", "capture" if source != "import" else "import", f"{label} saved: {os.path.basename(path)}", item_id,
        {"path": path, "bytes": size, "width": width, "height": height, "source": source})
    return get_item(item_id) or row


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for key in ("rect", "style"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except ValueError:
                pass
    d["exists"] = bool(d.get("path")) and os.path.exists(d["path"])
    return d


def get_item(item_id: str) -> dict | None:
    conn = connect()
    r = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    conn.close()
    return _row(r) if r else None


def _fts_query(q: str) -> str:
    words = re.findall(r"[\w\-\.@']+", q, flags=re.UNICODE)
    parts = []
    for w in words:
        w = w.replace('"', "")
        if w:
            parts.append(f'"{w}"*')
    return " AND ".join(parts)


def prune_missing() -> int:
    conn = connect()
    gone = [(r[0], r[1]) for r in conn.execute("SELECT id, path FROM items WHERE deleted IS NULL")
            if not r[1] or not os.path.exists(r[1])]
    conn.close()
    now = time.time()
    for item_id, path in gone:
        update_item(item_id, deleted=now)
        log("info", "delete", f"File was deleted outside Hagaki: {os.path.basename(path or '')}", item_id)
    return len(gone)


def list_items(q: str = "", kind: str = "all", app: str = "", days: int = 0, limit: int = 200,
               offset: int = 0, shared: bool = False) -> dict:
    prune_missing()
    conn = connect()
    where = ["deleted IS NULL"]
    args: list = []
    if kind == "styled":
        where.append("(kind='styled' OR source='style')")
    elif kind and kind != "all":
        where.append("kind=?")
        args.append(kind)
    if app:
        where.append("app=?")
        args.append(app)
    if days and days > 0:
        where.append("created>=?")
        args.append(time.time() - days * 86400)
    if shared:
        where.append("share_url IS NOT NULL")
    match_ids = None
    if q.strip():
        fq = _fts_query(q)
        ids: list[str] = []
        if fq:
            try:
                ids = [r[0] for r in conn.execute("SELECT id FROM items_fts WHERE items_fts MATCH ? ORDER BY rank LIMIT 2000", (fq,))]
            except sqlite3.Error:
                ids = []
        like = f"%{q.strip()}%"
        ids += [r[0] for r in conn.execute(
            "SELECT id FROM items WHERE name LIKE ? OR app LIKE ? OR title LIKE ? OR ocr LIKE ? OR tags LIKE ? OR note LIKE ? LIMIT 2000",
            (like,) * 6)]
        match_ids = list(dict.fromkeys(ids))
        if not match_ids:
            conn.close()
            return {"items": [], "total": 0, "facets": facets()}
        where.append(f"id IN ({','.join('?' for _ in match_ids)})")
        args.extend(match_ids)
    sql_where = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM items WHERE {sql_where}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM items WHERE {sql_where} ORDER BY created DESC LIMIT ? OFFSET ?", (*args, limit, offset)
    ).fetchall()
    conn.close()
    items = [_row(r) for r in rows]
    for it in items:
        if it["exists"] and not (it.get("thumb") and os.path.exists(it["thumb"])):
            it["thumb"] = make_thumb(it["id"], it["path"], it["kind"])
            update_item(it["id"], thumb=it["thumb"])
    if q.strip():
        for it in items:
            it["snippet"] = _snippet(it, q)
    return {"items": items, "total": total, "facets": facets()}


def _snippet(item: dict, q: str) -> str:
    words = [w.lower() for w in re.findall(r"\w+", q)]
    text = item.get("ocr") or ""
    low = text.lower()
    for w in words:
        i = low.find(w)
        if i >= 0:
            start = max(0, i - 40)
            return ("…" if start else "") + text[start:i + 80].replace("\n", " ") + "…"
    return ""


def facets() -> dict:
    conn = connect()
    apps = [dict(r) for r in conn.execute(
        "SELECT app, COUNT(*) AS n FROM items WHERE deleted IS NULL AND app<>'' GROUP BY app ORDER BY n DESC LIMIT 40")]
    kinds = {r["kind"]: r["n"] for r in conn.execute(
        "SELECT kind, COUNT(*) AS n FROM items WHERE deleted IS NULL GROUP BY kind")}
    total = sum(kinds.values())
    styled_videos = conn.execute(
        "SELECT COUNT(*) FROM items WHERE deleted IS NULL AND kind='video' AND source='style'").fetchone()[0]
    if styled_videos:
        kinds["styled"] = kinds.get("styled", 0) + styled_videos
    shared = conn.execute("SELECT COUNT(*) FROM items WHERE deleted IS NULL AND share_url IS NOT NULL").fetchone()[0]
    midnight = time.mktime(dt.date.today().timetuple())
    today = conn.execute("SELECT COUNT(*) FROM items WHERE deleted IS NULL AND created>=?", (midnight,)).fetchone()[0]
    conn.close()
    return {"apps": apps, "kinds": kinds, "shared": shared, "all": total, "today": today}


def events(limit: int = 300, level: str = "", kind: str = "", item: str = "", q: str = "") -> list[dict]:
    conn = connect()
    where, args = ["1=1"], []
    if level:
        where.append("level=?")
        args.append(level)
    if kind:
        where.append("kind=?")
        args.append(kind)
    if item:
        where.append("item=?")
        args.append(item)
    if q:
        where.append("message LIKE ?")
        args.append(f"%{q}%")
    rows = conn.execute(f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("data"):
            try:
                d["data"] = json.loads(d["data"])
            except ValueError:
                pass
        out.append(d)
    return out


def rename(item_id: str, new_name: str, source: str = "manual") -> dict:
    item = get_item(item_id)
    if not item:
        raise KeyError("no such item")
    stem = _sanitize(new_name, lead=KIND_LEAD.get(item["kind"], "Capture"))
    old = item["path"]
    if not os.path.exists(old):
        raise FileNotFoundError(old)
    target = unique_path(os.path.dirname(old), stem, item["ext"], ignore=old)
    if target != old:
        os.rename(old, target)
    update_item(item_id, path=target, name=os.path.splitext(os.path.basename(target))[0], name_source=source)
    log("info", "rename", f"Renamed to {os.path.basename(target)}", item_id,
        {"from": os.path.basename(old), "to": os.path.basename(target), "by": source})
    return get_item(item_id)


def set_meta(item_id: str, tags: str | None = None, note: str | None = None) -> dict:
    fields = {}
    if tags is not None:
        fields["tags"] = ", ".join(t.strip() for t in tags.split(",") if t.strip())
    if note is not None:
        fields["note"] = note
    update_item(item_id, **fields)
    return get_item(item_id)


def trash(item_id: str) -> dict:
    item = get_item(item_id)
    if not item:
        raise KeyError("no such item")
    moved = False
    if os.path.exists(item["path"]):
        try:
            from Foundation import NSURL, NSFileManager

            ok, _url, _err = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
                NSURL.fileURLWithPath_(item["path"]), None, None)
            moved = bool(ok)
        except Exception:
            moved = False
        if not moved:
            log("error", "delete", f"Could not move to the Trash: {os.path.basename(item['path'])}", item_id)
            raise RuntimeError("This disk has no Trash, so Hagaki did not delete the file. Delete it in Finder.")
    update_item(item_id, deleted=time.time())
    log("info", "delete", f"Moved to the Trash: {os.path.basename(item['path'])}", item_id)
    return {"ok": True, "trashed": moved}


def verify(item_id: str) -> dict:
    item = get_item(item_id)
    if not item:
        raise KeyError("no such item")
    if not os.path.exists(item["path"]):
        state = "missing"
    else:
        state = "ok" if sha256_file(item["path"]) == item["sha256"] else "changed"
    update_item(item_id, verify=state, verified=time.time())
    level = "info" if state == "ok" else "warn"
    words = {"ok": "File is correct (SHA-256 matches)", "missing": "File is missing", "changed": "File changed after capture"}
    log(level, "verify", f"{words[state]}: {item['name']}", item_id)
    return get_item(item_id)


def verify_all() -> dict:
    conn = connect()
    ids = [r[0] for r in conn.execute("SELECT id FROM items WHERE deleted IS NULL")]
    conn.close()
    counts = {"ok": 0, "missing": 0, "changed": 0}
    for i in ids:
        item = get_item(i)
        if not os.path.exists(item["path"]):
            state = "missing"
        else:
            state = "ok" if sha256_file(item["path"]) == item["sha256"] else "changed"
        counts[state] += 1
        update_item(i, verify=state, verified=time.time())
    log("info" if counts["missing"] + counts["changed"] == 0 else "warn", "verify",
        f"Checked {len(ids)} files: {counts['ok']} correct, {counts['changed']} changed, {counts['missing']} missing", None, counts)
    return counts


def rescan() -> dict:
    root = library_dir()
    conn = connect()
    known = {r[0] for r in conn.execute("SELECT path FROM items WHERE deleted IS NULL")}
    conn.close()
    added = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("."):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext not in IMAGE_EXTS and ext not in VIDEO_EXTS:
                continue
            full = os.path.join(dirpath, fn)
            if full in known:
                continue
            ingest(full, source="import", created=os.path.getmtime(full))
            added += 1
    log("info", "import", f"Library scan found {added} new files", None, {"root": root})
    return {"added": added}


def ocr_pending() -> dict:
    settings = load_settings()
    conn = connect()
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM items WHERE deleted IS NULL AND kind<>'video' AND ocr_state='pending' ORDER BY created DESC")]
    conn.close()
    if not (settings.get("after_capture") or {}).get("ocr", True):
        for i in ids:
            update_item(i, ocr_state="none")
        return {"read": 0}
    for i in ids:
        if (get_item(i) or {}).get("ocr_state") == "pending":
            run_ocr(i)
    return {"read": len(ids)}


def ocr_image(path: str, accurate: bool = True) -> str:
    import objc
    from Foundation import NSURL

    objc.loadBundle("Vision", {}, bundle_path="/System/Library/Frameworks/Vision.framework")
    request_cls = objc.lookUpClass("VNRecognizeTextRequest")
    handler_cls = objc.lookUpClass("VNImageRequestHandler")
    request = request_cls.alloc().init()
    request.setRecognitionLevel_(0 if accurate else 1)
    request.setUsesLanguageCorrection_(True)
    try:
        request.setAutomaticallyDetectsLanguage_(True)
    except Exception:
        pass
    handler = handler_cls.alloc().initWithURL_options_(NSURL.fileURLWithPath_(path), {})
    handler.performRequests_error_([request], None)
    lines = []
    for obs in request.results() or []:
        cands = obs.topCandidates_(1)
        if cands and len(cands):
            lines.append(str(cands[0].string()))
    return "\n".join(lines)


def run_ocr(item_id: str) -> dict:
    item = get_item(item_id)
    if not item or item["kind"] == "video":
        return item or {}
    try:
        text = ocr_image(item["path"])
        update_item(item_id, ocr=text, ocr_state="done")
        words = len(text.split())
        log("info", "ocr", f"Read {words} words of text", item_id)
    except Exception as exc:
        update_item(item_id, ocr_state="error")
        log("error", "ocr", f"Text reading failed: {exc}", item_id)
    return get_item(item_id)


def copy_image(path: str) -> bool:
    from AppKit import NSPasteboard, NSPasteboardItem
    from Foundation import NSData

    data = NSData.dataWithContentsOfFile_(path)
    if data is None:
        return False
    ext = os.path.splitext(path)[1].lower()
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    item = NSPasteboardItem.alloc().init()
    item.setData_forType_(data, "public.jpeg" if ext in (".jpg", ".jpeg") else "public.png")
    try:
        from AppKit import NSBitmapImageRep

        rep = NSBitmapImageRep.imageRepWithData_(data)
        if rep is not None:
            item.setData_forType_(rep.TIFFRepresentation(), "public.tiff")
    except Exception:
        pass
    return bool(pb.writeObjects_([item]))


def copy_file(path: str) -> bool:
    from AppKit import NSPasteboard
    from Foundation import NSURL

    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    return bool(pb.writeObjects_([NSURL.fileURLWithPath_(path)]))


def copy_text(text: str) -> bool:
    from AppKit import NSPasteboard

    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    return bool(pb.setString_forType_(text, "public.utf8-plain-text"))


def paste_clipboard() -> list[dict]:
    import shutil

    from AppKit import NSBitmapImageRep, NSPasteboard
    from Foundation import NSURL

    pb = NSPasteboard.generalPasteboard()
    settings = load_settings()
    ocr = (settings.get("after_capture") or {}).get("ocr", True)
    when = time.time()
    urls = pb.readObjectsForClasses_options_([NSURL], {"NSPasteboardURLReadingFileURLsOnlyKey": True}) or []
    files = [str(u.path()) for u in urls]
    files = [f for f in files if os.path.isfile(f) and os.path.splitext(f)[1].lower() in IMAGE_EXTS | VIDEO_EXTS]
    items = []
    if files:
        conn = connect()
        known = dict(conn.execute("SELECT path, id FROM items WHERE deleted IS NULL").fetchall())
        conn.close()
        for src in files:
            if os.path.abspath(src) in known:
                items.append(get_item(known[os.path.abspath(src)]))
                continue
            stem, ext = os.path.splitext(os.path.basename(src))
            out = unique_path(target_folder(settings, when), _sanitize(stem), ext.lower())
            shutil.copy2(src, out)
            item = ingest(out, source="paste", created=when, name_source="manual")
            if item["kind"] != "video" and ocr:
                item = run_ocr(item["id"])
            elif item["kind"] != "video":
                update_item(item["id"], ocr_state="none")
                item = get_item(item["id"])
            items.append(item)
        return items
    data = pb.dataForType_("public.png")
    if data is None:
        raw = pb.dataForType_("public.tiff") or pb.dataForType_("public.jpeg")
        rep = NSBitmapImageRep.imageRepWithData_(raw) if raw is not None else None
        data = rep.representationUsingType_properties_(4, {}) if rep is not None else None
    if data is None:
        raise ValueError("The clipboard has no image or image file.")
    tmp = os.path.join(WORK_DIR, f"paste-{int(when * 1000)}-{uuid.uuid4().hex[:4]}.png")
    if not data.writeToFile_atomically_(tmp, True):
        raise OSError("could not write the clipboard image")
    return [store_image(tmp, {}, "paste", when, ocr=ocr)]


def copy_item(item_id: str) -> dict:
    item = get_item(item_id)
    if not item or not os.path.exists(item["path"]):
        raise FileNotFoundError("the file is missing")
    ok = copy_file(item["path"]) if item["kind"] == "video" else copy_image(item["path"])
    log("info" if ok else "error", "clipboard",
        ("Copied to the clipboard" if ok else "Could not copy to the clipboard") + f": {item['name']}", item_id)
    return {"ok": ok}


def styled_path(parent: dict, ext: str) -> str:
    folder = os.path.dirname(parent["path"])
    if not os.path.isdir(folder):
        folder = target_folder(load_settings())
    return unique_path(folder, _sanitize(parent["name"] + " styled", lead="Styled"), ext)


def save_styled(parent_id: str, data_url: str, style: dict | None = None, copy: bool = False) -> dict:
    parent = get_item(parent_id)
    if not parent:
        raise KeyError("no such item")
    header, _, b64 = data_url.partition(",")
    raw = base64.b64decode(b64)
    out = styled_path(parent, ".png")
    _atomic_write(out, raw)
    item = ingest(out, kind="styled", source="style", app=parent.get("app") or "", title=parent.get("title") or "",
                  parent=parent_id, pixel_ratio=parent.get("pixel_ratio") or 1, style=style,
                  name_source="pattern")
    parent_state = parent.get("ocr_state") or "none"
    update_item(item["id"], ocr=parent.get("ocr") or "", ocr_state=parent_state if parent_state != "pending" else "none")
    if copy:
        copy_image(out)
        update_item(item["id"], clipboard=1)
        log("info", "clipboard", f"Copied styled image: {os.path.basename(out)}", item["id"])
    return get_item(item["id"])


def server_info() -> dict:
    home = os.environ.get("FUSED_RENDER_HOME_DIR") or os.path.expanduser("~/.fused-render")
    try:
        with open(os.path.join(home, "server.json"), encoding="utf-8") as fh:
            info = json.load(fh)
    except (OSError, ValueError):
        info = {}
    origin = os.environ.get("FUSED_RENDER_ORIGIN") or info.get("origin") or "http://127.0.0.1:1777"
    info["origin"] = origin.rstrip("/")
    return info


def server_call(path: str, body: dict | None = None, method: str = "POST", timeout: float = 30) -> dict:
    url = server_info()["origin"] + path
    data = json.dumps(body or {}).encode() if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"X-Fused": "1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"{}")
        except ValueError:
            payload = {}
        message = payload.get("error") or payload.get("detail") or f"HTTP {exc.code}"
        raise RuntimeError(message) from None


def app_url(item_id: str | None = None) -> str:
    page = os.path.join(APP_DIR, "index.html")
    path = "/".join(urllib.parse.quote(seg) for seg in page.lstrip("/").split("/"))
    url = f"{server_info()['origin']}/explorer/view/{path}"
    if item_id:
        url += f"?item={urllib.parse.quote(item_id)}"
    return url


def share_status(item_id: str) -> dict:
    item = get_item(item_id)
    if not item:
        raise KeyError("no such item")
    q = urllib.parse.urlencode({"path": item["path"]})
    return server_call(f"/api/share/file/status?{q}", method="GET")


SIGN_IN_FIRST = "Sign in to Fused first. Then put the capture online again."


def fused_signed_in() -> bool:
    try:
        return bool(server_call("/api/canvases/status", method="GET", timeout=10).get("logged_in"))
    except Exception:
        return True


def _share_helper(req: dict, timeout: float) -> dict:
    env = dict(os.environ)
    env.pop("OPENFUSED_ENV", None)
    env["FUSED_ENV"] = os.environ.get("FUSED_RENDER_WORKBENCH_ENV", "prod")
    try:
        proc = subprocess.run([sys.executable, "-I", os.path.join(APP_DIR, "share_canvas.py")], input=json.dumps(req),
                              capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Fused did not answer in {int(timeout)} s")
    if proc.returncode != 0:
        lines = [ln for ln in proc.stderr.strip().splitlines() if ln.strip()]
        raise RuntimeError(lines[-1] if lines else "the Fused upload failed")
    for line in proc.stderr.strip().splitlines():
        log("warn", "share", line)
    return json.loads(proc.stdout)


def _share_slug(item: dict) -> str:
    stem = re.sub(r"[^a-z0-9]+", "_", item["name"].lower()).strip("_")[:40] or "capture"
    if stem[0].isdigit():
        stem = "capture_" + stem
    return f"{stem}_{item['id']}"


def _publish_canvas(canvas: str, temporary: bool, entries: list[dict], delete: list[str]) -> dict:
    return _share_helper({"action": "publish", "canvas": canvas, "scope": "team" if temporary else "public",
                          "session": temporary, "entries": entries, "delete": delete}, 600)


def _canvas_entries(canvas: str, skip: str) -> list[dict]:
    with _lock:
        conn = connect()
        rows = conn.execute("SELECT id, name, share_slug, share_uri, share_viewer FROM items "
                            "WHERE share_canvas=? AND id<>? AND share_uri IS NOT NULL ORDER BY shared",
                            (canvas, skip)).fetchall()
        conn.close()
    return [{"slug": r["share_slug"], "title": r["name"], "uri": r["share_uri"], "viewer": r["share_viewer"]} for r in rows]


def _share_locked():
    import fcntl

    ensure_dirs()
    handle = open(os.path.join(DATA_DIR, "share.lock"), "w")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


def share(item_id: str, mode: str = "public") -> dict:
    item = get_item(item_id)
    if not item:
        raise KeyError("no such item")
    if mode not in SHARE_CANVASES:
        raise ValueError(f"mode must be one of {', '.join(SHARE_CANVASES)}")
    if not fused_signed_in():
        update_item(item_id, share_state=None)
        log("warn", "share", f"Not put online: {SIGN_IN_FIRST}", item_id)
        raise RuntimeError(SIGN_IN_FIRST)
    canvas = SHARE_CANVASES[mode]
    update_item(item_id, share_state="uploading")
    log("info", "share", f"Upload started: {item['name']} (canvas {canvas})", item_id)
    try:
        up = _share_helper({"action": "upload", "file": item["path"], "folder": item_id}, 1800)
        slug = item.get("share_slug") or _share_slug(item)
        lock = _share_locked()
        try:
            entries = _canvas_entries(canvas, item_id)
            entries.append({"slug": slug, "title": item["name"], "uri": up["s3_uri"], "viewer": up["viewer"]})
            old = item.get("share_remote")
            out = _publish_canvas(canvas, mode == "temporary", entries, [old] if old and old != up["remote"] else [])
            url = f"{out['base']}/{slug}.html"
            if out.get("session_token"):
                url += f"?fused_session_token={out['session_token']}"
            update_item(item_id, share_url=url, share_state="done", shared=time.time(), share_canvas=canvas,
                        share_slug=slug, share_uri=up["s3_uri"], share_viewer=up["viewer"], share_remote=up["remote"])
            before = item.get("share_canvas")
            if before and before != canvas:
                try:
                    _publish_canvas(before, before == SHARE_CANVASES["temporary"], _canvas_entries(before, item_id), [])
                except Exception as exc:
                    log("warn", "share", f"Could not take it out of the canvas {before}: {exc}", item_id)
        finally:
            lock.close()
        log("info", "share", f"Online: {url}", item_id, {"url": url, "canvas": canvas, "workbench": out.get("workbench_url")})
        if load_settings()["share"].get("copy_link"):
            try:
                copy_text(url)
            except Exception:
                pass
        return get_item(item_id)
    except Exception as exc:
        update_item(item_id, share_state="error")
        log("error", "share", f"Upload failed: {exc}", item_id)
        raise


def spawn_job(op: str, item_id: str = "") -> None:
    script = os.path.join(APP_DIR, "jobs.py")
    subprocess.Popen([sys.executable, "-s", script, op] + ([item_id] if item_id else []), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=open(os.path.join(CACHE_DIR, "jobs.log"), "ab"),
                     cwd=APP_DIR, start_new_session=True, close_fds=True)


def _ai_image(path: str) -> str:
    from PIL import Image

    out = os.path.join(WORK_DIR, f"ai-{os.getpid()}-{uuid.uuid4().hex[:6]}.jpg")
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((1568, 1568))
        im.save(out, "JPEG", quality=85)
    return out


def _clean_ai_name(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    text = text.strip().splitlines()[0] if text.strip() else ""
    text = re.sub(r"^(file ?name|name|title)\s*[:\-]\s*", "", text, flags=re.I)
    text = text.strip("`\"' .")
    text = re.sub(r"\.(png|jpe?g|mov|mp4)$", "", text, flags=re.I)
    name = _sanitize(text, 80, lead="")
    return "" if name == "Capture" else name


AI_PROMPT = (
    "Give this screenshot a short file title. Reply with ONLY the title: 3 to 7 words, Title Case. "
    "Name the main content first, then the app or site. Do not use the words screenshot, window, "
    "showing, displaying, image or view. Start with a letter, not a number. No date, no quotes, no file extension. "
    "Examples: Stripe Dashboard Monthly Revenue / Fused Render Project Folder List / Slack Thread About Q3 Launch."
)


def _shared_path() -> None:
    try:
        info = server_info()
        shared = info.get("shared")
        if shared and shared not in sys.path:
            sys.path.insert(0, shared)
    except Exception:
        pass


def ai_name(item_id: str) -> dict:
    item = get_item(item_id)
    if not item:
        raise KeyError("no such item")
    _shared_path()
    import fused_ai

    context = []
    if item.get("app"):
        context.append(f"App: {item['app']}")
    if item.get("title"):
        context.append(f"Window title: {item['title']}")
    ocr = (item.get("ocr") or "").strip()
    if ocr:
        context.append("Text on screen (first lines): " + " | ".join(ocr.splitlines()[:25])[:900])
    prompt = AI_PROMPT + ("\n\n" + "\n".join(context) if context else "")
    errors = []
    name, used = "", ""
    if item["kind"] != "video" and os.path.exists(item["path"]):
        small = _ai_image(item["path"])
        try:
            name = _clean_ai_name(fused_ai.text(prompt, images=[small]))
            used = "claude"
        except Exception as exc:
            errors.append(f"image model: {exc}")
        finally:
            try:
                os.remove(small)
            except OSError:
                pass
    if not name and context:
        try:
            name = _clean_ai_name(fused_ai.text(prompt, provider="apple"))
            used = "apple"
        except Exception as exc:
            errors.append(f"apple: {exc}")
    if not name and context:
        try:
            name = _clean_ai_name(fused_ai.text(prompt))
            used = "default"
        except Exception as exc:
            errors.append(f"default: {exc}")
    if not name:
        message = "; ".join(errors) or "the model gave no name"
        log("error", "rename", f"Magic rename failed: {message}", item_id)
        raise RuntimeError(message)
    stamp = dt.datetime.fromtimestamp(item["created"]).strftime("%Y-%m-%d %H.%M")
    out = rename(item_id, f"{name} {stamp}", source="ai")
    log("info", "rename", f"Magic rename used the {used} model", item_id)
    return out


def front_app() -> dict:
    try:
        from AppKit import NSWorkspace

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return {"app": str(app.localizedName() or ""), "bundle": str(app.bundleIdentifier() or ""), "pid": int(app.processIdentifier())}
    except Exception:
        return {"app": "", "bundle": "", "pid": 0}


def window_list(exclude_pid: int | None = None) -> list[dict]:
    import Quartz

    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    out = []
    for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []:
        layer = int(w.get("kCGWindowLayer", 0))
        pid = int(w.get("kCGWindowOwnerPID", 0))
        if exclude_pid and pid == exclude_pid:
            continue
        if layer < 0 or layer >= 24:
            continue
        if float(w.get("kCGWindowAlpha", 1)) <= 0.01:
            continue
        b = w.get("kCGWindowBounds") or {}
        x, y, width, height = float(b.get("X", 0)), float(b.get("Y", 0)), float(b.get("Width", 0)), float(b.get("Height", 0))
        if width < 40 or height < 24:
            continue
        owner = str(w.get("kCGWindowOwnerName") or "")
        if owner in ("Window Server", "Dock", "Wallpaper"):
            continue
        out.append({"x": x, "y": y, "w": width, "h": height, "app": owner, "title": str(w.get("kCGWindowName") or ""),
                    "pid": pid, "layer": layer, "id": int(w.get("kCGWindowNumber", 0))})
    return out


def bundle_for_pid(pid: int) -> str:
    try:
        from AppKit import NSRunningApplication

        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        return str(app.bundleIdentifier() or "") if app else ""
    except Exception:
        return ""


def displays() -> list[dict]:
    import Quartz

    err, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
    main_id = Quartz.CGMainDisplayID()
    out = []
    for did in list(ids)[:count]:
        b = Quartz.CGDisplayBounds(did)
        mode = Quartz.CGDisplayCopyDisplayMode(did)
        scale = 1
        if mode is not None:
            pts = max(1, int(Quartz.CGDisplayModeGetWidth(mode)))
            scale = max(1, round(int(Quartz.CGDisplayModeGetPixelWidth(mode)) / pts))
        out.append({"id": int(did), "main": int(did) == int(main_id), "x": float(b.origin.x), "y": float(b.origin.y),
                    "width": float(b.size.width), "height": float(b.size.height), "scale": scale})
    return out


def display_for_point(x: float, y: float) -> dict | None:
    for d in displays():
        if d["x"] <= x < d["x"] + d["width"] and d["y"] <= y < d["y"] + d["height"]:
            return d
    return None


def capture_to_library(display: dict, rect=None, info: dict | None = None, source: str = "screen",
                       copy: bool = False, name: str = "", cursor: bool = False, ocr: bool = True,
                       created: float | None = None) -> dict:
    when = created or time.time()
    tmp = os.path.join(WORK_DIR, f"agent-{int(when * 1000)}-{uuid.uuid4().hex[:4]}.png")
    body = {"display": display["id"], "path": tmp, "cursor": bool(cursor)}
    if rect:
        body["rect"] = [float(n) for n in rect]
    shot = server_call("/api/capture/screenshot", body, timeout=30)
    width = int(shot.get("width") or 0)
    local_w = (rect[2] if rect else display["width"]) or 1
    ratio = width / local_w if width else display.get("scale", 1)
    factor = scale_factor(ratio, (load_settings().get("capture") or {}).get("scale"))
    scale_image_file(tmp, factor)
    return store_image(tmp, info or {}, source, when, name=name, copy=copy, ocr=ocr, display=display["id"],
                       rect=rect, pixel_ratio=ratio * factor)


def store_image(tmp: str, info: dict, source: str, when: float, *, name: str = "", copy: bool = False,
                ocr: bool = True, display: int | None = None, rect=None, pixel_ratio: float = 1.0,
                note: str = "") -> dict:
    settings = load_settings()
    width, height = image_size(tmp)
    meta = {"app": info.get("app"), "title": info.get("title"), "width": width, "height": height,
            "kind": "image", "source": source}
    stem = _sanitize(name, lead="Screenshot") if name else format_name(settings.get("name_pattern"), meta, when)
    jpeg = settings.get("image_format") == "jpg"
    out = unique_path(target_folder(settings, when), stem, ".jpg" if jpeg else ".png")
    if jpeg:
        from PIL import Image

        with Image.open(tmp) as im:
            im.convert("RGB").save(out, "JPEG", quality=int(settings.get("jpeg_quality") or 90))
        os.remove(tmp)
    else:
        move_file(tmp, out)
    copied = False
    if copy:
        copied = copy_image(out)
    item = ingest(out, kind="image", source=source, app=info.get("app") or "", title=info.get("title") or "",
                  bundle=info.get("bundle") or "", display=display, rect=rect, pixel_ratio=pixel_ratio, created=when,
                  name_source="manual" if name else "pattern", clipboard=copied, note=note)
    if copied:
        log("info", "clipboard", "Copied to the clipboard", item["id"])
    if ocr:
        item = run_ocr(item["id"])
    else:
        update_item(item["id"], ocr_state="none")
        item = get_item(item["id"])
    return item


def engine() -> dict:
    page = os.path.join(APP_DIR, "index.html")
    q = urllib.parse.urlencode({"html": page})
    return server_call(f"/api/apps/background/status?{q}", method="GET", timeout=10)


def ensure_daemon() -> dict:
    status = engine()
    if not status.get("running"):
        server_call("/api/apps/background/start", {"html": os.path.join(APP_DIR, "index.html")}, timeout=60)
        status = engine()
    return status


def daemon_call(route: str, body: dict | None = None, start: bool = True, timeout: float = 60) -> dict:
    status = ensure_daemon() if start else engine()
    if not status.get("running"):
        raise RuntimeError("Hagaki is not running")
    return server_call(f"/api/engines/{status['engine_id']}/proxy/{route.lstrip('/')}", body or {}, timeout=timeout)


CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)


def _browser() -> str:
    for path in CHROME_PATHS:
        if os.path.exists(path):
            return path
    raise RuntimeError("Hagaki needs Google Chrome, Chromium, Edge or Brave for this")


def _script_json(value) -> str:
    return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _studio_source() -> str:
    with open(os.path.join(APP_DIR, "studio.js"), encoding="utf-8") as fh:
        source = fh.read()
    if "</script" in source.lower():
        raise RuntimeError("studio.js cannot be inlined")
    return source


def _headless_dom(name: str, body: str, timeout: float = 90, budget: int = 0) -> str:
    page = os.path.join(WORK_DIR, f"{name}-{uuid.uuid4().hex[:8]}.html")
    with open(page, "w", encoding="utf-8") as fh:
        fh.write(f"<!doctype html><html><head><meta charset=\"utf-8\"></head><body>{body}</body></html>")
    args = [_browser(), "--headless=new", "--disable-gpu", f"--user-data-dir={os.path.join(CACHE_DIR, 'chrome-profile')}"]
    if budget:
        args.append(f"--virtual-time-budget={budget}")
    try:
        proc = subprocess.run(args + ["--dump-dom", "file://" + urllib.parse.quote(page)],
                              capture_output=True, text=True, timeout=timeout)
    finally:
        try:
            os.remove(page)
        except OSError:
            pass
    err = re.search(r'data-err="([^"]*)"', proc.stdout)
    if err:
        raise RuntimeError(f"style render failed: {err.group(1)}")
    if proc.returncode and "data-out" not in proc.stdout:
        raise RuntimeError(f"headless Chrome failed: {proc.stderr[-300:]}")
    return proc.stdout


def _merged_style(item: dict, opts: dict | None) -> dict:
    merged = dict(load_settings().get("style") or {})
    merged.pop("zoom", None)
    merged.update({k: v for k, v in (opts or {}).items() if v not in (None, "")})
    merged.setdefault("title", item.get("title") or item.get("app") or "")
    merged.setdefault("date", dt.datetime.fromtimestamp(item["created"]).strftime("%Y-%m-%d"))
    merged["scale"] = float(merged.get("scale") or 1)
    return merged


def render_style(item_id: str, opts: dict | None = None, copy: bool = False) -> dict:
    item = get_item(item_id)
    if not item or item["kind"] == "video":
        raise ValueError("only screenshots can be styled")
    if not os.path.exists(item["path"]):
        raise FileNotFoundError(item["path"])
    merged = _merged_style(item, opts)
    ext = item["ext"].lstrip(".").replace("jpg", "jpeg")
    with open(item["path"], "rb") as fh:
        data = base64.b64encode(fh.read()).decode()
    meta = {"pixelRatio": item.get("pixel_ratio") or 1}
    dom = _headless_dom("style", (
        "<canvas id=c></canvas>"
        f"<script>{_studio_source()}</script>"
        "<script>const img=new Image();img.onload=()=>{try{"
        f"HagakiStudio.render(document.getElementById('c'),img,{_script_json(merged)},{_script_json(meta)});"
        "document.body.setAttribute('data-out',document.getElementById('c').toDataURL('image/png'));"
        "}catch(e){document.body.setAttribute('data-err',String(e));}};"
        f"img.src='data:image/{ext};base64,{data}';</script>"), budget=8000)
    out = re.search(r'data-out="(data:image/png;base64,[^"]+)"', dom)
    if not out:
        raise RuntimeError("style render gave no image")
    style = {k: v for k, v in merged.items() if k not in ("title",)}
    return save_styled(item_id, out.group(1), style, copy=copy)


def render_video_plate(item: dict, opts: dict | None, zoom: float) -> dict:
    merged = _merged_style(item, opts)
    meta = {"pixelRatio": item.get("pixel_ratio") or 1}
    dom = _headless_dom("plate", (
        f"<script>{_studio_source()}</script><script>try{{"
        f"const p=HagakiStudio.videoPlate({int(item['width'])},{int(item['height'])},{_script_json(merged)},"
        f"{_script_json(meta)},{{zoom:{float(zoom)}}});"
        "document.body.setAttribute('data-base',p.base.toDataURL('image/png'));"
        "document.body.setAttribute('data-mask',p.mask.toDataURL('image/png'));"
        "document.body.setAttribute('data-out',encodeURIComponent(JSON.stringify(p.layout)));"
        "}catch(e){document.body.setAttribute('data-err',String(e));}</script>"), timeout=180)
    m = re.search(r'data-out="([^"]+)"', dom)
    if not m:
        raise RuntimeError("the style plate render gave no layout")
    out = {"layout": json.loads(urllib.parse.unquote(m.group(1))), "style": {k: v for k, v in merged.items() if k != "title"}}
    images = {key: re.search(rf'data-{key}="data:image/png;base64,([^"]+)"', dom) for key in ("base", "mask")}
    if not all(images.values()):
        raise RuntimeError("the style plate render gave no image")
    tag = uuid.uuid4().hex[:8]
    for key, found in images.items():
        out[key] = os.path.join(WORK_DIR, f"plate-{item['id']}-{tag}-{key}.png")
        _atomic_write(out[key], base64.b64decode(found.group(1)))
    return out


def studio_catalog() -> dict:
    studio = os.path.join(APP_DIR, "studio.js")
    cache = os.path.join(CACHE_DIR, "studio_catalog.json")
    stamp = os.path.getmtime(studio)
    try:
        with open(cache, encoding="utf-8") as fh:
            cached = json.load(fh)
        if cached.get("mtime") == stamp:
            return cached["catalog"]
    except (OSError, ValueError, KeyError):
        pass
    dom = _headless_dom("catalog", (
        f"<script>{_studio_source()}</script><script>"
        "const S=window.HagakiStudio;document.body.setAttribute('data-out',encodeURIComponent(JSON.stringify("
        "{backgrounds:S.backgrounds,frames:S.frames,aspects:S.aspects,defaults:S.defaults})));</script>"), timeout=60)
    m = re.search(r'data-out="([^"]+)"', dom)
    if not m:
        raise RuntimeError("could not read the style catalog")
    catalog = json.loads(urllib.parse.unquote(m.group(1)))
    _atomic_write(cache, json.dumps({"mtime": stamp, "catalog": catalog}).encode())
    return catalog


def screen_locked() -> bool:
    import Quartz

    return bool((Quartz.CGSessionCopyCurrentDictionary() or {}).get("CGSSessionScreenIsLocked"))


def browser_for(url: str) -> str:
    from AppKit import NSWorkspace
    from Foundation import NSURL

    app = NSWorkspace.sharedWorkspace().URLForApplicationToOpenURL_(NSURL.URLWithString_(url))
    return os.path.splitext(os.path.basename(str(app.path())))[0] if app else ""


def web_url(url: str) -> str:
    url = (url or "").strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError("give an http or https address, for example https://example.com")
    return url


class _Cdp:
    def __init__(self, args: list[str]):
        import fcntl

        browser = _browser()
        to_r, self._w = os.pipe()
        self._r, from_w = os.pipe()
        a, b = fcntl.fcntl(to_r, fcntl.F_DUPFD, 10), fcntl.fcntl(from_w, fcntl.F_DUPFD, 10)
        os.close(to_r)
        os.close(from_w)
        try:
            self.proc = subprocess.Popen(
                ["/bin/bash", "-c", f'exec "$0" "$@" 3<&{a} 4>&{b}', browser, *args],
                pass_fds=(a, b), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except BaseException:
            os.close(self._w)
            os.close(self._r)
            raise
        finally:
            os.close(a)
            os.close(b)
        self._buf, self._n, self._events = bytearray(), 0, set()

    def _next(self, deadline: float) -> dict:
        import select

        end, start = self._buf.find(b"\0"), 0
        while end < 0:
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError
            if select.select([self._r], [], [], left)[0]:
                chunk = os.read(self._r, 1 << 20)
                if not chunk:
                    raise RuntimeError("the hidden browser stopped")
                start = len(self._buf)
                self._buf += chunk
                end = self._buf.find(b"\0", start)
        msg = json.loads(self._buf[:end])
        del self._buf[:end + 1]
        if "method" in msg:
            self._events.add(msg["method"])
        return msg

    def call(self, method: str, session: str | None = None, timeout: float = 30, **params) -> dict:
        self._n += 1
        self._events.clear()
        msg = {"id": self._n, "method": method, "params": params}
        if session:
            msg["sessionId"] = session
        os.write(self._w, json.dumps(msg).encode() + b"\0")
        deadline = time.time() + timeout
        while True:
            try:
                m = self._next(deadline)
            except TimeoutError:
                raise TimeoutError(f"the hidden browser did not answer {method} in {timeout:g} s") from None
            if m.get("id") == self._n:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error'].get('message')}")
                return m.get("result") or {}

    def wait_for(self, method: str, timeout: float) -> bool:
        deadline = time.time() + timeout
        while method not in self._events:
            try:
                self._next(deadline)
            except TimeoutError:
                return False
        return True

    def close(self) -> None:
        for fd in (self._w, self._r):
            try:
                os.close(fd)
            except OSError:
                pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def capture_web(url: str, width: int = 1440, height: int = 900, full_page: bool = False, wait: float = 2,
                dark: bool = False, scale: float = 1, copy: bool = False, name: str = "", ocr: bool = True) -> dict:
    import shutil
    import tempfile

    url = web_url(url)
    width, height = max(320, min(3840, int(width))), max(240, min(2400, int(height)))
    scale = max(1.0, min(3.0, float(scale)))
    when = time.time()
    ensure_dirs()
    for old in os.scandir(WORK_DIR):
        if old.name.startswith("web-") and old.is_dir() and when - old.stat().st_mtime > 3600:
            shutil.rmtree(old.path, ignore_errors=True)
    with tempfile.TemporaryDirectory(prefix="web-", dir=WORK_DIR, ignore_cleanup_errors=True) as profile:
        shot, page = _shoot_page(profile, url, width, height, full_page, wait, dark, scale)
    tmp = os.path.join(WORK_DIR, f"web-{int(when * 1000)}-{uuid.uuid4().hex[:4]}.png")
    with open(tmp, "wb") as fh:
        fh.write(base64.b64decode(shot["data"]))
    final = page.get("url") or url
    host = urllib.parse.urlsplit(final).hostname or ""
    info = {"app": host.removeprefix("www."), "title": page.get("title") or ""}
    return store_image(tmp, info, "web", when, name=name, copy=copy, ocr=ocr, pixel_ratio=scale, note=final)


def _shoot_page(profile: str, url: str, width: int, height: int, full_page: bool, wait: float, dark: bool,
                scale: float) -> tuple[dict, dict]:
    cdp = _Cdp(["--headless=new", "--remote-debugging-pipe", f"--user-data-dir={profile}", "--hide-scrollbars",
                "--no-first-run", "--no-default-browser-check", "--mute-audio", "--use-mock-keychain", "about:blank"])
    try:
        target = cdp.call("Target.createTarget", url="about:blank")["targetId"]
        sid = cdp.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
        cdp.call("Page.enable", sid)
        cdp.call("Emulation.setDeviceMetricsOverride", sid, width=width, height=height, deviceScaleFactor=scale,
                 mobile=False)
        cdp.call("Emulation.setEmulatedMedia", sid,
                 features=[{"name": "prefers-color-scheme", "value": "dark" if dark else "light"}])
        try:
            nav = cdp.call("Page.navigate", sid, timeout=20, url=url)
        except TimeoutError:
            raise RuntimeError(f"{urllib.parse.urlsplit(url).hostname} did not answer in 20 s") from None
        if nav.get("errorText"):
            raise RuntimeError(f"the page did not open: {nav['errorText']}")
        cdp.wait_for("Page.loadEventFired", 20)
        time.sleep(max(0.0, min(30.0, float(wait))))
        page = cdp.call("Runtime.evaluate", sid, expression="({title: document.title, url: location.href})",
                        returnByValue=True).get("result", {}).get("value") or {}
        clip = {"x": 0, "y": 0, "width": width, "height": height, "scale": 1}
        if full_page:
            size = cdp.call("Page.getLayoutMetrics", sid).get("cssContentSize") or {}
            clip["height"] = max(height, min(int(16000 / scale), int(size.get("height") or height)))
        shot = cdp.call("Page.captureScreenshot", sid, timeout=60, format="png", captureBeyondViewport=full_page,
                        clip=clip)
    finally:
        cdp.close()
    return shot, page
