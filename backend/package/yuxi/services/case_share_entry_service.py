"""Public case-to-mini-program entry. Viewing this entry never creates a customer lead."""

from __future__ import annotations

import base64
import html
import json
import os
import re
import uuid
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import HTTPException
from redis.exceptions import LockError

from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.mp_service import (
    _WECHAT_ACCESS_TOKEN_INVALID,
    _invalidate_wechat_access_token,
    _wechat_access_token,
    _wechat_credentials,
)
from yuxi.services.run_queue_service import get_redis_client

CASE_PAGE = "pages/materials/shared-case"
ENTRY_CODE_PATTERN = r"^[0-9a-f]{24}$"


def entry_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"error": {"code": code, "message": message}})


async def booking_share(db, token: str):
    share = await MaterialLibraryRepository(db).share_by_token(token)
    if share is None:
        # A 64-character share may still be an asynchronous pending intent.
        from yuxi.services.material_share_jobs import read_material_share_state

        state = await read_material_share_state(db, token) if re.fullmatch(r"[0-9a-f]{64}", token) else None
        if state and state["share"]["status"] in {"pending", "failed"}:
            raise entry_error(409, "CASE_SHARE_NOT_READY", "案例尚未准备完成，请稍后重试或联系分享人")
        raise entry_error(404, "MATERIAL_SHARE_NOT_FOUND", "案例分享不存在")
    return share


async def ensure_entry_code(db, share) -> str:
    if share.mp_entry_code:
        return share.mp_entry_code
    code = await MaterialLibraryRepository(db).ensure_share_entry_code(share.id, uuid.uuid4().hex[:24])
    await db.commit()
    return code


async def resolve_entry(db, code: str) -> dict:
    if not re.fullmatch(ENTRY_CODE_PATTERN, code):
        raise entry_error(422, "CASE_ENTRY_INVALID", "预约入口无效，请重新打开分享链接")
    share = await MaterialLibraryRepository(db).share_by_entry_code(code)
    if share is None:
        raise entry_error(404, "MATERIAL_SHARE_NOT_FOUND", "案例分享不存在")
    return {"share_id": share.token, "booking": True}


def entry_environment() -> str:
    value = os.getenv("WECHAT_MP_ENTRY_ENV_VERSION", "release").strip()
    if value not in {"release", "trial", "develop"}:
        raise entry_error(503, "CASE_ENTRY_CONFIG_INVALID", "预约入口配置异常，请联系分享人")
    return value


async def wechat_entry_asset(db, token: str, kind: str):
    share = await booking_share(db, token)
    appid, secret = _wechat_credentials()
    if not appid or not secret:
        raise entry_error(503, "WECHAT_NOT_CONFIGURED", "微信预约服务未配置，请联系分享人")
    env = entry_environment()
    code = await ensure_entry_code(db, share)
    key = f"mp:case-entry:{appid}:{env}:{code}:{kind}"
    redis = await get_redis_client()
    try:
        async with redis.lock(key + ":lock", timeout=45, blocking_timeout=2):
            cached = await redis.get(key)
            if cached:
                value = json.loads(cached)
                return (base64.b64decode(value["data"]), value["type"]) if kind == "qrcode" else value["url"]
            if kind == "qrcode":
                endpoint = "https://api.weixin.qq.com/wxa/getwxacodeunlimit"
                payload = {
                    "scene": code,
                    "page": CASE_PAGE,
                    "env_version": env,
                    "width": 430,
                    "check_path": env == "release",
                }
            else:
                endpoint = "https://api.weixin.qq.com/wxa/generate_urllink"
                payload = {
                    "path": CASE_PAGE,
                    "query": urlencode({"entryCode": code, "booking": "1"}),
                    "env_version": env,
                    "is_expire": True,
                    "expire_type": 1,
                    "expire_interval": 7,
                }
            for attempt in range(2):
                try:
                    access_token = await _wechat_access_token(appid, secret, force_refresh=attempt == 1)
                    async with httpx.AsyncClient(timeout=10) as client:
                        response = await client.post(endpoint, params={"access_token": access_token}, json=payload)
                        response.raise_for_status()
                except (httpx.HTTPError, ValueError):
                    raise entry_error(503, "WECHAT_UNAVAILABLE", "微信预约入口暂时不可用，请稍后重试") from None
                content_type = response.headers.get("content-type", "").split(";")[0]
                if kind == "qrcode" and content_type in {"image/png", "image/jpeg"}:
                    value = {"data": base64.b64encode(response.content).decode(), "type": content_type}
                    await redis.setex(key, 30 * 86400, json.dumps(value))
                    return response.content, content_type
                try:
                    body = response.json()
                    errcode = int(body.get("errcode") or 0)
                except (ValueError, AttributeError, TypeError):
                    raise entry_error(503, "WECHAT_ENTRY_INVALID", "微信预约入口返回异常，请稍后重试") from None
                if errcode in _WECHAT_ACCESS_TOKEN_INVALID and attempt == 0:
                    await _invalidate_wechat_access_token()
                    continue
                if not errcode and kind == "link":
                    url = body.get("url_link", "")
                    parsed = urlsplit(url)
                    if parsed.scheme == "https" and parsed.hostname in {"wxmpurl.cn", "wxaurl.cn", "wxaurl.qq.com"}:
                        await redis.setex(key, 6 * 86400, json.dumps({"url": url}))
                        return url
                raise entry_error(503, "WECHAT_ENTRY_FAILED", "微信预约入口生成失败，请稍后重试或联系分享人")
    except LockError:
        raise entry_error(409, "CASE_ENTRY_BUSY", "预约入口正在准备，请稍后重试") from None


