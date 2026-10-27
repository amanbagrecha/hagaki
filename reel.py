from __future__ import annotations

import bisect
import json
import math
import os
import tempfile
import threading
import time

TRACK_VERSION = 1
ZOOM_IN = 0.6
ZOOM_OUT = 0.8
HOLD = 1.6
PLAN_RATE = 30
SPRING = 7.0
SAFE = 0.7
ZOOM_DEFAULT = 1.8
ZOOM_RANGE = (1.2, 3.0)
RING = {"seconds": 0.5, "points": 22}
DETECT_FPS = 8
DETECT_WIDTH = 960
CELL = 8
CURSOR_CELLS = 16
VISION_MODEL = "vikhyatk/moondream2"
VISION_PROMPTS = ("mouse pointer", "pointing hand cursor")


def _core():
    import core

    return core


def _ci():
    import Quartz

    return Quartz


def _ease(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 4 * p * p * p if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def _interp(rows: list, times: list, t: float, cols: tuple) -> tuple:
    i = bisect.bisect_right(times, t)
    if i <= 0:
        return tuple(rows[0][c] for c in cols)
    if i >= len(rows):
        return tuple(rows[-1][c] for c in cols)
    a, b = rows[i - 1], rows[i]
    k = (t - a[0]) / ((b[0] - a[0]) or 1)
    return tuple(a[c] + (b[c] - a[c]) * k for c in cols)


def zoom_groups(clicks: list, duration: float) -> list[dict]:
    out: list[dict] = []
    for c in clicks:
        if out and c[0] - out[-1]["clicks"][-1][0] < HOLD + ZOOM_OUT + ZOOM_IN:
            out[-1]["clicks"].append(c)
        else:
            out.append({"clicks": [c]})
    for g in out:
        g["start"] = max(0.0, g["clicks"][0][0] - ZOOM_IN)
        g["end"] = min(duration, g["clicks"][-1][0] + HOLD + ZOOM_OUT)
    return [g for g in out if g["end"] - g["clicks"][0][0] >= ZOOM_OUT + 0.3]


def _level(g: dict, t: float) -> float:
    first = g["clicks"][0][0]
    if t < g["start"] or t > g["end"]:
        return 0.0
    if t < first:
        return _ease((t - g["start"]) / (first - g["start"])) if first > g["start"] else 1.0
    fall = g["end"] - ZOOM_OUT
    return 1.0 if t <= fall else 1.0 - _ease((t - fall) / ZOOM_OUT)


def plan(track: dict | None, duration: float, amount: float, follow: bool, skip) -> dict:
    track = track or {}
    off = set(skip)
    clicks = sorted([float(c[0]), float(c[1]), float(c[2])] for i, c in enumerate(track.get("clicks") or [])
                    if i not in off and 0 <= float(c[0]) <= duration)
    moves = sorted(track.get("moves") or [])
    times = [m[0] for m in moves]
    groups = zoom_groups(clicks, duration)
    rows = []
    fx = fy = 0.5
    vx = vy = 0.0
    anchor = None
    gi = ci = 0
    steps = 4
    dt = 1.0 / PLAN_RATE / steps
    for i in range(int(math.ceil(duration * PLAN_RATE)) + 1):
        t = min(duration, i / PLAN_RATE)
        while gi < len(groups) and groups[gi]["end"] < t:
            gi += 1
        g = groups[gi] if gi < len(groups) else None
        s = 1 + (amount - 1) * (_level(g, t) if g else 0.0)
        while ci < len(clicks) and clicks[ci][0] <= t:
            anchor = (clicks[ci][1], clicks[ci][2])
            ci += 1
        if g is not None and t < g["clicks"][0][0]:
            anchor = (g["clicks"][0][1], g["clicks"][0][2])
        if follow and moves and s > 1.05 and anchor is not None:
            cur = _interp(moves, times, t, (1, 2))
            if abs(cur[0] - fx) > SAFE * 0.5 / s or abs(cur[1] - fy) > SAFE * 0.5 / s:
                anchor = cur
        tx, ty = anchor if anchor is not None else (fx, fy)
        tx, ty = min(1.0, max(0.0, tx)), min(1.0, max(0.0, ty))
        if s <= 1.01:
            fx, fy, vx, vy = tx, ty, 0.0, 0.0
        else:
            for _ in range(steps):
                vx += (SPRING * SPRING * (tx - fx) - 2 * SPRING * vx) * dt
                vy += (SPRING * SPRING * (ty - fy) - 2 * SPRING * vy) * dt
                fx += vx * dt
                fy += vy * dt
        rows.append([round(t, 4), round(s, 4), round(fx, 4), round(fy, 4)])
    return {
        "rows": rows,
        "groups": [{"start": round(g["start"], 3), "end": round(g["end"], 3)} for g in groups],
        "clicks": [[round(float(c[0]), 3), round(float(c[1]), 4), round(float(c[2]), 4)] for c in track.get("clicks") or []],
        "ring": RING,
    }


def zoom_options(track: dict | None, overrides: dict | None = None) -> dict:
    saved = (_core().load_settings().get("style") or {}).get("zoom") or {}
    out = {"auto": True, "amount": ZOOM_DEFAULT, "follow": True,
           "rings": bool(track and track.get("source") == "recorded"), "skip": []}
    out.update({k: v for k, v in saved.items() if k in ("auto", "amount", "follow")})
    out.update({k: v for k, v in (overrides or {}).items() if k in out and v is not None})
    out["amount"] = min(ZOOM_RANGE[1], max(ZOOM_RANGE[0], float(out["amount"] or ZOOM_DEFAULT)))
    out["skip"] = sorted({int(i) for i in out["skip"] or ()})
    for key in ("auto", "follow", "rings"):
        out[key] = bool(out[key])
    return out


def view_rect(W: float, H: float, shot: dict, s: float, fx: float, fy: float, top: float) -> tuple[float, float, float, float]:
    vw, vh = W / s, H / s
    cx = shot["x"] + fx * shot["w"]
    cy = shot["y"] + fy * shot["h"]
    p = 0.0 if top <= 1.0001 else min(1.0, max(0.0, (s - 1) / (top - 1)))
    pad = 0.015 * min(shot["w"], shot["h"])
    lo_x = (shot["x"] + pad) * p
    lo_y = (shot["y"] + pad) * p
    hi_x = W + (shot["x"] + shot["w"] - pad - W) * p
    hi_y = H + (shot["y"] + shot["h"] - pad - H) * p
    if hi_x - lo_x < vw:
        lo_x, hi_x = (lo_x + hi_x - vw) / 2, (lo_x + hi_x + vw) / 2
    if hi_y - lo_y < vh:
        lo_y, hi_y = (lo_y + hi_y - vh) / 2, (lo_y + hi_y + vh) / 2
    x = min(max(cx - vw / 2, lo_x), hi_x - vw)
    y = min(max(cy - vh / 2, lo_y), hi_y - vh)
    return x, y, vw, vh


def load_image(path: str, raw: bool = False):
    Quartz = _ci()
    from Foundation import NSNull, NSURL

    opts = {Quartz.kCIImageColorSpace: NSNull.null()} if raw else None
    img = Quartz.CIImage.imageWithContentsOfURL_options_(NSURL.fileURLWithPath_(path), opts)
    if img is None:
        raise OSError(f"could not read {path}")
    return img


def ring_image():
    Quartz = _ci()
    size = 256
    space = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)
    ctx = Quartz.CGBitmapContextCreate(None, size, size, 8, 0, space, Quartz.kCGImageAlphaPremultipliedLast)
    circle = Quartz.CGRectMake(size * 0.08, size * 0.08, size * 0.84, size * 0.84)
    Quartz.CGContextSetRGBFillColor(ctx, 1, 1, 1, 0.28)
    Quartz.CGContextFillEllipseInRect(ctx, circle)
    Quartz.CGContextSetRGBStrokeColor(ctx, 0, 0, 0, 0.22)
    Quartz.CGContextSetLineWidth(ctx, size * 0.07)
    Quartz.CGContextStrokeEllipseInRect(ctx, circle)
    Quartz.CGContextSetRGBStrokeColor(ctx, 1, 1, 1, 0.95)
    Quartz.CGContextSetLineWidth(ctx, size * 0.04)
    Quartz.CGContextStrokeEllipseInRect(ctx, circle)
    return Quartz.CIImage.imageWithCGImage_(Quartz.CGBitmapContextCreateImage(ctx))


