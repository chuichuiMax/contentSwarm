#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")
LOCAL_DESIGN = Path(__file__).resolve().with_suffix(".design.png")
LOCAL_BOTTOM = Path(__file__).resolve().with_suffix(".design_bottom.png")
LOCAL_JSON = Path(__file__).resolve().with_suffix(".design.json")

REMOTE = r'''
import asyncio, json, os
from pathlib import Path
import httpx
from PIL import Image

DESIGN = "2769a923-4297-4577-bd19-06455d95980b"
SOURCE_DESIGN = "7b54dec4-f940-4263-b4ec-657f9fde89a4"  # template sourceDesignId
TEMPLATE = "baa7b9b9-effc-4a98-a6c3-f1e790fe34cc"

def walk(node, out, depth=0, parent=None):
    if not isinstance(node, dict):
        return
    transform = node.get("transform") or {}
    size = node.get("size") or {}
    box = node.get("box") or {}
    x = float(transform.get("x") or 0)
    y = float(transform.get("y") or 0)
    w = float(size.get("width") or box.get("width") or 0)
    h = float(size.get("height") or box.get("height") or 0)
    text = ""
    if node.get("type") == "text":
        chunks = []
        for p in node.get("content") or []:
            for r in (p.get("runs") or []):
                chunks.append(str(r.get("text") or ""))
        text = "".join(chunks)[:120]
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    source = node.get("source") if isinstance(node.get("source"), dict) else {}
    out.append({
        "depth": depth,
        "id": node.get("id"),
        "name": node.get("name"),
        "type": node.get("type"),
        "x": round(x, 1), "y": round(y, 1), "w": round(w, 1), "h": round(h, 1),
        "bottom": round(y + h, 1),
        "right": round(x + w, 1),
        "opacity": node.get("opacity"),
        "fit": node.get("fit"),
        "locked": node.get("locked"),
        "text": text,
        "data": data,
        "assetId": source.get("assetId"),
        "fill": node.get("fill"),
        "stroke": node.get("stroke"),
        "effect": node.get("effect") or node.get("effects"),
        "keys": sorted(node.keys()),
    })
    for child in node.get("children") or []:
        walk(child, out, depth + 1, node)

async def dump_design(client, design_id, label):
    r = await client.get(f"/api/v1/designs/{design_id}/file")
    print("FILE", label, design_id, r.status_code, len(r.content))
    if r.status_code >= 400:
        print("BODY", r.text[:400])
        return None
    doc = r.json()
    Path(f"/tmp/{label}_file.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    pages = doc.get("pages") or []
    print(label, "pages", len(pages), "top_keys", list(doc.keys())[:20])
    for i, page in enumerate(pages):
        pw = float(page.get("width") or 0)
        ph = float(page.get("height") or 0)
        bg = page.get("background")
        print(label, f"PAGE{i}", pw, ph, "bg_type", type(bg).__name__, "bg", json.dumps(bg, ensure_ascii=False)[:300] if bg else None)
        nodes = []
        for child in page.get("children") or []:
            walk(child, nodes)
        print(label, "NODE_COUNT", len(nodes), "TYPES", sorted({n["type"] for n in nodes}))
        # nodes intersecting bottom 200px
        near = [n for n in nodes if n["bottom"] >= ph - 200 or n["y"] >= ph - 200]
        print(label, "NEAR_BOTTOM_COUNT", len(near))
        for n in sorted(near, key=lambda n: -n["bottom"]):
            print(label, "NEAR", json.dumps({
                k: n[k] for k in ("type","name","text","x","y","w","h","bottom","opacity","fit","assetId","data","keys")
            }, ensure_ascii=False)[:800])
        # all non-text summary
        for n in nodes:
            if n["type"] != "text":
                print(label, "NODE", json.dumps({
                    k: n[k] for k in ("type","name","x","y","w","h","bottom","opacity","fit","assetId","data")
                }, ensure_ascii=False)[:500])
        for n in nodes:
            if n["type"] == "text":
                print(label, "TEXT", json.dumps({
                    k: n[k] for k in ("name","text","x","y","w","h","bottom")
                }, ensure_ascii=False))
    return doc

async def main():
    base = (os.getenv("HYCANVAS_BASE_URL") or "").rstrip("/")
    key = (os.getenv("HYCANVAS_API_KEY") or "").strip()
    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=90.0) as client:
        await dump_design(client, DESIGN, "design")
        await dump_design(client, SOURCE_DESIGN, "source_design")

        # also list assets referenced?
        r = await client.get(f"/api/v1/designs/{DESIGN}/render.png")
        print("RENDER", r.status_code, len(r.content))
        if r.status_code == 200:
            Path("/tmp/design_render.png").write_bytes(r.content)
            im = Image.open("/tmp/design_render.png"); im.load()
            im.crop((0, im.height-140, im.width, im.height)).save("/tmp/design_render_bottom.png")
            print("RENDER_SIZE", im.size)

        # template overlay render may show decoration without photo
        for label, path in (
            ("tpl_overlay", f"/api/v1/templates/{TEMPLATE}/render-overlay.png"),
            ("tpl_render", f"/api/v1/templates/{TEMPLATE}/render.png"),
        ):
            rr = await client.get(path)
            print(label, rr.status_code, rr.headers.get("content-type"), len(rr.content))
            if rr.status_code == 200 and rr.content[:8].startswith(b"\x89PNG"):
                Path(f"/tmp/{label}.png").write_bytes(rr.content)
                im = Image.open(f"/tmp/{label}.png"); im.load()
                print(label, "size", im.size)
                im.crop((0, max(0, im.height-140), im.width, im.height)).save(f"/tmp/{label}_bottom.png")

asyncio.run(main())
'''


