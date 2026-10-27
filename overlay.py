from __future__ import annotations

import math
import os
import time

import objc
import Quartz
from AppKit import (
    NSApp, NSAttributedString, NSBackingStoreBuffered, NSBezierPath, NSColor, NSCursor,
    NSFont, NSFontAttributeName, NSForegroundColorAttributeName, NSGraphicsContext, NSImage,
    NSImageInterpolationNone, NSImageSymbolConfiguration, NSPanel, NSPasteboard, NSPasteboardTypeString,
    NSScreen, NSTrackingArea, NSView, NSWindow,
)
from Foundation import NSMakePoint, NSMakeRect, NSMakeSize, NSObject, NSTimer, NSZeroRect

import marks

COMPOSITE_COPY = 1
COMPOSITE_OVER = 2
EVEN_ODD = 1
BORDERLESS = 0
NONACTIVATING = 1 << 7
TRACK_FLAGS = 2 | 128 | 512 | 1
LEVEL_OVERLAY = 1000
LEVEL_PANEL = 101
LEVEL_DRAW = 100
JOIN_ALL = 1 | 256 | 16 | 64

ESC, SPACE, RETURN, ENTER, DELETE, FWD_DELETE = 53, 49, 36, 76, 51, 117
ARROWS = {123: (-1, 0), 124: (1, 0), 125: (0, 1), 126: (0, -1)}
SHIFT, CMD = 1 << 17, 1 << 20
MAG_CELLS, MAG_ZOOM = 15, 9
BAR_H, BAR_BTN, BAR_GAP, BAR_SWATCH = 40.0, 30.0, 2.0, 22.0
FADE_HOLD, FADE_SPAN = 3.0, 0.6
TOOLS = {
    "move": ("Move", "v", "cursorarrow"),
    "rect": ("Rectangle", "r", "rectangle"),
    "ellipse": ("Ellipse", "e", "circle"),
    "arrow": ("Arrow", "a", "arrow.up.right"),
    "line": ("Line", "l", "line.diagonal"),
    "pen": ("Pen", "d", "scribble.variable"),
    "marker": ("Marker", "m", "highlighter"),
    "text": ("Text", "t", "textformat"),
    "step": ("Step number", "c", "1.circle"),
    "spotlight": ("Spotlight", "h", "light.max"),
    "blur": ("Blur", "b", "drop.halffull"),
    "pixelate": ("Pixelate", "p", "squareshape.split.3x3"),
}
SHOT_TOOLS = tuple(TOOLS)
LIVE_TOOLS = ("pen", "marker", "arrow", "line", "rect", "ellipse", "step", "text", "spotlight")


def rgba(r, g, b, a=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r / 255.0, g / 255.0, b / 255.0, a)


SHU = (221, 70, 48)
PAPER = (245, 243, 238)
SUMI = (39, 48, 74)
SKY = (156, 196, 211)
SAND = (233, 197, 139)


def _text(s, size=12, color=(255, 255, 255), weight=0.3, mono=False):
    font = NSFont.monospacedDigitSystemFontOfSize_weight_(size, weight) if mono else NSFont.systemFontOfSize_weight_(size, weight)
    return NSAttributedString.alloc().initWithString_attributes_(
        s, {NSFontAttributeName: font, NSForegroundColorAttributeName: rgba(*color)})


def _pill(rect, fill, radius=None):
    r = radius if radius is not None else rect.size.height / 2
    fill.set()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, r, r).fill()


TIPS = {
    "undo": "Undo   ⌘Z", "redo": "Redo   ⇧⌘Z", "save": "Save   ↩", "style": "Save and open the style editor",
    "cancel": "Cancel   Esc", "fill": "Fill   F", "fade": "Fade the marks after 3 s", "clear": "Remove all marks",
    "done": "Stop drawing   Esc",
}
ACTION_ICONS = {"undo": "arrow.uturn.backward", "redo": "arrow.uturn.forward", "style": "photo.artframe",
                "cancel": "xmark", "clear": "trash", "fade": "timer", "done": "checkmark"}
_SYMBOLS = {}


def symbol(name, rgb=PAPER, alpha=1.0, size=15.0):
    key = (name, rgb, alpha, size)
    if key not in _SYMBOLS:
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
        if img is not None:
            cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, 0.23)
            cfg = cfg.configurationByApplyingConfiguration_(
                NSImageSymbolConfiguration.configurationWithPaletteColors_([rgba(*rgb, alpha)]))
            img = img.imageWithSymbolConfiguration_(cfg)
        _SYMBOLS[key] = img
    return _SYMBOLS[key]


def draw_symbol(name, rect, rgb=PAPER, alpha=1.0, size=15.0):
    img = symbol(name, rgb, alpha, size)
    if img is None:
        return
    s = img.size()
    dest = NSMakeRect(rect.origin.x + (rect.size.width - s.width) / 2,
                      rect.origin.y + (rect.size.height - s.height) / 2, s.width, s.height)
    img.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(dest, NSZeroRect, COMPOSITE_OVER, 1.0, True, None)


def _inside(rect, p):
    return (rect.origin.x <= p[0] <= rect.origin.x + rect.size.width
            and rect.origin.y <= p[1] <= rect.origin.y + rect.size.height)


def _point(view, event):
    p = view.convertPoint_fromView_(event.locationInWindow(), None)
    return (p.x, p.y)


def _shift(event):
    return bool(event.modifierFlags() & SHIFT)


def _tag(label, x, y, bounds, size=12, mono=False):
    text = _text(label, size, PAPER, 0.4, mono=mono)
    s = text.size()
    w, h = s.width + 18, s.height + 8
    x = min(max(4, x), bounds.size.width - w - 4)
    _pill(NSMakeRect(x, y, w, h), rgba(*SUMI, 0.9), 6)
    text.drawAtPoint_(NSMakePoint(x + 9, y + 4))


def _tag_height(size=12):
    return _text("0", size).size().height + 8


def _key_window(frame, level, opaque):
    win = KeyWindow.alloc().initWithContentRect_styleMask_backing_defer_(frame, BORDERLESS, NSBackingStoreBuffered, False)
    win.setReleasedWhenClosed_(False)
    win.setLevel_(level)
    win.setOpaque_(opaque)
    win.setHasShadow_(False)
    win.setCollectionBehavior_(JOIN_ALL)
    if not opaque:
        win.setBackgroundColor_(NSColor.clearColor())
    return win


def snapshot_view(view, path):
    rep = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
    view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), rep)
    rep.representationUsingType_properties_(4, None).writeToFile_atomically_(path, True)
    return path


def front_app():
    try:
        from AppKit import NSWorkspace

        return NSWorkspace.sharedWorkspace().frontmostApplication()
    except Exception:
        return None


def reactivate(app):
    if app is not None and int(app.processIdentifier()) != os.getpid():
        try:
            app.activateWithOptions_(0)
        except Exception:
            pass


