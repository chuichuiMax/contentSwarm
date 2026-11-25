#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

# long: 信息流 水电施工与隐蔽验收
# short: 专业干货型 油漆施工
DESIGNS = {
    "info_long": "d817340b-fcf1-4544-8409-450897b28c90",
    "dry_short": "6d39eb3c-7cc6-4f8b-abcc-9c800996d5c0",
    "craft_short": "8666aae6-6c24-45ce-bcf8-59201633d321",
    "dry_long": "cabb631f-32a2-4bb5-9548-de374774060c",
}

REMOTE = r'''
import asyncio, json, os
import httpx

DESIGNS = {
    "info_long": "d817340b-fcf1-4544-8409-450897b28c90",
    "dry_short": "6d39eb3c-7cc6-4f8b-abcc-9c800996d5c0",
    "craft_short": "8666aae6-6c24-45ce-bcf8-59201633d321",
    "dry_long": "cabb631f-32a2-4bb5-9548-de374774060c",
}

def summarize(doc):
    rows = []
    def walk(n):
        if not isinstance(n, dict):
            return
        if n.get("type") == "text":
            text = "".join(str(r.get("text") or "") for p in (n.get("content") or []) for r in (p.get("runs") or []))
            style = {}
            for p in n.get("content") or []:
                for r in p.get("runs") or []:
                    style = r.get("style") or {}
                    break
            box = n.get("box") or {}
            rows.append({
                "name": n.get("name"),
                "text": text,
                "fontSize": style.get("fontSize"),
                "fontFamily": style.get("fontFamily"),
                "fill": style.get("fill"),
                "wght": (style.get("axes") or {}).get("wght"),
                "box_w": box.get("width"),
                "box_h": box.get("height"),
                "effects": n.get("textEffects"),
            })
        for ch in n.get("children") or []:
            walk(ch)
    for page in doc.get("pages") or []:
        for ch in page.get("children") or []:
            walk(ch)
    return rows

async def main():
    base = (os.getenv("HYCANVAS_BASE_URL") or "").rstrip("/")
    key = (os.getenv("HYCANVAS_API_KEY") or "").strip()
    async with httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {key}"}, timeout=60) as client:
        for label, did in DESIGNS.items():
            r = await client.get(f"/api/v1/designs/{did}/file")
            print(label, r.status_code)
            if r.status_code != 200:
                print(r.text[:300]); continue
            print(label, json.dumps(summarize(r.json()), ensure_ascii=False)[:2500])

asyncio.run(main())
'''


def run(c, cmd, timeout=120):
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
        with sftp.file("/tmp/_compare_short_long.py", "w") as f:
            f.write(REMOTE)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_compare_short_long.py api-dev:/tmp/_compare_short_long.py && "
            "timeout 60 docker exec -w /app api-dev python /tmp/_compare_short_long.py",
            timeout=90,
        )
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "backslashreplace").decode("ascii"))
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
