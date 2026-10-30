# Hagaki notes

## FusedRender features Hagaki uses
- `/api/capture/screenshot` and `/api/capture/start|stop|cancel|state` work from a daemon with `X-Fused: 1`, so capture rides FusedRender's Screen Recording grant.
- `/api/share/file/publish` and `/upload` give a Fused share link for PNG and MOV files (Image_File and Video_File viewers).
- `GET /api/canvases/status` (`logged_in`) and `POST /api/canvases/login` let Hagaki ask for the Fused sign-in before "Put online".
- `fused_render/hotkey.py` (Carbon `RegisterEventHotKey` through ctypes) is ported as `hotkeys.py`; it needs no Accessibility grant.
- The bundled interpreter has PyObjC (AppKit, Quartz, AVFoundation, ScreenCaptureKit); Vision loads with `objc.loadBundle`, so OCR needs no new package.
- `fused_ai` from `server.json["shared"]` runs the magic rename from the daemon or a detached job.
- `fused app serve` + `mcp.toml` turn `tools.py` into 25 MCP tools; `tools.py main(action, args)` gives FusedRender bots the same set.
- `/api/apps/background/status|start` and `/api/engines/<id>/proxy/<route>` let the agent tools reach the daemon without a page.
- `POST /api/claude-config/mcp` (`action=add`, name `hagaki-mcp`) registered the server at user scope, the same call the MCP pane makes.
- Bots find Hagaki through `registered_apps.json`, so all 25 tools show in `apptools.registry()` with no extra step.
- `fused.uploadFile(<absolute path>, blob)` writes the video style plates (PNG) from the page into `.fused/cache/work` with no base64 step.
- The bundled PyObjC has SF Symbols (`NSImage.imageWithSystemSymbolName_accessibilityDescription_` with a palette `NSImageSymbolConfiguration`) and Core Image (`CIPixellate`, `CIGaussianBlur`), so the annotation tool bar and blur need no new package.
- `fused_ai.vision(image, "point", "mouse pointer", model="vikhyatk/moondream2")` finds the cursor in a frame; `fused_ai.models.catalog()` tells if the model is on the Mac before a call starts a 3.9 GB download.

## Bugs found
- `import_collection_toml_zip` replaces all nodes of a canvas, so adding one shared file means sending every node again; the share token and old `<token>/<slug>.html` links stay valid (tested).
- Screen recording through `/api/capture/start` fails with "invalid parameter" when `path` has a space; Hagaki records to a temp path and moves the file.
- `fused.stat("relative/path")` returns 404; it does not resolve beside the page like `rawUrl` and `runPython` do.
- `/api/run` with a `pyproject.toml` did not build the app venv; the daemon then ran on the bundled fallback interpreter.
- Autostart at server start skips an app whose declared venv is not built ("open the app once to install it"), but opening the app never built it, so Hagaki stayed off after each Fused Render restart. Hagaki now declares no dependencies (the bundled interpreter has Pillow and PyObjC), and the page starts the daemon again when autostart is on but the daemon is not running.
- ScreenCaptureKit honours `NSWindowSharingNone`, so Hagaki's toast and stop pill never show in captures (good), but tests cannot see them either.
- The engine host sends SIGKILL 3 s after SIGTERM, so daemon cleanup must be fast; Hagaki restores the macOS keys first and finishes an open recording on the next start.
- If the daemon dies with SIGKILL before that, macOS keeps ⌘⇧3/4/5 off until Hagaki starts again.
- On macOS 26 the new menu bar icon reports "not visible"; the person may need to allow it in System Settings › Menu Bar.
- `/api/capture/screenshot` failed with "The user declined TCCs" while the capture status still said `granted: true`; a failed capture now shows a toast that links to the Screen Recording settings.
- The bundled interpreter needs `PYTHONHOME`, so `python -I` (which drops it) fails with "zlib not available"; background jobs (upload, text reading, scans) now start with `-s`.
- `/api/share/file/publish` makes the Fused collection name from the file name (`<slug>_<h6>`), and Fused refuses a name that starts with a number, so each date-first file failed to upload. Hagaki names now always start with a letter and have no spaces (`core._sanitize`), and "Put online" renames an older file first.
- `AVAssetImageGenerator.copyCGImageAtTime` gives a raw `PyObjCPointer` when `Quartz` is not imported before `AVFoundation`, and `CGImageDestination` then fails with no reason ("could not encode").
- `AVAssetExportSession` pads a render size that is not a multiple of 4 (2630×1694 became 2632×1696) and fills the new columns with edge pixels; Hagaki makes each video side a multiple of 4.
- `/api/engines/<id>/proxy/ping` always answers "not found" (the host keeps `/ping` for itself), so it cannot test that a daemon is up.
- The bundled interpreter has no post-event access (`CGPreflightPostEventAccess()` is false), so a test cannot make a click; tests move the cursor with `CGWarpMouseCursorPosition` and read clicks from `CGEventSourceCounterForEventType`.
- ScreenCaptureKit recordings have a variable frame rate, so `nominalFrameRate` is an average (43 fps for a 60 Hz capture); a re-encode at that rate made motion choppy. Hagaki now takes the rate from `minFrameDuration` (capped at 60).
- In a plain bundled-Python process, `NSCursor.IBeamCursor()` returned None (other cursors were fine); Hagaki falls back to the crosshair.
- A window with the default sharing type is in a ScreenCaptureKit recording and a `sharingType 0` panel is not, so the draw layer is in the video and the draw tool strip is not.
- The `/draw marks` test hook takes marks as they are; a mark with `tool` and no `kind` raised in the draw layer's `drawRect_` and AppKit ended the daemon (`_crashOnException`). The pending GIF recording was saved as a GIF on the next start.
- Pillow's `save(save_all=True)` keeps every GIF frame in memory and gives each frame its own palette; `gif.py` writes each frame as it comes, with one palette, through the public `GifImagePlugin.getdata`.
- A browser plays a GIF delay under 2 cs at 10 cs, and GIF delays are whole centiseconds; `gif.py` takes each delay from rounded absolute times, so 15 fps alternates 6 and 7 cs and the total length stays exact.
- Moondream 2 `point` misses the I-beam text cursor and sometimes points at a cursor-like toolbar icon; at 640 px it misses more. Hagaki uses motion to follow the cursor and asks the model only at likely clicks.

