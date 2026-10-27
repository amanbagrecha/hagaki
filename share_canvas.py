from __future__ import annotations

import io
import json
import os
import sys
import zipfile

import fused
from fused._global_api import get_api
from fused_render import _fused_share_app as fs
from fused_render import share_file

COLUMNS = 4


def upload(req: dict) -> dict:
    local = req["file"]
    if not os.path.isfile(local):
        raise fs.ShareError(f"no such file: {local}")
    rule = share_file.resolve_viewer(local)
    if not rule:
        raise fs.ShareError(f"Fused has no viewer for {os.path.basename(local)}")
    remote = f"fd://{fs._handle()}/hagaki/shared/{req['folder']}/{fs._sha10(local)}/{os.path.basename(local)}"
    fused.api.upload(local, remote)
    return {"remote": remote, "s3_uri": fs._resolve_remote(remote), "viewer": rule["token"]}


def _canvas_toml(name: str, entries: list[dict]) -> str:
    lines = ['type = "canvas"', "version = 2", f"name = {json.dumps(name)}", "", "[canvas]",
             "comments = []", "edges = []", ""]
    for i, e in enumerate(entries):
        lines += ["[[canvas.nodes]]", f"udfName = {json.dumps(e['slug'])}",
                  f"x = {(i % COLUMNS) * 950}", f"y = {(i // COLUMNS) * 680}", "zIndex = 1",
                  "width = 900", "height = 620", f"title = {json.dumps(e['title'])}",
                  'description = "Hagaki capture"', "visible = true", ""]
    lines += ["[canvas.viewport]", "x = 400", "y = 300", "zoom = 0.4", ""]
    return "\n".join(lines)


def publish(req: dict) -> dict:
    api = get_api()
    name, scope, entries = req["canvas"], req["scope"], req["entries"]
    existing = fs._find_collection(api, name)
    if existing:
        canvas_id, current = existing["id"], fs._scope(existing.get("access_scope"))
    else:
        created = api.create_collection(name=name, access_scope=scope)
        canvas_id, current = created.id, fs._scope(created.access_scope)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("canvas.toml", _canvas_toml(name, entries))
        for e in entries:
            zf.writestr(e["slug"] + ".py", fs._wrapper_source(e["slug"], e["uri"], e["title"], e["viewer"]))
    api.import_collection_toml_zip(canvas_id, buf.getvalue())
    if current != scope:
        api.update_collection(canvas_id, name=name, access_scope=scope)
        shared = api.share_collection(canvas_id, new_token=True)
    else:
        shared = api.share_collection(canvas_id)
    token = shared.share_token
    if not token:
        raise fs.ShareError("Fused shared the canvas but returned no share token.")
    session = None
    if req.get("session"):
        session, _expires = fs._mint_session(api, token)
    for old in req.get("delete") or []:
        try:
            fused.api.delete(old)
        except Exception as exc:
            print(f"could not delete the previous upload {old}: {exc}", file=sys.stderr)
    return {"canvas_id": canvas_id, "base": f"{fs.OPTIONS.shared_udf_base_url}/{token}",
            "session_token": session, "workbench_url": fs._workbench_url(fs._handle(), name)}


ACTIONS = {"upload": upload, "publish": publish}


def main() -> int:
    try:
        req = json.load(sys.stdin)
        out = ACTIONS[req["action"]](req)
    except Exception as exc:
        msg = str(exc)
        if any(k in msg.lower() for k in ("401", "unauthorized", "not authenticated")):
            msg = "Fused refused the request as not signed in. Sign in to Fused and retry."
        print(msg[-2000:], file=sys.stderr)
        return 1
    json.dump(out, sys.stdout, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