class ToolBar:
    def __init__(self, board, actions, toggles=None):
        self.board = board
        self.actions = actions
        self.toggles = toggles or {}
        self.items = []
        self.hover = None
        self.layout(0, 0)
        self.width = self.frame.size.width

    def layout(self, x, y):
        items = []
        cx = x + 6
        top = y + (BAR_H - BAR_BTN) / 2

        def add(kind, value, w):
            nonlocal cx
            items.append((kind, value, NSMakeRect(cx, top, w, BAR_BTN)))
            cx += w + BAR_GAP

        def sep():
            nonlocal cx
            items.append(("sep", None, NSMakeRect(cx + 3, top + 7, 1, BAR_BTN - 14)))
            cx += 9

        for tool in self.board.tools:
            add("tool", tool, BAR_BTN)
        sep()
        for i in range(len(marks.COLORS)):
            add("color", i, BAR_SWATCH)
        sep()
        for i in range(len(marks.SIZES)):
            add("size", i, 24)
        if any(t in marks.FILLABLE for t in self.board.tools):
            add("fill", None, BAR_BTN)
        for action in self.actions:
            if action == "|":
                sep()
            else:
                add(action, None, 70 if action == "save" else BAR_BTN)
        self.items = items
        self.frame = NSMakeRect(x, y, cx - BAR_GAP + 6 - x, BAR_H)

    def item_at(self, p):
        for kind, value, rect in self.items:
            if kind != "sep" and _inside(rect, p):
                return (kind, value)
        return None

    def contains(self, p):
        return _inside(self.frame, p)

    def set_hover(self, p):
        item = self.item_at(p)
        changed = item != self.hover
        self.hover = item
        return changed

    def enabled(self, kind):
        b = self.board
        if kind == "undo":
            return bool(b.done) or b.editing is not None
        if kind == "redo":
            return bool(b.undone)
        if kind == "clear":
            return bool(b.marks)
        return True

    def click(self, item):
        kind, value = item
        b = self.board
        if kind == "tool":
            b.set_tool(value)
        elif kind == "color":
            b.set_color(value)
        elif kind == "size":
            b.set_size(value)
        elif kind == "fill":
            b.toggle_fill()
        elif kind == "undo":
            b.undo()
        elif kind == "redo":
            b.redo()
        elif kind == "clear":
            b.clear()
        else:
            return kind
        return None

    def draw(self):
        f = self.frame
        _pill(f, rgba(*SUMI, 0.96), 11)
        rim = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(f.origin.x + 0.5, f.origin.y + 0.5, f.size.width - 1, f.size.height - 1), 10.5, 10.5)
        rim.setLineWidth_(1)
        rgba(255, 255, 255, 0.1).set()
        rim.stroke()
        b = self.board
        for kind, value, r in self.items:
            hover = self.hover == (kind, value)
            mx, my = r.origin.x + r.size.width / 2, r.origin.y + r.size.height / 2
            if kind == "sep":
                rgba(255, 255, 255, 0.14).set()
                NSBezierPath.fillRect_(r)
            elif kind == "tool":
                on = b.tool == value
                if on:
                    _pill(r, rgba(*SHU), 7)
                elif hover:
                    _pill(r, rgba(255, 255, 255, 0.1), 7)
                draw_symbol(TOOLS[value][2], r, PAPER, 1.0 if on or hover else 0.86)
            elif kind == "color":
                rgb = marks.COLORS[value][1]
                if b.color == value:
                    ring = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(mx - 9, my - 9, 18, 18))
                    ring.setLineWidth_(1.6)
                    rgba(*PAPER).set()
                    ring.stroke()
                elif hover:
                    rgba(255, 255, 255, 0.12).set()
                    NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(mx - 10, my - 10, 20, 20)).fill()
                marks.color(rgb).set()
                dot = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(mx - 6.5, my - 6.5, 13, 13))
                dot.fill()
                if sum(rgb) < 200:
                    dot.setLineWidth_(1)
                    rgba(255, 255, 255, 0.4).set()
                    dot.stroke()
            elif kind in ("size", "fill", "fade"):
                on = b.size == value if kind == "size" else (b.fill if kind == "fill" else self.toggles["fade"]())
                if on:
                    _pill(r, rgba(255, 255, 255, 0.17), 7)
                elif hover:
                    _pill(r, rgba(255, 255, 255, 0.08), 7)
                if kind == "size":
                    d = (4.0, 7.0, 10.0)[value]
                    rgba(*PAPER, 1.0 if on else 0.7).set()
                    NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(mx - d / 2, my - d / 2, d, d)).fill()
                elif kind == "fill":
                    draw_symbol("square.fill" if on else "square", r, PAPER, 1.0 if on else 0.8, 14)
                else:
                    draw_symbol(ACTION_ICONS["fade"], r, PAPER, 1.0 if on else 0.8)
            elif kind == "save":
                _pill(r, rgba(*SHU) if not hover else rgba(240, 96, 72), 7)
                draw_symbol("checkmark", NSMakeRect(r.origin.x + 9, r.origin.y, 16, r.size.height), PAPER, 1.0, 12.5)
                _text("Save", 12.5, PAPER, 0.55).drawAtPoint_(NSMakePoint(r.origin.x + 29, r.origin.y + 6.5))
            else:
                on = self.enabled(kind)
                if hover and on:
                    _pill(r, rgba(255, 255, 255, 0.1), 7)
                draw_symbol(ACTION_ICONS.get(kind, "questionmark"), r, PAPER, 0.92 if on else 0.3)

    def tip(self):
        if self.hover is None:
            return None
        kind, value = self.hover
        if kind == "tool":
            name, key, _icon = TOOLS[value]
            return f"{name}   {key.upper()}"
        if kind == "color":
            return f"{marks.COLORS[value][0]}   {value + 1}"
        if kind == "size":
            return f"{marks.SIZES[value]}   [ ]"
        return TIPS.get(kind)

    def draw_tip(self, bounds):
        label = self.tip()
        rect = next((r for k, v, r in self.items if (k, v) == self.hover), None)
        if not label or rect is None:
            return
        w = _text(label, 11.5).size().width + 18
        h = _tag_height(11.5)
        y = self.frame.origin.y - h - 6
        if y < 4:
            y = self.frame.origin.y + self.frame.size.height + 6
        _tag(label, rect.origin.x + rect.size.width / 2 - w / 2, y, bounds, 11.5)


def board_key(board, event):
    code = int(event.keyCode())
    flags = int(event.modifierFlags())
    cmd, shift = bool(flags & CMD), bool(flags & SHIFT)
    chars = str(event.charactersIgnoringModifiers() or "")
    if board.editing is not None:
        if code == ESC or (code in (RETURN, ENTER) and not shift):
            board.finish_text()
        elif code in (RETURN, ENTER):
            board.type_text("\n")
        elif code in (DELETE, FWD_DELETE):
            board.backspace()
        elif cmd and chars.lower() == "v":
            pasted = NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)
            if pasted:
                board.type_text(str(pasted))
        elif cmd and chars.lower() == "z":
            if shift:
                board.redo()
            else:
                board.undo()
        elif not cmd:
            typed = "".join(c for c in str(event.characters() or "") if c >= " " and not "\uf700" <= c <= "\uf8fe")
            board.type_text(typed)
        return True
    if cmd:
        if chars.lower() == "z":
            if shift:
                board.redo()
            else:
                board.undo()
            return True
        return False
    if code in (DELETE, FWD_DELETE):
        return board.delete_selected()
    if code in ARROWS and board.selected is not None:
        dx, dy = ARROWS[code]
        step = 10 if shift else 1
        return board.nudge(dx * step, dy * step)
    if chars in ("[", "]"):
        board.set_size(board.size + (1 if chars == "]" else -1))
        return True
    if len(chars) == 1 and chars in "123456":
        board.set_color(int(chars) - 1)
        return True
    if chars.lower() == "f" and any(t in marks.FILLABLE for t in board.tools):
        board.toggle_fill()
        return True
    for tool in board.tools:
        if TOOLS[tool][1] == chars.lower():
            board.set_tool(tool)
            return True
    return False