def _affine(a, d, tx, ty):
    return _ci().CGAffineTransformMake(a, 0, 0, d, tx, ty)


def export(src: str, out: str, base_png: str, mask_png: str, layout: dict, rows: list, clicks: list, ring_px: float,
           progress=None) -> dict:
    Quartz = _ci()
    import AVFoundation
    import CoreMedia
    from Foundation import NSURL

    W, H = int(layout["W"]), int(layout["H"])
    shot = layout["shot"]
    times = [r[0] for r in rows]
    top = max([r[1] for r in rows] or [1.0])
    asset = AVFoundation.AVURLAsset.URLAssetWithURL_options_(NSURL.fileURLWithPath_(src), None)
    tracks = asset.tracksWithMediaType_(AVFoundation.AVMediaTypeVideo)
    if not tracks or not len(tracks):
        raise ValueError("the file has no video track")
    size = tracks[0].naturalSize()
    vw, vh = float(size.width), float(size.height)
    fastest = float(CoreMedia.CMTimeGetSeconds(tracks[0].minFrameDuration()) or 0)
    fps = 60 if (tracks[0].nominalFrameRate() or 0) >= 45 or 0 < fastest < 1 / 45 else 30
    base = load_image(base_png)
    mask = load_image(mask_png, raw=True)
    kx, ky = base.extent().size.width / W, base.extent().size.height / H
    black = Quartz.CIImage.imageWithColor_(Quartz.CIColor.colorWithRed_green_blue_(0, 0, 0))
    ring = ring_image() if clicks and ring_px > 0 else None
    taps = sorted((float(c[0]), float(c[1]), float(c[2])) for c in clicks)
    tap_times = [c[0] for c in taps]
    ctx = Quartz.CIContext.contextWithOptions_(
        {Quartz.kCIContextWorkingColorSpace: Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)})
    ax, ay = shot["w"] / vw, shot["h"] / vh
    crop = Quartz.CGRectMake(0, 0, W, H)
    errors = []

    def frame(request):
        try:
            t = float(CoreMedia.CMTimeGetSeconds(request.compositionTime()))
            s, fx, fy = _interp(rows, times, t, (1, 2, 3)) if rows else (1.0, 0.5, 0.5)
            x0, y0, _, view_h = view_rect(W, H, shot, s, fx, fy, top)
            yb = H - (y0 + view_h)
            video = request.sourceImage()
            if ring is not None:
                i = bisect.bisect_left(tap_times, t - RING["seconds"])
                while i < len(taps) and taps[i][0] <= t:
                    age = (t - taps[i][0]) / RING["seconds"]
                    r = ring_px * (0.45 + 0.75 * (1 - (1 - age) ** 3))
                    sc = 2 * r / 256
                    cx, cy = taps[i][1] * vw, (1 - taps[i][2]) * vh
                    dot = ring.imageByApplyingTransform_(_affine(sc, sc, cx - r, cy - r))
                    dot = dot.imageByApplyingFilter_withInputParameters_(
                        "CIColorMatrix", {"inputAVector": Quartz.CIVector.vectorWithX_Y_Z_W_(0, 0, 0, max(0.0, 1 - age))})
                    video = dot.imageByCompositingOverImage_(video)
                    i += 1
            video = video.imageByApplyingTransform_(
                _affine(ax * s, ay * s, (shot["x"] - x0) * s, (H - shot["y"] - shot["h"] - yb) * s))
            plate = _affine(s / kx, s / ky, -x0 * s, -yb * s)
            cut = video.imageByApplyingFilter_withInputParameters_(
                "CIBlendWithMask", {"inputBackgroundImage": black, "inputMaskImage": mask.imageByApplyingTransform_(plate)})
            img = cut.imageByApplyingFilter_withInputParameters_(
                "CIAdditionCompositing", {"inputBackgroundImage": base.imageByApplyingTransform_(plate)})
            request.finishWithImage_context_(img.imageByCroppingToRect_(crop), ctx)
        except Exception as exc:
            errors.append(str(exc))
            request.finishWithError_(None)

    comp = AVFoundation.AVMutableVideoComposition.videoCompositionWithAsset_applyingCIFiltersWithHandler_(asset, frame)
    comp.setRenderSize_((W, H))
    comp.setFrameDuration_(CoreMedia.CMTimeMake(1, fps))
    started = time.time()
    try:
        _core().run_export(asset, comp, out, AVFoundation.AVFileTypeMPEG4, 3600, progress)
    except RuntimeError as exc:
        raise RuntimeError(f"The video export failed: {exc}" + (f" ({errors[0]})" if errors else "")) from None
    return {"width": W, "height": H, "fps": fps, "seconds": round(time.time() - started, 2)}


