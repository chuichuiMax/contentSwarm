"""Verify 103.15 compile-brief related backend after deploy."""
from __future__ import annotations

import sys

import paramiko

from _deploy_10315_brief_fix import HOST, _pw


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    pw = _pw()
    if not pw:
        print("Set CONTENTSWARM_TEST_SSH_PASSWORD or .deploy/10315.password", file=sys.stderr)
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    cmd = (
        "curl -sS -m 20 http://127.0.0.1:5050/api/system/health; echo; "
        "docker exec api-dev uv run --no-sync python -c "
        "\"from yuxi.services.target_audience_tag_service import parse_audience_tag_lines; "
        "from yuxi.content.mp_studio_direct import creative_style_options; "
        "assert parse_audience_tag_lines('#装修 #家装')[:2]==['装修','家装']; "
        "assert len(creative_style_options('CT06','装修知识科普'))==2; print('brief_modules_ok')\""
    )
    _, o, e = c.exec_command(cmd, timeout=120)
    print(o.read().decode("utf-8", errors="replace"))
    err = e.read().decode("utf-8", errors="replace").strip()
    if err:
        print(err, file=sys.stderr)
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
