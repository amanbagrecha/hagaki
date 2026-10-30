from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import objc
import Quartz
from AppKit import (
    NSApplication, NSEvent, NSImage, NSMenu, NSMenuItem, NSOpenPanel, NSSound, NSStatusBar, NSWorkspace,
)
from Foundation import NSURL, NSMakeSize, NSObject, NSTimer
from PyObjCTools import AppHelper

import core
import gif
import hotkeys
import marks
import overlay
import reel
import syskeys

STATUS_FILE = os.path.join(core.DATA_DIR, "daemon_status.json")
PENDING_FILE = os.path.join(core.DATA_DIR, "pending_recording.json")
PRIVACY_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
BLOCKED_HINTS = ("TCC", "declined")
SHUTTER = "/System/Library/Components/CoreAudio.component/Contents/SharedSupport/SystemSounds/system/Screen Capture.aif"
MENU_MODS = {"cmd": 1 << 20, "shift": 1 << 17, "alt": 1 << 19, "ctrl": 1 << 18}
MENU_KEYS = {"space": " ", "enter": "\r", "return": "\r", "tab": "\t", "escape": "\x1b", "comma": ",", "period": ".",
             "slash": "/", "minus": "-", "equal": "=", "semicolon": ";", "quote": "'", "backquote": "`",
             "bracketleft": "[", "bracketright": "]", "backslash": "\\"}
ACTIONS = {
    "region": "Capture Area",
    "window": "Capture Window",
    "screen": "Capture Screen",
    "record": "Record Area",
    "gif": "Record GIF",
    "open": "Open Hagaki",
}

TOKEN = secrets.token_urlsafe(24)
APP = None


def run_on_main(fn, *args, timeout=600):
    done = threading.Event()
    box = {}

    def wrapper():
        try:
            box["value"] = fn(*args)
        except Exception as exc:
            box["error"] = exc
            traceback.print_exc()
        finally:
            done.set()

    AppHelper.callAfter(wrapper)
    if not done.wait(timeout):
        raise TimeoutError("the main thread did not answer")
    if "error" in box:
        raise box["error"]
    return box.get("value")


class AppDelegate(NSObject):
    def captureRegion_(self, sender):
        APP.trigger("region")

    def captureWindow_(self, sender):
        APP.trigger("window")

    def captureScreen_(self, sender):
        APP.trigger("screen")

    def recordRegion_(self, sender):
        APP.trigger("record")

    def recordGif_(self, sender):
        APP.trigger("gif")

    def openApp_(self, sender):
        APP.open_app()

    def openFolder_(self, sender):
        subprocess.Popen(["open", core.library_dir()])

    def togglePause_(self, sender):
        APP.set_paused(not APP.paused)

    def quitApp_(self, sender):
        APP.quit_from_menu()

    def heartbeat_(self, timer):
        APP.heartbeat()

    def recordTick_(self, timer):
        APP.record_tick()