def draw_board_focus(board):
    i = board.target()
    if i is None or i >= len(board.marks):
        return
    m = board.marks[i]
    x0, y0, x1, y1 = marks.bounds(m)
    path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(x0 - 5, y0 - 5, x1 - x0 + 10, y1 - y0 + 10), 5, 5)
    path.setLineWidth_(1.2)
    path.setLineDash_count_phase_([4.0, 3.0], 2, 0.0)
    rgba(*SUMI, 0.8).set()
    path.stroke()
    path.setLineDash_count_phase_([4.0, 3.0], 2, 3.5)
    rgba(255, 255, 255, 0.95).set()
    path.stroke()
    if board.editing == i:
        cx, cy, line_h = marks.caret(m)
        marks.color(marks.contrast(tuple(m["color"])) if m.get("fill") else m["color"]).set()
        NSBezierPath.fillRect_(NSMakeRect(cx, cy + 2, 2, line_h - 4))


def screens_info():
    out = []
    main_h = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID()).size.height
    for screen in NSScreen.screens():
        desc = screen.deviceDescription()
        did = int(desc["NSScreenNumber"])
        frame = screen.frame()
        b = Quartz.CGDisplayBounds(did)
        out.append({
            "display": did,
            "screen": screen,
            "frame": frame,
            "cg_x": float(b.origin.x),
            "cg_y": float(b.origin.y),
            "w": float(b.size.width),
            "h": float(b.size.height),
            "main_h": float(main_h),
        })
    return out


def load_cgimage(path):
    from Foundation import NSURL

    src = Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(path), None)
    if src is None:
        return None
    return Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)


class KeyWindow(NSWindow):
    def canBecomeKeyWindow(self):
        return True

    def canBecomeMainWindow(self):
        return True