## Harness ideas
- `capture_web_page` drives headless Chrome over `--remote-debugging-pipe` (fd 3/4) because the app venv has no websocket client; a small CDP helper in the runtime would help each app that screenshots pages.
- Put `fused-render calls` on PATH in Claude sessions; I read `~/.fused-render/logs/<app>/*.calls.jsonl` by hand.
- Ship a page driver (open, wait for runPython, click, screenshot, console); `dev/page_check.py` is Hagaki's (headless Chrome over `--remote-debugging-pipe`, steps as JSON: go, js, until, sleep, shot).
- Headless `--virtual-time-budget` does not wait for runPython WebSocket replies, and `/explorer/embed/` hides iframe console errors; `/render?path=` works.
- Give daemons a test hook for native UI; I added `/capture {rect}` and `/selection {finish|cancel}` so tests need no synthetic mouse.
- macOS has no `timeout` command; a portable wait helper in the shell would help.
- The Safari check needs a visible Safari window and AppleScript (which raises a permission prompt); a headless WebKit driver would make the required cross-browser check safe.
- `/api/share/file/publish` makes one canvas for each file, so Hagaki has its own `share_canvas.py` (bundled `python -I`, reusing `fused_render._fused_share_app` helpers) to put all captures as nodes in `hagaki_public` / `hagaki_temporary`. A "publish into canvas X" option on that endpoint would remove the private imports.
- `fused.capture` screenshots and screen recordings have no `scale` or size option, so they always come at full Retina size; Hagaki makes images smaller with Quartz and recordings with an `AVAssetExportSession` pass after stop (about 1 s for 8 s of video).
- `mcp.toml` has no per-parameter descriptions, so every argument must be explained inside the tool description.
- A "register this app's MCP server" call (instead of the MCP panel or `claude mcp add`) would let an agent wire its own tools.
- The page is in an iframe, so a tab that `open` starts has focus in the shell and single-key shortcuts do nothing; Hagaki focuses its drawer when an item opens. A shell option to focus the view on load would help each app.
- A headless Chrome screenshot of `/render?path=…/index.html` stays at "Loading…" because the gallery data does not come, so a visual check of a style change needs a static sample page. A "render with data, headless" screenshot endpoint would help agents check UI changes.
- Headless Chrome over `--remote-debugging-pipe` with `/render?path=…` and `fused.params.set(...)` did load the gallery data and the video editor, so a page check works when the driver waits for the page instead of `--virtual-time-budget`.
- A first Moondream call on an image takes about 1 s and later prompts on the same image about 0.14 s; a `points` call with several prompts in one request would halve the cost.
- `mcp__fused_approvals__app_state` timed out (20 s) while the Hagaki page was open, so I checked the Settings page with the headless Chrome driver. This happened again for the GIF work.
- Native overlay UI cannot be tested with real clicks here; a hidden `NSWindow` plus in-process `NSEvent` objects sent to the view (`dev/annotate_check.py`) tests the tool bar with no change to the screen.
- A "cursor and clicks" track from `fused.capture` recordings (as Screen Studio keeps) would give every app exact zoom data; Hagaki polls the cursor in its daemon for this.

## To do
- Edit the marks again after a save (keep the marks JSON and the clean image beside the file), and annotate from the gallery.
- An MCP tool that adds marks (arrow, box, text, blur) to a capture, with `marks.render`.
- Scrolling capture, pin to screen.
- GIF: trim before the GIF is made, a GIF size estimate in Settings, and an MP4 or WebP choice for chat apps that do not play GIFs.
- Video editor: drag a zoom span, add a zoom by hand, trim, a smooth cursor drawn by Hagaki (record with the cursor hidden).
- EmbeddingGemma 2 buckets and meaning search; AI rename on by default.
- More upload targets (S3, Imgur) and a URL shortener.
- Arrow-key nudge, Shift-resize and snap sizes in the area overlay.
- A Python port of the style renderer, so agents and auto-style on capture work without the page.
- Test "Put online" end to end (not run yet, because it publishes a public link).
