from __future__ import annotations

import math
import time

import Quartz
from AppKit import (
    NSAttributedString, NSBezierPath, NSColor, NSFont, NSFontAttributeName, NSForegroundColorAttributeName,
    NSGraphicsContext, NSImage, NSStrokeColorAttributeName, NSStrokeWidthAttributeName,
)
from Foundation import NSMakePoint, NSMakeRect, NSMakeSize, NSZeroRect

COLORS = (
    ("Red", (234, 72, 50)),
    ("Yellow", (255, 197, 36)),
    ("Green", (36, 168, 110)),
    ("Blue", (40, 122, 236)),
    ("White", (255, 255, 255)),
    ("Ink", (31, 37, 56)),
)
SIZES = ("Thin", "Medium", "Thick")
STROKE = (2.5, 4.0, 6.5)
FONT = (16.0, 22.0, 32.0)
STEP = (24.0, 30.0, 40.0)
MARKER = (14.0, 20.0, 30.0)
PIXEL_CELL = 9.0
BLUR_RADIUS = 9.0
DIM = 0.55
INK = (31, 37, 56)
WHITE = (255, 255, 255)
BOXES = {"rect", "ellipse", "spotlight", "blur", "pixelate"}
FREEHAND = {"pen", "marker"}
EFFECTS = {"blur", "pixelate"}
FILLABLE = {"rect", "ellipse", "text"}


def color(rgb, a=1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, a)


def _clamp(i, seq):
    return min(max(int(i), 0), len(seq) - 1)


def _alpha(m):
    return min(1.0, m.get("alpha", 1.0))


def _two(m):
    return m["pts"] if len(m["pts"]) > 1 else m["pts"] * 2


def contrast(rgb):
    lum = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    return INK if lum > 165 else WHITE


def box(m):
    (x0, y0), (x1, y1) = m["pts"][0], m["pts"][-1]
    return min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)


def font(m):
    return NSFont.systemFontOfSize_weight_(FONT[m["size"]], 0.4)


def text_string(m, fill, stroke=None):
    attrs = {NSFontAttributeName: font(m), NSForegroundColorAttributeName: color(fill)}
    if stroke is not None:
        attrs[NSStrokeColorAttributeName] = color(stroke)
        attrs[NSStrokeWidthAttributeName] = 22.0
    return NSAttributedString.alloc().initWithString_attributes_(m.get("text") or " ", attrs)


def text_pad(m):
    return (FONT[m["size"]] * 0.45, FONT[m["size"]] * 0.18) if m.get("fill") else (0.0, 0.0)


def text_box(m):
    size = text_string(m, m["color"]).size()
    px, py = text_pad(m)
    x, y = m["pts"][0]
    return x, y, size.width + 2 * px, size.height + 2 * py


def caret(m):
    x, y, w, h = text_box(m)
    px, py = text_pad(m)
    lines = (m.get("text") or "").split("\n")
    line_h = (h - 2 * py) / max(1, len(lines))
    last = dict(m, text=lines[-1])
    lw = text_string(last, m["color"]).size().width if lines[-1] else 0.0
    cx = x + px + lw + 1.5
    cy = y + py + line_h * (len(lines) - 1)
    return cx, cy, line_h


def step_radius(m):
    return STEP[m["size"]] / 2.0


def bounds(m):
    kind = m["kind"]
    if kind == "text":
        x, y, w, h = text_box(m)
        return x, y, x + w, y + h
    if kind == "step":
        r = step_radius(m)
        cx, cy = m["pts"][0]
        xs, ys = [cx - r, cx + r], [cy - r, cy + r]
        if len(m["pts"]) > 1:
            xs.append(m["pts"][1][0])
            ys.append(m["pts"][1][1])
        return min(xs), min(ys), max(xs), max(ys)
    pad = width(m) / 2.0
    xs = [p[0] for p in m["pts"]]
    ys = [p[1] for p in m["pts"]]
    if kind == "arrow":
        pad = head(m, 1e9)[1]
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad


def width(m):
    if m["kind"] == "marker":
        return MARKER[m["size"]]
    if m["kind"] in EFFECTS or m["kind"] == "spotlight":
        return 0.0
    return STROKE[m["size"]]