class CursorTracker:
    BUTTONS = ((1, 0), (3, 1))
    RATE = 60

    def __init__(self, rect: tuple[float, float, float, float]):
        self.rect = tuple(float(n) for n in rect)
        self.created = time.time()
        self.moves: list[list[float]] = []
        self.clicks: list[list[float]] = []
        self.t0: float | None = None
        self.quiet = False
        self._halt = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hagaki-cursor", daemon=True)

    @classmethod
    def on(cls, shot: dict, rect) -> "CursorTracker":
        tracker = cls((shot["cg_x"] + rect[0], shot["cg_y"] + rect[1], rect[2], rect[3]))
        tracker._thread.start()
        return tracker

    def mark(self, wall: float | None = None) -> None:
        if self.t0 is None:
            self.t0 = time.monotonic() - (max(0.0, time.time() - wall) if wall else 0.0)

    def _run(self) -> None:
        Quartz = _ci()
        state = Quartz.kCGEventSourceStateCombinedSessionState
        seen = [Quartz.CGEventSourceCounterForEventType(state, kind) for kind, _ in self.BUTTONS]
        last = None
        while not self._halt.wait(1.0 / self.RATE):
            now = time.monotonic()
            event = Quartz.CGEventCreate(None)
            if event is None:
                continue
            loc = Quartz.CGEventGetLocation(event)
            point = (round(loc.x, 1), round(loc.y, 1))
            if point != last:
                self.moves.append([now, *point])
                last = point
            for i, (kind, button) in enumerate(self.BUTTONS):
                count = Quartz.CGEventSourceCounterForEventType(state, kind)
                if count > seen[i] and not self.quiet:
                    self.clicks.append([now, *point, button])
                seen[i] = count

    def halt(self) -> None:
        self._halt.set()
        if self._thread.is_alive():
            self._thread.join(1)

    def track(self) -> dict:
        self.halt()
        t0 = self.t0 if self.t0 is not None else time.monotonic()
        x, y, w, h = self.rect

        def norm(px, py):
            return round((px - x) / w, 4), round((py - y) / h, 4)

        moves = []
        for t, px, py in self.moves:
            row = [round(max(0.0, t - t0), 3), *norm(px, py)]
            if row[0] == 0 and moves:
                moves[-1] = row
            else:
                moves.append(row)
        clicks = []
        for t, px, py, button in self.clicks:
            nx, ny = norm(px, py)
            if t >= t0 and -0.01 <= nx <= 1.01 and -0.01 <= ny <= 1.01:
                clicks.append([round(t - t0, 3), nx, ny, button])
        return {"version": TRACK_VERSION, "source": "recorded", "rate": self.RATE, "rect": list(self.rect),
                "moves": moves, "clicks": clicks}


