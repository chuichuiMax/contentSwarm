"""Deploy compile-brief fixes (目标人群标签 + 创作风格) to 103.15 api-dev."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path, PurePosixPath

import paramiko

HOST = "172.16.103.15"
BASE = "/srv/contentswarm"
FILES = (
    "backend/package/yuxi/services/target_audience_tag_service.py",
    "backend/package/yuxi/services/mp_service.py",
    "backend/package/yuxi/content/mp_studio_direct.py",
)


def _pw() -> str:
    for key in ("DEPLOY_SSH_PASSWORD", "CONTENTSWARM_TEST_SSH_PASSWORD"):
        direct = os.environ.get(key, "").strip()
        if direct:
            return direct
    repo = Path(__file__).resolve().parents[2]
    for p in (
        repo / ".deploy" / "10315.password",
        Path.home() / ".contentSwarm_test_ssh",
    ):
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return ""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    pw = _pw()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD", file=sys.stderr)
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    sftp = c.open_sftp()
    for rel in FILES:
        src = Path(__file__).resolve().parents[2] / rel
        remote = f"{BASE}/{PurePosixPath(rel)}"
        sftp.put(str(src), remote)
        print("OK", rel)
    sftp.close()
    checks = (
        f"grep -c _HASHTAG_TOKEN {BASE}/backend/package/yuxi/services/target_audience_tag_service.py",
        f"grep -c MP_BRIEF_PREP_FAILED {BASE}/backend/package/yuxi/services/mp_service.py",
        f"grep -c _canonical_style_type_name {BASE}/backend/package/yuxi/content/mp_studio_direct.py",
    )
    for cmd in checks:
        _, o, _ = c.exec_command(cmd, timeout=30)
        print("verify:", cmd.split()[-1], o.read().decode().strip())
    verify_py = (
        "docker exec api-dev uv run --no-sync python -c "
        "\"from yuxi.services.target_audience_tag_service import parse_audience_tag_lines; "
        "print(parse_audience_tag_lines('#装修 #家装')[:2])\""
    )
    _, o, e = c.exec_command(verify_py, timeout=90)
    print("hashtag parse:", o.read().decode("utf-8", errors="replace").strip())
    err = e.read().decode("utf-8", errors="replace").strip()
    if err:
        print(err, file=sys.stderr)
    _, o, _ = c.exec_command("docker restart api-dev", timeout=120)
    print(o.read().decode())
    time.sleep(8)
    _, o, _ = c.exec_command("curl -sS -m 15 http://127.0.0.1:5050/api/system/health", timeout=30)
    print("health:", o.read().decode().strip())
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
