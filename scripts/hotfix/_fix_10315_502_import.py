#!/usr/bin/env python3
"""Fix 502 on 172.16.103.15 by quarantining incomplete material-audience feature."""
from __future__ import annotations

import os
import sys
import time

import paramiko

HOST = "172.16.103.15"
USER = "root"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
ROOT = "/srv/contentswarm"


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 180) -> str:
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    if err and err.strip() and err not in out:
        return (out + "\n" + err).strip()
    return out.strip()


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWORD, timeout=20)
    try:
        print("=== audience-related files ===")
        print(
            run(
                c,
                f"ls -la {ROOT}/backend/package/yuxi/services/enterprise_gallery_audience* "
                f"{ROOT}/backend/server/routers/material_audience* 2>&1; "
                f"grep -rn 'enterprise_gallery_audience\\|material_audience\\|ContentMaterialAudience' "
                f"{ROOT}/backend --include='*.py' | head -60",
            )
        )

        print("=== routers __init__ audience lines ===")
        print(
            run(
                c,
                f"grep -n 'audience\\|material_audience' {ROOT}/backend/server/routers/__init__.py",
            )
        )

        # Quarantine incomplete feature: rename router module + remove import/include from __init__.py
        # Also keep audience service renamed (already .broken_bak).
        fix = rf'''
import pathlib, re
root = pathlib.Path("{ROOT}/backend")
init = root / "server/routers/__init__.py"
text = init.read_text(encoding="utf-8")
orig = text

# Remove import line(s)
text = re.sub(
    r"\nfrom server\.routers\.material_audience_router import[^\n]*\n",
    "\n",
    text,
)
# Remove include_router lines referencing material_audiences / material_audience
text = re.sub(
    r"\n\s*router\.include_router\([^\n]*material_audience[^\n]*\n",
    "\n",
    text,
)
# Also common pattern: include_router(material_audiences)
text = re.sub(
    r"\n\s*router\.include_router\(material_audiences[^\n]*\n",
    "\n",
    text,
)

if text != orig:
    bak = init.with_suffix(".py.bak_audience502")
    if not bak.exists():
        bak.write_text(orig, encoding="utf-8")
    init.write_text(text, encoding="utf-8")
    print("patched routers/__init__.py")
else:
    print("routers/__init__.py already clean?")
    for i, line in enumerate(orig.splitlines(), 1):
        if "audience" in line.lower():
            print(f"  {i}: {{line}}")

# Rename router file if present
for name in ["material_audience_router.py"]:
    p = root / "server/routers" / name
    if p.exists():
        bak = p.with_suffix(".py.broken_bak")
        if bak.exists():
            bak.unlink()
        p.rename(bak)
        print(f"renamed {{p}}")

# Ensure audience service stays quarantined
svc = root / "package/yuxi/services/enterprise_gallery_audience.py"
if svc.exists():
    bak = svc.with_suffix(".py.broken_bak")
    if bak.exists():
        bak.unlink()
    svc.rename(bak)
    print(f"renamed {{svc}}")

# Ensure MLS does not import it
mls = root / "package/yuxi/services/material_library_service.py"
if mls.exists():
    t = mls.read_text(encoding="utf-8")
    nt, n = re.subn(
        r"\nfrom yuxi\.services\.enterprise_gallery_audience import \([^)]*\)\n"
        r"|\nfrom yuxi\.services\.enterprise_gallery_audience import [^\n]+\n",
        "\n",
        t,
        count=10,
        flags=re.M,
    )
    if n:
        mls.write_text(nt, encoding="utf-8")
        print(f"MLS cleaned imports {{n}}")
    else:
        print("MLS imports clean")

print("--- verify ---")
print("init audience refs:")
it = init.read_text(encoding="utf-8")
for i, line in enumerate(it.splitlines(), 1):
    if "audience" in line.lower():
        print(f"  {{i}}: {{line}}")
print("files:")
for p in sorted((root/"server/routers").glob("*audience*")):
    print(" ", p)
for p in sorted((root/"package/yuxi/services").glob("*audience*")):
    print(" ", p)
'''
        sftp = c.open_sftp()
        with sftp.file("/tmp/_fix_audience_feature.py", "w") as f:
            f.write(fix)
        sftp.close()
        print("=== apply fix ===")
        print(run(c, "python3 /tmp/_fix_audience_feature.py"))

        print("=== restart api container ===")
        # compose service name is `api`, container name api-dev
        print(run(c, f"cd {ROOT} && docker compose restart api || docker restart api-dev"))

        for i in range(48):
            time.sleep(5)
            code = run(
                c,
                "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5050/api/system/health || true",
            ).strip()
            print(f"wait {i}: {code}")
            if code == "200":
                break
            if i in (5, 15, 30):
                print("--- logs ---")
                print(run(c, "docker logs api-dev --tail 25 2>&1"))

        print("=== final ===")
        print(
            run(
                c,
                "curl -s -o /dev/null -w 'api:%{http_code}\\n' http://127.0.0.1:5050/api/system/health; "
                "curl -s -o /dev/null -w '8080:%{http_code}\\n' http://127.0.0.1:8080/; "
                "docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' api-dev; "
                "docker logs api-dev --tail 40 2>&1",
            )
        )
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main())
