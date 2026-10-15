#!/usr/bin/env python3
"""Query WeChat MP server/download domains (uses WECHAT_MP_* from container env)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request


def post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    appid = os.environ.get("WECHAT_MP_APPID", "").strip()
    secret = os.environ.get("WECHAT_MP_SECRET", "").strip()
    oss_public = os.environ.get("OSS_PUBLIC_BASE_URL", "").strip()
    if not appid or not secret:
        print("MISSING_WECHAT_CREDENTIALS", file=sys.stderr)
        return 1

    token_body = post_json(
        "https://api.weixin.qq.com/cgi-bin/stable_token",
        {"grant_type": "client_credential", "appid": appid, "secret": secret},
    )
    token = token_body.get("access_token")
    if not token:
        legacy_url = (
            "https://api.weixin.qq.com/cgi-bin/token?"
            + urllib.parse.urlencode({"grant_type": "client_credential", "appid": appid, "secret": secret})
        )
        with urllib.request.urlopen(legacy_url, timeout=20) as resp:
            token_body = json.loads(resp.read().decode("utf-8"))
        token = token_body.get("access_token")
    if not token:
        print("TOKEN_FAILED", json.dumps(token_body, ensure_ascii=False), file=sys.stderr)
        return 1

    # 与线上一致：优先使用 Redis 中 mp_service 缓存的 access_token
    try:
        cached = subprocess.check_output(
            ["redis-cli", "-h", os.environ.get("REDIS_HOST", "redis"), "GET", "mp:wx:access_token"],
            stderr=subprocess.DEVNULL,
            timeout=5,
            text=True,
        ).strip()
        if cached:
            token = cached
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass

    domain_body = post_json(
        f"https://api.weixin.qq.com/wxa/modify_domain?access_token={urllib.parse.quote(token)}",
        {"action": "get"},
    )
    effective = post_json(
        f"https://api.weixin.qq.com/wxa/get_effective_domain?access_token={urllib.parse.quote(token)}",
        {},
    )
    print("modify_domain:", json.dumps(domain_body, ensure_ascii=False, indent=2))
    print("effective_domain:", json.dumps(effective, ensure_ascii=False, indent=2))

    oss_host = urllib.parse.urlparse(oss_public).netloc if oss_public else ""
    download_domains = (
        domain_body.get("downloaddomain")
        or effective.get("download_domain", {}).get("list")
        or effective.get("downloaddomain")
        or []
    )
    request_domains = (
        domain_body.get("requestdomain")
        or effective.get("request_domain", {}).get("list")
        or effective.get("requestdomain")
        or []
    )
    print("\nOSS_HOST", oss_host)
    print("OSS_IN_DOWNLOAD", oss_host in download_domains if oss_host else False)
    print("HI_RUN_IN_REQUEST", any("hi-run.net" in d for d in request_domains))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