class SelectionView(NSView):
    def isFlipped(self):
        return True

    def acceptsFirstResponder(self):
        return True

    def acceptsFirstMouse_(self, event):
        return True

    @objc.python_method
    def setup(self, ctl, shot):
        self.ctl = ctl
        self.shot = shot
        self.mouse = None
        self.start = None
        self.current = None
        self.hover = None
        self.area = None
        self.sel = None
        self.prev_sel = None
        self.picked = None
        self.board = None
        self.bar = None
        self.resize = None
        self.updateTrackingAreas()

    def updateTrackingAreas(self):
        area = getattr(self, "area", None)
        if area is not None:
            self.removeTrackingArea_(area)
        self.area = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(self.bounds(), TRACK_FLAGS, self, None)
        self.addTrackingArea_(self.area)

    def resetCursorRects(self):
        self.addCursorRect_cursor_(self.bounds(), NSCursor.crosshairCursor())

    @objc.python_method
    def point(self, event):
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        b = self.bounds()
        return (min(max(p.x, 0.0), b.size.width), min(max(p.y, 0.0), b.size.height))

    @objc.python_method
    def selection(self):
        if self.start is None or self.current is None:
            return None
        x0, y0 = self.start
        x1, y1 = self.current
        x, y = min(x0, x1), min(y0, y1)
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w < 3 or h < 3:
            return None
        return (round(x), round(y), round(w), round(h))

    @objc.python_method
    def window_under(self, p):
        if not self.ctl.snap:
            return None
        for win in self.shot["windows"]:
            if win["x"] <= p[0] <= win["x"] + win["w"] and win["y"] <= p[1] <= win["y"] + win["h"]:
                return win
        return None

    def mouseMoved_(self, event):
        if self.sel is not None:
            self.ctl.focus(self)
            self.annotate_hover(self.point(event))
            return
        NSCursor.crosshairCursor().set()
        old_mouse, old_hover = self.mouse, self.hover
        was_focused = self.ctl.focused is self
        old_rects = self.pointer_rects()
        self.mouse = self.point(event)
        self.hover = self.window_under(self.mouse)
        self.ctl.focus(self)
        if (not was_focused or old_mouse is None or self.start is not None or self.hover is not old_hover
                or (old_mouse[1] < 120) != (self.mouse[1] < 120)):
            self.setNeedsDisplay_(True)
            return
        for rect in old_rects + self.pointer_rects():
            self.setNeedsDisplayInRect_(rect)

    @objc.python_method
    def pointer_rects(self):
        if self.mouse is None:
            return []
        mx, my = self.mouse
        b = self.bounds()
        rects = [NSMakeRect(0, round(my) - 6, b.size.width, 13), NSMakeRect(round(mx) - 6, 0, 13, b.size.height)]
        if self.ctl.magnifier:
            x, y, side = self.magnifier_box()
            rects.append(NSMakeRect(x - 6, y - 6, side + 12, side + 36))
        return rects

    @objc.python_method
    def magnifier_box(self):
        mx, my = self.mouse
        side = MAG_CELLS * MAG_ZOOM
        b = self.bounds()
        x = mx + 22
        y = my + 22
        if x + side + 8 > b.size.width:
            x = mx - side - 22
        if y + side + 30 > b.size.height:
            y = my - side - 44
        return x, y, side

    def mouseDown_(self, event):
        p = self.point(event)
        self.ctl.focus(self)
        if self.sel is not None and self.annotate_down(p, event):
            self.setNeedsDisplay_(True)
            return
        other = self.ctl.annotating
        if other is not None and other is not self:
            other.drop_selection()
        self.start = p
        self.current = p
        self.mouse = p
        self.setNeedsDisplay_(True)

    def mouseDragged_(self, event):
        p = self.point(event)
        self.mouse = p
        if self.sel is not None:
            if self.resize is not None:
                self.drag_frame(p)
            else:
                self.board.drag(p, _shift(event))
            self.setNeedsDisplay_(True)
            return
        self.current = p
        self.setNeedsDisplay_(True)

    def mouseUp_(self, event):
        p = self.point(event)
        if self.sel is not None:
            if self.resize is not None:
                self.resize = None
            else:
                self.board.release(p, _shift(event))
            self.setNeedsDisplay_(True)
            return
        if self.start is None:
            return
        self.current = p
        rect = self.selection()
        if rect is None:
            win = self.window_under(self.current)
            self.start = self.current = None
            if win is not None:
                self.pick(self.clip(win), win)
                return
            if self.prev_sel is not None:
                self.sel, self.prev_sel = self.prev_sel, None
            self.setNeedsDisplay_(True)
            return
        self.pick(rect, None)

    def rightMouseDown_(self, event):
        self.ctl.cancel()

    def keyDown_(self, event):
        target = self.ctl.annotating
        if target is not None and target is not self:
            target.keyDown_(event)
            return
        if self.sel is not None:
            self.annotate_key(event)
            return
        code = int(event.keyCode())
        if code == ESC:
            self.ctl.cancel()
        elif code == SPACE:
            self.pick((0, 0, round(self.shot["w"]), round(self.shot["h"])), None)
        elif code in (RETURN, ENTER):
            rect = self.selection()
            if rect is not None:
                self.pick(rect, None)
            elif self.hover is not None:
                self.pick(self.clip(self.hover), self.hover)

    @objc.python_method
    def pick(self, rect, win):
        if not self.ctl.annotate:
            self.ctl.finish(self.shot, rect, win)
            return
        self.begin(rect, win)

    @objc.python_method
    def begin(self, rect, win):
        self.sel = [float(n) for n in rect]
        self.prev_sel = None
        self.picked = win
        self.start = self.current = None
        if self.board is None:
            self.board = marks.Board(SHOT_TOOLS, self.ctl.prefs)
            self.bar = ToolBar(self.board, ("|", "undo", "redo", "|", "cancel", "style", "save"))
        self.ctl.annotating = self
        self.setNeedsDisplay_(True)

    @objc.python_method
    def drop_selection(self):
        self.sel = self.prev_sel = self.picked = self.board = self.bar = self.resize = None
        if self.ctl.annotating is self:
            self.ctl.annotating = None
        self.setNeedsDisplay_(True)

    @objc.python_method
    def inside(self, p):
        return _inside(NSMakeRect(*self.sel), p)

    @objc.python_method
    def place(self, x, y):
        b = self.bounds().size
        w, h = self.sel[2], self.sel[3]
        self.sel = [min(max(0.0, x), b.width - w), min(max(0.0, y), b.height - h), w, h]

    @objc.python_method
    def handle_points(self):
        x, y, w, h = self.sel
        mx, my, x1, y1 = x + w / 2, y + h / 2, x + w, y + h
        return {"nw": (x, y), "n": (mx, y), "ne": (x1, y), "e": (x1, my),
                "se": (x1, y1), "s": (mx, y1), "sw": (x, y1), "w": (x, my)}

    @objc.python_method
    def handle_at(self, p):
        for name, (hx, hy) in self.handle_points().items():
            if abs(p[0] - hx) <= 7 and abs(p[1] - hy) <= 7:
                return name
        return None

    @objc.python_method
    def annotate_down(self, p, event):
        item = self.bar.item_at(p)
        if item is not None:
            self.bar_action(item)
            return True
        if self.bar.contains(p):
            return True
        board = self.board
        handle = self.handle_at(p)
        if handle is not None:
            board.finish_text()
            self.resize = (handle, list(self.sel), p)
            return True
        inside = self.inside(p)
        if board.tool == "move":
            if board.press(p):
                NSCursor.closedHandCursor().set()
                return True
            if inside:
                if event.clickCount() >= 2:
                    self.save("save")
                    return True
                self.resize = ("move", list(self.sel), p)
                NSCursor.closedHandCursor().set()
                return True
        elif inside:
            board.press(p)
            return True
        board.deselect()
        self.prev_sel, self.sel = self.sel, None
        return False

    @objc.python_method
    def drag_frame(self, p):
        kind, (x0, y0, w, h), start = self.resize
        dx, dy = p[0] - start[0], p[1] - start[1]
        if kind == "move":
            self.place(x0 + dx, y0 + dy)
            return
        bw, bh = self.bounds().size.width, self.bounds().size.height
        x1, y1 = x0 + w, y0 + h
        if "w" in kind:
            x0 += dx
        if "e" in kind:
            x1 += dx
        if "n" in kind:
            y0 += dy
        if "s" in kind:
            y1 += dy
        x0, x1 = (min(max(0.0, v), bw) for v in (x0, x1))
        y0, y1 = (min(max(0.0, v), bh) for v in (y0, y1))
        w, h = max(4.0, abs(x1 - x0)), max(4.0, abs(y1 - y0))
        self.sel = [min(min(x0, x1), bw - w), min(min(y0, y1), bh - h), w, h]

    @objc.python_method
    def annotate_hover(self, p):
        self.mouse = p
        if self.bar.set_hover(p):
            self.setNeedsDisplay_(True)
        self.cursor_at(p, self.bar.hover).set()

    @objc.python_method
    def cursor_at(self, p, item):
        if item is not None or self.bar.contains(p):
            return NSCursor.arrowCursor()
        handle = self.handle_at(p)
        if handle in ("n", "s"):
            return NSCursor.resizeUpDownCursor()
        if handle in ("e", "w"):
            return NSCursor.resizeLeftRightCursor()
        if handle is not None or not self.inside(p):
            return NSCursor.crosshairCursor()
        if self.board.tool == "text":
            return NSCursor.IBeamCursor() or NSCursor.crosshairCursor()
        if self.board.tool == "move":
            return NSCursor.openHandCursor()
        return NSCursor.crosshairCursor()

    @objc.python_method
    def bar_action(self, item):
        action = self.bar.click(item)
        if action in ("save", "style"):
            self.save(action)
        elif action == "cancel":
            self.ctl.cancel()

    @objc.python_method
    def annotate_key(self, event):
        if board_key(self.board, event):
            self.setNeedsDisplay_(True)
            return
        code = int(event.keyCode())
        flags = int(event.modifierFlags())
        chars = str(event.charactersIgnoringModifiers() or "").lower()
        if code in (RETURN, ENTER) or (flags & CMD and chars in ("c", "s")):
            self.save("save")
        elif code == ESC:
            if self.board.selected is not None:
                self.board.selected = None
                self.setNeedsDisplay_(True)
            else:
                self.ctl.cancel()
        elif code in ARROWS:
            dx, dy = ARROWS[code]
            step = 10 if flags & SHIFT else 1
            self.place(self.sel[0] + dx * step, self.sel[1] + dy * step)
            self.setNeedsDisplay_(True)

    @objc.python_method
    def save(self, action):
        drawn = self.board.output()
        x, y, w, h = self.sel
        rect = (round(x), round(y), max(1, round(w)), max(1, round(h)))
        self.ctl.finish(self.shot, rect, self.picked, drawn, action)

    @objc.python_method
    def clip(self, win):
        x0 = max(0.0, win["x"])
        y0 = max(0.0, win["y"])
        x1 = min(self.shot["w"], win["x"] + win["w"])
        y1 = min(self.shot["h"], win["y"] + win["h"])
        return (round(x0), round(y0), round(x1 - x0), round(y1 - y0))

    def drawRect_(self, dirty):
        b = self.bounds()
        self.shot["nsimage"].drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
            b, NSZeroRect, COMPOSITE_COPY, 1.0, True, None)
        if self.sel is not None:
            self.draw_annotate()
            return
        sel = self.selection()
        hole = None
        snapped = None
        if sel is not None:
            hole = NSMakeRect(*sel)
        elif self.start is None and self.hover is not None and self.ctl.focused is self:
            snapped = self.clip(self.hover)
            hole = NSMakeRect(*snapped)
        self.draw_shade(hole)
        if hole is not None:
            self.draw_frame(hole, dashed=snapped is not None)
            self.draw_label(hole, self.size_label(hole, self.hover if snapped is not None else None))
        if self.ctl.focused is self and self.mouse is not None:
            if sel is None:
                self.draw_crosshair()
            if self.ctl.magnifier:
                self.draw_magnifier()
        if self.ctl.focused is self:
            self.draw_hint()

    @objc.python_method
    def draw_annotate(self):
        b = self.bounds()
        x, y, w, h = self.sel
        hole = NSMakeRect(x, y, w, h)
        self.draw_shade(hole)
        shown = self.board.shown()
        effects = self.shot.get("effects")
        if effects is None and any(m["kind"] in marks.EFFECTS for m in shown):
            effects = self.shot["effects"] = marks.Effects(self.shot["cgimage"], (self.shot["w"], self.shot["h"]))
        NSGraphicsContext.saveGraphicsState()
        NSBezierPath.clipRect_(hole)
        marks.paint(shown, hole, effects, 1.0)
        draw_board_focus(self.board)
        NSGraphicsContext.restoreGraphicsState()
        self.draw_frame(hole, handles=True)
        bar_w = self.bar.width
        bx = min(max(8.0, x + w / 2 - bar_w / 2), b.size.width - bar_w - 8)
        by = y + h + 10
        if by + BAR_H > b.size.height - 8:
            by = y - BAR_H - 10 if y - BAR_H - 10 >= 8 else y + h - BAR_H - 10
        self.bar.layout(bx, by)
        self.draw_label(hole, self.size_label(hole, self.picked), above=by > y)
        self.bar.draw()
        self.bar.draw_tip(b)
        if self.resize is not None and self.resize[0] != "move" and self.ctl.magnifier and self.mouse is not None:
            self.draw_magnifier()

    @objc.python_method
    def draw_shade(self, hole):
        shade = NSBezierPath.bezierPathWithRect_(self.bounds())
        if hole is not None:
            shade.appendBezierPathWithRect_(hole)
            shade.setWindingRule_(EVEN_ODD)
        rgba(18, 24, 40, 0.42 if self.ctl.mode != "record" else 0.5).set()
        shade.fill()

    @objc.python_method
    def size_label(self, hole, win):
        label = f"{round(hole.size.width * self.shot['scale'])} × {round(hole.size.height * self.shot['scale'])}"
        if win is None:
            return label
        name = win["app"] + (f" — {win['title']}" if win["title"] else "")
        return f"{name[:60]}   {label}"

    @objc.python_method
    def draw_frame(self, hole, dashed=False, handles=False):
        path = NSBezierPath.bezierPathWithRect_(NSMakeRect(hole.origin.x - 0.5, hole.origin.y - 0.5,
                                                           hole.size.width + 1, hole.size.height + 1))
        path.setLineWidth_(1.5)
        if dashed:
            path.setLineDash_count_phase_([6.0, 4.0], 2, 0.0)
        color = SHU if self.ctl.mode != "record" else (230, 57, 70)
        rgba(*color).set()
        path.stroke()
        if dashed:
            return
        x0, y0 = hole.origin.x, hole.origin.y
        x1, y1 = x0 + hole.size.width, y0 + hole.size.height
        points = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
        if handles:
            points = list(self.handle_points().values())
        s = 7.0 if handles else 6.0
        for cx, cy in points:
            dot = NSMakeRect(cx - s / 2, cy - s / 2, s, s)
            rgba(255, 255, 255, 0.95).set()
            NSBezierPath.bezierPathWithOvalInRect_(dot).fill()
            rgba(*color).set()
            ring = NSBezierPath.bezierPathWithOvalInRect_(dot)
            ring.setLineWidth_(1.2)
            ring.stroke()

    @objc.python_method
    def draw_label(self, hole, label, above=False):
        h = _tag_height()
        x = hole.origin.x
        y = hole.origin.y + hole.size.height + 8
        if above or y + h > self.bounds().size.height - 4:
            y = hole.origin.y - h - 8
        if y < 4:
            y = hole.origin.y + 8
            x = hole.origin.x + 8
        _tag(label, x, y, self.bounds(), mono=True)

    @objc.python_method
    def draw_crosshair(self):
        mx, my = self.mouse
        b = self.bounds()
        rgba(255, 255, 255, 0.55).set()
        for rect in (NSMakeRect(0, round(my), b.size.width, 1), NSMakeRect(round(mx), 0, 1, b.size.height)):
            NSBezierPath.fillRect_(rect)
        rgba(*SHU, 0.9).set()
        NSBezierPath.fillRect_(NSMakeRect(round(mx) - 5, round(my), 11, 1))
        NSBezierPath.fillRect_(NSMakeRect(round(mx), round(my) - 5, 1, 11))

    @objc.python_method
    def draw_magnifier(self):
        mx, my = self.mouse
        scale = self.shot["scale"]
        cells, zoom = MAG_CELLS, MAG_ZOOM
        x, y, side = self.magnifier_box()
        px = int(mx * scale) - cells // 2
        py = int(my * scale) - cells // 2
        cg = self.shot["cgimage"]
        sx0, sy0 = max(px, 0), max(py, 0)
        sx1 = min(px + cells, int(Quartz.CGImageGetWidth(cg)))
        sy1 = min(py + cells, int(Quartz.CGImageGetHeight(cg)))
        sub = None
        if sx1 > sx0 and sy1 > sy0:
            sub = Quartz.CGImageCreateWithImageInRect(cg, Quartz.CGRectMake(sx0, sy0, sx1 - sx0, sy1 - sy0))
        _pill(NSMakeRect(x - 3, y - 3, side + 6, side + 30), rgba(*SUMI, 0.9), 8)
        if sub is not None:
            NSGraphicsContext.currentContext().setImageInterpolation_(NSImageInterpolationNone)
            img = NSImage.alloc().initWithCGImage_size_(sub, NSMakeSize(sx1 - sx0, sy1 - sy0))
            dest = NSMakeRect(x + (sx0 - px) * zoom, y + (sy0 - py) * zoom, (sx1 - sx0) * zoom, (sy1 - sy0) * zoom)
            img.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(dest, NSZeroRect, COMPOSITE_COPY, 1.0, True, None)
        rgba(255, 255, 255, 0.12).set()
        for i in range(1, cells):
            NSBezierPath.fillRect_(NSMakeRect(x + i * zoom, y, 0.5, side))
            NSBezierPath.fillRect_(NSMakeRect(x, y + i * zoom, side, 0.5))
        c = (cells // 2) * zoom
        ring = NSBezierPath.bezierPathWithRect_(NSMakeRect(x + c, y + c, zoom, zoom))
        ring.setLineWidth_(1.5)
        rgba(*SHU).set()
        ring.stroke()
        coords = _text(f"{int(mx * scale)}, {int(my * scale)}", 11, PAPER, 0.3, mono=True)
        coords.drawAtPoint_(NSMakePoint(x + 4, y + side + 6))

    @objc.python_method
    def draw_hint(self):
        if self.ctl.mode == "record":
            msg = "Drag an area to record   ·   Click a window   ·   Space: full screen   ·   Esc: cancel"
        else:
            msg = "Drag an area   ·   Click a window   ·   Space: full screen   ·   Esc: cancel"
        text = _text(msg, 12.5, PAPER, 0.35)
        size = text.size()
        b = self.bounds()
        w, h = size.width + 34, size.height + 14
        x = (b.size.width - w) / 2
        y = 46
        if self.mouse is not None and self.mouse[1] < 120:
            y = b.size.height - h - 40
        _pill(NSMakeRect(x, y, w, h), rgba(*SUMI, 0.82))
        dot = NSMakeRect(x + 13, y + h / 2 - 3.5, 7, 7)
        rgba(*(SHU if self.ctl.mode != "record" else (230, 57, 70))).set()
        NSBezierPath.bezierPathWithOvalInRect_(dot).fill()
        text.drawAtPoint_(NSMakePoint(x + 26, y + 7))


class RegionSelector:
    def __init__(self, mode, shots, on_done, on_cancel, snap=True, magnifier=True, annotate=False, prefs=None,
                 on_prefs=None):
        self.mode = mode
        self.shots = shots
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.snap = snap
        self.magnifier = magnifier
        self.annotate = bool(annotate) and mode != "record"
        self.prefs = dict(prefs or {})
        self.on_prefs = on_prefs
        self.annotating = None
        self.windows = []
        self.views = []
        self.focused = None
        self.closed = False

    def show(self):
        from AppKit import NSEvent

        mouse = NSEvent.mouseLocation()
        key_window = None
        for shot in self.shots:
            frame = shot["frame"]
            win = _key_window(frame, LEVEL_OVERLAY, True)
            win.setAcceptsMouseMovedEvents_(True)
            win.setIgnoresMouseEvents_(False)
            view = SelectionView.alloc().initWithFrame_(NSMakeRect(0, 0, frame.size.width, frame.size.height))
            view.setup(self, shot)
            win.setContentView_(view)
            win.setFrame_display_(frame, False)
            self.windows.append(win)
            self.views.append(view)
            inside = (frame.origin.x <= mouse.x <= frame.origin.x + frame.size.width
                      and frame.origin.y <= mouse.y <= frame.origin.y + frame.size.height)
            if inside or key_window is None:
                key_window = (win, view, shot)
        for win in self.windows:
            win.orderFrontRegardless()
        if key_window is not None:
            win, view, shot = key_window
            local = win.convertPointFromScreen_(mouse) if hasattr(win, "convertPointFromScreen_") else mouse
            p = view.convertPoint_fromView_(local, None)
            view.mouse = (p.x, p.y)
            view.hover = view.window_under(view.mouse)
            self.focused = view
            win.makeKeyAndOrderFront_(None)
            win.makeFirstResponder_(view)
        _activate()
        NSCursor.crosshairCursor().push()
        for view in self.views:
            view.setNeedsDisplay_(True)

    def focus(self, view):
        if self.focused is not view:
            previous = self.focused
            self.focused = view
            if previous is not None:
                previous.setNeedsDisplay_(True)
            win = view.window()
            if win is not None and not win.isKeyWindow():
                win.makeKeyWindow()
                win.makeFirstResponder_(view)

    def close(self):
        if self.closed:
            return
        self.closed = True
        board = self.annotating.board if self.annotating is not None else None
        if board is not None and self.on_prefs is not None:
            self.on_prefs(board.prefs())
        NSCursor.pop()
        for win in self.windows:
            win.orderOut_(None)
        self.windows = []
        self.views = []
        self.shots = []
        self.focused = self.annotating = None

    def finish(self, shot, rect, window, drawn=None, action="save"):
        if self.closed:
            return
        self.close()
        self.on_done(shot, rect, window, list(drawn or []), action)

    def view(self):
        return self.annotating or self.focused or (self.views[0] if self.views else None)

    def submit(self, rect=None):
        view = self.view()
        if view is None:
            return False
        if view.sel is not None:
            view.save("save")
            return True
        r = tuple(round(float(n)) for n in rect) if rect else (0, 0, round(view.shot["w"]), round(view.shot["h"]))
        self.finish(view.shot, r, None)
        return True

    def open_board(self, rect, drawn=None, tool=None):
        view = self.view()
        view.begin(rect, None)
        view.board.load(drawn or [])
        if tool:
            view.board.set_tool(tool)
        view.setNeedsDisplay_(True)
        return view.board.tools

    def snapshot(self, path):
        return snapshot_view(self.view(), path)

    def cancel(self):
        if self.closed:
            return
        self.close()
        self.on_cancel()


def _activate():
    try:
        NSApp.activateIgnoringOtherApps_(True)
    except Exception:
        try:
            NSApp.activate()
        except Exception:
            pass


class BorderView(NSView):
    def drawRect_(self, dirty):
        b = self.bounds()
        path = NSBezierPath.bezierPathWithRect_(NSMakeRect(1.5, 1.5, b.size.width - 3, b.size.height - 3))
        path.setLineWidth_(2.0)
        path.setLineDash_count_phase_([8.0, 5.0], 2, 0.0)
        rgba(230, 57, 70, 0.95).set()
        path.stroke()


class ControlView(NSView):
    def isFlipped(self):
        return True

    def acceptsFirstMouse_(self, event):
        return True

    @objc.python_method
    def setup(self, on_stop, on_cancel, on_draw):
        self.on_stop = on_stop
        self.on_cancel = on_cancel
        self.on_draw = on_draw
        self.drawing = False
        self.seconds = 0.0
        self.blink = True

    @objc.python_method
    def buttons(self):
        b = self.bounds()
        stop = NSMakeRect(b.size.width - 132, 7, 62, 24)
        cancel = NSMakeRect(b.size.width - 64, 7, 56, 24)
        draw = NSMakeRect(b.size.width - 202, 7, 64, 24)
        return stop, cancel, draw

    def drawRect_(self, dirty):
        b = self.bounds()
        _pill(b, rgba(*SUMI, 0.94), 10)
        if self.blink:
            rgba(230, 57, 70).set()
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(14, b.size.height / 2 - 5, 10, 10)).fill()
        s = int(self.seconds)
        _text(f"{s // 60:02d}:{s % 60:02d}", 14, PAPER, 0.5, mono=True).drawAtPoint_(NSMakePoint(32, 9))
        stop, cancel, draw = self.buttons()
        _pill(draw, rgba(*PAPER) if self.drawing else rgba(255, 255, 255, 0.12), 6)
        ink = SUMI if self.drawing else PAPER
        draw_symbol("pencil", NSMakeRect(draw.origin.x + 4, draw.origin.y, 20, 24), ink, 1.0, 12)
        _text("Draw", 12, ink, 0.5).drawAtPoint_(NSMakePoint(draw.origin.x + 25, draw.origin.y + 4))
        _pill(stop, rgba(*SHU), 6)
        _text("Stop", 12, PAPER, 0.5).drawAtPoint_(NSMakePoint(stop.origin.x + 17, stop.origin.y + 4))
        _pill(cancel, rgba(255, 255, 255, 0.12), 6)
        _text("Cancel", 12, PAPER, 0.4).drawAtPoint_(NSMakePoint(cancel.origin.x + 9, cancel.origin.y + 4))

    def mouseDown_(self, event):
        p = _point(self, event)
        stop, cancel, draw = self.buttons()
        if _inside(stop, p):
            self.on_stop()
        elif _inside(cancel, p):
            self.on_cancel()
        elif _inside(draw, p) and self.on_draw is not None:
            self.on_draw()


