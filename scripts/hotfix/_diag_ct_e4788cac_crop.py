#!/usr/bin/env python3
"""Diagnose why ct_e4788cac cover footer is cropped."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")
LOCAL_TPL = Path(__file__).resolve().with_suffix(".tpl.png")
LOCAL_DESIGN = Path(__file__).resolve().with_suffix(".design.png")
LOCAL_TPL_BOTTOM = Path(__file__).resolve().with_suffix(".tpl_bottom.png")
LOCAL_DESIGN_BOTTOM = Path(__file__).resolve().with_suffix(".design_bottom.png")

REMOTE = r'''
import asyncio, json, os
from pathlib import Path

import httpx
from PIL import Image, ImageStat

TEMPLATE = "baa7b9b9-effc-4a98-a6c3-f1e790fe34cc"
DESIGN = "2769a923-4297-4577-bd19-06455d95980b"


def walk(node, out, depth=0):
    if not isinstance(node, dict):
        return
    ntype = node.get("type")
    transform = node.get("transform") or {}
    size = node.get("size") or {}
    box = node.get("box") or {}
    x = float(transform.get("x") or 0)
    y = float(transform.get("y") or 0)
    w = float(size.get("width") or box.get("width") or 0)
    h = float(size.get("height") or box.get("height") or 0)
    text = ""
    if ntype == "text":
        chunks = []
        for p in node.get("content") or []:
            for r in (p.get("runs") or []):
                chunks.append(str(r.get("text") or ""))
        text = "".join(chunks)[:80]
    out.append({
        "id": node.get("id"), "name": node.get("name"), "type": ntype,
        "x": x, "y": y, "w": w, "h": h, "bottom": y + h, "text": text, "depth": depth,
    })
    for child in node.get("children") or []:
        walk(child, out, depth + 1)


def summarize(label, file_obj):
    pages = (file_obj or {}).get("pages") or []
    print(f"==== {label} pages={len(pages)} ====")
    for i, page in enumerate(pages):
        pw = float(page.get("width") or 0)
        ph = float(page.get("height") or 0)
        print(f"PAGE {i} {pw}x{ph}")
        nodes = []
        for child in page.get("children") or []:
            walk(child, nodes)
        text_nodes = [n for n in nodes if n["type"] == "text"]
        overflow = [
            n for n in text_nodes
            if n["bottom"] > ph + 0.5 or n["y"] < -0.5 or n["x"] + n["w"] > pw + 0.5
        ]
        near_bottom = sorted(text_nodes, key=lambda n: -n["bottom"])[:8]
        print("TEXT_COUNT", len(text_nodes), "OVERFLOW", len(overflow))
        for n in near_bottom:
            print("NEAR_BOTTOM", json.dumps({
                "name": n["name"], "text": n["text"],
                "y": round(n["y"], 1), "h": round(n["h"], 1),
                "bottom": round(n["bottom"], 1),
                "overflow_y": round(n["bottom"] - ph, 1),
                "x": round(n["x"], 1), "w": round(n["w"], 1),
            }, ensure_ascii=False))
        for n in overflow:
            print("OVERFLOW_NODE", json.dumps({
                "name": n["name"], "type": n["type"], "text": n["text"],
                "x": round(n["x"], 1), "y": round(n["y"], 1),
                "w": round(n["w"], 1), "h": round(n["h"], 1),
                "bottom": round(n["bottom"], 1), "page_h": ph,
            }, ensure_ascii=False))
        for n in [n for n in nodes if n["type"] == "image"][:6]:
            print("IMAGE", json.dumps({
                "name": n["name"], "y": round(n["y"], 1), "h": round(n["h"], 1),
                "bottom": round(n["bottom"], 1),
                "overflow_y": round(n["bottom"] - ph, 1),
            }, ensure_ascii=False))


def bottom_stats(path, label):
    im = Image.open(path); im.load()
    rgb = im.convert("RGB")
    bottom = rgb.crop((0, im.height - 100, im.width, im.height))
    px = list(bottom.getdata())
    bright = sum(1 for r, g, b in px if r + g + b > 600)
    print(label, "size", im.size, "bottom_bright", bright, "/", len(px),
          "mean", [round(x, 1) for x in ImageStat.Stat(bottom).mean])
    bottom.save(str(path).replace(".png", "_bottom.png"))


async def main():
    base = (os.getenv("HYCANVAS_BASE_URL") or "").rstrip("/")
    key = (os.getenv("HYCANVAS_API_KEY") or "").strip()
    print("BASE", base, "KEY_LEN", len(key), "WS", os.getenv("HYCANVAS_WORKSPACE_ID"))
    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=60.0) as client:
        meta = (await client.get(f"/api/v1/templates/{TEMPLATE}")).raise_for_status().json()
        print("TPL_META", json.dumps({
            "id": meta.get("id"), "title": meta.get("title"),
            "format": meta.get("format"), "tags": meta.get("tags"),
            "fillableFields": meta.get("fillableFields"),
        }, ensure_ascii=False)[:4000])

        tpl_file = (await client.get(f"/api/v1/templates/{TEMPLATE}/file")).raise_for_status().json()
        summarize("TEMPLATE_FILE", tpl_file if isinstance(tpl_file, dict) else {"pages": []})
        Path("/tmp/tpl_file.json").write_text(
            json.dumps(tpl_file, ensure_ascii=False)[:200000], encoding="utf-8"
        )

        design_file = (await client.get(f"/api/v1/designs/{DESIGN}/file")).raise_for_status().json()
        doc = design_file.get("file") if isinstance(design_file, dict) and "file" in design_file else design_file
        if not isinstance(doc, dict):
            doc = design_file
        summarize("DESIGN_FILE", doc if isinstance(doc, dict) else {"pages": []})
        print("DESIGN_TOP_KEYS", list(design_file.keys())[:30] if isinstance(design_file, dict) else type(design_file))
        Path("/tmp/design_file.json").write_text(
            json.dumps(design_file, ensure_ascii=False)[:200000], encoding="utf-8"
        )

        r = await client.get(f"/api/v1/templates/{TEMPLATE}/render.png")
        r.raise_for_status()
        Path("/tmp/tpl_render.png").write_bytes(r.content)
        print("TPL_RENDER", r.headers.get("content-type"), len(r.content))
        bottom_stats("/tmp/tpl_render.png", "tpl_render")

        r2 = await client.get(f"/api/v1/designs/{DESIGN}/render.png")
        r2.raise_for_status()
        Path("/tmp/design_render.png").write_bytes(r2.content)
        print("DESIGN_RENDER", r2.headers.get("content-type"), len(r2.content))
        bottom_stats("/tmp/design_render.png", "design_render")

asyncio.run(main())
'''


def run(c, cmd, timeout=180):
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
        with sftp.file("/tmp/_diag_ct_e4788cac_crop.py", "w") as f:
            f.write(REMOTE)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_diag_ct_e4788cac_crop.py api-dev:/tmp/_diag_ct_e4788cac_crop.py && "
            "timeout 150 docker exec -w /app api-dev python /tmp/_diag_ct_e4788cac_crop.py",
            timeout=180,
        )
        OUT.write_text(out, encoding="utf-8")
        print(out[-8000:])
        # pull images
        sftp = c.open_sftp()
        for remote, local in (
            ("/tmp/tpl_render.png", LOCAL_TPL),
            ("/tmp/design_render.png", LOCAL_DESIGN),
            ("/tmp/tpl_render_bottom.png", LOCAL_TPL_BOTTOM),
            ("/tmp/design_render_bottom.png", LOCAL_DESIGN_BOTTOM),
        ):
            try:
                # files are inside container; copy out first
                pass
            except Exception as e:
                print("skip", remote, e)
        sftp.close()
        pull = run(
            c,
            "docker cp api-dev:/tmp/tpl_render.png /tmp/tpl_render.png; "
            "docker cp api-dev:/tmp/design_render.png /tmp/design_render.png; "
            "docker cp api-dev:/tmp/tpl_render_bottom.png /tmp/tpl_render_bottom.png; "
            "docker cp api-dev:/tmp/design_render_bottom.png /tmp/design_render_bottom.png; "
            "ls -la /tmp/tpl_render*.png /tmp/design_render*.png",
            timeout=60,
        )
        print(pull)
        sftp = c.open_sftp()
        for remote, local in (
            ("/tmp/tpl_render.png", LOCAL_TPL),
            ("/tmp/design_render.png", LOCAL_DESIGN),
            ("/tmp/tpl_render_bottom.png", LOCAL_TPL_BOTTOM),
            ("/tmp/design_render_bottom.png", LOCAL_DESIGN_BOTTOM),
        ):
            try:
                sftp.get(remote, str(local))
                print("GOT", local, local.stat().st_size)
            except Exception as e:
                print("MISS", remote, e)
        sftp.close()
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
