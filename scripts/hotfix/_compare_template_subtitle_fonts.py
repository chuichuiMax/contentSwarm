#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")
TEMPLATE = "fac4ace0-cd40-4e5a-8539-5d5f3d59c629"
INFO_DESIGN = "d817340b-fcf1-4544-8409-450897b28c90"

REMOTE = rf'''
import asyncio, json, os
import httpx

TEMPLATE = "{TEMPLATE}"
INFO_DESIGN = "{INFO_DESIGN}"

def text_nodes(doc):
    out = []
    def walk(n):
        if not isinstance(n, dict):
            return
        if n.get("type") == "text":
            paras = n.get("content") or []
            text = "".join(str(r.get("text") or "") for p in paras for r in (p.get("runs") or []))
            styles = []
            for p in paras:
                for r in (p.get("runs") or []):
                    styles.append(r.get("style") or {{}})
            out.append({{
                "id": n.get("id"),
                "name": n.get("name"),
                "text": text[:80],
                "fontFamily": (styles[0] or {{}}).get("fontFamily") if styles else None,
                "fontSize": (styles[0] or {{}}).get("fontSize") if styles else None,
                "fill": (styles[0] or {{}}).get("fill") if styles else None,
                "axes": (styles[0] or {{}}).get("axes") if styles else None,
                "textEffects": n.get("textEffects"),
                "styles": styles[:2],
            }})
        for ch in n.get("children") or []:
            walk(ch)
    for page in (doc.get("pages") or []):
        for ch in page.get("children") or []:
            walk(ch)
    return out

async def main():
    base = (os.getenv("HYCANVAS_BASE_URL") or "").rstrip("/")
    key = (os.getenv("HYCANVAS_API_KEY") or "").strip()
    headers = {{"Authorization": f"Bearer {{key}}"}}
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=60.0) as client:
        meta = (await client.get(f"/api/v1/templates/{{TEMPLATE}}")).raise_for_status().json()
        print("TPL", json.dumps({{
            "id": meta.get("id"),
            "title": meta.get("title"),
            "fillableFields": [
                {{
                    "key": f.get("key"),
                    "label": f.get("label"),
                    "semanticRole": f.get("semanticRole"),
                    "nodeId": f.get("nodeId"),
                    "typography_keys": list((f.get("typography") or {{}}).keys()),
                    "typography": f.get("typography"),
                }}
                for f in (meta.get("fillableFields") or [])
            ],
        }}, ensure_ascii=False)[:8000])

        info = (await client.get(f"/api/v1/designs/{{INFO_DESIGN}}/file")).raise_for_status().json()
        print("INFO_NODES", json.dumps(text_nodes(info), ensure_ascii=False)[:6000])

        # find sibling designs from same template via content DB is done outside;
        # also dump template source design if available
        src = meta.get("sourceDesignId")
        if src:
            sd = await client.get(f"/api/v1/designs/{{src}}/file")
            print("SRC_STATUS", sd.status_code)
            if sd.status_code == 200:
                print("SRC_NODES", json.dumps(text_nodes(sd.json()), ensure_ascii=False)[:6000])

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
        # also find other artifacts using same template
        find_siblings = r'''
import asyncio, json, os, asyncpg
TEMPLATE="fac4ace0-cd40-4e5a-8539-5d5f3d59c629"
async def main():
    url=(os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://","postgresql://")
    conn=await asyncpg.connect(url)
    rows=await conn.fetch(
        """
        select a.id, a.title, a.hycanvas_design_snapshot, a.runtime_config_snapshot
        from content_artifacts a
        where a.hycanvas_design_snapshot::text like $1
        order by a.id desc limit 12
        """,
        f"%{TEMPLATE}%",
    )
    print("SIBLINGS", len(rows))
    for row in rows:
        snap=row["hycanvas_design_snapshot"]
        runtime=row["runtime_config_snapshot"]
        if isinstance(snap,str):
            try: snap=json.loads(snap)
            except Exception: snap={}
        if isinstance(runtime,str):
            try: runtime=json.loads(runtime)
            except Exception: runtime={}
        style=(runtime or {}).get("creative_style")
        print("SIB", json.dumps({
            "id": row["id"],
            "title": row["title"],
            "design_id": (snap or {}).get("design_id"),
            "fields": (snap or {}).get("fields"),
            "creative_style": style,
        }, ensure_ascii=False, default=str)[:1200])
    await conn.close()
asyncio.run(main())
'''
        sftp = c.open_sftp()
        with sftp.file("/tmp/_compare_subtitle_fonts.py", "w") as f:
            f.write(REMOTE)
        with sftp.file("/tmp/_find_sibs.py", "w") as f:
            f.write(find_siblings)
        sftp.close()
        sibs = run(
            c,
            "docker cp /tmp/_find_sibs.py api-dev:/tmp/_find_sibs.py && "
            "timeout 60 docker exec -w /app api-dev python /tmp/_find_sibs.py",
            timeout=90,
        )
        fonts = run(
            c,
            "docker cp /tmp/_compare_subtitle_fonts.py api-dev:/tmp/_compare_subtitle_fonts.py && "
            "timeout 60 docker exec -w /app api-dev python /tmp/_compare_subtitle_fonts.py",
            timeout=90,
        )
        out = sibs + "\n" + fonts
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "backslashreplace").decode("ascii")[-14000:])
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