class DrawView(NSView):
    def isFlipped(self):
        return True

    def acceptsFirstResponder(self):
        return True

    def acceptsFirstMouse_(self, event):
        return True

    @objc.python_method
    def setup(self, layer):
        self.layer = layer

    def resetCursorRects(self):
        if self.layer.active:
            cursor = NSCursor.IBeamCursor() if self.layer.board.tool == "text" else None
            self.addCursorRect_cursor_(self.bounds(), cursor or NSCursor.crosshairCursor())

    def mouseDown_(self, event):
        self.layer.board.press(_point(self, event))
        self.layer.changed()

    def mouseDragged_(self, event):
        self.layer.board.drag(_point(self, event), _shift(event))
        self.setNeedsDisplay_(True)

    def mouseUp_(self, event):
        self.layer.board.release(_point(self, event), _shift(event))
        self.layer.changed()

    def rightMouseDown_(self, event):
        self.layer.on_escape()

    def keyDown_(self, event):
        board = self.layer.board
        if board_key(board, event):
            self.layer.changed()
            return
        code = int(event.keyCode())
        if code == ESC:
            self.layer.on_escape()
        elif code in (DELETE, FWD_DELETE):
            board.clear()
            self.layer.changed()

    def drawRect_(self, dirty):
        board = self.layer.board
        marks.paint(board.shown(), self.bounds(), None, 1.0, backdrop=False)
        if board.editing is not None:
            draw_board_focus(board)


