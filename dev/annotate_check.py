from __future__ import annotations

import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import Quartz

import core
import marks
import overlay

SAMPLE = [
    {"kind": "rect", "color": [234, 72, 50], "size": 1, "pts": [[560, 140], [990, 210]]},
    {"kind": "arrow", "color": [234, 72, 50], "size": 1, "pts": [[300, 330], [520, 270]]},
    {"kind": "ellipse", "color": [40, 122, 236], "size": 1, "pts": [[1000, 30], [1150, 70]]},
    {"kind": "line", "color": [36, 168, 110], "size": 2, "pts": [[380, 560], [700, 560]]},
    {"kind": "pen", "color": [234, 72, 50], "size": 1, "pts": [[300, 620], [320, 600], [350, 640], [380, 600], [410, 640], [440, 610]]},
    {"kind": "marker", "color": [255, 197, 36], "size": 1, "pts": [[410, 450], [960, 450]]},
    {"kind": "text", "color": [234, 72, 50], "size": 1, "pts": [[70, 420]], "text": "Look here"},
    {"kind": "text", "color": [40, 122, 236], "size": 1, "pts": [[70, 480]], "text": "Two lines\nof label", "fill": True},
    {"kind": "step", "color": [234, 72, 50], "size": 1, "pts": [[540, 120]], "n": 1},
    {"kind": "step", "color": [40, 122, 236], "size": 1, "pts": [[200, 220], [260, 160]], "n": 2},
    {"kind": "blur", "color": [234, 72, 50], "size": 1, "pts": [[480, 290], [980, 340]]},
    {"kind": "pixelate", "color": [234, 72, 50], "size": 1, "pts": [[480, 360], [980, 410]]},
    {"kind": "rect", "color": [31, 37, 56], "size": 1, "pts": [[1000, 120], [1100, 150]], "fill": True},
]


def pixels(cg):
    w, h = Quartz.CGImageGetWidth(cg), Quartz.CGImageGetHeight(cg)
    space = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)
    ctx = Quartz.CGBitmapContextCreate(None, w, h, 8, w * 4, space, Quartz.kCGImageAlphaPremultipliedLast)
    Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, w, h), cg)
    data = Quartz.CGBitmapContextGetData(ctx)
    buf = bytes(data.as_buffer(w * h * 4))
    return w, h, buf


def px(img, x, y):
    w, _h, buf = img
    i = (int(y) * w + int(x)) * 4
    return tuple(buf[i:i + 3])


def spread(img, x0, y0, x1, y1):
    vals = [sum(px(img, x, y)) for x in range(int(x0), int(x1), 3) for y in range(int(y0), int(y1), 3)]
    return statistics.pstdev(vals)


def check_render(base_path, out_dir):
    cg = overlay.load_cgimage(base_path)
    size = (Quartz.CGImageGetWidth(cg) / 2.0, Quartz.CGImageGetHeight(cg) / 2.0)
    rect = (40, 20, 1140, 660)
    t = time.time()
    effects = marks.Effects(cg, size)
    out = marks.render(cg, size, rect, SAMPLE, effects)
    took = time.time() - t
    core.write_cgimage(out, os.path.join(out_dir, "render.png"))
    plain = marks.render(cg, size, rect, [], effects)
    core.write_cgimage(plain, os.path.join(out_dir, "plain.png"))
    a, b = pixels(out), pixels(plain)
    k = 2.0

    def at(x, y):
        return ((x - rect[0]) * k, (y - rect[1]) * k)

    results = {
        "size": (Quartz.CGImageGetWidth(out), Quartz.CGImageGetHeight(out)) == (rect[2] * 2, rect[3] * 2),
        "plain_is_crop": px(b, *at(600, 250)) == px(pixels(Quartz.CGImageCreateWithImageInRect(cg, Quartz.CGRectMake(rect[0] * 2, rect[1] * 2, rect[2] * 2, rect[3] * 2))), *at(600, 250)),
        "rect_edge_red": px(a, *at(700, 140)),
        "arrow_tip_red": px(a, *at(515, 271.5)),
        "blur_smoother": (spread(a, *at(500, 300), *at(960, 330)), spread(b, *at(500, 300), *at(960, 330))),
        "pixel_blocks": px(a, *at(600, 370)) == px(a, *at(602, 372)),
        "marker_multiply": (px(a, *at(700, 450)), px(b, *at(700, 450))),
        "seconds": round(took, 3),
    }
    return results


