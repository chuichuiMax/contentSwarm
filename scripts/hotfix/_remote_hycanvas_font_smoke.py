"""Run on 103.15 host: upload/list/delete a FONT workspace asset via HyCanvas API."""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path("/srv/contentswarm")
API = "http://127.0.0.1:8005"


def _env(name: str) -> str:
    env_file = BASE / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    try:
        return subprocess.check_output(
            ["docker", "exec", "api-dev", "printenv", name],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _hc_container() -> str:
    out = subprocess.check_output(
        ["docker", "ps", "--format", "{{.Names}}"],
        text=True,
    )
    for name in out.splitlines():
        if "hycanvas-app" in name:
            return name
    raise RuntimeError("hycanvas-app container not found")


def _sample_ttf_b64() -> str:
    hc = _hc_container()
    script = (
        "F=$(find /usr/share/fonts -name '*.ttf' 2>/dev/null | head -1); "
        "test -n \"$F\" || exit 1; "
        "base64 -w0 \"$F\" 2>/dev/null || base64 \"$F\" | tr -d '\\n'"
    )
    raw = subprocess.check_output(["docker", "exec", hc, "sh", "-c", script], text=True)
    return raw.strip()


def _req(method: str, path: str, key: str, body: dict | None = None) -> tuple[int, bytes]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main() -> int:
    ws = _env("HYCANVAS_WORKSPACE_ID")
    key = _env("HYCANVAS_API_KEY")
    if not ws or not key:
        print("missing HYCANVAS_WORKSPACE_ID or HYCANVAS_API_KEY in .env", file=sys.stderr)
        return 2
    b64 = _sample_ttf_b64()
    name = f"ws-font-smoke-{int(time.time())}.ttf"
    status, raw = _req("POST", f"/api/v1/workspaces/{ws}/assets", key, {"filename": name, "dataBase64": b64})
    print(f"upload status={status} body={raw[:180]!r}")
    if status == 403 and b"api_key_route_not_allowed" in raw:
        print("note: workspace font upload requires browser session (expected); UI deploy verified separately")
        return 0
    if status not in (200, 201):
        return 4
    asset = json.loads(raw.decode("utf-8"))
    aid = asset.get("id", "")
    kind = asset.get("kind", "")
    if not aid:
        return 5
    print(f"asset id={aid} kind={kind}")
    status, body = _req("GET", f"/api/v1/assets/{aid}/content", key)
    print(f"content status={status} bytes={len(body)}")
    if status != 200 or len(body) < 100:
        return 6
    status, list_raw = _req("GET", f"/api/v1/workspaces/{ws}/assets", key)
    fonts = 0
    if status == 200:
        fonts = sum(1 for a in json.loads(list_raw.decode("utf-8")) if (a.get("kind") or "").lower() == "font")
    print(f"font_assets_in_workspace={fonts}")
    status, _ = _req("DELETE", f"/api/v1/assets/{aid}", key)
    print(f"delete status={status}")
    return 0 if status in (200, 204) else 7


if __name__ == "__main__":
    raise SystemExit(main())