class DrawLayer:
    def __init__(self, frame, prefs, fade, on_escape):
        self.board = marks.Board(LIVE_TOOLS, prefs)
        self.active = False
        self.fade = bool(fade)
        self.on_escape = on_escape
        self.timer = None
        self.window = _key_window(frame, LEVEL_DRAW, False)
        self.window.setIgnoresMouseEvents_(True)
        self.view = DrawView.alloc().initWithFrame_(NSMakeRect(0, 0, frame.size.width, frame.size.height))
        self.view.setup(self)
        self.window.setContentView_(self.view)
        self.window.orderFrontRegardless()

    def on(self):
        self.active = True
        self.window.setIgnoresMouseEvents_(False)
        _activate()
        self.window.makeKeyAndOrderFront_(None)
        self.window.makeFirstResponder_(self.view)
        self.window.invalidateCursorRectsForView_(self.view)

    def off(self):
        self.board.finish_text()
        self.active = False
        self.window.setIgnoresMouseEvents_(True)
        self.changed()

    def set_fade(self, fade):
        self.fade = bool(fade)
        self.board.restart_fade(time.monotonic())
        self.changed()

    def changed(self):
        self.window.invalidateCursorRectsForView_(self.view)
        self.view.setNeedsDisplay_(True)
        self.schedule()

    def schedule(self):
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None
        due = self.board.next_fade(FADE_HOLD) if self.fade else None
        if due is not None:
            wait = max(1 / 30.0, due - time.monotonic())
            self.timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(wait, False, lambda t: self.tick())

    def tick(self):
        self.timer = None
        self.board.fade(time.monotonic(), FADE_HOLD, FADE_SPAN)
        self.view.setNeedsDisplay_(True)
        self.schedule()

    def close(self):
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None
        self.window.orderOut_(None)
        self.window.setContentView_(None)
        self.view = None