def _seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    n = dx * dx + dy * dy
    t = 0.0 if n == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / n))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def hit(m, p, tol=6.0):
    kind = m["kind"]
    if kind in ("line", "arrow") or kind in FREEHAND:
        pts = _two(m)
        reach = max(tol, width(m) / 2.0 + 4)
        return any(_seg_dist(p, pts[i], pts[i + 1]) <= reach for i in range(len(pts) - 1))
    x0, y0, x1, y1 = bounds(m)
    return x0 - tol <= p[0] <= x1 + tol and y0 - tol <= p[1] <= y1 + tol


def translate(m, dx, dy):
    m["pts"] = [[x + dx, y + dy] for x, y in m["pts"]]


def constrain(a, b, kind, shift):
    if not shift:
        return [b[0], b[1]]
    dx, dy = b[0] - a[0], b[1] - a[1]
    if kind in BOXES:
        side = max(abs(dx), abs(dy))
        return [a[0] + math.copysign(side, dx or 1), a[1] + math.copysign(side, dy or 1)]
    ang = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
    dist = math.hypot(dx, dy)
    return [a[0] + dist * math.cos(ang), a[1] + dist * math.sin(ang)]


def head(m, length):
    w = STROKE[m["size"]]
    hl = min(max(w * 3.6, 13.0), length * 0.6)
    return hl, hl * 0.62


def _smooth(pts):
    path = NSBezierPath.bezierPath()
    path.moveToPoint_(NSMakePoint(*pts[0]))
    if len(pts) < 3:
        path.lineToPoint_(NSMakePoint(*pts[-1]))
        return path
    for i in range(1, len(pts) - 1):
        cx, cy = pts[i]
        mx, my = (pts[i][0] + pts[i + 1][0]) / 2.0, (pts[i][1] + pts[i + 1][1]) / 2.0
        px, py = path.currentPoint()
        path.curveToPoint_controlPoint1_controlPoint2_(
            NSMakePoint(mx, my),
            NSMakePoint(px + 2.0 / 3.0 * (cx - px), py + 2.0 / 3.0 * (cy - py)),
            NSMakePoint(mx + 2.0 / 3.0 * (cx - mx), my + 2.0 / 3.0 * (cy - my)))
    path.lineToPoint_(NSMakePoint(*pts[-1]))
    return path


def arrow_path(m):
    (x0, y0), (x1, y1) = m["pts"][0], m["pts"][-1]
    length = math.hypot(x1 - x0, y1 - y0)
    w = STROKE[m["size"]]
    hl, hw = head(m, length)
    ux, uy = ((x1 - x0) / length, (y1 - y0) / length) if length else (1.0, 0.0)
    nx, ny = -uy, ux

    def at(along, side):
        return NSMakePoint(x0 + ux * along + nx * side, y0 + uy * along + ny * side)

    neck = max(0.0, length - hl * 0.82)
    path = NSBezierPath.bezierPath()
    path.moveToPoint_(at(0, -w * 0.22))
    path.lineToPoint_(at(neck, -w * 0.62))
    path.lineToPoint_(at(length - hl, -hw))
    path.lineToPoint_(at(length, 0))
    path.lineToPoint_(at(length - hl, hw))
    path.lineToPoint_(at(neck, w * 0.62))
    path.lineToPoint_(at(0, w * 0.22))
    path.closePath()
    return path


def step_tail(m):
    (cx, cy), (tx, ty) = m["pts"][0], m["pts"][1]
    r = step_radius(m)
    dist = math.hypot(tx - cx, ty - cy) or 1.0
    ux, uy = (tx - cx) / dist, (ty - cy) / dist
    nx, ny = -uy, ux
    base = r * 0.62
    path = NSBezierPath.bezierPath()
    path.moveToPoint_(NSMakePoint(cx + nx * base, cy + ny * base))
    path.lineToPoint_(NSMakePoint(tx, ty))
    path.lineToPoint_(NSMakePoint(cx - nx * base, cy - ny * base))
    path.closePath()
    return path


_CI = []


def _ci_context():
    if not _CI:
        from Quartz import CIContext

        _CI.append(CIContext.contextWithOptions_(None))
    return _CI[0]