def render_booking_entry(share, base_url: str) -> str:
    """One responsive handoff page shared by Vue and server-rendered case pages."""
    base = base_url.rstrip("/")
    api = html.escape(f"{base}/api/material-library/shares/{share.token}", quote=True)
    case_url = html.escape(f"{base}/share/case/{share.token}", quote=True)
    title = html.escape(share.title)
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>预约参观工地 · {title}</title>
<style>
:root{{--ink:#202927;--muted:#697570;--accent:#267345;--paper:#f4f5f0;--line:#dce2d9}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);
font:16px/1.7 -apple-system,BlinkMacSystemFont,"Noto Sans SC",sans-serif}}
main{{max-width:540px;margin:8vh auto;padding:36px 28px;text-align:center;background:white;
border:1px solid var(--line);border-radius:20px}}.eyebrow{{color:var(--accent);font-size:13px;letter-spacing:3px}}
h1{{font-size:27px;line-height:1.4;margin:12px 0}}.case{{color:var(--muted);overflow-wrap:anywhere}}
.code{{width:246px;height:246px;max-width:100%;margin:22px auto;position:relative}}
.code img{{display:block;width:100%;height:100%}}.loading{{position:absolute;inset:0;display:grid;place-items:center;
background:var(--paper);border-radius:12px;color:var(--muted)}}.loading[hidden]{{display:none}}
.open{{display:block;padding:13px 20px;border-radius:10px;background:var(--accent);color:white;
text-decoration:none;font-weight:600}}.note{{font-size:14px;color:var(--muted)}}.back{{color:var(--accent)}}
.error{{color:#a33d27}}button{{border:1px solid var(--line);border-radius:6px;background:white;
padding:8px 16px;color:var(--ink);font:inherit;cursor:pointer}}@media(min-width:768px){{.open{{display:none}}}}
@media(max-width:580px){{main{{margin:20px 14px;padding:28px 22px}}}}
</style></head><body><main><div class="eyebrow">免费预约 · 实景工地</div>
<h1>来现场，看看家的样子</h1><p class="case">{title}</p>
<div class="code"><div class="loading" id="code-state" role="status">正在准备小程序码…</div>
<img id="entry-code" src="{api}/mp-code" alt="微信扫码预约参观本案例工地"></div>
<div id="code-error" class="error" role="alert" hidden><p>小程序码暂时无法加载，请重试或联系分享人。</p>
<button id="retry" type="button">重新加载小程序码</button></div>
<a class="open" href="{api}/open-mp">打开小程序预约</a>
<p class="note">电脑端请使用微信扫一扫。手机端可长按小程序码识别；若按钮无法打开，请使用小程序码。</p>
<p class="note">进入案例后选择头像、填写昵称并授权手机号，提交成功后工作人员将联系您。</p>
<a class="back" href="{case_url}">返回案例详情</a></main>
<script>
const image=document.getElementById('entry-code'),state=document.getElementById('code-state'),
error=document.getElementById('code-error');
function loaded(){{state.hidden=true;error.hidden=false;}}
image.onload=()=>{{state.hidden=true;error.hidden=true;}};
image.onerror=loaded;
if(image.complete){{if(image.naturalWidth)state.hidden=true;else loaded();}}
document.getElementById('retry').onclick=()=>{{state.hidden=false;error.hidden=true;
image.src=image.src.split('?')[0]+'?retry='+Date.now();}};
</script></body></html>"""


def render_entry_failure(message: str, booking_url: str) -> str:
    return (
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>预约入口暂不可用</title><main style='max-width:480px;margin:15vh auto;padding:24px'>"
        f"<h1>暂时无法打开小程序</h1><p>{html.escape(message)}</p>"
        f"<a href='{html.escape(booking_url, quote=True)}'>返回预约页，重试或使用小程序码</a></main></html>"
    )