def check_board():
    b = marks.Board(overlay.SHOT_TOOLS, {"tool": "arrow", "color": 0, "size": 1})
    b.press((10, 10))
    b.drag((60, 60))
    assert b.release((100, 100))
    assert len(b.marks) == 1 and b.marks[0]["pts"][-1] == [100.0, 100.0]
    b.set_tool("step")
    b.press((200, 200)); b.release((200, 200))
    b.press((300, 300)); b.release((300, 300))
    assert [m["n"] for m in b.marks if m["kind"] == "step"] == [1, 2]
    b.set_tool("text")
    b.press((50, 300)); b.type_text("Hi"); b.backspace(); b.type_text("ello")
    b.set_tool("rect")
    assert b.marks[-1]["text"] == "Hello" and b.editing is None
    b.set_tool("text")
    b.press((400, 400)); b.finish_text()
    assert len(b.marks) == 4
    assert b.undo() and len(b.marks) == 3
    assert b.redo() and len(b.marks) == 4
    b.set_tool("move")
    assert b.press((55, 55))
    b.drag((65, 75)); b.release((65, 75))
    assert b.marks[0]["pts"][0] == [20.0, 30.0]
    b.set_color(3)
    assert b.marks[0]["color"] == list(marks.COLORS[3][1])
    assert b.delete_selected() and len(b.marks) == 3
    b.set_tool("rect")
    b.press((0, 0)); assert not b.release((1, 1))
    b.press((0, 0)); b.release((50, 20), shift=True)
    assert b.marks[-1]["pts"][-1] == [50.0, 50.0]
    live = marks.Board(overlay.LIVE_TOOLS, {"tool": "pen"})
    live.press((0, 0)); live.drag((5, 5)); live.release((10, 10))
    t0 = live.marks[0]["t"]
    assert live.fade(t0 + 1.0, 2.0, 0.6) and live.marks[0]["alpha"] == 1.0
    live.fade(t0 + 2.3, 2.0, 0.6)
    assert 0.0 < live.marks[0]["alpha"] < 1.0
    assert not live.fade(t0 + 3.0, 2.0, 0.6) and not live.marks

    live.set_tool("arrow")
    live.press((0, 0)); live.release((50, 50))
    t1 = live.marks[0]["t"]
    live.set_tool("text")
    live.press((200, 200)); live.type_text("hel")
    assert live.next_fade(2.0) == t1 + 2.0
    live.fade(t1 + 3.0, 2.0, 0.6)
    assert live.editing == 0 and live.marks[0]["text"] == "hel" and live.next_fade(2.0) is None
    live.type_text("lo"); live.finish_text()
    assert live.marks[0]["text"] == "hello" and live.next_fade(2.0) is not None

    u = marks.Board(overlay.SHOT_TOOLS, {"tool": "arrow"})
    u.press((0, 0)); u.release((50, 50))
    u.set_tool("text"); u.press((200, 200))
    assert u.undo() and [m["kind"] for m in u.marks] == ["arrow"]
    u.set_tool("rect"); u.press((100, 100)); u.release((150, 150))
    assert u.undo() and len(u.undone) == 1
    u.set_tool("move"); u.press((25, 25)); u.release((25, 25))
    assert u.selected == 0 and len(u.undone) == 1 and u.redo() and len(u.marks) == 2
    u.set_tool("text"); u.press((300, 300)); u.type_text("abc"); u.finish_text()
    n = len(u.done)
    u.press((305, 300)); u.finish_text()
    assert len(u.done) == n and u.marks[-1]["text"] == "abc"
    u.press((305, 300)); u.type_text("d"); u.finish_text()
    assert len(u.done) == n + 1 and u.undo() and u.marks[-1]["text"] == "abc"
    u.press((305, 300)); u.set_color(3); u.finish_text()
    assert len(u.done) == n + 1 and u.marks[-1]["color"] == list(marks.COLORS[3][1])
    assert all(snap is not None for snap in u.done)
    return "ok"


def check_backdrop():
    from AppKit import NSGraphicsContext
    from Foundation import NSMakeRect

    space = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)
    ctx = Quartz.CGBitmapContextCreate(None, 100, 100, 8, 400, space, Quartz.kCGImageAlphaPremultipliedLast)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithCGContext_flipped_(ctx, False))
    stroke = {"kind": "marker", "color": [255, 197, 36], "size": 1, "pts": [[10, 50], [90, 50]]}
    spot = {"kind": "spotlight", "color": [0, 0, 0], "size": 1, "pts": [[200, 200], [260, 260]]}
    marks.paint([stroke, spot], NSMakeRect(0, 0, 100, 100), None, 1.0, backdrop=False)
    NSGraphicsContext.restoreGraphicsState()
    buf = bytes(Quartz.CGBitmapContextGetData(ctx).as_buffer(100 * 100 * 4))
    marker_alpha = buf[(49 * 100 + 50) * 4 + 3]
    corner_alpha = buf[(5 * 100 + 5) * 4 + 3]
    assert 80 <= marker_alpha <= 130 and corner_alpha == 0, (marker_alpha, corner_alpha)
    return "ok"


