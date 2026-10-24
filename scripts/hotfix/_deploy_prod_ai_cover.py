"""Sync AI cover prompts + catalog to production (103.17) and point OSS at hongyang01."""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import paramiko

REPO = Path(__file__).resolve().parents[2]
HOST = "172.16.103.17"
BASE = "/opt/contentSwarm"

PROD_AI_COVER_ENV: dict[str, str] = {
    "AI_COVER_OSS_BUCKET": "hongyang01",
    "AI_COVER_OSS_REGION": "cn-beijing",
    "AI_COVER_OSS_ENDPOINT": "oss-cn-beijing.aliyuncs.com",
    "AI_COVER_OSS_PUBLIC_BASE_URL": "https://hongyang01.oss-cn-beijing.aliyuncs.com",
    "AI_COVER_OSS_PREFIX": "AI-cover/",
    "AI_COVER_OSS_SUBDIR": "AI封面",
}

FILES = [
    "docker-compose.yml",
    "backend/server/worker_dev.py",
    "web/src/views/ContentStudioView.vue",
    "backend/package/yuxi/content_cover/ai_cover_prompt.py",
    "backend/package/yuxi/content_cover/ai_cover_prompt_catalog.py",
    "backend/package/yuxi/services/ai_cover_prompt_catalog_service.py",
    "backend/package/yuxi/services/content_cover_service.py",
    "backend/package/yuxi/services/content_cover_worker.py",
    "backend/package/yuxi/content/service_entry_form.py",
    "backend/package/yuxi/content/generation.py",
    "backend/package/yuxi/content/mp_studio_direct.py",
    "backend/package/yuxi/content/v3/fixtures/foreman_rule_catalog_v1.json",
    "backend/server/routers/content_cover_router.py",
    "web/src/apis/content_api.js",
    "web/src/constants/aiCoverPromptCatalog.js",
]


def _deploy_password() -> str:
    direct = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if direct:
        return direct
    for candidate in (
        REPO / ".deploy" / "10317.password",
        REPO / ".deploy" / "prod.password",
        REPO / ".deploy" / "10315.password",
        Path(__file__).resolve().parent / "10317.password",
    ):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip()
    return ""


def _patch_env(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        matched = False
        for key, value in PROD_AI_COVER_ENV.items():
            if line.startswith(f"{key}="):
                out.append(f"{key}={value}")
                seen.add(key)
                matched = True
                break
        if not matched:
            out.append(line)
    for key, value in PROD_AI_COVER_ENV.items():
        if key not in seen:
            out.append(f"{key}={value}")
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    pw = _deploy_password()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD or .deploy/prod.password", file=sys.stderr)
        return 1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=pw, timeout=30)

    sftp = client.open_sftp()
    for rel in FILES:
        src = REPO / rel
        if not src.is_file():
            print(f"missing: {src}", file=sys.stderr)
            return 1
        sftp.put(str(src), f"{BASE}/{rel}")
        print(f"OK {rel}")

    env_path = f"{BASE}/.env"
    with sftp.open(env_path, "r") as remote_env:
        env_text = remote_env.read().decode("utf-8", errors="replace")
    patched = _patch_env(env_text)
    with sftp.open(env_path, "w") as remote_env:
        remote_env.write(patched.encode("utf-8"))
    print("OK .env AI_COVER_* -> hongyang01 / AI封面")
    sftp.close()

    compose = f"cd {BASE} && docker compose --env-file .env up -d --no-deps api web worker"
    _, stdout, stderr = client.exec_command(compose, timeout=300)
    out = stdout.read().decode()
    err = stderr.read().decode()
    if out.strip():
        print(out.strip())
    if err.strip():
        print(err.strip(), file=sys.stderr)

    print("waiting for api reload...")
    time.sleep(10)

    for svc in ("api-dev", "worker-dev"):
        _, o, _ = client.exec_command(
            f"docker exec {svc} uv pip install --system 'alibabacloud-oss-v2>=1.1.0' 2>/dev/null; "
            f"docker exec {svc} python -c 'import alibabacloud_oss_v2; print(\"{svc} oss ok\")'",
            timeout=180,
        )
        print(o.read().decode().strip())

    verify = (
        "docker exec api-dev python -c \""
        "import os; "
        "from yuxi.content_cover.ai_cover_prompt_catalog import variant_from_oss_basename, specs_for_content_type; "
        "from yuxi.content_cover.ai_cover_prompt import AI_COVER_P17_PROMPT; "
        "v=[s.variant for s in specs_for_content_type(content_type_code='CT02')]; "
        "print('bucket', os.getenv('AI_COVER_OSS_BUCKET')); "
        "print('subdir', os.getenv('AI_COVER_OSS_SUBDIR')); "
        "print('variants', len(v), 'p17' in v, variant_from_oss_basename('提示词十六.png')); "
        "print('p17_prompt', '不需要显示文案' in AI_COVER_P17_PROMPT)\""
    )
    _, o, e = client.exec_command(verify, timeout=120)
    print(o.read().decode().strip())
    err_line = e.read().decode().strip()
    if err_line:
        print(err_line, file=sys.stderr)

    for variant in ("p1", "p16", "p17"):
        _, o, _ = client.exec_command(
            f"curl -s -o /dev/null -w '{variant} %{{http_code}} %{{size_download}}\\n' "
            f"'http://127.0.0.1:5050/api/content/covers/ai-cover-prompts/{variant}/preview.png?content_type_code=CT06'",
            timeout=120,
        )
        print(o.read().decode().strip())

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
