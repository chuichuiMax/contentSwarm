from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from yuxi.services import dangjia_callback_service
from yuxi.services.dangjia_callback_service import (
    build_failure_payload,
    build_success_payload,
    notify_dangjia_content_result,
)


def test_success_payload_uses_original_serial_and_permanent_cover_url():
    payload = build_success_payload(
        serial_no="202609141600",
        title="旧房焕新",
        body="生成后的正文",
        keywords=["旧房改造", "装修分享"],
        image_url="https://content.example.com/api/dangjia/content/media/cover-1/1.png",
    )

    assert payload == {
        "serialNo": "202609141600",
        "generationStatus": 30,
        "title": "旧房焕新",
        "body": "生成后的正文",
        "keywords": ["旧房改造", "装修分享"],
        "images": ["https://content.example.com/api/dangjia/content/media/cover-1/1.png"],
    }
    assert payload["images"][0].startswith("https://")


@pytest.mark.parametrize(
    ("terminal_status", "task_status", "expected_reason"),
    [
        ("failed", "failed", "内容生成失败，请重新发起"),
        ("cancelled", "cancelled", "内容生成任务已取消，请重新发起"),
        ("completed", "review_blocked", "内容审核未通过，请调整后重新发起"),
    ],
)
def test_failure_payload_omits_success_fields(terminal_status: str, task_status: str, expected_reason: str):
    payload = build_failure_payload(
        serial_no="202609141600",
        terminal_status=terminal_status,
        task_status=task_status,
    )

    assert payload == {
        "serialNo": "202609141600",
        "generationStatus": 40,
        "failReason": expected_reason,
    }
    assert not {"title", "body", "keywords", "images"} & payload.keys()


def install_callback_fakes(monkeypatch, *, task, artifact=None, asset=None, cover_bytes=b"cover-bytes"):
    @asynccontextmanager
    async def session_context():
        yield object()

    class FakeContentRepository:
        def __init__(self, db):
            del db

        async def get_task(self, task_id):
            return task if task_id == task.id else None

        async def get_artifact_for_task(self, task_id):
            return artifact if task_id == task.id else None

    class FakeCoverRepository:
        def __init__(self, db):
            del db

        async def get_asset(self, asset_id):
            return asset if asset is not None and asset_id == asset.id else None

    class FakeMinioClient:
        async def adownload_file(self, bucket_name, object_name):
            assert asset is not None
            assert (bucket_name, object_name) == (asset.bucket_name, asset.object_name)
            return cover_bytes

    monkeypatch.setattr(dangjia_callback_service.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(dangjia_callback_service, "ContentRepository", FakeContentRepository)
    monkeypatch.setattr(dangjia_callback_service, "ContentCoverRepository", FakeCoverRepository)
    monkeypatch.setattr(dangjia_callback_service, "get_minio_client", lambda: FakeMinioClient())


@pytest.mark.asyncio
async def test_success_notification_reads_committed_cover_and_posts_once(monkeypatch):
    task = SimpleNamespace(
        id="task-1",
        status="reviewed",
        brief_json={"form_values": {"external_source": "dangjia", "external_serial_no": "202609141600"}},
    )
    artifact = SimpleNamespace(
        title="旧房焕新",
        body="生成后的正文",
        topics=["旧房改造"],
        cover_asset_id="cover-1",
    )
    asset = SimpleNamespace(
        id="cover-1",
        content_task_id=task.id,
        role="output",
        content_type="image/png",
        bucket_name="content-covers",
        object_name="cover-1.png",
    )
    install_callback_fakes(monkeypatch, task=task, artifact=artifact, asset=asset)
    monkeypatch.setenv("DANGJIA_CALLBACK_BASE_URL", "http://mgr.dev.dangjia.com:8001/")
    monkeypatch.setenv("DANGJIA_CALLBACK_API_KEY", "test-secret")
    monkeypatch.setenv(
        "DANGJIA_MEDIA_PUBLIC_BASE_URL",
        "https://content.example.com/api/dangjia/content/media/",
    )
    calls = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"code": "200", "msg": "success", "data": {}}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] == httpx.Timeout(15.0, connect=3.0)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(dangjia_callback_service.httpx, "AsyncClient", FakeAsyncClient)

    await notify_dangjia_content_result(task_id=task.id, run_id="run-1", terminal_status="completed")

    assert calls == [
        (
            "http://mgr.dev.dangjia.com:8001/v1/callback/ai/gen/result/notify",
            {
                "headers": {
                    "Content-Type": "application/json; charset=UTF-8",
                    "x-api-key": "test-secret",
                },
                "json": {
                    "serialNo": "202609141600",
                    "generationStatus": 30,
                    "title": "旧房焕新",
                    "body": "生成后的正文",
                    "keywords": ["旧房改造"],
                    "images": ["https://content.example.com/api/dangjia/content/media/cover-1/1.png"],
                },
            },
        )
    ]


