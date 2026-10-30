"""Diagnose slow responses on 103.15 test server."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"


def _pw() -> str:
    direct = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if direct:
        return direct
    p = Path(__file__).resolve().parents[2] / ".deploy" / "10315.password"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=_pw(), timeout=30)

    remote = r'''
import json, subprocess, time, urllib.request

def curl_ms(url, timeout=30):
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(4096)
        return int((time.perf_counter() - t0) * 1000), r.status
    except Exception as e:
        return int((time.perf_counter() - t0) * 1000), str(e)[:120]

print("=== host ===")
subprocess.run(["uptime"], check=False)
subprocess.run(["free", "-h"], check=False)

print("\n=== docker stats (no-stream) ===")
subprocess.run(
    "docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}'",
    shell=True,
    check=False,
)

print("\n=== containers ===")
subprocess.run(
    "docker ps -a --format 'table {{.Names}}\t{{.Status}}' | head -25",
    shell=True,
    check=False,
)

endpoints = [
    ("vite_root", "http://127.0.0.1:5173/"),
    ("api_health", "http://127.0.0.1:5050/api/system/health"),
    ("hycanvas_health", "http://127.0.0.1:8005/healthz"),
    ("next_root", "http://127.0.0.1:3000/"),
    ("nginx_8080", "http://127.0.0.1:8080/"),
    ("nginx_api", "http://127.0.0.1:8080/api/system/health"),
    ("nginx_hycanvas_v1", "http://127.0.0.1:8080/api/v1/workspaces"),
]
print("\n=== latency (localhost, ms) ===")
for name, url in endpoints:
    ms, st = curl_ms(url, timeout=45)
    print(f"{name:20} {ms:5}ms  {st}")

print("\n=== hycanvas recent compile? ===")
subprocess.run("docker logs --tail 15 contentswarm-hycanvas-app-1 2>&1", shell=True, check=False)

print("\n=== api-dev recent ===")
subprocess.run("docker logs --tail 8 api-dev 2>&1", shell=True, check=False)

print("\n=== dmesg oom (tail) ===")
subprocess.run("dmesg -T 2>/dev/null | grep -iE 'oom|killed process' | tail -3 || true", shell=True, check=False)

print("\n=== disk ===")
subprocess.run("df -h / /srv 2>/dev/null | head -5", shell=True, check=False)
'''
    path = "/tmp/_diag_slow.py"
    sftp = c.open_sftp()
    with sftp.file(path, "w") as f:
        f.write(remote)
    sftp.close()
    _, o, e = c.exec_command(f"python3 {path}", timeout=300)
    print(o.read().decode("utf-8", "replace"))
    err = e.read().decode("utf-8", "replace")
    if err.strip():
        print(err, file=sys.stderr)
    c.close()


if __name__ == "__main__":
    main()
