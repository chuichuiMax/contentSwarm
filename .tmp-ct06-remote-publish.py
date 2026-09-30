"""Sync CT06 rule/skill changes to test server, publish rules, requeue viral prep."""
from __future__ import annotations

import io
import os
import sys
import tarfile
import time

import paramiko

HOST = "172.16.103.15"
USER = "root"
PASSWORD = "Hirun2026@"
REMOTE_ROOT = "/srv/contentswarm"
ADMIN_UID = "tiechuideUId"

SYNC_PATHS = [
    "backend/package/yuxi/content/v3/foreman_rules.py",
    "backend/package/yuxi/content/v3/formula_lexicons.py",
    "backend/package/yuxi/content/v3/fixtures/foreman_direction_matrix_v2.json",
    "backend/package/yuxi/content/v3/fixtures/foreman_rule_catalog_v1.json",
    "backend/package/yuxi/agents/skills/buildin/viral-asset-preparer/SKILL.md",
    "backend/package/yuxi/services/viral_asset_worker.py",
    "backend/scripts/publish_craft_daily_rules.py",
    "backend/scripts/reprepare_viral_types.py",
]


def connect() -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=30)
    return client


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 600) -> tuple[int, str]:
    print(f"$ {cmd}", flush=True)
    _stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    text = (out + ("\n" + err if err else "")).strip()
    if text:
        print(text, flush=True)
    print(f"exit={code}", flush=True)
    return code, text


def main() -> int:
    repo = os.path.dirname(os.path.abspath(__file__))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in SYNC_PATHS:
            path = os.path.join(repo, rel.replace("/", os.sep))
            if not os.path.isfile(path):
                print(f"missing local file: {rel}", file=sys.stderr)
                return 1
            tar.add(path, arcname=rel)
    payload = buf.getvalue()
    print(f"tar size={len(payload)} bytes", flush=True)

    client = connect()
    sftp = client.open_sftp()
    remote_tar = "/tmp/ct06-sync.tar.gz"
    with sftp.file(remote_tar, "wb") as remote:
        remote.write(payload)
    sftp.close()

    code, _ = run(client, f"mkdir -p {REMOTE_ROOT} && tar -xzf {remote_tar} -C {REMOTE_ROOT}")
    if code != 0:
        return code

    code, text = run(
        client,
        f"docker exec api-dev uv run python -c \""
        f"import asyncio; from sqlalchemy import select; "
        f"from yuxi.storage.postgres.manager import pg_manager; "
        f"from yuxi.storage.postgres.models_business import User; "
        f"async def q(): "
        f" pg_manager.initialize(); "
        f" async with pg_manager.AsyncSession() as db: "
        f"  u=(await db.execute(select(User).where(User.uid=='{ADMIN_UID}', User.is_deleted==0))).scalar_one_or_none(); "
        f"  print('user', u.uid if u else None, u.role if u else None); "
        f" await pg_manager.async_engine.dispose(); "
        f"asyncio.run(q())\"",
        timeout=120,
    )
    if code != 0 or "user None" in text:
        code2, admins = run(
            client,
            "docker exec api-dev uv run python -c \""
            "import asyncio; from sqlalchemy import select; "
            "from yuxi.storage.postgres.manager import pg_manager; "
            "from yuxi.storage.postgres.models_business import User; "
            "async def q(): "
            " pg_manager.initialize(); "
            " async with pg_manager.AsyncSession() as db: "
            "  rows=(await db.execute(select(User.uid, User.role).where(User.role.in_(['admin','superadmin']), User.is_deleted==0).limit(10))).all(); "
            "  print(rows); "
            " await pg_manager.async_engine.dispose(); "
            "asyncio.run(q())\"",
        )
        print(f"Admin uid {ADMIN_UID!r} not found; admins={admins}", file=sys.stderr)
        if code2 != 0:
            return code2
        return 1

    code, text = run(
        client,
        f"docker exec api-dev uv run python scripts/publish_craft_daily_rules.py --uid {ADMIN_UID} --publish",
        timeout=300,
    )
    if code != 0:
        return code

    code, text = run(
        client,
        "docker exec api-dev uv run python -c \""
        "import asyncio; from sqlalchemy import select; "
        "from yuxi.storage.postgres.manager import pg_manager; "
        "from yuxi.storage.postgres.models_knowledge import KnowledgeBase; "
        "async def q(): "
        " pg_manager.initialize(); "
        " async with pg_manager.AsyncSession() as db: "
        "  rows=(await db.execute(select(KnowledgeBase.kb_id, KnowledgeBase.name, KnowledgeBase.additional_params['viral_content_type'].as_string()).where(KnowledgeBase.additional_params['viral_content_type'].as_string()=='CT06'))).all(); "
        "  print('CT06_kbs', rows); "
        " await pg_manager.async_engine.dispose(); "
        "asyncio.run(q())\"",
        timeout=120,
    )

    print("Starting viral re-preparation (decoration, stale skill hash)...", flush=True)
    code, text = run(
        client,
        "docker exec api-dev uv run python scripts/reprepare_viral_types.py --apply",
        timeout=7200,
    )
    client.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
