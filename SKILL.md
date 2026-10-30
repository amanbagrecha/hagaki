---
name: hagaki
description: Screenshot, screen recording and GIF library for this Mac. Capture a screen, window, area or web page, record a video or a GIF, search captures by the text inside them, read text, rename, copy.
approve: [api.py]
---
# Hagaki

Captures live in the library folder (default `~/Fused/Screenshots`, one folder per month). The index is `.fused/data/hagaki.db`; the page, the daemon, the MCP tools and these files share it. FusedRender must be running, because it takes the pictures with its Screen Recording permission. The background daemon (`daemon.py`, shortcuts and menu bar icon) is not callable this way; `tools.py` starts it when a tool needs it, and `start_service` / `stop_service` turn it on and off.

The same tools are also an MCP server: `fused app serve <this folder>` reads `mcp.toml`.

## tools.py
Captures screens, windows, areas and web pages into the library, records, searches and reads captures. Writes new files to the library and rows to `.fused/data/hagaki.db`.
- Args: `action` (str, default "status"; one of status, start_service, stop_service, list_displays, list_windows, capture_screen, capture_window, capture_area, open_url, capture_web_page, ask_user_to_capture, start_recording, stop_recording, search_captures, latest_capture, get_capture, read_text, rename_capture, tag_capture, copy_capture, list_styles, style_capture, make_gif, verify_captures, recent_events, open_in_hagaki), `args` (str, JSON object of that action's arguments, default "{}")
- Action arguments: start_service `{restart}` (restart true reloads changed code or settings); capture_screen `{display, cursor, copy, name, read_text}`; capture_window `{app, title, window_id, copy, name, read_text}`; capture_area `{x, y, width, height, display, copy, name, read_text}` in points; open_url `{url, browser, wait_seconds}` (returns the browser window; give its id to capture_window); capture_web_page `{url, width, height, full_page, wait_seconds, dark, scale, copy, name, read_text}` (hidden browser, works when the screen is locked); ask_user_to_capture `{mode: region|window|screen|record|gif, timeout ≤ 50}`; start_recording `{x, y, width, height, display, app, title, audio: none|mic|system|both, max_seconds, gif}` (gif true saves an animated GIF at stop); stop_recording `{recording_id}`; make_gif `{id, fps, size, colors: fewer|normal|smooth, once}` (from a recording or a styled recording; 0, -1 or empty use the GIF settings); search_captures `{query, kind: all|image|video|styled, app, days, limit}`; get_capture / read_text `{id}`; rename_capture `{id, name, use_ai}`; tag_capture `{id, tags, note}`; style_capture `{id, bg, frame, padding, radius, shadow, inset, aspect, title, caption, stamp, scale, copy, zoom, zoom_amount, follow, rings, find_clicks}` (ids from list_styles; for a recording the result is an MP4 that zooms in at each click; `find_clicks` estimates clicks for an older recording with motion and Moondream 2, about 0.6 s per second of video).
- Returns: a dict; captures come back as `{"capture": {"id", "name", "path", "width", "height", "app", "title", "captured_at", "text"?}}`.
- Example: `{"action":"py","app":"hagaki","file":"tools.py","args":{"action":"capture_window","args":"{\"app\": \"Safari\"}"}}`

## api.py
The page's own backend: list, rename, tag, move to Trash, share online, save styled images, make GIFs and change settings. Writes the library, `.fused/data/hagaki.db` and `.fused/data/settings.json`; `op=share` uploads to the person's Fused account.
- Args: `op` (str, default "list"; list, item, events, settings, save_settings, rename, meta, trash, verify, verify_all, copy, copy_text, ocr, ai_rename, share, share_status, reveal, open_file, open_folder, rescan, video_info, video_plan, style_video, detect_clicks, make_gif, video_job), `id` (str), `q` (str), `kind` (str, default "all"), `app` (str), `days` (int, default 0), `limit` (int, default 200), `name` (str), `tags` (str), `note` (str), `text` (str), `patch` (str, JSON settings patch)
- Returns: `op=list` gives `{"items": [...], "total": int, "facets": {...}}`; `op=item` gives `{"item": {...}, "events": [...]}`; other ops give `{"item": {...}}` or `{"ok": true}`.
- Example: `{"action":"py","app":"hagaki","file":"api.py","args":{"op":"list","q":"invoice","limit":10}}`
