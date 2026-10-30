from __future__ import annotations

import math
import os
import struct

COLORS = {"fewer": (64, False), "normal": (255, False), "smooth": (255, True)}
PALETTE_FRAMES = 16
NOISE = 8


def _core():
    import core

    return core


def options(overrides: dict | None = None) -> dict:
    o = dict(_core().load_settings().get("gif") or {})
    o.update({k: v for k, v in (overrides or {}).items() if v is not None and v != ""})
    try:
        fps = int(o.get("fps") or 15)
    except (TypeError, ValueError):
        fps = 15
    try:
        size = int(o.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    colors = o.get("colors") if o.get("colors") in COLORS else "normal"
    return {"fps": max(1, min(30, fps)), "size": max(0, size), "colors": colors, "loop": bool(o.get("loop", True))}


def target_size(width: int, height: int, size: int, pixel_ratio: float = 1.0) -> tuple[int, int]:
    long = max(width, height, 1)
    cap = long / max(1.0, float(pixel_ratio or 1)) if size <= 0 else min(size, long)
    k = min(1.0, cap / long)
    return max(1, round(width * k)), max(1, round(height * k))


def _rgb(image, w: int, h: int):
    import numpy as np
    import Quartz

    space = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)
    ctx = Quartz.CGBitmapContextCreate(None, w, h, 8, w * 4, space, Quartz.kCGImageAlphaNoneSkipLast)
    Quartz.CGContextSetInterpolationQuality(ctx, Quartz.kCGInterpolationHigh)
    Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, w, h), image)
    out = Quartz.CGBitmapContextCreateImage(ctx)
    row = Quartz.CGImageGetBytesPerRow(out)
    raw = bytes(Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(out)))
    return np.frombuffer(raw, dtype=np.uint8).reshape(h, row)[:, :w * 4].reshape(h, w, 4)[..., :3].copy()


def _palette(samples: list, colors: int):
    import numpy as np
    from PIL import Image

    montage = Image.fromarray(np.concatenate(samples, axis=0))
    picked = montage.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
    rgb = bytes(picked.getpalette()[:colors * 3])
    used = len(rgb) // 3
    pal = Image.new("P", (1, 1))
    pal.putpalette(rgb)
    return pal, rgb, used


def _header(w: int, h: int, rgb: bytes, used: int, loop: bool) -> bytes:
    bits = max(1, math.ceil(math.log2(used + 1)))
    table = rgb + b"\0" * (3 * (2 ** bits) - len(rgb))
    packed = 0x80 | ((bits - 1) << 4) | (bits - 1)
    out = b"GIF89a" + struct.pack("<HHBBB", w, h, packed, 0, 0) + table
    if loop:
        out += b"!\xff\x0bNETSCAPE2.0\x03\x01" + struct.pack("<H", 0) + b"\0"
    return out