@pytest.mark.asyncio
async def test_non_dangjia_task_skips_storage_and_http(monkeypatch):
    task = SimpleNamespace(
        id="task-internal",
        status="reviewed",
        brief_json={"form_values": {"external_source": "internal"}},
    )
    install_callback_fakes(monkeypatch, task=task)
    monkeypatch.delenv("DANGJIA_CALLBACK_BASE_URL", raising=False)
    monkeypatch.delenv("DANGJIA_CALLBACK_API_KEY", raising=False)
    errors = []

    class FakeLogger:
        @staticmethod
        def error(*args):
            errors.append(args)

        @staticmethod
        def info(*args):
            raise AssertionError(f"success must not be logged: {args}")

    monkeypatch.setattr(dangjia_callback_service, "logger", FakeLogger())

    class UnexpectedAsyncClient:
        def __init__(self, **kwargs):
            raise AssertionError(f"HTTP client must not be created: {kwargs}")

    monkeypatch.setattr(dangjia_callback_service.httpx, "AsyncClient", UnexpectedAsyncClient)

    await notify_dangjia_content_result(task_id=task.id, run_id="run-internal", terminal_status="completed")

    assert errors == []


@pytest.mark.asyncio
async def test_review_blocked_completion_posts_failure_without_cover(monkeypatch):
    task = SimpleNamespace(
        id="task-blocked",
        status="review_blocked",
        brief_json={"form_values": {"external_source": "dangjia", "external_serial_no": "202609141600"}},
    )
    install_callback_fakes(monkeypatch, task=task)
    monkeypatch.setenv("DANGJIA_CALLBACK_BASE_URL", "http://mgr.dev.dangjia.com:8001")
    monkeypatch.setenv("DANGJIA_CALLBACK_API_KEY", "test-secret")
    payloads = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"code": "200", "msg": "success", "data": {}}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            del kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb

        async def post(self, url, **kwargs):
            del url
            payloads.append(kwargs["json"])
            return FakeResponse()

    monkeypatch.setattr(dangjia_callback_service.httpx, "AsyncClient", FakeAsyncClient)

    await notify_dangjia_content_result(task_id=task.id, run_id="run-blocked", terminal_status="completed")

    assert payloads == [
        {
            "serialNo": "202609141600",
            "generationStatus": 40,
            "failReason": "内容审核未通过，请调整后重新发起",
        }
    ]


@pytest.mark.asyncio
async def test_http_error_is_logged_without_retry_or_escape(monkeypatch):
    task = SimpleNamespace(
        id="task-failed",
        status="failed",
        brief_json={"form_values": {"external_source": "dangjia", "external_serial_no": "202609141600"}},
    )
    install_callback_fakes(monkeypatch, task=task)
    monkeypatch.setenv("DANGJIA_CALLBACK_BASE_URL", "http://mgr.dev.dangjia.com:8001")
    monkeypatch.setenv("DANGJIA_CALLBACK_API_KEY", "test-secret")
    calls = []

    class FailingAsyncClient:
        def __init__(self, **kwargs):
            del kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            raise httpx.ConnectError("callback unavailable")

    monkeypatch.setattr(dangjia_callback_service.httpx, "AsyncClient", FailingAsyncClient)

    await notify_dangjia_content_result(task_id=task.id, run_id="run-failed", terminal_status="failed")

    assert len(calls) == 1
    assert calls[0][1]["json"] == {
        "serialNo": "202609141600",
        "generationStatus": 40,
        "failReason": "内容生成失败，请重新发起",
    }


@pytest.mark.asyncio
async def test_read_media_returns_only_current_dangjia_final_cover(monkeypatch):
    task = SimpleNamespace(
        id="task-media",
        status="reviewed",
        brief_json={"form_values": {"external_source": "dangjia", "external_serial_no": "202609141600"}},
    )
    artifact = SimpleNamespace(cover_asset_id="cover-media")
    asset = SimpleNamespace(
        id="cover-media",
        content_task_id=task.id,
        role="output",
        content_type="image/png",
        bucket_name="content-covers",
        object_name="cover-media.png",
    )
    install_callback_fakes(
        monkeypatch,
        task=task,
        artifact=artifact,
        asset=asset,
        cover_bytes=b"final-cover",
    )

    result = await dangjia_callback_service.read_dangjia_cover_media(asset.id)

    assert result == (b"final-cover", "image/png", "1.png")


@pytest.mark.asyncio
async def test_read_media_hides_non_dangjia_cover(monkeypatch):
    task = SimpleNamespace(
        id="task-internal",
        status="reviewed",
        brief_json={"form_values": {"external_source": "internal"}},
    )
    artifact = SimpleNamespace(cover_asset_id="cover-internal")
    asset = SimpleNamespace(
        id="cover-internal",
        content_task_id=task.id,
        role="output",
        content_type="image/png",
        bucket_name="content-covers",
        object_name="cover-internal.png",
    )
    install_callback_fakes(monkeypatch, task=task, artifact=artifact, asset=asset)

    result = await dangjia_callback_service.read_dangjia_cover_media(asset.id)

    assert result is None


@pytest.mark.asyncio
async def test_read_media_hides_stale_dangjia_cover(monkeypatch):
    task = SimpleNamespace(
        id="task-media",
        status="reviewed",
        brief_json={"form_values": {"external_source": "dangjia", "external_serial_no": "202609141600"}},
    )
    artifact = SimpleNamespace(cover_asset_id="cover-current")
    asset = SimpleNamespace(
        id="cover-stale",
        content_task_id=task.id,
        role="output",
        content_type="image/png",
        bucket_name="content-covers",
        object_name="cover-stale.png",
    )
    install_callback_fakes(monkeypatch, task=task, artifact=artifact, asset=asset)

    result = await dangjia_callback_service.read_dangjia_cover_media(asset.id)

    assert result is None
