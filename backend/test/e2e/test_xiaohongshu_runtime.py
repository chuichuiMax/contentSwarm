from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import yuxi.integrations.xiaohongshu.runtime as runtime_module
import yuxi.integrations.xiaohongshu.session_manager as session_manager_module
from yuxi.integrations.xiaohongshu import XiaohongshuRuntime
from yuxi.integrations.xiaohongshu.session_manager import XiaohongshuBrowserSessionManager

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
    pytest.mark.skipif(
        os.getenv("XHS_BROWSER_E2E") != "1",
        reason="Set XHS_BROWSER_E2E=1 in an environment with Patchright Chromium installed.",
    ),
]


PUBLISH_FORM = """<!doctype html>
<html lang="zh-CN">
  <body>
    <input type="file" accept="image/png,image/jpeg" />
    <input placeholder="填写标题" />
    <p data-placeholder="输入正文描述" contenteditable="true"></p>
    <div id="creator-editor-topic-container"><div class="item">选择话题</div></div>
    <button onclick="document.querySelector('#draft-status').textContent='草稿保存成功'">保存草稿</button>
    <button onclick="window.location.href='/publish/success?note=1'">发布</button>
    <div id="draft-status"></div>
  </body>
</html>
"""


class _CreatorPageHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = (
            "<!doctype html><html><body><h1>发布成功</h1></body></html>"
            if self.path.startswith("/publish/success")
            else PUBLISH_FORM
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


@pytest.mark.asyncio
async def test_browser_runtime_keeps_draft_and_publish_actions_separate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CreatorPageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(runtime_module, "XHS_LOGIN_URL", f"{base_url}/login")
    monkeypatch.setattr(runtime_module, "XHS_HOME_URL", f"{base_url}/home")
    monkeypatch.setattr(runtime_module, "XHS_PUBLISH_NOTE_URL", f"{base_url}/publish")
    monkeypatch.setattr(runtime_module, "XHS_SUCCESS_URL_PATTERN", f"{base_url}/publish/success?**")
    monkeypatch.setattr(session_manager_module, "XHS_LOGIN_URL", f"{base_url}/login")
    monkeypatch.setattr(session_manager_module, "XHS_HOME_URL", f"{base_url}/home")
    runtime = XiaohongshuRuntime(root=tmp_path)
    manager = XiaohongshuBrowserSessionManager(runtime=XiaohongshuRuntime(root=tmp_path / "gateway"))

    try:
        draft = await runtime.distribute(
            "user-1",
            "xha_draft",
            "job_draft",
            title="草稿链路验证",
            body="这段内容只允许保存到草稿箱。",
            topics=["内容创作", "草稿验证"],
            mode="draft",
        )
        published = await runtime.distribute(
            "user-1",
            "xha_publish",
            "job_publish",
            title="发布链路验证",
            body="这段内容必须等待发布成功页面。",
            topics=["内容创作", "发布验证"],
            mode="publish",
        )
        opened = await manager.open("session_mock_1", "user-1", "xha_gateway")
        session_draft = await manager.distribute(
            session_id="session_mock_1",
            owner_uid="user-1",
            account_id="xha_gateway",
            job_id="job_session_draft",
            title="同会话草稿验证",
            body="自动化任务与人工画面必须复用同一个浏览器会话。",
            topics=["远程浏览器"],
            mode="draft",
        )
        session_screenshot = await manager.screenshot(
            session_id="session_mock_1",
            owner_uid="user-1",
            account_id="xha_gateway",
        )
        await manager.close(
            session_id="session_mock_1",
            owner_uid="user-1",
            account_id="xha_gateway",
        )
    finally:
        await manager.close_all()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert draft["note_url"] == ""
    assert Path(draft["screenshot_path"]).is_file()
    assert Path(draft["screenshot_path"]).with_name("cover.png").is_file()
    assert published["note_url"].startswith(f"{base_url}/publish/success?")
    assert Path(published["screenshot_path"]).is_file()
    assert Path(published["screenshot_path"]).with_name("cover.png").is_file()
    assert opened["status"] == "ready"
    assert Path(session_draft["screenshot_path"]).is_file()
    assert session_screenshot.startswith(b"\x89PNG")
