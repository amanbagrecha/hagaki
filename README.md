# Hagaki

Hagaki is a screenshot, screen recording and GIF app for macOS. It runs inside FusedRender. It works like ShareX: press a shortcut, drag an area, and the capture goes to the clipboard and the gallery. It does not ask.

## Start
1. Open Hagaki in FusedRender.
2. Click **Turn on**. Hagaki then starts each time FusedRender starts.
3. Press **⌥⇧4** and drag an area. Click a window to capture all of it. Press Space for the full screen. Press Esc to cancel.
4. Add arrows, boxes or text with the tool bar under the area. Press Enter to save.

## Shortcuts (you can change them in Settings)
| Keys | Action |
|---|---|
| ⌥⇧4 | Capture an area (or click a window) |
| ⌥⇧2 | Capture the front window |
| ⌥⇧3 | Capture the full screen |
| ⌥⇧5 | Record an area. Press again to stop. |
| ⌥⇧6 | Record a GIF. Press again to stop. |
| ⌥⇧H | Open Hagaki |
| ⌘V (in the Hagaki window) | Add the image or image file on the clipboard to the gallery |

To use ⌘⇧3, ⌘⇧4, ⌘⇧5 and ⌘⇧6 (GIF), turn on **Settings › Shortcuts › Use the macOS screenshot keys**. Hagaki asks first and keeps a copy of your macOS settings. macOS gets the keys back when you turn this off or when Hagaki stops.

## Turn Hagaki on or off from the command line
Run these in the Hagaki folder:
```sh
/Applications/FusedRender.app/Contents/MacOS/python tools.py start_service
/Applications/FusedRender.app/Contents/MacOS/python tools.py start_service '{"restart": true}'
/Applications/FusedRender.app/Contents/MacOS/python tools.py stop_service
```
FusedRender must be running. Agents can use the `start_service` and `stop_service` MCP tools.

## What Hagaki does
- Lets you mark up an area before you save it: arrow, box, ellipse, line, pen, marker, text, step numbers, spotlight, blur and pixelate.
- Lets you draw on the screen while you record. The marks are in the video.
- Saves each capture in one folder (default `~/Fused/Screenshots`, one folder per month). You can change the folder.
- Names files from a pattern such as `{date} {time} {app} - {title}`, with the app and window title.
- Reads the text in each image on your Mac, so search finds words inside screenshots.
- Checks each file with SHA-256 and keeps a log of each event.
- Styles a screenshot with Japanese backgrounds, window frames (macOS, Windows, Linux, postcard), padding, corners and shadow.
- Styles a recording the same way and zooms in where you click. The result is a new MP4 with the sound of the original.
- Records an area as an animated GIF, or makes a GIF from a recording or a styled recording.
- Renames a capture with AI (the wand button).
- Puts a capture online with a Fused share link. All public captures share one canvas, `hagaki_public`; temporary ones share `hagaki_temporary`.

## Annotate a capture
When you release the mouse (or click a window, or press Space), the area stays on the screen with a tool bar.
1. Select a tool, or press its key.
2. Drag in the area to draw. Hold Shift for a straight line at 45°, a square or a circle.
3. Press Enter or click **Save**. Hagaki saves the image and copies it. The frame button saves it and opens the style editor.

| Key | Tool |
|---|---|
| V | Move. Drag a mark or the area. Delete removes the selected mark. Double-click the area to save. |
| R | Rectangle |
| E | Ellipse |
| A | Arrow |
| L | Line |
| D | Pen |
| M | Marker (highlighter) |
| T | Text. Click, then type. Return ends the text. Shift-Return starts a new line. |
| C | Step number (1, 2, 3). Drag to add a pointer. |
| H | Spotlight. The area outside the box becomes dark. |
| B | Blur |
| P | Pixelate |

More keys: 1 to 6 select a colour. `[` and `]` change the size. F fills boxes, ellipses and text labels. ⌘Z undoes and ⇧⌘Z redoes. The arrow keys move the area or the selected mark. Drag a handle to change the area. Click outside the area to select a new area; your marks stay.

Hagaki keeps your last tool, colour and size. To save when you release the mouse, with no tool bar, turn off **Settings › Capture › Show the annotation tools after the drag**.