class StripView(NSView):
    def isFlipped(self):
        return True

    def acceptsFirstMouse_(self, event):
        return True

    @objc.python_method
    def setup(self, bar, on_action, on_top):
        self.bar = bar
        self.on_action = on_action
        self.on_top = on_top
        self.addTrackingArea_(NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(self.bounds(), TRACK_FLAGS, self, None))

    def mouseMoved_(self, event):
        if self.bar.set_hover(_point(self, event)):
            self.setNeedsDisplay_(True)

    def mouseExited_(self, event):
        self.bar.hover = None
        self.setNeedsDisplay_(True)

    def mouseDown_(self, event):
        item = self.bar.item_at(_point(self, event))
        if item is not None:
            self.on_action(self.bar.click(item))
            self.setNeedsDisplay_(True)

    def drawRect_(self, dirty):
        self.bar.layout(0, 0 if self.on_top else self.bounds().size.height - BAR_H)
        self.bar.draw()
        self.bar.draw_tip(self.bounds())


def _panel(frame, level=LEVEL_PANEL):
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        frame, BORDERLESS | NONACTIVATING, NSBackingStoreBuffered, False)
    panel.setReleasedWhenClosed_(False)
    panel.setLevel_(level)
    panel.setOpaque_(False)
    panel.setBackgroundColor_(NSColor.clearColor())
    panel.setHasShadow_(True)
    panel.setCollectionBehavior_(JOIN_ALL)
    panel.setHidesOnDeactivate_(False)
    try:
        panel.setSharingType_(0)
    except Exception:
        pass
    return panel