class Driver:
    def __init__(self, base_path, annotate=True):
        from AppKit import NSApplication, NSImage
        from Foundation import NSMakeRect, NSMakeSize

        NSApplication.sharedApplication()
        cg = overlay.load_cgimage(base_path)
        w, h = Quartz.CGImageGetWidth(cg) / 2.0, Quartz.CGImageGetHeight(cg) / 2.0
        self.h = h
        self.shot = {"display": 1, "cgimage": cg, "nsimage": NSImage.alloc().initWithCGImage_size_(cg, NSMakeSize(w, h)),
                     "scale": 2.0, "w": w, "h": h, "frame": NSMakeRect(0, 0, w, h), "cg_x": 0.0, "cg_y": 0.0,
                     "windows": [{"x": 40.0, "y": 30.0, "w": 600.0, "h": 400.0, "app": "Chrome", "title": "Tasks", "pid": 1, "layer": 0}]}
        self.done = []
        self.prefs = []
        self.sel = overlay.RegionSelector("image", [self.shot], lambda *a: self.done.append(a), lambda: self.done.append("cancel"),
                                          snap=True, magnifier=True, annotate=annotate, prefs={"tool": "arrow"},
                                          on_prefs=self.prefs.append)
        self.win = overlay._key_window(NSMakeRect(-6000, -6000, w, h), overlay.LEVEL_OVERLAY, True)
        self.view = overlay.SelectionView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        self.view.setup(self.sel, self.shot)
        self.win.setContentView_(self.view)
        self.sel.windows, self.sel.views, self.sel.focused = [self.win], [self.view], self.view

    def mouse(self, kind, x, y, flags=0, clicks=1):
        from AppKit import NSEvent

        types = {"down": 1, "up": 2, "moved": 5, "drag": 6}
        ev = NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
            types[kind], (x, self.h - y), flags, 0, self.win.windowNumber(), None, 0, clicks, 1.0)
        {"down": self.view.mouseDown_, "up": self.view.mouseUp_, "moved": self.view.mouseMoved_, "drag": self.view.mouseDragged_}[kind](ev)

    def drag(self, a, b, flags=0, steps=6):
        self.mouse("down", *a, flags)
        for i in range(1, steps + 1):
            self.mouse("drag", a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps, flags)
        self.mouse("up", *b, flags)

    def key(self, chars, code=0, flags=0):
        from AppKit import NSEvent

        ev = NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
            10, (0, 0), flags, 0, self.win.windowNumber(), None, chars, chars, False, code)
        self.view.keyDown_(ev)


