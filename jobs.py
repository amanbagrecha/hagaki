from __future__ import annotations

import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import core
import gif
import reel


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return 2
    op = argv[1]
    item_id = argv[2] if len(argv) > 2 else ""
    try:
        if op == "verify_all":
            core.verify_all()
        elif op == "rescan":
            core.rescan()
            core.ocr_pending()
        elif op == "ocr_pending":
            core.ocr_pending()
        elif not item_id:
            return 2
        elif op == "share":
            core.share(item_id, core.load_settings()["share"].get("mode") or "public")
        elif op == "ocr":
            core.run_ocr(item_id)
        elif op == "ai_rename":
            core.ai_name(item_id)
        elif op == "style_video":
            reel.run_style_job(item_id)
        elif op == "detect_clicks":
            reel.run_detect_job(item_id)
        elif op == "make_gif":
            gif.run_job(item_id)
        else:
            return 2
    except Exception as exc:
        print(f"{op} failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