class Effects:
    def __init__(self, cgimage, size):
        self.cg = cgimage
        self.size = (float(size[0]), float(size[1]))
        self.scale = Quartz.CGImageGetWidth(cgimage) / max(1.0, self.size[0])
        self.cache = {}

    def image(self, kind):
        if kind not in self.cache:
            self.cache[kind] = self._make(kind)
        return self.cache[kind]

    def _make(self, kind):
        from Quartz import CIFilter, CIImage, CIVector

        src = CIImage.imageWithCGImage_(self.cg)
        extent = src.extent()
        if kind == "blur":
            f = CIFilter.filterWithName_("CIGaussianBlur")
            f.setValue_forKey_(BLUR_RADIUS * self.scale, "inputRadius")
        else:
            f = CIFilter.filterWithName_("CIPixellate")
            f.setValue_forKey_(PIXEL_CELL * self.scale, "inputScale")
            f.setValue_forKey_(CIVector.vectorWithX_Y_(0, 0), "inputCenter")
        f.setValue_forKey_(src.imageByClampingToExtent(), "inputImage")
        out = f.valueForKey_("outputImage").imageByCroppingToRect_(extent)
        cg = _ci_context().createCGImage_fromRect_(out, extent)
        return NSImage.alloc().initWithCGImage_size_(cg, NSMakeSize(*self.size))


def _layer(ctx, m, k, shadow=True):
    x0, y0, x1, y1 = bounds(m)
    margin = 8.0 + width(m)
    Quartz.CGContextSaveGState(ctx)
    Quartz.CGContextClipToRect(ctx, Quartz.CGRectMake(x0 - margin, y0 - margin, x1 - x0 + 2 * margin, y1 - y0 + 2 * margin))
    Quartz.CGContextSetAlpha(ctx, _alpha(m))
    if shadow:
        shade = Quartz.CGColorCreateSRGB(0, 0, 0, 0.32)
        Quartz.CGContextSetShadowWithColor(ctx, Quartz.CGSizeMake(0, -1.2 * k), 3.0 * k, shade)
    Quartz.CGContextBeginTransparencyLayer(ctx, None)


def _end(ctx):
    Quartz.CGContextEndTransparencyLayer(ctx)
    Quartz.CGContextRestoreGState(ctx)


def _effect(m, effects):
    img = effects.image(m["kind"]) if effects is not None else None
    if img is None:
        return
    x, y, w, h = box(m)
    NSGraphicsContext.saveGraphicsState()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(x, y, w, h), 3, 3).addClip()
    size = effects.size
    img.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
        NSMakeRect(0, 0, size[0], size[1]), NSZeroRect, 2, _alpha(m), True, None)
    NSGraphicsContext.restoreGraphicsState()


def _spotlight(ctx, spots, area):
    Quartz.CGContextSaveGState(ctx)
    Quartz.CGContextSetAlpha(ctx, max(_alpha(s) for s in spots))
    Quartz.CGContextBeginTransparencyLayer(ctx, None)
    color(INK, DIM).set()
    NSBezierPath.fillRect_(area)
    Quartz.CGContextSetBlendMode(ctx, Quartz.kCGBlendModeClear)
    for s in spots:
        x, y, w, h = box(s)
        r = min(10.0, w / 2, h / 2)
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(x, y, w, h), r, r).fill()
    Quartz.CGContextEndTransparencyLayer(ctx)
    Quartz.CGContextRestoreGState(ctx)


def _marker(ctx, m, backdrop):
    rgb = m["color"]
    Quartz.CGContextSaveGState(ctx)
    Quartz.CGContextSetAlpha(ctx, _alpha(m))
    if tuple(rgb) == WHITE:
        color(WHITE, 0.55).set()
    elif not backdrop:
        color(rgb, 0.42).set()
    else:
        Quartz.CGContextSetBlendMode(ctx, Quartz.kCGBlendModeMultiply)
        color(tuple(c * 0.7 + 255 * 0.3 for c in rgb)).set()
    path = _smooth(_two(m))
    path.setLineWidth_(MARKER[m["size"]])
    path.setLineCapStyle_(2)
    path.setLineJoinStyle_(1)
    path.stroke()
    Quartz.CGContextRestoreGState(ctx)


def _text(ctx, m):
    x, y, w, h = text_box(m)
    px, py = text_pad(m)
    rgb = tuple(m["color"])
    if m.get("fill"):
        color(rgb).set()
        r = min(8.0, h / 2)
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(x, y, w, h), r, r).fill()
        text_string(m, contrast(rgb)).drawAtPoint_(NSMakePoint(x + px, y + py))
        return
    Quartz.CGContextSetLineJoin(ctx, Quartz.kCGLineJoinRound)
    text_string(m, contrast(rgb), contrast(rgb)).drawAtPoint_(NSMakePoint(x, y))
    text_string(m, rgb).drawAtPoint_(NSMakePoint(x, y))