def run(c, cmd, timeout=200):
    chan = c.get_transport().open_session()
    chan.settimeout(timeout)
    chan.exec_command(cmd)
    chunks = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        if chan.recv_ready():
            chunks.append(chan.recv(65535))
        if chan.recv_stderr_ready():
            chunks.append(chan.recv_stderr(65535))
        if chan.exit_status_ready():
            while chan.recv_ready():
                chunks.append(chan.recv(65535))
            while chan.recv_stderr_ready():
                chunks.append(chan.recv_stderr(65535))
            break
        time.sleep(0.05)
    else:
        chan.close()
        raise TimeoutError(cmd[:120])
    return b"".join(chunks).decode("utf-8", "replace")


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        sftp = c.open_sftp()
        with sftp.file("/tmp/_diag_nodes.py", "w") as f:
            f.write(REMOTE)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_diag_nodes.py api-dev:/tmp/_diag_nodes.py && "
            "timeout 180 docker exec -w /app api-dev python /tmp/_diag_nodes.py",
            timeout=200,
        )
        OUT.write_text(out, encoding="utf-8")
        # avoid console encoding crash
        print(out.encode("ascii", "backslashreplace").decode("ascii")[-10000:])
        run(
            c,
            "docker cp api-dev:/tmp/design_render.png /tmp/design_render.png; "
            "docker cp api-dev:/tmp/design_render_bottom.png /tmp/design_render_bottom.png; "
            "docker cp api-dev:/tmp/design_file.json /tmp/design_file.json; "
            "docker cp api-dev:/tmp/tpl_overlay.png /tmp/tpl_overlay.png; "
            "docker cp api-dev:/tmp/tpl_overlay_bottom.png /tmp/tpl_overlay_bottom.png; "
            "docker cp api-dev:/tmp/source_design_file.json /tmp/source_design_file.json; "
            "ls -la /tmp/design_render*.png /tmp/tpl_overlay*.png /tmp/*_file.json 2>/dev/null | head",
            timeout=60,
        )
        sftp = c.open_sftp()
        for remote, local in (
            ("/tmp/design_render.png", LOCAL_DESIGN),
            ("/tmp/design_render_bottom.png", LOCAL_BOTTOM),
            ("/tmp/design_file.json", LOCAL_JSON),
            ("/tmp/tpl_overlay.png", Path(__file__).resolve().with_suffix(".overlay.png")),
            ("/tmp/tpl_overlay_bottom.png", Path(__file__).resolve().with_suffix(".overlay_bottom.png")),
            ("/tmp/source_design_file.json", Path(__file__).resolve().with_suffix(".source.json")),
        ):
            try:
                sftp.get(remote, str(local))
                print("GOT", local.name, local.stat().st_size)
            except Exception as e:
                print("MISS", remote, type(e).__name__, e)
        sftp.close()
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