class Hagaki:
    def __init__(self, version: str):
        self.version = version
        self.settings = core.load_settings()
        self.keys = hotkeys.HotKeySet()
        self.bound: dict[str, list[str]] = {}
        self.errors: list[dict] = []
        self.selector = None
        self.freeze_error = None
        self.busy = False
        self.busy_since = 0.0
        self.recording = None
        self.record_timer = None
        self.agent_tracks = {}
        self.paused = False
        self.pool = ThreadPoolExecutor(max_workers=3)
        self.ctl = ThreadPoolExecutor(max_workers=2)
        self.grab = ThreadPoolExecutor(max_workers=1)
        self.starting = False
        self.started = time.time()
        self.prev_app = None
        self.last_capture = None
        self.captures = 0
        self.quitting = False
        self.beats = 0

    def boot(self):
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(1)
        self.delegate = AppDelegate.alloc().init()
        app.setDelegate_(self.delegate)
        self.build_status_item()
        if self.settings.get("override_system"):
            try:
                if not syskeys.status()["all_disabled"]:
                    syskeys.disable(core.SYSKEYS_BACKUP)
                    core.log("info", "settings", "macOS screenshot shortcuts handed to Hagaki")
            except Exception as exc:
                core.log("error", "settings", f"Could not take over the macOS shortcuts: {exc}")
        self.bind_hotkeys()
        self.pool.submit(self.finish_pending_recording)
        core.spawn_job("ocr_pending")
        self.heartbeat_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self.delegate, "heartbeat:", None, True)
        core.log("info", "daemon", f"Hagaki started (pid {os.getpid()})")
        self.write_status()

    def heartbeat(self):
        self.beats += 1
        if self.busy and self.selector is None and self.recording is None and not self.starting and time.time() - self.busy_since > 20:
            self.busy = False
        if self.beats % 30 == 0:
            self.write_status(touch=False)

    def write_status(self, touch=True):
        data = {
            "pid": os.getpid(),
            "version": self.version,
            "started": self.started,
            "updated": time.time(),
            "paused": self.paused,
            "bound": {k: [hotkeys.display(s) for s in v] for k, v in self.bound.items()},
            "bound_specs": self.bound,
            "errors": self.errors,
            "recording": None if not self.recording else {
                "id": self.recording["id"], "started": self.recording["started"], "path": self.recording["path"],
                "gif": self.recording["gif"]},
            "override_system": bool(self.settings.get("override_system")),
            "captures": self.captures,
            "last_capture": self.last_capture,
            "menubar": self.menubar_state(),
        }
        try:
            core.ensure_dirs()
            tmp = STATUS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, STATUS_FILE)
            if touch:
                core.touch()
        except OSError:
            pass
        return data

    def menubar_state(self):
        try:
            win = self.status_item.button().window()
            frame = win.frame()
            return {"visible": bool(win.isVisible()), "on_screen": bool(win.occlusionState() & 2),
                    "x": float(frame.origin.x), "width": float(frame.size.width)}
        except Exception as exc:
            return {"error": str(exc)}

    def build_status_item(self):
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(-1)
        self.icon_idle = NSImage.imageWithSystemSymbolName_accessibilityDescription_("camera.viewfinder", "Hagaki")
        self.icon_rec = NSImage.imageWithSystemSymbolName_accessibilityDescription_("record.circle", "Recording")
        for img in (self.icon_idle, self.icon_rec):
            if img is not None:
                img.setTemplate_(True)
        self.status_item.button().setImage_(self.icon_idle)
        self.status_item.button().setToolTip_("Hagaki screenshots")
        self.menu = NSMenu.alloc().init()
        self.menu.setAutoenablesItems_(False)
        self.status_item.setMenu_(self.menu)
        self.rebuild_menu()

    def _menu_item(self, title, action, spec=None):
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
        item.setTarget_(self.delegate)
        if spec:
            try:
                _kc, _flags, names, key = hotkeys.parse_spec(spec)
                char = MENU_KEYS.get(key, key if len(key) == 1 else "")
                if char:
                    item.setKeyEquivalent_(char)
                    mask = 0
                    for n in names:
                        mask |= MENU_MODS[n]
                    item.setKeyEquivalentModifierMask_(mask)
            except hotkeys.SpecError:
                pass
        self.menu.addItem_(item)
        return item

    def rebuild_menu(self):
        self.menu.removeAllItems()
        first = lambda a: (self.bound.get(a) or [None])[0]
        self._menu_item("Capture Area", "captureRegion:", first("region"))
        self._menu_item("Capture Window", "captureWindow:", first("window"))
        self._menu_item("Capture Screen", "captureScreen:", first("screen"))
        if self.recording:
            self._menu_item("Stop Recording", "recordRegion:", first("record"))
        else:
            self._menu_item("Record Area", "recordRegion:", first("record"))
            self._menu_item("Record GIF", "recordGif:", first("gif"))
        self.menu.addItem_(NSMenuItem.separatorItem())
        self._menu_item("Open Hagaki", "openApp:", first("open"))
        self._menu_item("Open Screenshots Folder", "openFolder:")
        self.menu.addItem_(NSMenuItem.separatorItem())
        pause = self._menu_item("Pause Shortcuts", "togglePause:")
        pause.setState_(1 if self.paused else 0)
        self.menu.addItem_(NSMenuItem.separatorItem())
        self._menu_item("Quit Hagaki", "quitApp:")

    def bind_hotkeys(self):
        self.settings = core.load_settings()
        self.keys.clear()
        self.bound = {}
        self.errors = []
        if not self.paused:
            pairs = list((self.settings.get("hotkeys") or {}).items())
            if self.settings.get("override_system"):
                pairs += list(syskeys.REPLACEMENTS.items())
            for action, spec in pairs:
                if not spec or action not in ACTIONS:
                    continue
                try:
                    canon = hotkeys.canonical(spec)
                    if canon in sum(self.bound.values(), []):
                        if not (self.settings.get("override_system") and spec in syskeys.REPLACEMENTS.values()):
                            self.errors.append({"action": action, "spec": spec, "error": "another Hagaki action uses it"})
                            core.log("warn", "hotkey", f"Shortcut {hotkeys.display(spec)} for {ACTIONS[action]} is already used by another action")
                        continue
                    self.keys.bind(canon, lambda a=action: self.trigger(a))
                    self.bound.setdefault(action, []).append(canon)
                except (hotkeys.SpecError, OSError) as exc:
                    self.errors.append({"action": action, "spec": spec, "error": str(exc)})
                    core.log("warn", "hotkey", f"Shortcut {hotkeys.display(spec)} for {ACTIONS[action]} is not available: {exc}")
        self.rebuild_menu()
        return self.write_status()

    def set_paused(self, paused):
        self.paused = bool(paused)
        core.log("info", "hotkey", "Shortcuts paused" if self.paused else "Shortcuts active again")
        return self.bind_hotkeys()

    def trigger(self, action):
        if self.quitting:
            return
        if action == "open":
            self.open_app()
            return
        if action in ("record", "gif") and self.recording:
            self.stop_recording()
            return
        if self.selector is not None or self.busy or self.starting:
            return
        self.settings = core.load_settings()
        delay = float((self.settings.get("capture") or {}).get("delay") or 0)
        start = {"region": lambda: self.start_region("image"), "record": lambda: self.start_region("record"),
                 "gif": lambda: self.start_region("gif"), "screen": self.capture_screen,
                 "window": self.capture_window}.get(action)
        if start is None:
            return
        self.busy = True
        self.busy_since = time.time()
        if delay > 0:
            NSTimer.scheduledTimerWithTimeInterval_repeats_block_(delay, False, lambda t: self._safe(start))
        else:
            self._safe(start)

    def _safe(self, fn):
        try:
            fn()
        except Exception as exc:
            traceback.print_exc()
            self._capture_failed(exc)

    def _capture_failed(self, exc):
        self.busy = False
        self.selector = None
        core.log("error", "capture", f"Capture failed: {exc}")
        self.show_error("Capture failed", str(exc))

    def _off_main(self, work, done):
        def run():
            try:
                result = work()
            except Exception as exc:
                traceback.print_exc()
                AppHelper.callAfter(self._capture_failed, exc)
                return
            AppHelper.callAfter(self._safe, lambda: done(result))

        self.grab.submit(run)

    def _grab(self, body):
        core.server_call("/api/capture/screenshot", body, timeout=20)
        return overlay.load_cgimage(body["path"])

    def remember_front(self):
        self.prev_app = overlay.front_app()

    def restore_front(self):
        app, self.prev_app = self.prev_app, None
        overlay.reactivate(app)

    def freeze(self, screens):
        shots = []
        self.freeze_error = None
        for s in screens:
            path = os.path.join(core.WORK_DIR, f"freeze-{s['display']}.png")
            try:
                core.server_call("/api/capture/screenshot", {"display": s["display"], "path": path}, timeout=20)
            except Exception as exc:
                self.freeze_error = exc
                core.log("error", "capture", f"Display {s['display']} could not be captured: {exc}")
                continue
            cg = overlay.load_cgimage(path)
            if cg is None:
                continue
            scale = Quartz.CGImageGetWidth(cg) / s["w"]
            shot = dict(s)
            shot.update({"path": path, "cgimage": cg, "scale": scale,
                         "nsimage": NSImage.alloc().initWithCGImage_size_(cg, NSMakeSize(s["w"], s["h"]))})
            shots.append(shot)
        return shots

    def local_windows(self, windows, shot):
        out = []
        for win in windows:
            x, y = win["x"] - shot["cg_x"], win["y"] - shot["cg_y"]
            if x + win["w"] <= 0 or y + win["h"] <= 0 or x >= shot["w"] or y >= shot["h"]:
                continue
            local = dict(win)
            local.update({"x": x, "y": y})
            out.append(local)
        return out

    def start_region(self, mode):
        self.remember_front()
        windows = core.window_list(exclude_pid=os.getpid())
        screens = overlay.screens_info()
        mouse = NSEvent.mouseLocation()
        screens.sort(key=lambda s: 0 if (s["frame"].origin.x <= mouse.x <= s["frame"].origin.x + s["frame"].size.width
                                          and s["frame"].origin.y <= mouse.y <= s["frame"].origin.y + s["frame"].size.height) else 1)
        self._off_main(lambda: self.freeze(screens), lambda shots: self.show_selector(mode, shots, windows))

    def show_selector(self, mode, shots, windows):
        if not shots:
            self.busy = False
            core.log("error", "capture", "No display could be captured. Check the Screen Recording permission for FusedRender.")
            self.show_error("Capture failed", str(self.freeze_error or "No display could be captured."))
            return
        for shot in shots:
            shot["windows"] = self.local_windows(windows, shot)
        cap = self.settings.get("capture") or {}
        self.selector = overlay.RegionSelector(
            "record" if mode == "gif" else mode, shots,
            lambda shot, rect, win, drawn, action: self._safe(lambda: self.region_done(mode, shot, rect, win, drawn, action)),
            self.region_cancelled,
            snap=bool(cap.get("snap_windows", True)), magnifier=bool(cap.get("magnifier", True)),
            annotate=bool(cap.get("annotate", True)), prefs=self.settings.get("annotate"),
            on_prefs=lambda prefs: self.keep_prefs({"annotate": prefs}))
        self.selector.show()

    def region_cancelled(self):
        self.selector = None
        self.busy = False
        self.restore_front()

    def window_info(self, shot, rect, win=None):
        if win is None:
            cx, cy = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
            for w in shot.get("windows") or []:
                if w["x"] <= cx <= w["x"] + w["w"] and w["y"] <= cy <= w["y"] + w["h"]:
                    win = w
                    break
        if win is None:
            front = core.front_app()
            prev = self.prev_app
            if prev is not None and int(prev.processIdentifier()) != os.getpid():
                front = {"app": str(prev.localizedName() or ""), "bundle": str(prev.bundleIdentifier() or ""),
                         "pid": int(prev.processIdentifier())}
            elif front["pid"] == os.getpid():
                front = {"app": "", "bundle": "", "pid": 0}
            main = self.main_window(shot.get("windows") or [], front["pid"])
            return {"app": front["app"], "title": main["title"] if main else "", "bundle": front["bundle"]}
        title = win["title"]
        if not title:
            main = self.main_window(shot.get("windows") or [], win["pid"])
            title = main["title"] if main else ""
        return {"app": win["app"], "title": title, "bundle": core.bundle_for_pid(win["pid"])}

    def keep_prefs(self, patch):
        try:
            current = core.load_settings()
            if core._merge(current, patch) != current:
                self.settings = core.save_settings(patch)
        except Exception as exc:
            core.log("warn", "settings", f"Could not keep the tool settings: {exc}")

    def region_done(self, mode, shot, rect, win, drawn, action):
        self.selector = None
        info = self.window_info(shot, rect, win)
        self.restore_front()
        if mode in ("record", "gif"):
            self.start_recording(shot, rect, info, mode == "gif")
            return
        crop = marks.render(shot["cgimage"], (shot["w"], shot["h"]), rect, drawn, shot.get("effects"))
        if drawn:
            core.log("info", "capture", f"Annotated with {len(drawn)} marks")
        self.save_still(crop, None, shot, rect, info, "window" if win else "region",
                        {"open_editor": True} if action == "style" else None)

    def capture_rect(self, rect, display=None):
        if self.selector is not None or self.busy:
            return
        self.busy = True
        self.busy_since = time.time()
        screens = overlay.screens_info()
        shot = next((s for s in screens if display and s["display"] == int(display)), None)
        if shot is None:
            gx, gy = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
            shot = next((s for s in screens if s["cg_x"] <= gx <= s["cg_x"] + s["w"] and s["cg_y"] <= gy <= s["cg_y"] + s["h"]), screens[0])
            rect = [rect[0] - shot["cg_x"], rect[1] - shot["cg_y"], rect[2], rect[3]]
        x0, y0 = max(0.0, rect[0]), max(0.0, rect[1])
        x1, y1 = min(shot["w"], rect[0] + rect[2]), min(shot["h"], rect[1] + rect[3])
        local = (round(x0), round(y0), round(x1 - x0), round(y1 - y0))
        windows = core.window_list(exclude_pid=os.getpid())
        shot["windows"] = self.local_windows(windows, dict(shot, cg_x=shot["cg_x"], cg_y=shot["cg_y"]))
        info = self.window_info(shot, local)

        tmp = os.path.join(core.WORK_DIR, f"rect-{int(time.time() * 1000)}.png")

        def done(cg):
            shot["scale"] = Quartz.CGImageGetWidth(cg) / max(1, local[2])
            self.save_still(cg, tmp, shot, local, info, "region")

        self._off_main(lambda: self._grab({"display": shot["display"], "rect": list(local), "path": tmp}), done)

    def drive_selection(self, action, rect=None, drawn=None, tool=None):
        sel = self.selector
        if sel is None:
            return {"ok": False, "error": "no selection is open"}
        if action == "cancel":
            sel.cancel()
            return {"ok": True}
        if action == "finish":
            return {"ok": sel.submit(rect)}
        if action == "annotate":
            if not rect:
                return {"ok": False, "error": "annotate needs rect [x, y, width, height] in points"}
            return {"ok": True, "tools": list(sel.open_board(rect, drawn, tool))}
        if action == "snapshot":
            return {"ok": True, "path": sel.snapshot(os.path.join(core.WORK_DIR, "selection-snapshot.png"))}
        return {"ok": False, "error": "action must be finish, cancel, annotate or snapshot"}

    def capture_screen(self):
        mouse = NSEvent.mouseLocation()
        screens = overlay.screens_info()
        shot = next((s for s in screens if s["frame"].origin.x <= mouse.x <= s["frame"].origin.x + s["frame"].size.width
                     and s["frame"].origin.y <= mouse.y <= s["frame"].origin.y + s["frame"].size.height), screens[0])
        front = core.front_app()
        main = self.main_window(core.window_list(exclude_pid=os.getpid()), front["pid"])
        title = main["title"] if main else ""
        tmp = os.path.join(core.WORK_DIR, f"screen-{int(time.time() * 1000)}.png")
        cursor = bool((self.settings.get("capture") or {}).get("cursor"))

        def done(cg):
            shot["scale"] = Quartz.CGImageGetWidth(cg) / shot["w"]
            self.save_still(cg, tmp, shot, (0, 0, shot["w"], shot["h"]), {"app": front["app"], "title": title, "bundle": front["bundle"]}, "screen")

        self._off_main(lambda: self._grab({"display": shot["display"], "path": tmp, "cursor": cursor}), done)

    def main_window(self, windows, pid):
        mine = [w for w in windows if w["pid"] == pid and w["layer"] == 0 and w["w"] >= 120 and w["h"] >= 80]
        titled = [w for w in mine if w["title"]]
        return (titled or mine or [None])[0]

    def capture_window(self):
        front = core.front_app()
        windows = core.window_list(exclude_pid=os.getpid())
        win = self.main_window(windows, front["pid"])
        if win is None:
            win = next((w for w in windows if w["layer"] == 0 and w["w"] >= 120 and w["h"] >= 80), None)
        if win is None:
            self.busy = False
            core.log("warn", "capture", "No window to capture")
            return
        cx, cy = win["x"] + win["w"] / 2, win["y"] + win["h"] / 2
        screens = overlay.screens_info()
        shot = next((s for s in screens if s["cg_x"] <= cx <= s["cg_x"] + s["w"] and s["cg_y"] <= cy <= s["cg_y"] + s["h"]), screens[0])
        x0 = max(win["x"], shot["cg_x"]) - shot["cg_x"]
        y0 = max(win["y"], shot["cg_y"]) - shot["cg_y"]
        x1 = min(win["x"] + win["w"], shot["cg_x"] + shot["w"]) - shot["cg_x"]
        y1 = min(win["y"] + win["h"], shot["cg_y"] + shot["h"]) - shot["cg_y"]
        rect = (round(x0), round(y0), round(x1 - x0), round(y1 - y0))
        tmp = os.path.join(core.WORK_DIR, f"window-{int(time.time() * 1000)}.png")
        info = {"app": win["app"], "title": win["title"], "bundle": core.bundle_for_pid(win["pid"])}

        def done(cg):
            shot["scale"] = Quartz.CGImageGetWidth(cg) / max(1, rect[2])
            self.save_still(cg, tmp, shot, rect, info, "window")

        self._off_main(lambda: self._grab({"display": shot["display"], "rect": list(rect), "path": tmp}), done)

    def save_still(self, cg, tmp_png, shot, rect, info, source, after=None):
        settings = core.load_settings()
        factor = core.scale_factor(shot.get("scale") or 1, (settings.get("capture") or {}).get("scale"))
        if factor < 0.999:
            cg = core.scale_cgimage(cg, factor)
            shot = {**shot, "scale": (shot.get("scale") or 1) * factor}
        width, height = int(Quartz.CGImageGetWidth(cg)), int(Quartz.CGImageGetHeight(cg))
        when = time.time()
        meta = {"app": info.get("app"), "title": info.get("title"), "width": width, "height": height,
                "kind": "image", "source": source}
        stem = core.format_name(settings.get("name_pattern"), meta, when)
        jpeg = settings.get("image_format") == "jpg"
        folder = core.target_folder(settings, when)
        out = core.unique_path(folder, stem, ".jpg" if jpeg else ".png")
        if tmp_png and not jpeg and factor >= 0.999:
            core.move_file(tmp_png, out)
        else:
            quality = max(0.4, min(1.0, float(settings.get("jpeg_quality") or 90) / 100.0))
            core.write_cgimage(cg, out, "public.jpeg" if jpeg else "public.png", quality if jpeg else None)
            if tmp_png:
                try:
                    os.remove(tmp_png)
                except OSError:
                    pass
        after = {**(settings.get("after_capture") or {}), **(after or {})}
        copied = False
        if after.get("clipboard", True):
            try:
                copied = core.copy_image(out)
            except Exception as exc:
                core.log("error", "clipboard", f"Could not copy to the clipboard: {exc}")
        if after.get("sound", True):
            self.play_shutter()
        self.busy = False
        self.captures += 1
        self.last_capture = when
        self.pool.submit(self.post_still, out, {
            "source": source, "app": info.get("app") or "", "title": info.get("title") or "",
            "bundle": info.get("bundle") or "", "display": shot.get("display"), "rect": list(rect),
            "pixel_ratio": shot.get("scale") or 1, "created": when, "clipboard": copied,
        }, after, shot.get("screen"))

    def post_still(self, path, fields, after, screen):
        try:
            item = core.ingest(path, kind="image", **fields)
            if fields.get("clipboard"):
                core.log("info", "clipboard", "Copied to the clipboard", item["id"])
            if after.get("toast", True):
                AppHelper.callAfter(self.show_toast, item, bool(fields.get("clipboard")), screen)
            if after.get("open_editor"):
                self.open_app(item["id"], studio=True)
            if after.get("ocr", True):
                core.run_ocr(item["id"])
            else:
                core.update_item(item["id"], ocr_state="none")
            if after.get("auto_style"):
                try:
                    core.render_style(item["id"], {}, copy=bool(fields.get("clipboard")))
                except Exception as exc:
                    core.log("warn", "capture", f"Auto style failed: {exc}", item["id"])
            if after.get("ai_rename"):
                try:
                    core.ai_name(item["id"])
                except Exception:
                    pass
        except Exception as exc:
            traceback.print_exc()
            core.log("error", "capture", f"Saving failed after capture: {exc}")
        finally:
            AppHelper.callAfter(self.write_status)

    def show_toast(self, item, copied, screen=None):
        subtitle = item["name"]
        title = "Saved and copied" if copied else "Saved"
        if item["kind"] == "video":
            title = "Recording saved" + (" and copied" if copied else "")
        elif item["source"] == "gif":
            title = "GIF saved" + (" and copied" if copied else "")
        overlay.Toast(item.get("thumb"), title, subtitle, lambda: self.open_app(item["id"]), screen=screen)

    def show_error(self, title, detail, what="screenshot"):
        if any(h in detail for h in BLOCKED_HINTS):
            overlay.Toast(None, f"macOS blocked the {what}",
                          "Turn on FusedRender in Privacy & Security › Screen & System Audio Recording, "
                          "then restart FusedRender. Click to open the settings.",
                          self.open_privacy, seconds=12.0, error=True)
            return
        detail = detail if len(detail) <= 110 else detail[:107] + "…"
        overlay.Toast(None, title, f"{detail} Click to open Hagaki.", self.open_app, seconds=8.0, error=True)

    def open_privacy(self):
        NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(PRIVACY_URL))

    def play_shutter(self):
        try:
            sound = NSSound.alloc().initWithContentsOfFile_byReference_(SHUTTER, True) if os.path.exists(SHUTTER) else NSSound.soundNamed_("Tink")
            if sound is not None:
                sound.play()
                self._sound = sound
        except Exception:
            pass

    def start_recording(self, shot, rect, info, as_gif=False):
        settings = core.load_settings()
        rec = settings.get("gif" if as_gif else "record") or {}
        when = time.time()
        k = shot["scale"] * core.scale_factor(shot["scale"], rec.get("scale"))
        meta = {"app": info.get("app"), "title": info.get("title"), "width": int(rect[2] * k), "height": int(rect[3] * k),
                "kind": "video", "source": "record"}
        stem = core.format_name(settings.get("name_pattern"), meta, when)
        out = core.unique_path(core.target_folder(settings, when), stem, ".mov")
        temp = os.path.join(core.WORK_DIR, f"rec-{int(when * 1000)}.mov")
        audio = rec.get("audio") or "none"
        body = {"mode": "screen", "display": shot["display"], "rect": list(rect),
                "audio": False if audio == "none" else audio, "cursor": bool(rec.get("cursor", True)),
                "path": temp, "maxSeconds": int(rec.get("max_seconds") or (60 if as_gif else 1800)),
                "title": "Hagaki recording"}

        self.starting = True
        tracker = reel.CursorTracker.on(shot, rect)

        def go():
            try:
                started = core.server_call("/api/capture/start", body, timeout=40)
            except Exception as exc:
                tracker.halt()
                AppHelper.callAfter(self.recording_failed, str(exc))
                return
            tracker.mark()
            AppHelper.callAfter(self.recording_started, started, shot, rect, info, when, out, tracker, as_gif)

        self.ctl.submit(go)

    def recording_failed(self, message):
        self.busy = False
        self.starting = False
        core.log("error", "record", f"Recording did not start: {message}")
        self.show_error("Recording did not start", message, "recording")
        self.write_status()

    def recording_started(self, started, shot, rect, info, when, out, tracker, as_gif=False):
        shot = {k: v for k, v in shot.items() if k not in ("cgimage", "nsimage")}
        self.busy = False
        self.starting = False
        self.write_pending({"cid": started.get("id"), "temp": started.get("path"), "final": out, "info": info,
                            "when": when, "display": shot.get("display"), "rect": list(rect), "scale": shot.get("scale") or 1,
                            "gif": as_gif})
        chrome = overlay.RecordingChrome(shot, rect, self.stop_recording, self.cancel_recording, self.draw_options(tracker))
        self.recording = {"id": started.get("id"), "path": started.get("path"), "final": out, "started": time.time(),
                          "chrome": chrome, "info": info, "shot": shot, "rect": rect, "when": when,
                          "stopping": False, "polls": 0, "tracker": tracker, "gif": as_gif}
        self.record_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.5, self.delegate, "recordTick:", None, True)
        if self.icon_rec is not None:
            self.status_item.button().setImage_(self.icon_rec)
        self.rebuild_menu()
        core.log("info", "record", "GIF recording started" if as_gif else "Recording started", None,
                 {"path": self.recording["path"], "rect": list(rect)})
        self.write_status()

    def draw_options(self, tracker):
        settings = core.load_settings()

        def changed(active, prefs, fade):
            tracker.quiet = active
            if not active:
                self.keep_prefs({"draw": prefs, "record": {"draw_fade": bool(fade)}})

        return {"prefs": settings["draw"], "fade": settings["record"]["draw_fade"], "on_change": changed, "tracker": tracker}

    def drive_draw(self, action, drawn=None):
        chrome = overlay.RecordingChrome.current
        if chrome is None:
            return {"ok": False, "error": "no recording is open"}
        if action == "on":
            chrome.start_draw()
        elif action == "off":
            chrome.stop_draw()
        elif action == "marks":
            chrome.load_marks(drawn or [])
        elif action == "snapshot":
            chrome.snapshot(core.WORK_DIR)
        elif action != "state":
            return {"ok": False, "error": "action must be on, off, marks, snapshot or state"}
        return {"ok": True, **chrome.state(), "quiet": chrome.draw["tracker"].quiet}

    def record_tick(self):
        r = self.recording
        if not r:
            return
        elapsed = time.time() - r["started"]
        r["chrome"].tick(elapsed)
        s = int(elapsed)
        self.status_item.button().setTitle_(f" {s // 60}:{s % 60:02d}")
        r["polls"] += 1
        if r["polls"] % 4 == 0 and not r["stopping"]:
            self.ctl.submit(self._poll_recording, r["id"])

    def _poll_recording(self, cid):
        try:
            state = core.server_call(f"/api/capture/{cid}/state", method="GET", timeout=10)
            if state.get("state") in ("stopped", "done", "finished", "error", "cancelled"):
                AppHelper.callAfter(self.stop_recording)
        except RuntimeError:
            AppHelper.callAfter(self.stop_recording)
        except Exception:
            pass

    def _end_recording_ui(self):
        if self.record_timer is not None:
            self.record_timer.invalidate()
            self.record_timer = None
        if self.recording and self.recording.get("chrome"):
            self.recording["chrome"].close()
        self.status_item.button().setTitle_("")
        self.status_item.button().setImage_(self.icon_idle)

    def stop_recording(self):
        r = self.recording
        if not r or r["stopping"]:
            return
        r["stopping"] = True
        r["tracker"].halt()
        self._end_recording_ui()

        def go():
            try:
                res = core.server_call(f"/api/capture/{r['id']}/stop", {}, timeout=180)
                AppHelper.callAfter(self.recording_done, r, res, None)
            except Exception as exc:
                AppHelper.callAfter(self.recording_done, r, None, str(exc))

        self.ctl.submit(go)

    def cancel_recording(self):
        r = self.recording
        if not r or r["stopping"]:
            return
        r["stopping"] = True
        r["tracker"].halt()
        self._end_recording_ui()

        def go():
            try:
                core.server_call(f"/api/capture/{r['id']}/cancel", {}, timeout=60)
            except Exception:
                pass
            AppHelper.callAfter(self.recording_cancelled)

        self.ctl.submit(go)

    def recording_cancelled(self):
        self.recording = None
        self.clear_pending()
        self.rebuild_menu()
        core.log("info", "record", "Recording cancelled; the file was deleted")
        self.write_status()

    def recording_done(self, r, res, err):
        self.recording = None
        self.rebuild_menu()
        res = res or {}
        if res.get("error"):
            core.log("warn", "record", f"The recorder reported: {res['error']}")
        path = res.get("path") or r["path"]
        if res.get("state") == "cancelled" or not (path and os.path.exists(path)):
            if res.get("state") == "cancelled":
                self.recording_cancelled()
                return
            self.clear_pending()
            core.log("error", "record", f"Recording failed: {err or 'the file is missing'}")
            self.show_error("Recording failed", err or res.get("error") or "The recording file is missing.", "recording")
            self.write_status()
            return
        self.clear_pending()
        if r.get("gif"):
            self.captures += 1
            self.last_capture = r["when"]
            self.pool.submit(self.save_gif, path, r)
            return
        final = r.get("final")
        if final and path and os.path.exists(path) and os.path.abspath(path) != os.path.abspath(final):
            final = core.unique_path(os.path.dirname(final), os.path.splitext(os.path.basename(final))[0], os.path.splitext(path)[1] or ".mov")
            try:
                path = core.move_file(path, final)
            except OSError as exc:
                core.log("warn", "record", f"The recording stays in the work folder: {exc}")
        if res and res.get("warning"):
            core.log("warn", "record", str(res["warning"]))
        self.captures += 1
        self.last_capture = r["when"]
        settings = core.load_settings()
        after = settings.get("after_capture") or {}

        def post():
            try:
                info = r["info"]
                ratio = core.fit_recording(path, r["shot"].get("scale") or 1)
                item = core.ingest(path, kind="video", source="record", app=info.get("app") or "",
                                   title=info.get("title") or "", bundle=info.get("bundle") or "",
                                   display=r["shot"].get("display"), rect=list(r["rect"]),
                                   pixel_ratio=ratio, created=r["when"])
                reel.save_track(item["id"], r["tracker"].track())
                copied = False
                if after.get("clipboard", True):
                    copied = run_on_main(core.copy_file, path)
                    core.update_item(item["id"], clipboard=1 if copied else 0)
                    core.log("info", "clipboard", "Recording file copied to the clipboard", item["id"])
                if after.get("toast", True):
                    AppHelper.callAfter(self.show_toast, core.get_item(item["id"]), copied, r["shot"].get("screen"))
            except Exception as exc:
                traceback.print_exc()
                core.log("error", "record", f"Saving the recording failed: {exc}")
            finally:
                AppHelper.callAfter(self.write_status)

        self.pool.submit(post)

    def show_progress(self, text):
        if not self.recording:
            self.status_item.button().setTitle_(text)

    def save_gif(self, src, r):
        after = core.load_settings().get("after_capture") or {}
        shot, info = r["shot"], r["info"]
        try:
            try:
                item = gif.store(src, info, r["when"], shot.get("scale") or 1,
                                 lambda p: AppHelper.callAfter(self.show_progress, f" GIF {int(p * 100)}%"),
                                 display=shot.get("display"), rect=list(r["rect"]))
            except Exception as exc:
                traceback.print_exc()
                core.log("error", "record", f"Making the GIF failed: {exc}")
                try:
                    kept = self.keep_video(src, r["final"], info, r["when"], shot.get("scale") or 1, shot.get("display"),
                                           list(r["rect"]))
                    reel.save_track(kept["id"], r["tracker"].track())
                except Exception as keep_exc:
                    core.log("error", "record", f"The video stays in the work folder: {keep_exc}")
                AppHelper.callAfter(self.show_error, "The GIF failed",
                                    f"{exc} Hagaki kept the video. Open it and click GIF to try again.", "recording")
                return
            os.remove(src)
            copied = False
            if after.get("clipboard", True):
                copied = run_on_main(core.copy_gif, item["path"])
                core.update_item(item["id"], clipboard=1 if copied else 0)
                core.log("info", "clipboard", "GIF copied to the clipboard", item["id"])
            if after.get("toast", True):
                AppHelper.callAfter(self.show_toast, core.get_item(item["id"]), copied, shot.get("screen"))
        except Exception as exc:
            traceback.print_exc()
            core.log("error", "record", f"Saving the GIF failed: {exc}")
        finally:
            AppHelper.callAfter(self.show_progress, "")
            AppHelper.callAfter(self.write_status)

    def keep_video(self, src, final, info, when, scale, display, rect):
        final = core.unique_path(os.path.dirname(final), os.path.splitext(os.path.basename(final))[0], ".mov")
        core.move_file(src, final)
        ratio = core.fit_recording(final, scale)
        return core.ingest(final, kind="video", source="record", app=info.get("app") or "", title=info.get("title") or "",
                           bundle=info.get("bundle") or "", display=display, rect=rect, pixel_ratio=ratio, created=when)

    def show_agent_recording(self, cid, display=None, rect=None, started=None):
        screens = overlay.screens_info()
        shot = next((sc for sc in screens if display and sc["display"] == int(display)), screens[0])
        if not rect:
            rect = (0, 0, round(shot["w"]), round(shot["h"]))
        state = {"cid": cid, "started": time.time(), "done": False}
        tracker = reel.CursorTracker.on(shot, rect)
        tracker.mark(started)
        self.agent_tracks = {k: v for k, v in self.agent_tracks.items() if time.time() - v.created < 7200}
        self.agent_tracks[cid] = tracker

        def finish(action):
            if state["done"]:
                return
            state["done"] = True
            state["chrome"].close()
            state["timer"].invalidate()
            self.end_agent_track(cid)
            self.pool.submit(lambda: self._safe_call(f"/api/capture/{cid}/{action}"))
            core.log("info", "record", "The person stopped an agent recording" if action == "stop" else "The person cancelled an agent recording")

        def tick(timer):
            if state["done"]:
                timer.invalidate()
                return
            state["chrome"].tick(time.time() - state["started"])
            state["polls"] = state.get("polls", 0) + 1
            if state["polls"] % 4 == 0:
                self.pool.submit(poll)

        def poll():
            try:
                st = core.server_call(f"/api/capture/{cid}/state", method="GET", timeout=10)
                ended = st.get("state") not in ("recording", "paused", "starting")
            except Exception:
                ended = True
            if ended:
                AppHelper.callAfter(close)

        def close():
            if not state["done"]:
                state["done"] = True
                state["chrome"].close()
                state["timer"].invalidate()
                self.end_agent_track(cid)

        state["chrome"] = overlay.RecordingChrome(shot, tuple(rect), lambda: finish("stop"), lambda: finish("cancel"),
                                                  self.draw_options(tracker))
        state["timer"] = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.5, True, tick)
        return {"ok": True}

    def end_agent_track(self, cid):
        tracker = self.agent_tracks.get(cid)
        if tracker is not None:
            tracker.halt()

    def take_agent_track(self, cid):
        self.end_agent_track(cid)
        return self.agent_tracks.pop(cid, None)

    def _safe_call(self, route):
        try:
            core.server_call(route, {}, timeout=180)
        except Exception as exc:
            core.log("warn", "record", f"{route} failed: {exc}")

    def write_pending(self, data):
        try:
            core._atomic_write(PENDING_FILE, json.dumps(data).encode())
        except OSError:
            pass

    def clear_pending(self):
        try:
            os.remove(PENDING_FILE)
        except OSError:
            pass

    def finish_pending_recording(self):
        try:
            with open(PENDING_FILE, encoding="utf-8") as fh:
                p = json.load(fh)
        except (OSError, ValueError):
            return
        try:
            try:
                core.server_call(f"/api/capture/{p.get('cid')}/stop", {}, timeout=60)
            except Exception:
                pass
            temp = p.get("temp")
            if not temp or not os.path.exists(temp):
                return
            info = p.get("info") or {}
            if p.get("gif"):
                try:
                    gif.store(temp, info, p.get("when") or time.time(), p.get("scale") or 1, display=p.get("display"),
                              rect=p.get("rect"))
                    os.remove(temp)
                    core.log("info", "record", "Saved a GIF recording that was open when Hagaki stopped")
                    return
                except Exception as exc:
                    core.log("warn", "record", f"Could not make the GIF, so Hagaki keeps the video: {exc}")
            self.keep_video(temp, p.get("final") or temp, info, p.get("when"), p.get("scale") or 1, p.get("display"),
                            p.get("rect"))
            core.log("info", "record", "Saved a recording that was open when Hagaki stopped")
        except Exception as exc:
            core.log("error", "record", f"Could not save the open recording: {exc}")
        finally:
            self.clear_pending()

    def open_app(self, item_id=None, studio=False):
        url = core.app_url(item_id)
        if studio and item_id:
            url += "&studio=1"
        subprocess.Popen(["open", url])

    def pick_folder(self, start=None):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setCanCreateDirectories_(True)
        panel.setAllowsMultipleSelection_(False)
        panel.setPrompt_("Use Folder")
        panel.setMessage_("Choose the folder for your screenshots and recordings.")
        if start and os.path.isdir(os.path.expanduser(start)):
            panel.setDirectoryURL_(NSURL.fileURLWithPath_(os.path.expanduser(start)))
        overlay._activate()
        result = panel.runModal()
        if result == 1 and panel.URL() is not None:
            return str(panel.URL().path())
        return None

    def set_override(self, enable):
        if enable:
            syskeys.disable(core.SYSKEYS_BACKUP)
            core.save_settings({"override_system": True})
            core.log("info", "settings", "Hagaki now uses ⌘⇧3, ⌘⇧4, ⌘⇧5 and ⌘⇧6. The macOS screenshot shortcuts are off.")
        else:
            syskeys.restore(core.SYSKEYS_BACKUP)
            core.save_settings({"override_system": False})
            core.log("info", "settings", "The macOS screenshot shortcuts are back on.")
        return run_on_main(self.bind_hotkeys)

    def cleanup(self):
        if self.quitting:
            return
        self.quitting = True
        try:
            if core.load_settings().get("override_system"):
                syskeys.restore(core.SYSKEYS_BACKUP)
                core.log("info", "settings", "Hagaki stopped. The macOS screenshot shortcuts are back on.")
        except Exception:
            pass
        try:
            self.keys.clear()
        except Exception:
            pass
        try:
            if self.recording and not self.recording["stopping"]:
                core.server_call(f"/api/capture/{self.recording['id']}/stop", {}, timeout=2)
        except Exception:
            pass
        core.log("info", "daemon", "Hagaki stopped")
        try:
            os.remove(STATUS_FILE)
            core.touch()
        except OSError:
            pass

    def quit_from_menu(self):
        def go():
            try:
                core._shared_path()
                import background_app

                background_app.stop()
            except Exception:
                AppHelper.callAfter(terminate)

        threading.Thread(target=go, daemon=True).start()