def encode(src: str, out: str, opts: dict | None = None, pixel_ratio: float = 1.0, progress=None) -> dict:
    import numpy as np
    from PIL import GifImagePlugin, Image

    o = options(opts)
    core = _core()
    info = core.video_info(src)
    if not info["width"] or not info["duration"]:
        raise RuntimeError("macOS could not read the video")
    w, h = target_size(info["width"], info["height"], o["size"], pixel_ratio)
    fps = o["fps"]
    colors, dither = COLORS[o["colors"]]
    grab, seconds = core.frame_generator(src, max(w, h), 0.5 / fps)
    seconds = seconds or info["duration"]
    count = max(1, int(math.ceil(seconds * fps - 1e-6)))
    picks = sorted({round(i * (count - 1) / max(1, PALETTE_FRAMES - 1)) for i in range(min(count, PALETTE_FRAMES))})
    samples = [_rgb(img, w, h) for img in (grab(i / fps) for i in picks) if img is not None]
    if not samples:
        raise RuntimeError("macOS could not read the video frames")
    pal, rgb, used = _palette(samples, colors)
    clear = used
    mode = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
    tmp = out + ".part"
    frames = 0
    shown_rgb = shown_idx = None
    pending = None

    def emit(fh, frame, until):
        image, offset, trans, start = frame
        fh.write(b"".join(GifImagePlugin.getdata(image, offset, duration=max(2, until - start) * 10, disposal=1,
                                                 transparency=trans)))

    try:
        with open(tmp, "wb") as fh:
            fh.write(_header(w, h, rgb, used, o["loop"]))
            for i in range(count):
                if progress:
                    progress(i / count)
                image = grab(i / fps)
                if image is None:
                    continue
                cur = _rgb(image, w, h)
                idx = np.asarray(Image.fromarray(cur).quantize(palette=pal, dither=mode), dtype=np.uint8)
                cs = round(i * 100 / fps)
                if shown_idx is None:
                    shown_rgb, shown_idx = cur, idx.copy()
                    frame = (Image.fromarray(idx, "L"), (0, 0), None, cs)
                else:
                    moved = np.abs(cur.astype(np.int16) - shown_rgb).max(axis=2) > NOISE
                    if not moved.any():
                        continue
                    ys = np.flatnonzero(moved.any(axis=1))
                    xs = np.flatnonzero(moved.any(axis=0))
                    y0, y1, x0, x1 = int(ys[0]), int(ys[-1]) + 1, int(xs[0]), int(xs[-1]) + 1
                    crop = np.where(moved[y0:y1, x0:x1], idx[y0:y1, x0:x1], clear).astype(np.uint8)
                    shown_rgb[moved] = cur[moved]
                    shown_idx[moved] = idx[moved]
                    frame = (Image.fromarray(crop, "L"), (x0, y0), clear, cs)
                if pending is not None:
                    emit(fh, pending, cs)
                    frames += 1
                pending = frame
            if pending is None:
                raise RuntimeError("macOS could not read the video frames")
            emit(fh, pending, round(seconds * 100))
            frames += 1
            fh.write(b";")
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    if progress:
        progress(1.0)
    return {"width": w, "height": h, "fps": fps, "frames": frames, "seconds": round(seconds, 2),
            "bytes": os.path.getsize(out), **{k: o[k] for k in ("colors", "loop", "size")}}


def _keep(out: str, result: dict, src_width: int, pixel_ratio: float, **fields) -> dict:
    core = _core()
    ratio = float(pixel_ratio or 1) * result["width"] / max(1, src_width)
    item = core.ingest(out, kind="image", source="gif", pixel_ratio=ratio, **fields)
    core.log("info", "record", f"GIF made: {result['frames']} frames at {result['fps']} fps, {result['width']}×{result['height']} px",
             item["id"], result)
    if (core.load_settings().get("after_capture") or {}).get("ocr", True):
        core.run_ocr(item["id"])
    else:
        core.update_item(item["id"], ocr_state="none")
    return core.get_item(item["id"])


def store(src: str, info: dict, when: float, pixel_ratio: float = 1.0, progress=None, **fields) -> dict:
    core = _core()
    settings = core.load_settings()
    video = core.video_info(src)
    w, h = target_size(video["width"], video["height"], options()["size"], pixel_ratio)
    meta = {"app": info.get("app"), "title": info.get("title"), "width": w, "height": h, "kind": "gif", "source": "gif"}
    out = core.unique_path(core.target_folder(settings, when), core.format_name(settings.get("name_pattern"), meta, when), ".gif")
    result = encode(src, out, None, pixel_ratio, progress)
    return _keep(out, result, video["width"], pixel_ratio, app=info.get("app") or "", title=info.get("title") or "",
                 bundle=info.get("bundle") or "", created=when, **fields)


def _recording(item_id: str) -> dict:
    item = _core().get_item(item_id)
    if not item or item["kind"] != "video":
        raise ValueError("This capture is not a recording.")
    if not os.path.exists(item["path"]):
        raise FileNotFoundError(item["path"])
    return item


def make(item_id: str, opts: dict | None = None, progress=None) -> dict:
    core = _core()
    item = _recording(item_id)
    folder = os.path.dirname(item["path"])
    if not os.path.isdir(folder):
        folder = core.target_folder(core.load_settings())
    out = core.unique_path(folder, item["name"], ".gif")
    result = encode(item["path"], out, opts, item.get("pixel_ratio") or 1, progress)
    return _keep(out, result, int(item.get("width") or result["width"]), item.get("pixel_ratio") or 1,
                 app=item.get("app") or "", title=item.get("title") or "", bundle=item.get("bundle") or "", parent=item_id)


def start(item_id: str) -> dict:
    import reel

    _recording(item_id)
    return reel._start("gif", item_id, "make_gif", "Hagaki is still making this GIF.")


def run_job(item_id: str) -> None:
    import reel

    reel._run_job("gif", item_id, lambda report: {"item": make(item_id, None, report)["id"]}, "GIF failed", "record")