def track_path(item_id: str) -> str:
    return os.path.join(_core().DATA_DIR, "tracks", f"{item_id}.json")


def load_track(item_id: str) -> dict | None:
    try:
        with open(track_path(item_id), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def save_track(item_id: str, track: dict) -> None:
    os.makedirs(os.path.dirname(track_path(item_id)), exist_ok=True)
    _core()._atomic_write(track_path(item_id), json.dumps(track, separators=(",", ":")).encode())


def track_summary(track: dict | None) -> dict | None:
    if not track:
        return None
    return {"source": track.get("source"), "method": track.get("method") or "", "clicks": len(track.get("clicks") or []),
            "moves": len(track.get("moves") or [])}


def job_path(kind: str, item_id: str) -> str:
    return os.path.join(_core().CACHE_DIR, "jobs", f"{kind}-{item_id}.json")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def job_state(kind: str, item_id: str) -> dict:
    try:
        with open(job_path(kind, item_id), encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return {"state": "none"}
    if state.get("state") == "running":
        pid = state.get("pid")
        if (pid and not _alive(int(pid))) or (not pid and time.time() - float(state.get("ts") or 0) > 60):
            state.update(state="error", error="The job stopped without an answer.")
    return state


def set_job(kind: str, item_id: str, **state) -> None:
    os.makedirs(os.path.dirname(job_path(kind, item_id)), exist_ok=True)
    state["ts"] = time.time()
    _core()._atomic_write(job_path(kind, item_id), json.dumps(state).encode())


def _reporter(kind: str, item_id: str):
    last = [-1.0]

    def report(p: float) -> None:
        if p - last[0] >= 0.02:
            last[0] = p
            set_job(kind, item_id, state="running", progress=round(p, 3), pid=os.getpid())

    report(0.0)
    return report


def request_path(item_id: str) -> str:
    return os.path.join(_core().WORK_DIR, f"reel-{item_id}.json")


def _video_item(item_id: str) -> dict:
    item = _core().get_item(item_id)
    if not item or item["kind"] != "video":
        raise ValueError("This capture is not a recording.")
    if not os.path.exists(item["path"]):
        raise FileNotFoundError(item["path"])
    if not item.get("duration"):
        item["duration"] = _core().video_info(item["path"])["duration"] or 0
    return item


def _frame_rgba(image):
    Quartz = _ci()
    import numpy as np

    w, h = Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)
    row = Quartz.CGImageGetBytesPerRow(image)
    raw = bytes(Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image)))
    return np.frombuffer(raw, dtype=np.uint8).reshape(h, row)[:, :w * 4].reshape(h, w, 4)