def _step(m):
    rgb = tuple(m["color"])
    cx, cy = m["pts"][0]
    r = step_radius(m)
    color(rgb).set()
    if len(m["pts"]) > 1:
        step_tail(m).fill()
    disc = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - r, cy - r, 2 * r, 2 * r))
    disc.fill()
    ring = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - r + 1, cy - r + 1, 2 * r - 2, 2 * r - 2))
    ring.setLineWidth_(max(1.5, r * 0.12))
    color(contrast(rgb), 0.9).set()
    ring.stroke()
    label = NSAttributedString.alloc().initWithString_attributes_(str(m.get("n", 1)), {
        NSFontAttributeName: NSFont.monospacedDigitSystemFontOfSize_weight_(r * 1.05, 0.5),
        NSForegroundColorAttributeName: color(contrast(rgb))})
    size = label.size()
    label.drawAtPoint_(NSMakePoint(cx - size.width / 2, cy - size.height / 2))


def _shape(ctx, m):
    kind = m["kind"]
    rgb = tuple(m["color"])
    w = STROKE[m["size"]]
    color(rgb).set()
    if kind == "text":
        _text(ctx, m)
        return
    if kind == "step":
        _step(m)
        return
    if kind == "arrow":
        path = arrow_path(m)
        path.fill()
        path.setLineWidth_(max(1.0, w * 0.35))
        path.setLineJoinStyle_(1)
        path.stroke()
        return
    if kind in ("rect", "ellipse"):
        x, y, bw, bh = box(m)
        rect = NSMakeRect(x, y, bw, bh)
        if kind == "rect":
            r = min(3.0 + w, bw / 2, bh / 2)
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, r, r)
        else:
            path = NSBezierPath.bezierPathWithOvalInRect_(rect)
        if m.get("fill"):
            path.fill()
            return
        path.setLineWidth_(w)
        path.setLineJoinStyle_(1)
        path.stroke()
        return
    pts = _two(m)
    path = _smooth(pts) if kind == "pen" else _smooth([pts[0], pts[-1]])
    path.setLineWidth_(w)
    path.setLineCapStyle_(1)
    path.setLineJoinStyle_(1)
    path.stroke()


def _meets(m, area):
    x, y, w, h = box(m)
    return (x < area.origin.x + area.size.width and area.origin.x < x + w
            and y < area.origin.y + area.size.height and area.origin.y < y + h)


def paint(marks, area, effects=None, k=1.0, backdrop=True):
    ctx = NSGraphicsContext.currentContext().CGContext()
    shown = [m for m in marks if m.get("alpha", 1.0) > 0.01]
    for m in shown:
        if m["kind"] in EFFECTS:
            _effect(m, effects)
    spots = [m for m in shown if m["kind"] == "spotlight" and _meets(m, area)]
    if spots:
        _spotlight(ctx, spots, area)
    for m in shown:
        if m["kind"] == "marker":
            _marker(ctx, m, backdrop)
    for m in shown:
        if m["kind"] in EFFECTS or m["kind"] in ("spotlight", "marker"):
            continue
        _layer(ctx, m, k)
        _shape(ctx, m)
        _end(ctx)


def render(cgimage, size, rect, marks, effects=None):
    full_w, full_h = float(size[0]), float(size[1])
    scale = Quartz.CGImageGetWidth(cgimage) / max(1.0, full_w)
    x, y, w, h = (float(n) for n in rect)
    if not marks:
        return Quartz.CGImageCreateWithImageInRect(cgimage, Quartz.CGRectMake(x * scale, y * scale, w * scale, h * scale))
    pw, ph = max(1, round(w * scale)), max(1, round(h * scale))
    space = Quartz.CGImageGetColorSpace(cgimage)
    if space is None or Quartz.CGColorSpaceGetModel(space) != Quartz.kCGColorSpaceModelRGB:
        space = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)
    ctx = Quartz.CGBitmapContextCreate(None, pw, ph, 8, 0, space, Quartz.kCGImageAlphaNoneSkipLast)
    Quartz.CGContextTranslateCTM(ctx, 0, ph)
    Quartz.CGContextScaleCTM(ctx, scale, -scale)
    Quartz.CGContextTranslateCTM(ctx, -x, -y)
    gc = NSGraphicsContext.graphicsContextWithCGContext_flipped_(ctx, True)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(gc)
    try:
        Quartz.CGContextSaveGState(ctx)
        Quartz.CGContextSetInterpolationQuality(ctx, Quartz.kCGInterpolationNone)
        NSImage.alloc().initWithCGImage_size_(cgimage, NSMakeSize(full_w, full_h)).drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
            NSMakeRect(0, 0, full_w, full_h), NSZeroRect, 1, 1.0, True, None)
        Quartz.CGContextRestoreGState(ctx)
        paint(marks, NSMakeRect(x, y, w, h), effects or Effects(cgimage, size), k=scale)
    finally:
        NSGraphicsContext.restoreGraphicsState()
    return Quartz.CGBitmapContextCreateImage(ctx)