def terminate():
    try:
        APP.cleanup()
    finally:
        threading.Timer(2.0, lambda: os._exit(0)).start()
        NSApplication.sharedApplication().terminate_(None)


class Handler(BaseHTTPRequestHandler):
    server_version = "Hagaki/1"
    version = ""

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return secrets.compare_digest((q.get("t") or [""])[0], TOKEN)

    def do_GET(self):
        if not self._auth():
            return self._send(403, {"error": "bad token"})
        route = urllib.parse.urlparse(self.path).path.rstrip("/")
        if route == "/ping":
            return self._send(200, {"ok": True, "version": Handler.version})
        if route == "/quit":
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            AppHelper.callAfter(terminate)
            return self._send(200, {"ok": True})
        if route == "/status":
            return self._send(200, run_on_main(APP.write_status, timeout=10))
        return self._send(404, {"error": "no such route"})

    def do_POST(self):
        if not self._auth():
            return self._send(403, {"error": "bad token"})
        route = urllib.parse.urlparse(self.path).path.rstrip("/")
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except ValueError:
            body = {}
        try:
            if route == "/capture":
                mode = str(body.get("mode") or "region")
                if mode not in ("region", "screen", "window", "record", "gif"):
                    return self._send(400, {"error": "mode must be region, screen, window, record or gif"})
                rect = body.get("rect")
                if rect is not None and mode == "region":
                    if not (isinstance(rect, list) and len(rect) == 4 and all(isinstance(n, (int, float)) for n in rect)):
                        return self._send(400, {"error": "rect must be [x, y, width, height] in points"})
                    AppHelper.callAfter(APP.capture_rect, [float(n) for n in rect], body.get("display"))
                    return self._send(200, {"ok": True})
                AppHelper.callAfter(APP.trigger, mode)
                return self._send(200, {"ok": True})
            if route == "/selection":
                return self._send(200, run_on_main(APP.drive_selection, str(body.get("action") or ""), body.get("rect"),
                                                   body.get("marks"), body.get("tool"), timeout=20))
            if route == "/draw":
                return self._send(200, run_on_main(APP.drive_draw, str(body.get("action") or "state"), body.get("marks"), timeout=20))
            if route == "/stop":
                AppHelper.callAfter(APP.stop_recording)
                return self._send(200, {"ok": True})
            if route == "/reload":
                return self._send(200, run_on_main(APP.bind_hotkeys, timeout=20))
            if route == "/pause":
                return self._send(200, run_on_main(APP.set_paused, bool(body.get("paused")), timeout=20))
            if route == "/override":
                return self._send(200, APP.set_override(bool(body.get("enable"))))
            if route == "/pick_folder":
                return self._send(200, {"path": run_on_main(APP.pick_folder, body.get("start"))})
            if route == "/status":
                return self._send(200, run_on_main(APP.write_status, timeout=10))
            if route == "/show_recording":
                return self._send(200, run_on_main(APP.show_agent_recording, str(body.get("cid") or ""), body.get("display"), body.get("rect"), body.get("started"), timeout=20))
            if route == "/track":
                tracker = run_on_main(APP.take_agent_track, str(body.get("cid") or ""), timeout=10)
                return self._send(200, {"track": tracker.track() if tracker else None})
            if route == "/windows":
                return self._send(200, {"windows": core.window_list(exclude_pid=os.getpid()), "displays": core.displays()})
            if route == "/ping":
                return self._send(200, {"ok": True, "version": Handler.version})
        except Exception as exc:
            traceback.print_exc()
            return self._send(500, {"error": f"{exc.__class__.__name__}: {exc}"})
        return self._send(404, {"error": "no such route"})


def main():
    global APP
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", required=True)
    parser.add_argument("--cache", default="")
    parser.add_argument("--version", default="0")
    args, _unknown = parser.parse_known_args()
    Handler.version = args.version
    core.ensure_dirs()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    status = {"port": server.server_address[1], "token": TOKEN, "pid": os.getpid()}
    tmp = args.status + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(status, fh)
    os.replace(tmp, args.status)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    APP = Hagaki(args.version)
    signal.signal(signal.SIGTERM, lambda *a: AppHelper.callAfter(terminate))
    signal.signal(signal.SIGINT, lambda *a: AppHelper.callAfter(terminate))
    APP.boot()
    AppHelper.runEventLoop(installInterrupt=False)


if __name__ == "__main__":
    main()