def _gray_frames(path: str):
    import numpy as np

    grab, duration = _core().frame_generator(path, DETECT_WIDTH, 0.5 / DETECT_FPS)
    for i in range(int(duration * DETECT_FPS)):
        t = i / DETECT_FPS
        image = grab(t)
        if image is not None:
            rgba = _frame_rgba(image)
            yield t, (rgba[..., 0].astype(np.int16) * 3 + rgba[..., 1].astype(np.int16) * 6 + rgba[..., 2]) // 10


def _images_at(path: str, times: list[float]):
    grab, _ = _core().frame_generator(path, 1024, 0.0)
    for t in times:
        yield t, grab(t)


def _components(mask) -> list[tuple[int, float, float, float, float]]:
    gh, gw = mask.shape[0] // CELL, mask.shape[1] // CELL
    grid = mask[:gh * CELL, :gw * CELL].reshape(gh, CELL, gw, CELL).any(axis=(1, 3))
    cells = grid.tolist()
    seen = [[False] * gw for _ in range(gh)]
    out = []
    for y, x in zip(*grid.nonzero()):
        y, x = int(y), int(x)
        if seen[y][x]:
            continue
        seen[y][x] = True
        stack, part = [(y, x)], []
        while stack:
            cy, cx = stack.pop()
            part.append((cy, cx))
            for ny in (cy - 1, cy, cy + 1):
                if not 0 <= ny < gh:
                    continue
                for nx in (cx - 1, cx, cx + 1):
                    if 0 <= nx < gw and cells[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((ny, nx))
        ys = [c[0] for c in part]
        xs = [c[1] for c in part]
        out.append((len(part), min(xs) * CELL, min(ys) * CELL, (max(xs) + 1) * CELL, (max(ys) + 1) * CELL))
    return out


def _centre(c) -> tuple[float, float]:
    return (c[1] + c[3]) / 2, (c[2] + c[4]) / 2


def vision_ready() -> bool:
    try:
        _core()._shared_path()
        import fused_ai

        for cap in fused_ai.models.catalog().get("capabilities") or []:
            for model in cap.get("models") or []:
                if model.get("id") == VISION_MODEL:
                    return bool(cap.get("available")) and bool(model.get("downloaded"))
    except Exception:
        pass
    return False


def _vision_points(image_path: str) -> list[tuple[float, float]]:
    _core()._shared_path()
    import fused_ai

    points = []
    for prompt in VISION_PROMPTS:
        for _ in range(90):
            try:
                res = fused_ai.vision(image_path, "point", prompt, model=VISION_MODEL)
                break
            except fused_ai.AiError as exc:
                if getattr(exc, "type", "") != "model_loading":
                    raise
                time.sleep(2)
        else:
            raise RuntimeError("the vision model did not load")
        points += [(float(p["x"]), float(p["y"])) for p in res.get("points") or []]
        if points:
            break
    return points


def estimate_track(path: str, progress=None) -> dict:
    import numpy as np

    core = _core()
    duration = float(core.video_info(path)["duration"] or 0)
    total = max(1, int(duration * DETECT_FPS))
    vision = vision_ready()
    with tempfile.TemporaryDirectory(prefix="detect-", dir=core.WORK_DIR) as work:

        def look(t: float, image) -> list[tuple[float, float]]:
            if image is None:
                return []
            frame = os.path.join(work, f"f{int(t * 1000)}.jpg")
            core.write_cgimage(image, frame, "public.jpeg", 0.9)
            return _vision_points(frame)

        seed = [p for t, image in _images_at(path, [min(0.5, duration / 2)] if vision else []) for p in look(t, image)]
        prev = None
        cursor = None
        moves: list[list[float]] = [[0.0, round(seed[0][0], 4), round(seed[0][1], 4)]] if seed else []
        changes: list[tuple[float, tuple, int]] = []
        W = H = 1
        for i, (t, gray) in enumerate(_gray_frames(path)):
            H, W = gray.shape
            if cursor is None and seed:
                cursor = (seed[0][0] * W, seed[0][1] * H)
            if prev is not None and prev.shape == gray.shape:
                diff = np.abs(gray - prev) > 24
                if diff.any():
                    comps = _components(diff)
                    small = [c for c in comps if c[0] <= CURSOR_CELLS]
                    busy = sum(c[0] for c in comps if c[0] > CURSOR_CELLS) > 3 * CURSOR_CELLS
                    blob = None
                    if small and len(small) <= 3 and not busy:
                        if cursor is None or len(small) == 1:
                            blob = small[0]
                        else:
                            near = sorted(small, key=lambda c: math.dist(_centre(c), cursor))
                            blob = near[1] if math.dist(_centre(near[0]), cursor) < 3 * CELL else near[0]
                        cursor = _centre(blob)
                        moves.append([round(t, 3), round(float(cursor[0]) / W, 4), round(float(cursor[1]) / H, 4)])
                    rest = [c for c in comps if c is not blob and (c[0] > CURSOR_CELLS or blob is None
                                                                     or math.dist(_centre(c), cursor) > 6 * CELL)]
                    if rest:
                        box = (min(c[1] for c in rest), min(c[2] for c in rest), max(c[3] for c in rest),
                               max(c[4] for c in rest))
                        changes.append((t, box, sum(c[0] for c in rest)))
            prev = gray
            if progress and i % 8 == 0:
                progress(0.75 * i / total)
        times = [m[0] for m in moves]
        reach = 0.11 * W
        picks: list[list[float]] = []
        for t, box, cells in changes:
            if cells < 3 or t < 0.3 or not moves or times[0] > t:
                continue
            here = _interp(moves, times, t, (1, 2))
            cx, cy = here[0] * W, here[1] * H
            recent = moves[bisect.bisect_left(times, t - 0.4):bisect.bisect_right(times, t)]
            if any(math.dist((m[1] * W, m[2] * H), (cx, cy)) > 1.5 * CELL for m in recent):
                continue
            dx = max(box[0] - cx, 0, cx - box[2])
            dy = max(box[1] - cy, 0, cy - box[3])
            if math.hypot(dx, dy) > reach:
                continue
            at = max(0.0, t - 1.0 / DETECT_FPS)
            if picks and at - picks[-1][0] < 1.2 and math.dist((picks[-1][1] * W, picks[-1][2] * H), (cx, cy)) < reach / 2:
                continue
            picks.append([round(at, 3), round(float(here[0]), 4), round(float(here[1]), 4), 0])
        refined = 0
        for n, (t, image) in enumerate(_images_at(path, [p[0] + 1.0 / DETECT_FPS for p in picks] if vision else [])):
            pick = picks[n]
            found = look(t, image)
            if found:
                best = min(found, key=lambda p: math.dist(p, (pick[1], pick[2])))
                if math.dist(best, (pick[1], pick[2])) < 0.06:
                    pick[1], pick[2] = round(best[0], 4), round(best[1], 4)
                    refined += 1
            if progress:
                progress(0.75 + 0.25 * (n + 1) / len(picks))
    return {"version": TRACK_VERSION, "source": "estimate", "method": "motion + " + VISION_MODEL if vision else "motion",
            "rate": DETECT_FPS, "moves": moves, "clicks": picks, "refined": refined}


def _find_clicks(item: dict, progress=None) -> dict:
    track = estimate_track(item["path"], progress)
    save_track(item["id"], track)
    _core().log("info", "style", f"Found {len(track['clicks'])} likely clicks ({track['method']})", item["id"])
    return track


def video_details(item_id: str) -> dict:
    item = _video_item(item_id)
    track = load_track(item_id)
    return {"item": item, "track": track_summary(track), "zoom": zoom_options(track), "range": ZOOM_RANGE,
            "jobs": {k: job_state(k, item_id) for k in ("style", "detect")}, "vision": vision_ready(),
            "model": VISION_MODEL}


def video_plan(item_id: str, zoom: dict | None = None) -> dict:
    item = _video_item(item_id)
    track = load_track(item_id)
    z = zoom_options(track, zoom)
    return plan(track, float(item["duration"]), z["amount"], z["follow"], z["skip"])


def _start(kind: str, item_id: str, op: str, busy: str, request: dict | None = None) -> dict:
    if job_state(kind, item_id).get("state") == "running":
        raise RuntimeError(busy)
    if request is not None:
        _core()._atomic_write(request_path(item_id), json.dumps(request).encode())
    set_job(kind, item_id, state="running", progress=0.0)
    _core().spawn_job(op, item_id)
    return job_state(kind, item_id)


def start_style(item_id: str, request: dict) -> dict:
    item = _video_item(item_id)
    if item.get("source") == "style":
        raise ValueError("This video is already styled. Style the original recording.")
    return _start("style", item_id, "style_video", "Hagaki is still saving this video.",
                  {"opts": request.get("opts") or {}, "zoom": request.get("zoom") or {}, "copy": bool(request.get("copy"))})


def start_detect(item_id: str) -> dict:
    _video_item(item_id)
    return _start("detect", item_id, "detect_clicks", "Hagaki is still looking for clicks in this video.")


def render_video(item_id: str, opts: dict | None, zoom: dict | None, copy: bool = False, find_clicks: bool = True,
                 progress=None) -> dict:
    core = _core()
    item = _video_item(item_id)
    if item.get("source") == "style":
        raise ValueError("This video is already styled. Style the original recording.")
    track = load_track(item_id)
    if track is None and find_clicks and zoom_options(None, zoom)["auto"]:
        track = _find_clicks(item)
    z = zoom_options(track, zoom)
    plate = core.render_video_plate(item, opts, z["amount"] if z["auto"] else 1.0)
    try:
        rows = plan(track, float(item["duration"]), z["amount"], z["follow"], z["skip"])["rows"] if track and z["auto"] else []
        off = set(z["skip"])
        clicks = [c for i, c in enumerate(track.get("clicks") or []) if i not in off] if track and z["rings"] else []
        out = core.styled_path(item, ".mp4")
        result = export(item["path"], out, plate["base"], plate["mask"], plate["layout"], rows, clicks,
                        RING["points"] * float(item.get("pixel_ratio") or 1), progress)
    finally:
        for key in ("base", "mask"):
            if os.path.exists(plate[key]):
                os.remove(plate[key])
    style = dict(plate["style"], zoom={k: z[k] for k in ("auto", "amount", "rings", "follow")})
    new = core.ingest(out, kind="video", source="style", app=item.get("app") or "", title=item.get("title") or "",
                      bundle=item.get("bundle") or "", parent=item_id, pixel_ratio=item.get("pixel_ratio") or 1,
                      style=style)
    core.log("info", "style", f"Styled video saved: {os.path.basename(out)}", new["id"],
             {"seconds": result["seconds"], "zooms": bool(rows), "clicks": len(clicks), "fps": result["fps"]})
    if copy:
        core.update_item(new["id"], clipboard=1 if core.copy_file(out) else 0)
    return core.get_item(new["id"])


def _run_job(kind: str, item_id: str, work, failure: str) -> None:
    try:
        set_job(kind, item_id, state="done", progress=1.0, **work(_reporter(kind, item_id)))
    except Exception as exc:
        set_job(kind, item_id, state="error", error=str(exc))
        _core().log("error", "style", f"{failure}: {exc}", item_id)
        raise


def run_style_job(item_id: str) -> None:
    def work(report):
        with open(request_path(item_id), encoding="utf-8") as fh:
            request = json.load(fh)
        os.remove(request_path(item_id))
        new = render_video(item_id, request["opts"], request["zoom"], request["copy"], find_clicks=False, progress=report)
        return {"item": new["id"]}

    _run_job("style", item_id, work, "Video style failed")


def run_detect_job(item_id: str) -> None:
    def work(report):
        return {"clicks": len(_find_clicks(_video_item(item_id), report)["clicks"])}

    _run_job("detect", item_id, work, "Click search failed")
