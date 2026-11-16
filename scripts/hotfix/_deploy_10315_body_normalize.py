"""Deploy body normalize + hotpatch ct_9ec75dff on 103.15."""
from __future__ import annotations

import os
import sys
from pathlib import Path, PurePosixPath

import paramiko

HOST = "172.16.103.15"
BASE = "/srv/contentswarm"
TASK_ID = "ct_9ec75dff1cb44dad8fa905feff5a7fa9"
FILES = (
    "backend/package/yuxi/utils/line_breaks.py",
    "backend/package/yuxi/content/generation.py",
    "backend/package/yuxi/storage/postgres/models_content.py",
    "backend/package/yuxi/agents/buildin/content_workflow/graph.py",
    "backend/package/yuxi/services/content_service.py",
    "backend/test/unit/content/test_normalize_published_body.py",
)


def _pw() -> str:
    for key in ("DEPLOY_SSH_PASSWORD", "CONTENTSWARM_TEST_SSH_PASSWORD"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    p = Path(__file__).resolve().parents[2] / ".deploy" / "10315.password"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def main() -> int:
    pw = _pw()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD", file=sys.stderr)
        return 1
    repo = Path(__file__).resolve().parents[2]
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    sftp = c.open_sftp()
    for rel in FILES:
        sftp.put(str(repo / rel), f"{BASE}/{PurePosixPath(rel)}")
        print("OK", rel)
    sftp.close()

    _, o, e = c.exec_command(
        "docker exec api-dev uv run --no-sync pytest test/unit/content/test_normalize_published_body.py -q",
        timeout=120,
    )
    print(o.read().decode("utf-8", errors="replace"))
    err = e.read().decode("utf-8", errors="replace").strip()
    if err:
        print(err, file=sys.stderr)

    patch = f'''
import asyncio
from sqlalchemy import text
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils.line_breaks import normalize_published_body

TASK_ID = "{TASK_ID}"

async def main():
    async with pg_manager.get_async_session_context() as db:
        row = (await db.execute(text(
            "SELECT id, body FROM content_artifacts WHERE task_id=:tid ORDER BY updated_at DESC NULLS LAST LIMIT 1"
        ), {{"tid": TASK_ID}})).mappings().first()
        if not row:
            print("NO_ARTIFACT")
            return
        new_body = normalize_published_body(row["body"] or "")
        await db.execute(text(
            "UPDATE content_artifacts SET body=:body, updated_at=NOW() WHERE id=:id"
        ), {{"body": new_body, "id": row["id"]}})
        await db.commit()
        print("PATCHED", row["id"])
        print("has_zaishou", "在手工地" in new_body)
        print("has_zaijian", "在建工地" in new_body)
        print("blank_para", new_body.count(chr(10)+chr(10)))

asyncio.run(main())
'''
    stdin, stdout, stderr = c.exec_command("docker exec -i api-dev uv run --no-sync python -", timeout=120)
    stdin.write(patch)
    stdin.channel.shutdown_write()
    print(stdout.read().decode("utf-8", errors="replace"))
    err = stderr.read().decode("utf-8", errors="replace").strip()
    if err:
        # filter noisy logs
        for line in err.splitlines():
            if "ERROR" in line or "Traceback" in line or "Error" in line:
                print(line, file=sys.stderr)
    c.exec_command("docker restart api-dev", timeout=120)
    c.close()
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