def check_overlay(base_path, out_dir):
    d = Driver(base_path)
    d.drag((300, 180), (1180, 700))
    v = d.view
    out = {"annotating": v.sel == [300.0, 180.0, 880.0, 520.0] and d.sel.annotating is v and not d.done}
    d.drag((420, 330), (640, 260))
    d.key("r", 15)
    d.drag((660, 230), (1080, 300))
    d.key("c", 8)
    d.mouse("down", 650, 225); d.mouse("up", 650, 225)
    d.key("t", 17)
    d.mouse("down", 340, 470); d.mouse("up", 340, 470)
    d.key("Click Send"); d.key("\r", overlay.RETURN)
    d.key("p", 35)
    d.drag((620, 310), (1080, 360))
    d.key("m", 46)
    d.drag((640, 570), (1100, 570), flags=overlay.SHIFT)
    d.key("2", 19)
    d.key("t", 17)
    d.mouse("down", 340, 560); d.mouse("up", 340, 560)
    d.key("Second")
    assert v.board.editing is not None
    d.sel.snapshot(os.path.join(out_dir, "overlay_text.png"))
    d.key("\x1b", overlay.ESC)
    assert [m["kind"] for m in v.board.marks] == ["arrow", "rect", "step", "text", "pixelate", "marker", "text"]
    assert [m.get("text") for m in v.board.marks if m["kind"] == "text"] == ["Click Send", "Second"]
    assert v.board.marks[5]["pts"][0][1] == v.board.marks[5]["pts"][-1][1] and len(v.board.marks[5]["pts"]) == 2
    d.key("z", 6, overlay.CMD)
    assert len(v.board.marks) == 6
    d.key("z", 6, overlay.CMD | overlay.SHIFT)
    assert len(v.board.marks) == 7
    d.mouse("moved", v.bar.items[3][2].origin.x + 5, v.bar.items[3][2].origin.y + 5)
    assert v.bar.tip() == "Arrow   A"
    d.sel.snapshot(os.path.join(out_dir, "overlay.png"))
    d.drag((1180, 700), (1240, 760))
    assert v.sel == [300.0, 180.0, 940.0, 580.0]
    d.key("v", 9)
    d.drag((900, 230), (920, 250))
    assert v.board.marks[1]["pts"][0] == [680.0, 250.0] and v.board.selected == 1
    d.key("\x7f", overlay.DELETE)
    assert len(v.board.marks) == 6 and all(m["kind"] != "rect" for m in v.board.marks)
    d.drag((700, 650), (720, 640))
    assert v.sel == [320.0, 170.0, 940.0, 580.0]
    d.key("", 124, overlay.SHIFT)
    assert v.sel[0] == 330.0
    d.key("\r", overlay.RETURN)
    assert [(a[1], len(a[3]), a[4]) for a in d.done] == [((330, 170, 940, 580), 6, "save")]
    assert d.prefs == [{"tool": "move", "color": 1, "size": 1, "fill": False}]
    shot, rect, _win, drawn, _action = d.done[0]
    core.write_cgimage(marks.render(shot["cgimage"], (shot["w"], shot["h"]), rect, drawn), os.path.join(out_dir, "overlay_saved.png"))
    out["overlay_flow"] = "ok"

    q = Driver(base_path, annotate=False)
    q.drag((100, 100), (300, 250))
    assert [(a[1], a[3], a[4]) for a in q.done] == [((100, 100, 200, 150), [], "save")]

    s = Driver(base_path)
    s.mouse("moved", 200, 200)
    s.mouse("down", 200, 200); s.mouse("up", 200, 200)
    assert s.view.sel == [40.0, 30.0, 600.0, 400.0] and s.view.picked["app"] == "Chrome"
    s.key("a", 0)
    s.drag((100, 100), (200, 150))
    s.mouse("down", 1300, 900); s.mouse("up", 1300, 900)
    assert s.view.sel == [40.0, 30.0, 600.0, 400.0] and len(s.view.board.marks) == 1
    s.drag((900, 600), (1200, 800))
    assert s.view.sel == [900.0, 600.0, 300.0, 200.0] and len(s.view.board.marks) == 1
    s.key("\x1b", overlay.ESC)
    assert s.done == ["cancel"]

    e = Driver(base_path)
    e.key(" ", overlay.SPACE)
    e.drag((0, 491), (1512, 491))
    assert e.view.sel[0] + e.view.sel[2] <= 1512.0 and e.view.sel[2] >= 4.0, e.view.sel
    e.drag(tuple(e.view.handle_points()["s"]), (700, 990))
    assert e.view.sel[1] + e.view.sel[3] <= 982.0, e.view.sel
    assert e.view.sel[0] + e.view.sel[2] <= 1512.0
    e.key("t", 17)
    e.mouse("down", 1000, 500); e.mouse("up", 1000, 500)
    e.key("a"); e.key("\r", overlay.RETURN)
    e.key("a", 0)
    e.drag((800, 200), (900, 300))
    e.key("z", 6, overlay.CMD)
    assert [m["kind"] for m in e.view.board.marks] == ["text"]
    e.key("t", 17)
    e.mouse("down", 1005, 510); e.mouse("up", 1005, 510)
    assert e.view.board.editing == 0
    e.key("z", 6, overlay.CMD | overlay.SHIFT)
    assert [m["kind"] for m in e.view.board.marks] == ["text", "arrow"] and e.view.board.editing is None

    f = Driver(base_path)
    f.key(" ", overlay.SPACE)
    assert f.view.sel == [0.0, 0.0, 1512.0, 982.0] and not f.done
    f.key("h", 4)
    f.drag((600, 400), (900, 600))
    f.key("b", 11)
    f.drag((100, 100), (400, 200))
    f.key("\r", overlay.RETURN)
    assert [m["kind"] for m in f.done[0][3]] == ["spotlight", "blur"]
    out["edge_flows"] = "ok"
    return out


if __name__ == "__main__":
    base = sys.argv[1]
    out_dir = sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    print("board", check_board())
    print("backdrop", check_backdrop())
    for k, v in check_render(base, out_dir).items():
        print(k, v)
    for k, v in check_overlay(base, out_dir).items():
        print(k, v)