class Board:
    def __init__(self, tools, prefs=None):
        prefs = prefs or {}
        self.tools = tuple(tools)
        default = "arrow" if "arrow" in self.tools else self.tools[0]
        self.tool = prefs.get("tool") if prefs.get("tool") in self.tools else default
        self.color = _clamp(prefs.get("color") or 0, COLORS)
        size = prefs.get("size")
        self.size = _clamp(1 if size is None else size, SIZES)
        self.fill = bool(prefs.get("fill"))
        self.marks = []
        self.done = []
        self.undone = []
        self.active = None
        self.selected = None
        self.editing = None
        self.grab = None
        self.pending = None

    def prefs(self):
        return {"tool": self.tool, "color": self.color, "size": self.size, "fill": self.fill}

    def shown(self):
        return self.marks + ([self.active] if self.active is not None else [])

    def copy(self):
        return [dict(m) for m in self.marks]

    def save(self):
        self.commit(self.copy())

    def commit(self, snapshot):
        self.done.append(snapshot)
        del self.done[:-200]
        self.undone.clear()

    def undo(self):
        before = len(self.done)
        self.finish_text()
        if len(self.done) < before:
            return True
        if not self.done:
            return False
        self.undone.append(self.copy())
        self.marks = self.done.pop()
        self.selected = None
        return True

    def redo(self):
        self.finish_text()
        if not self.undone:
            return False
        self.done.append(self.copy())
        self.marks = self.undone.pop()
        self.selected = None
        return True

    def load(self, marks, now=None):
        self.marks = [dict(m, t=now) if now is not None else dict(m) for m in marks]
        self.selected = self.editing = None

    def deselect(self):
        self.finish_text()
        self.selected = None

    def restart_fade(self, now):
        for m in self.marks:
            if m.get("t") is not None:
                m["t"] = now
                m.pop("alpha", None)

    def target(self):
        return self.editing if self.editing is not None else self.selected

    def next_step(self):
        return max([m.get("n", 0) for m in self.marks if m["kind"] == "step"], default=0) + 1

    def new_mark(self, kind, p):
        m = {"kind": kind, "color": list(COLORS[self.color][1]), "size": self.size, "pts": [list(p), list(p)]}
        if kind in FILLABLE and self.fill:
            m["fill"] = True
        if kind in FREEHAND or kind in ("step", "text"):
            m["pts"] = [list(p)]
        if kind == "step":
            m["n"] = self.next_step()
        if kind == "text":
            m["text"] = ""
        return m

    def hit(self, p):
        for i in range(len(self.marks) - 1, -1, -1):
            if hit(self.marks[i], p):
                return i
        return None

    def set_tool(self, tool):
        if tool not in self.tools:
            return False
        self.finish_text()
        self.tool = tool
        self.selected = None
        return True

    def press(self, p):
        self.finish_text()
        p = [float(p[0]), float(p[1])]
        if self.tool == "move":
            i = self.hit(p)
            self.selected = i
            if i is None:
                return False
            self.grab = [i, p, self.copy()]
            return True
        self.selected = None
        if self.tool == "text":
            i = self.hit(p)
            if i is not None and self.marks[i]["kind"] == "text":
                self.editing = i
                self.pending = (self.copy(), self.marks[i].get("text"))
                self.marks[i].pop("alpha", None)
                return True
            self.save()
            self.marks.append(self.new_mark("text", p))
            self.editing = len(self.marks) - 1
            return True
        self.active = self.new_mark(self.tool, p)
        return True

    def drag(self, p, shift=False):
        p = [float(p[0]), float(p[1])]
        if self.grab is not None:
            i, last, snapshot = self.grab
            if snapshot is not None:
                self.commit(snapshot)
            translate(self.marks[i], p[0] - last[0], p[1] - last[1])
            self.grab = [i, p, None]
            return
        m = self.active
        if m is None:
            return
        kind = m["kind"]
        start = m["pts"][0]
        if kind in FREEHAND:
            if shift:
                m["pts"] = [start, constrain(start, p, "line", True)]
            elif math.hypot(p[0] - m["pts"][-1][0], p[1] - m["pts"][-1][1]) >= 1.0:
                m["pts"].append(p)
        elif kind == "step":
            far = math.hypot(p[0] - start[0], p[1] - start[1]) > step_radius(m) * 1.3
            m["pts"] = [start, p] if far else [start]
        else:
            m["pts"] = [start, constrain(start, p, kind, shift)]

    def release(self, p, shift=False):
        if self.grab is not None:
            self.grab = None
            return False
        m = self.active
        if m is None:
            return False
        self.drag(p, shift)
        self.active = None
        kind = m["kind"]
        x0, y0, x1, y1 = (m["pts"][0][0], m["pts"][0][1], m["pts"][-1][0], m["pts"][-1][1])
        if kind in BOXES and (abs(x1 - x0) < 3 or abs(y1 - y0) < 3):
            return False
        if kind in ("line", "arrow") and math.hypot(x1 - x0, y1 - y0) < 4:
            return False
        m["t"] = time.monotonic()
        self.save()
        self.marks.append(m)
        return True

    def finish_text(self):
        i = self.editing
        if i is None:
            return
        self.editing = None
        pending, self.pending = self.pending, None
        if i >= len(self.marks):
            return
        m = self.marks[i]
        if (m.get("text") or "").strip():
            m["t"] = time.monotonic()
            if pending is not None and pending[1] != m.get("text"):
                self.commit(pending[0])
            return
        del self.marks[i]
        if pending is not None:
            self.commit(pending[0])
        elif self.done and self.done[-1] == self.marks:
            self.done.pop()

    def type_text(self, chars):
        if self.editing is not None:
            self.marks[self.editing]["text"] = (self.marks[self.editing].get("text") or "") + chars

    def backspace(self):
        if self.editing is not None:
            m = self.marks[self.editing]
            m["text"] = (m.get("text") or "")[:-1]

    def delete_selected(self):
        if self.selected is None:
            return False
        self.save()
        del self.marks[self.selected]
        self.selected = None
        return True

    def nudge(self, dx, dy):
        if self.selected is None:
            return False
        self.save()
        translate(self.marks[self.selected], dx, dy)
        return True

    def _restyle(self, key, value, kinds=None):
        i = self.target()
        if i is None or i >= len(self.marks):
            return
        m = self.marks[i]
        if kinds is not None and m["kind"] not in kinds:
            return
        if self.editing is None:
            self.save()
        elif self.pending is not None:
            self.commit(self.pending[0])
            self.pending = None
        m[key] = value

    def set_color(self, i):
        self.color = _clamp(i, COLORS)
        self._restyle("color", list(COLORS[self.color][1]))

    def set_size(self, i):
        self.size = _clamp(i, SIZES)
        self._restyle("size", self.size)

    def toggle_fill(self):
        self.fill = not self.fill
        self._restyle("fill", self.fill, FILLABLE)

    def clear(self):
        self.finish_text()
        if not self.marks:
            return False
        self.save()
        self.marks = []
        self.selected = None
        return True

    def fading(self):
        editing = self.marks[self.editing] if self.editing is not None else None
        return [m for m in self.marks if m.get("t") is not None and m is not editing]

    def next_fade(self, hold):
        times = [m["t"] for m in self.fading()]
        return min(times) + hold if times else None

    def fade(self, now, hold, span):
        editing = self.marks[self.editing] if self.editing is not None else None
        keep = []
        for m in self.marks:
            t = m.get("t")
            if t is not None and m is not editing:
                m["alpha"] = 1.0 - max(0.0, now - t - hold) / span
                if m["alpha"] <= 0.0:
                    continue
            keep.append(m)
        if len(keep) != len(self.marks):
            self.marks = keep
            self.selected = None
            self.editing = next((i for i, m in enumerate(keep) if m is editing), None)
        return bool(self.fading())

    def output(self):
        self.finish_text()
        return [{k: v for k, v in m.items() if k not in ("t", "alpha")} for m in self.marks]