class RecordingChrome:
    current = None

    def __init__(self, shot, rect, on_stop, on_cancel, draw):
        RecordingChrome.current = self
        self.shot = shot
        self.rect = rect
        self.draw = draw
        self.layer = self.strip = self.front = None
        frame = shot["frame"]
        x, y, w, h = rect
        ns_x = frame.origin.x + x
        ns_y = frame.origin.y + frame.size.height - (y + h)
        self.area = NSMakeRect(ns_x, ns_y, w, h)
        margin = 4
        self.border = _panel(NSMakeRect(ns_x - margin, ns_y - margin, w + 2 * margin, h + 2 * margin), LEVEL_OVERLAY - 1)
        self.border.setIgnoresMouseEvents_(True)
        self.border.setHasShadow_(False)
        bview = BorderView.alloc().initWithFrame_(NSMakeRect(0, 0, w + 2 * margin, h + 2 * margin))
        self.border.setContentView_(bview)
        pw, ph = 306, 38
        px = ns_x + w / 2 - pw / 2
        py = ns_y - ph - 12
        if py < frame.origin.y + 6:
            py = ns_y + h + 12
        if py + ph > frame.origin.y + frame.size.height - 30:
            py = frame.origin.y + 70
        px = min(max(frame.origin.x + 8, px), frame.origin.x + frame.size.width - pw - 8)
        self.panel = _panel(NSMakeRect(px, py, pw, ph))
        self.view = ControlView.alloc().initWithFrame_(NSMakeRect(0, 0, pw, ph))
        self.view.setup(on_stop, on_cancel, self.toggle_draw)
        self.panel.setContentView_(self.view)
        full = w >= shot["w"] - 4 and h >= shot["h"] - 4
        if not full:
            self.border.orderFrontRegardless()
        self.panel.orderFrontRegardless()

    def ensure_layer(self):
        if self.layer is not None:
            return self.layer
        layer = self.layer = DrawLayer(self.area, self.draw["prefs"], self.draw["fade"], self.stop_draw)
        bar = ToolBar(layer.board, ("|", "fade", "undo", "clear", "|", "done"), {"fade": lambda: layer.fade})
        sw, sh = bar.width, BAR_H + 34
        pill = self.panel.frame()
        screen = self.shot["frame"]
        sx = min(max(screen.origin.x + 8, pill.origin.x + pill.size.width / 2 - sw / 2), screen.origin.x + screen.size.width - sw - 8)
        sy = pill.origin.y - sh - 8
        on_top = sy >= screen.origin.y + 4
        if not on_top:
            sy = pill.origin.y + pill.size.height + 8
        self.strip = _panel(NSMakeRect(sx, sy, sw, sh))
        self.strip.setHasShadow_(False)
        sview = StripView.alloc().initWithFrame_(NSMakeRect(0, 0, sw, sh))
        sview.setup(bar, self.strip_action, on_top)
        self.strip.setContentView_(sview)
        return layer

    def strip_action(self, action):
        layer = self.layer
        if action == "done":
            self.stop_draw()
            return
        if action == "fade":
            layer.set_fade(not layer.fade)
        layer.changed()
        if layer.active:
            layer.window.makeKeyWindow()

    def toggle_draw(self):
        if self.layer is not None and self.layer.active:
            self.stop_draw()
        else:
            self.start_draw()

    def start_draw(self):
        layer = self.ensure_layer()
        if layer.active:
            return
        self.front = front_app()
        layer.on()
        self.strip.orderFrontRegardless()
        self.set_drawing(True)

    def stop_draw(self):
        layer = self.layer
        if layer is None or not layer.active:
            return
        layer.off()
        self.strip.orderOut_(None)
        self.set_drawing(False)
        reactivate(self.front)
        self.front = None

    def set_drawing(self, active):
        self.view.drawing = active
        self.view.setNeedsDisplay_(True)
        self.draw["on_change"](active, self.layer.board.prefs(), self.layer.fade)

    def load_marks(self, drawn):
        layer = self.ensure_layer()
        layer.board.load(drawn, time.monotonic())
        layer.changed()

    def state(self):
        layer = self.layer
        return {"active": bool(layer and layer.active), "marks": len(layer.board.marks) if layer else 0,
                "fade": layer.fade if layer else bool(self.draw["fade"])}

    def snapshot(self, folder):
        views = {"pill": self.view, "strip": self.strip.contentView() if self.strip else None}
        return [snapshot_view(v, os.path.join(folder, f"draw-{k}.png")) for k, v in views.items() if v is not None]

    def tick(self, seconds):
        self.view.seconds = seconds
        self.view.blink = not self.view.blink
        self.view.setNeedsDisplay_(True)

    def close(self):
        self.stop_draw()
        if RecordingChrome.current is self:
            RecordingChrome.current = None
        if self.layer is not None:
            self.layer.close()
        for w in (self.border, self.panel, self.strip):
            if w is not None:
                w.orderOut_(None)
                w.setContentView_(None)
        self.layer = self.strip = None
        self.view.on_draw = None


class ToastView(NSView):
    def isFlipped(self):
        return True

    def acceptsFirstMouse_(self, event):
        return True

    @objc.python_method
    def setup(self, image, title, subtitle, on_click, error=False):
        self.image = image
        self.title = title
        self.subtitle = subtitle
        self.on_click = on_click
        self.error = error

    def drawRect_(self, dirty):
        b = self.bounds()
        _pill(b, rgba(*PAPER, 0.98), 12)
        border = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(0.5, 0.5, b.size.width - 1, b.size.height - 1), 12, 12)
        rgba(*SUMI, 0.12).set()
        border.setLineWidth_(1)
        border.stroke()
        if self.error:
            rgba(*SAND).set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(12, 12, 26, 26), 4, 4).fill()
            _text("!", 15, SUMI, 0.7).drawAtPoint_(NSMakePoint(22, 14))
            _text(self.title, 12.5, SUMI, 0.55).drawAtPoint_(NSMakePoint(46, 10))
            _text(self.subtitle, 11, (110, 118, 140), 0.3).drawInRect_(NSMakeRect(46, 29, b.size.width - 58, b.size.height - 37))
            return
        if self.image is not None:
            area = NSMakeRect(10, 10, b.size.width - 20, b.size.height - 58)
            iw, ih = self.image.size().width, self.image.size().height
            if iw > 0 and ih > 0:
                k = min(area.size.width / iw, area.size.height / ih)
                dw, dh = iw * k, ih * k
                dest = NSMakeRect(area.origin.x + (area.size.width - dw) / 2, area.origin.y + (area.size.height - dh) / 2, dw, dh)
                NSGraphicsContext.currentContext().saveGraphicsState()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(dest, 6, 6).addClip()
                self.image.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(dest, NSZeroRect, COMPOSITE_OVER, 1.0, True, None)
                NSGraphicsContext.currentContext().restoreGraphicsState()
        rgba(*SHU).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(12, b.size.height - 39, 26, 26), 4, 4).fill()
        _text("✓", 15, PAPER, 0.6).drawAtPoint_(NSMakePoint(18, b.size.height - 37))
        _text(self.title, 12.5, SUMI, 0.55).drawAtPoint_(NSMakePoint(46, b.size.height - 42))
        sub = self.subtitle if len(self.subtitle) < 44 else self.subtitle[:41] + "…"
        _text(sub, 11, (110, 118, 140), 0.3).drawAtPoint_(NSMakePoint(46, b.size.height - 25))

    def mouseDown_(self, event):
        if self.on_click:
            self.on_click()


class Toast:
    current = None

    def __init__(self, image_path, title, subtitle, on_click, seconds=5.0, screen=None, error=False):
        if Toast.current is not None:
            Toast.current.close()
        Toast.current = self
        image = NSImage.alloc().initWithContentsOfFile_(image_path) if image_path and os.path.exists(image_path) else None
        w = 300
        h = 220
        if image is not None and image.size().width > 0:
            ratio = image.size().height / image.size().width
            h = int(min(260, max(120, (w - 20) * ratio + 58)))
        if error:
            w, h = 320, 92
        screen = screen or NSScreen.mainScreen()
        vf = screen.visibleFrame()
        frame = NSMakeRect(vf.origin.x + vf.size.width - w - 18, vf.origin.y + 18, w, h)
        self.panel = _panel(frame, LEVEL_PANEL)
        view = ToastView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        view.setup(image, title, subtitle, self._clicked, error)
        self.on_click = on_click
        self.panel.setContentView_(view)
        self.panel.orderFrontRegardless()
        self.timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(seconds, False, lambda t: self.close())

    def _clicked(self):
        self.close()
        if self.on_click:
            self.on_click()

    def close(self):
        if self.panel is not None:
            self.panel.orderOut_(None)
            self.panel = None
        try:
            self.timer.invalidate()
        except Exception:
            pass
        if Toast.current is self:
            Toast.current = None
