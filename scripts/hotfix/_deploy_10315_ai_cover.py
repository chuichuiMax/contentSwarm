"""One-shot: sync AI cover + ContentStudioView to 103.15 and recreate api/web."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import paramiko

REPO = Path(__file__).resolve().parents[2]
HOST = "172.16.103.15"
BASE = "/srv/contentswarm"


def _deploy_password() -> str:
    direct = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if direct:
        return direct
    for candidate in (
        REPO / ".deploy" / "10315.password",
        REPO / ".deploy" / "password",
        Path(__file__).resolve().parent / "10315.password",
    ):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip()
    return ""
FILES = [
    "docker-compose.yml",
    "backend/server/worker_dev.py",
    "web/src/views/ContentStudioView.vue",
    "backend/package/yuxi/content_cover/ai_cover_prompt.py",
    "backend/package/yuxi/services/content_cover_service.py",
    "backend/package/yuxi/services/content_cover_worker.py",
    "backend/package/yuxi/content/service_entry_form.py",
    "backend/package/yuxi/content/generation.py",
    "backend/package/yuxi/content/mp_studio_direct.py",
    "backend/package/yuxi/content/v3/fixtures/foreman_rule_catalog_v1.json",
    "backend/package/yuxi/content_cover/ai_cover_prompt_catalog.py",
    "backend/package/yuxi/services/ai_cover_prompt_catalog_service.py",
    "backend/server/routers/content_cover_router.py",
    "web/src/apis/content_api.js",
    "web/src/constants/aiCoverPromptCatalog.js",
]


def main() -> int:
    pw = _deploy_password()
    if not pw:
        print(
            "Set DEPLOY_SSH_PASSWORD or create .deploy/10315.password (gitignored)",
            file=sys.stderr,
        )
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
    time.sleep(8)

    verify = (
        "docker exec api-dev python -c \""
        "import asyncio; "
        "from yuxi.content_cover.ai_cover_prompt_catalog import specs_for_content_type; "
        "s=specs_for_content_type(content_type_code='CT06'); "
        "labels=[x.label for x in s]; "
        "variants=[x.variant for x in s]; "
        "print('CT06 count', len(s), 'p14' in variants, labels[labels.index('提示词八') if '提示词八' in labels else -1]); "
        "import alibabacloud_oss_v2; "
        "print('oss_v2 ok')\""
    )
    _, o, e = client.exec_command(verify, timeout=120)
    print(o.read().decode().strip())
    err_line = e.read().decode().strip()
    if err_line and "oss_v2 ok" not in err_line:
        print(err_line, file=sys.stderr)
        _, o2, _ = client.exec_command(
            "docker exec api-dev uv pip install --system 'alibabacloud-oss-v2>=1.1.0'",
            timeout=180,
        )
        print(o2.read().decode().strip())

    for svc in ("api-dev", "worker-dev"):
        _, o, _ = client.exec_command(
            f"docker exec {svc} uv pip install --system 'alibabacloud-oss-v2>=1.1.0' 2>/dev/null; "
            f"docker exec {svc} python -c 'import alibabacloud_oss_v2; print(\"{svc} oss ok\")'",
            timeout=180,
        )
        print(o.read().decode().strip())

    p12_check = (
        "docker exec api-dev python -c \""
        "from yuxi.content_cover.ai_cover_prompt import build_site_header_lines; "
        "a,b=build_site_header_lines(title='封面主标题', subtitle='副标题', form_values={}); "
        "print('p12_fallback', a, b)\""
    )
    p17_check = (
        "docker exec api-dev python -c \""
        "from yuxi.content_cover.ai_cover_prompt_catalog import variant_from_oss_basename, specs_for_content_type; "
        "from yuxi.content_cover.ai_cover_prompt import AI_COVER_P17_PROMPT, ai_cover_suppresses_post_title_overlay; "
        "v=[s.variant for s in specs_for_content_type(content_type_code='CT02')]; "
        "print('p17', variant_from_oss_basename('提示词十六.png'), 'p17' in v, "
        "'不需要显示文案' in AI_COVER_P17_PROMPT, ai_cover_suppresses_post_title_overlay('p17'))\""
    )
    _, o, _ = client.exec_command(p12_check, timeout=60)
    print(o.read().decode().strip())
    _, o, _ = client.exec_command(p17_check, timeout=60)
    print(o.read().decode().strip())

    _, o, _ = client.exec_command(
        "curl -s -o /dev/null -w 'p17 %{http_code} %{size_download}\\n' "
        "'http://127.0.0.1:5050/api/content/covers/ai-cover-prompts/p17/preview.png?content_type_code=CT06'",
        timeout=120,
    )
    print(o.read().decode().strip())

    _, o, _ = client.exec_command(
        "curl -s -o /dev/null -w 'p14 %{http_code} %{size_download}\\n' "
        "'http://127.0.0.1:5050/api/content/covers/ai-cover-prompts/p14/preview.png?content_type_code=CT06'",
        timeout=120,
    )
    print(o.read().decode().strip())

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
