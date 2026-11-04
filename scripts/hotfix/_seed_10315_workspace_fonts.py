"""Upload local font files to ContentSwarm team HyCanvas workspace on 103.15."""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

import paramiko

REPO = Path(__file__).resolve().parents[2]
HOST = "172.16.103.15"
BASE = "/srv/contentswarm"
REMOTE_FONT_DIR = f"{BASE}/tmp/workspace_font_seed"
API = "http://127.0.0.1:8005"
WORKSPACE_FONT_TAG = "oc-font"
FONT_SUFFIXES = {".ttf", ".otf", ".woff", ".woff2"}


def _pw() -> str:
    direct = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if direct:
        return direct
    for p in (REPO / ".deploy" / "10315.password",):
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return ""


def _local_font_dir() -> Path:
    env = os.environ.get("FONT_PACK_DIR", "").strip()
    if env:
        return Path(env)
    extracted = REPO / "tmp" / "font_pack"
    if extracted.is_dir() and any(extracted.rglob("*.ttf")):
        return extracted
    zip_path = Path(os.environ.get("FONT_PACK_ZIP", r"C:\Users\10533\Desktop\字体包.zip"))
    if not zip_path.is_file():
        raise FileNotFoundError(f"font zip not found: {zip_path}")
    extracted.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(extracted)
    return extracted


def _collect_fonts(root: Path) -> list[Path]:
    fonts = sorted(
        p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in FONT_SUFFIXES
    )
    if not fonts:
        raise RuntimeError(f"no font files under {root}")
    return fonts


def _ssh_run(client: paramiko.SSHClient, cmd: str, timeout: int = 600) -> tuple[int, str, str]:
    _, o, e = client.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", errors="replace")
    err = e.read().decode("utf-8", errors="replace")
    return o.channel.recv_exit_status(), out, err


def _upload_tree(sftp: paramiko.SFTPClient, local_root: Path, remote_root: str) -> None:
    for path in _collect_fonts(local_root):
        rel = path.relative_to(local_root).as_posix()
        remote = f"{remote_root}/{rel}"
        remote_parent = str(PurePosixPath(remote).parent)
        try:
            sftp.stat(remote_parent)
        except OSError:
            parts = PurePosixPath(remote_parent).parts
            cur = ""
            for part in parts:
                cur = f"{cur}/{part}" if cur else part
                try:
                    sftp.stat(cur)
                except OSError:
                    sftp.mkdir(cur)
        sftp.put(str(path), remote)
        print("  put", rel)


def main() -> int:
    pw = _pw()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD", file=sys.stderr)
        return 1

    local_root = _local_font_dir()
    fonts = _collect_fonts(local_root)
    print(f"local fonts: {len(fonts)} from {local_root}")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=pw, timeout=30)
    _ssh_run(client, f"rm -rf {REMOTE_FONT_DIR} && mkdir -p {REMOTE_FONT_DIR}")
    sftp = client.open_sftp()
    _upload_tree(sftp, local_root, REMOTE_FONT_DIR)
    sftp.close()

    remote_py = r'''
import base64, json, os, subprocess, sys, urllib.error, urllib.parse, urllib.request, http.cookiejar
from pathlib import Path

API = "http://127.0.0.1:8005"
FONT_DIR = "/srv/contentswarm/tmp/workspace_font_seed"
TAG = "oc-font"
SUFFIX = {".ttf", ".otf", ".woff", ".woff2"}

def env_from_api(name):
    return subprocess.check_output(["docker", "exec", "api-dev", "printenv", name], text=True).strip()

WS = env_from_api("HYCANVAS_WORKSPACE_ID")
KEY = env_from_api("HYCANVAS_API_KEY")
if not WS or not KEY:
    sys.exit("missing HYCANVAS env")

def session_opener():
    req = urllib.request.Request(
        f"{API}/api/v1/auth/integration-ticket/workspace/{WS}",
        method="POST",
        headers={"Authorization": f"Bearer {KEY}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        ticket = json.loads(resp.read().decode())["ticket"]
    nxt = urllib.parse.quote("/dashboard/", safe="")
    redeem = (
        f"{API}/api/v1/auth/integration?ticket={urllib.parse.quote(ticket)}"
        f"&workspaceId={WS}&next={nxt}"
    )
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.open(redeem, timeout=60)
    return opener

def upload(opener, path: Path):
    data = path.read_bytes()
    body = json.dumps({"filename": path.name, "dataBase64": base64.b64encode(data).decode()}).encode()
    req = urllib.request.Request(
        f"{API}/api/v1/workspaces/{WS}/assets",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with opener.open(req, timeout=300) as resp:
        asset = json.loads(resp.read().decode())
    aid = asset.get("id")
    if aid:
        patch = json.dumps({"tags": list({*(asset.get("tags") or []), TAG})}).encode()
        preq = urllib.request.Request(
            f"{API}/api/v1/assets/{aid}",
            data=patch,
            method="PATCH",
            headers={"Content-Type": "application/json"},
        )
        try:
            opener.open(preq, timeout=60)
        except urllib.error.HTTPError:
            pass
    return asset

fonts = sorted(p for p in Path(FONT_DIR).rglob("*") if p.suffix.lower() in SUFFIX)
opener = session_opener()
ok = skip = fail = 0
for p in fonts:
    mb = p.stat().st_size / (1024 * 1024)
    if mb > 80:
        print(f"skip huge {p.name} ({mb:.1f}MB)")
        skip += 1
        continue
    try:
        asset = upload(opener, p)
        print(f"ok {p.name} -> {asset.get('id')} kind={asset.get('kind')}")
        ok += 1
    except Exception as e:
        print(f"fail {p.name}: {e}")
        fail += 1
print(f"summary ok={ok} skip={skip} fail={fail}")
sys.exit(0 if ok and not fail else (1 if fail else 0))
'''
    remote_script = f"{BASE}/scripts/hotfix/_remote_seed_workspace_fonts.py"
    _ssh_run(client, f"mkdir -p {BASE}/scripts/hotfix")
    sftp = client.open_sftp()
    with sftp.file(remote_script, "w") as f:
        f.write(remote_py.encode("utf-8"))
    sftp.close()

    code, out, err = _ssh_run(client, f"python3 {remote_script}", timeout=3600)
    print(out)
    if err.strip():
        print(err, file=sys.stderr)
    client.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