## Draw while you record
1. Start a recording (**⌥⇧5**).
2. Click **Draw** on the stop button. The tool strip opens.
3. Draw on the screen with the same tools and keys (no blur, pixelate or move). The video shows your marks. The stop button and the tool strip are not in the video.
4. Press Esc or click ✓ to use your apps again. The marks stay until they fade.

Each mark fades after 3 seconds. To keep the marks, click the timer button, or turn off **Settings › Recording › Fade the marks you draw**. While you draw, Hagaki does not keep your clicks for the zoom on clicks.

## Record a GIF
1. Press **⌥⇧6** (or click **GIF** in Hagaki, or **Record GIF** in the menu bar).
2. Drag an area, click a window, or press Space for the full screen.
3. Press **⌥⇧6** again or click the stop button. Hagaki makes the GIF, saves it and copies it.

You can draw on the screen while you record, the same as for a video. A GIF has no sound. GIFs are in **Recordings** in the gallery.

To make a GIF from a recording, open the recording and click **GIF**. This also works for a styled recording, so the GIF can zoom on clicks.

Set the GIF in **Settings › GIF**:

| Setting | Choices |
|---|---|
| Frame rate | 10, 15 (default), 20 or 30 frames each second |
| Size | The longest side: 480, 720 (default) or 1080 px, or Screen (the size on the screen) |
| Colors | Fewer (64 colors, the smallest file), Normal (256) or Smooth (256 colors mixed for photos and gradients) |
| Play in a loop | On (default) or off (the GIF plays one time) |
| Show the pointer | On (default) or off |
| Maximum length | 15 s, 30 s, 1 min (default) or 2 min |

If Hagaki cannot make the GIF, it keeps the video. Open the video and click **GIF** to try again.

## Style a recording and zoom on clicks
1. Open a recording and click **Style**.
2. Select a background and a frame. The preview plays the video in the style.
3. Set **Zoom on clicks**: auto zoom, zoom amount, follow the cursor and click rings.
4. Click **Save** (or **Copy** to also put the file on the clipboard). Hagaki makes the MP4 in the background and adds it to the gallery.

Each new recording keeps the cursor path and the clicks (Hagaki reads them from macOS while it records, with no extra permission). So the zoom goes to the exact click.

An older recording has no click data. Click **Find clicks with AI**: Hagaki looks at the motion in the video and asks the Moondream 2 vision model on this Mac where the cursor is. These clicks are an estimate. A dot under the video shows each click; select a dot to turn its zoom off or on.

## For agents
- MCP: `fused app serve <this folder>` serves the tools in `mcp.toml` (capture, record, GIF, web pages, search, read text, rename, share).
- Claude Code has it as `hagaki-mcp` (user scope), so each Claude session on this Mac gets the tools, also a session you control from your phone. To add it again, open the MCP pane of this folder in FusedRender and click Register.
- FusedRender bots (also over iMessage) call the same tools; see `SKILL.md`.
- `capture_web_page` uses a hidden Chrome. It works when the screen is locked or nobody is at the Mac. `open_url` and `capture_window` show the page in your own browser, with your logins.
- FusedRender must run for screen, window and area captures. Hagaki starts its background service when a tool needs it.

## Files
| File | Job |
|---|---|
| `index.html` | Gallery, logs, settings and style editor |
| `studio.js` | Background and frame renderer (also the style plate for videos) |
| `reel.py` | Video style and zoom: cursor tracker, zoom plan, click estimate, video export |
| `gif.py` | GIF from a recording: frames, one palette, changed-area frames, GIF file |
| `daemon.py` | Background service: shortcuts, menu bar icon, overlay, recording |
| `overlay.py` | Area selector and tool bar, recording frame and draw layer, preview toast |
| `marks.py` | Annotation marks: tools, keys and one renderer for the screen and the saved image |
| `core.py` | Library, index, OCR, clipboard, naming, sharing, web page capture |
| `api.py` | Page backend |
| `tools.py`, `mcp.toml` | Agent tools |
| `hotkeys.py`, `syskeys.py` | Global shortcuts and the macOS shortcut switch |
