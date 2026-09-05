from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.content_router import content
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.content.schemas import XiaohongshuDistributionCreate
from yuxi.integrations.xiaohongshu import XiaohongshuRuntime, XiaohongshuRuntimeError
from yuxi.repositories.xiaohongshu_repository import XiaohongshuRepository
import yuxi.services.xiaohongshu_service as xiaohongshu_service
import yuxi.services.xiaohongshu_worker as xiaohongshu_worker
from yuxi.services.run_worker import WorkerSettings
from yuxi.services.xiaohongshu_worker import process_xiaohongshu_distribution
from yuxi.storage.postgres.models_content import (
    ContentDistributionJob,
    ContentDistributionResult,
    XiaohongshuAccount,
    XiaohongshuBrowserSession,
    XiaohongshuLoginSession,
)
from yuxi.utils.datetime_utils import utc_now_naive


@pytest.mark.asyncio
async def test_account_repository_never_crosses_owner_boundary():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(XiaohongshuAccount.__table__.create)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as db:
        repo = XiaohongshuRepository(db)
        alice = await repo.create_account(owner_uid="alice", display_name="Alice 的账号")
        await repo.create_account(owner_uid="bob", display_name="Bob 的账号")
        await db.commit()

        assert [item.owner_uid for item in await repo.list_accounts("alice")] == ["alice"]
        assert await repo.get_account(alice.id, "bob") is None
        assert "owner_uid" not in alice.to_dict()

    await engine.dispose()


@pytest.mark.asyncio
async def test_xiaohongshu_account_api_is_user_private():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(XiaohongshuAccount.__table__.create)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    app = FastAPI()
    app.include_router(content, prefix="/api")
    current_uid = {"value": "alice"}
    async with session_factory() as db:

        async def override_db():
            yield db

        async def override_user():
            return SimpleNamespace(uid=current_uid["value"])

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_required_user] = override_user
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            alice_response = await client.post(
                "/api/content/xiaohongshu/accounts",
                json={"display_name": "主账号"},
            )
            assert alice_response.status_code == 200
            alice_id = alice_response.json()["account"]["id"]

            current_uid["value"] = "bob"
            bob_response = await client.post(
                "/api/content/xiaohongshu/accounts",
                json={"display_name": "主账号"},
            )
            assert bob_response.status_code == 200
            bob_items = (await client.get("/api/content/xiaohongshu/accounts")).json()["items"]
            assert [item["id"] for item in bob_items] == [bob_response.json()["account"]["id"]]
            assert all("owner_uid" not in item for item in bob_items)
            assert (
                await client.patch(
                    f"/api/content/xiaohongshu/accounts/{alice_id}",
                    json={"display_name": "越权修改"},
                )
            ).status_code == 404
            assert (await client.delete(f"/api/content/xiaohongshu/accounts/{alice_id}")).status_code == 404

            current_uid["value"] = "alice"
            alice_items = (await client.get("/api/content/xiaohongshu/accounts")).json()["items"]
            assert [item["id"] for item in alice_items] == [alice_id]

    await engine.dispose()


@pytest.mark.asyncio
async def test_browser_session_api_never_crosses_owner_boundary(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(XiaohongshuAccount.__table__.create)
        await connection.run_sync(XiaohongshuBrowserSession.__table__.create)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    app = FastAPI()
    app.include_router(content, prefix="/api")
    current_uid = {"value": "alice"}
    async with session_factory() as db:
        repo = XiaohongshuRepository(db)
        account = await repo.create_account(owner_uid="alice", display_name="Alice 的账号")
        browser_session = await repo.create_browser_session(
            owner_uid="alice",
            account_id=account.id,
            session_id="xhbs_alice_session",
        )
        browser_session.status = "ready"
        await db.commit()

        async def override_db():
            yield db

        async def override_user():
            return SimpleNamespace(uid=current_uid["value"])

        async def gateway_must_not_be_called(*args, **kwargs):
            del args, kwargs
            raise AssertionError("cross-owner request reached browser gateway")

        monkeypatch.setattr(xiaohongshu_service, "_gateway_request", gateway_must_not_be_called)
        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_required_user] = override_user
        current_uid["value"] = "bob"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            base = f"/api/content/xiaohongshu/accounts/{account.id}/browser-session"
            assert (await client.get(base)).status_code == 404
            assert (await client.post(f"{base}/heartbeat")).status_code == 404
            assert (await client.post(f"{base}/claim")).status_code == 404
            assert (await client.post(f"{base}/action", json={"action": "click", "x": 10, "y": 10})).status_code == 404
            assert (await client.get(f"{base}/screenshot")).status_code == 404
            assert (await client.delete(base)).status_code == 200

    await engine.dispose()


@pytest.mark.asyncio
async def test_gateway_recovery_rechecks_account_before_opening(monkeypatch: pytest.MonkeyPatch):
    open_calls = []
    repo = SimpleNamespace(
        get_account=lambda account_id, owner_uid, for_update=False: _async_value(None),
        get_browser_session=lambda account_id, owner_uid, for_update=False: _async_value(None),
    )

    @asynccontextmanager
    async def operation_slot(owner_uid, account_id):
        del owner_uid, account_id
        yield

    monkeypatch.setattr(xiaohongshu_service, "_browser_operation_slot", operation_slot)
    monkeypatch.setattr(
        xiaohongshu_service,
        "_open_gateway_session",
        lambda *args, **kwargs: open_calls.append((args, kwargs)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await xiaohongshu_service._recover_gateway_session(repo, "session-1", "alice", "account-1")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "XHS_ACCOUNT_NOT_FOUND"
    assert open_calls == []


@pytest.mark.asyncio
async def test_browser_action_requires_explicit_control_claim(monkeypatch: pytest.MonkeyPatch):
    account = SimpleNamespace(id="account-1", owner_uid="alice", enabled=True)
    session = SimpleNamespace(id="session-1")
    repo = SimpleNamespace(
        get_account=lambda account_id, owner_uid, for_update=False: _async_value(account),
        get_browser_session=lambda account_id, owner_uid, for_update=False: _async_value(session),
    )

    @asynccontextmanager
    async def operation_slot(owner_uid, account_id):
        del owner_uid, account_id
        yield

    monkeypatch.setattr(xiaohongshu_service, "XiaohongshuRepository", lambda db: repo)
    monkeypatch.setattr(xiaohongshu_service, "_browser_operation_slot", operation_slot)
    monkeypatch.setattr(
        xiaohongshu_service,
        "_has_browser_control",
        lambda session_id, owner_uid, account_id, refresh=False: _async_value(False),
    )

    with pytest.raises(HTTPException) as exc_info:
        await xiaohongshu_service.browser_session_action(
            object(),
            SimpleNamespace(uid="alice"),
            "account-1",
            {"action": "click", "x": 10, "y": 10},
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "XHS_BROWSER_CONTROL_REQUIRED"


@pytest.mark.asyncio
async def test_worker_detects_manual_browser_control_lease(monkeypatch: pytest.MonkeyPatch):
    class FakeLock:
        released = False

        async def acquire(self):
            return True

        async def release(self):
            self.released = True

    class FakeRedis:
        def __init__(self):
            self.operation_lock = FakeLock()

        async def get(self, key):
            assert key == "xhs:browser-control:alice:account-1"
            return b"session-1"

        def lock(self, key, **kwargs):
            assert key == "xhs:account-lock:alice:account-1"
            assert kwargs["timeout"] == xiaohongshu_worker.ACCOUNT_LOCK_SECONDS
            return self.operation_lock

    redis = FakeRedis()
    monkeypatch.setattr(xiaohongshu_worker, "get_redis_client", lambda: _async_value(redis))

    assert await xiaohongshu_worker._manual_control_active("alice", "account-1") is True
    with pytest.raises(xiaohongshu_worker.XiaohongshuRuntimeError) as exc_info:
        async with xiaohongshu_worker._browser_account_slot("alice", "account-1"):
            raise AssertionError("worker entered an account under manual control")
    assert exc_info.value.code == "XHS_ACCOUNT_BUSY"
    assert redis.operation_lock.released is True


@pytest.mark.asyncio
async def test_expired_login_session_is_closed_even_if_worker_never_started():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(XiaohongshuAccount.__table__.create)
        await connection.run_sync(XiaohongshuLoginSession.__table__.create)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as db:
        repo = XiaohongshuRepository(db)
        account = await repo.create_account(owner_uid="alice", display_name="主账号")
        account.login_status = "pending"
        login_session = await repo.create_login_session(
            account=account,
            expires_at=utc_now_naive() - timedelta(seconds=1),
        )
        await db.commit()

        response = await xiaohongshu_service.get_login_session(
            db,
            SimpleNamespace(uid="alice"),
            login_session.id,
        )

        assert response["session"]["status"] == "expired"
        assert response["session"]["qr_code"] is None
        assert account.login_status == "expired"

    await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_login_job_never_starts_second_browser_in_gateway_mode(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(XiaohongshuAccount.__table__.create)
        await connection.run_sync(XiaohongshuLoginSession.__table__.create)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as db:
        repo = XiaohongshuRepository(db)
        account = await repo.create_account(owner_uid="alice", display_name="主账号")
        login_session = await repo.create_login_session(
            account=account,
            expires_at=utc_now_naive() + timedelta(minutes=3),
        )
        await db.commit()

        @asynccontextmanager
        async def session_context():
            yield db

        class FakeRedis:
            async def set(self, *args, **kwargs):
                del args, kwargs

        runtime_calls = []

        class FakeRuntime:
            def __init__(self):
                runtime_calls.append("created")

            async def login(self, *args, **kwargs):
                runtime_calls.append((args, kwargs))
                raise AssertionError("legacy browser login must not run in gateway mode")

        monkeypatch.setenv("XHS_BROWSER_GATEWAY_URL", "http://gateway.test")
        monkeypatch.setattr(
            xiaohongshu_worker,
            "pg_manager",
            SimpleNamespace(get_async_session_context=session_context),
        )
        monkeypatch.setattr(xiaohongshu_worker, "get_redis_client", lambda: _async_value(FakeRedis()))
        monkeypatch.setattr(xiaohongshu_worker, "XiaohongshuRuntime", FakeRuntime)

        await xiaohongshu_worker.process_xiaohongshu_login({}, login_session.id)

        assert runtime_calls == ["created"]
        assert login_session.status == "failed"
        assert login_session.error_code == "XHS_REMOTE_LOGIN_REQUIRED"
        assert account.login_status == "unbound"

    await engine.dispose()


def test_runtime_account_path_is_private_and_traversal_safe(tmp_path: Path):
    runtime = XiaohongshuRuntime(root=tmp_path)

    owner_namespace = hashlib.sha256(b"oidc:user@example.com").hexdigest()
    assert runtime.account_dir("oidc:user@example.com", "xha_1") == tmp_path / owner_namespace / "xha_1"
    with pytest.raises(XiaohongshuRuntimeError, match="运行目录标识无效"):
        runtime.account_dir("user_1", "../another-account")


def test_generated_cover_is_ready_for_image_note(tmp_path: Path):
    runtime = XiaohongshuRuntime(root=tmp_path)

    cover = runtime.render_cover("user_1", "xha_1", "job_1", "一键分发测试", ["效率", "内容创作"])

    assert cover.is_file()
    assert cover.suffix == ".png"
    assert cover.stat().st_size > 0


def test_distribution_schema_requires_explicit_publish_confirmation_and_request_key():
    with pytest.raises(ValidationError):
        XiaohongshuDistributionCreate(account_ids=["xha_1"], mode="publish", confirm_publish=True)

    payload = XiaohongshuDistributionCreate(
        request_id="request-123",
        account_ids=["xha_1"],
        mode="publish",
        confirm_publish=True,
    )
    assert payload.confirm_publish is True


def test_xiaohongshu_distribution_is_registered_on_existing_worker():
    assert process_xiaohongshu_distribution in WorkerSettings.functions


@pytest.mark.asyncio
async def test_distribution_rejects_artifact_owned_by_another_user(monkeypatch: pytest.MonkeyPatch):
    artifact = SimpleNamespace(id="artifact_1", created_by="bob")
    monkeypatch.setattr(
        xiaohongshu_service,
        "ContentRepository",
        lambda db: SimpleNamespace(get_artifact=lambda artifact_id: _async_value(artifact)),
    )
    payload = XiaohongshuDistributionCreate(
        request_id="request-123",
        account_ids=["xha_1"],
    )

    with pytest.raises(HTTPException) as exc_info:
        await xiaohongshu_service.create_distribution(object(), SimpleNamespace(uid="alice"), "artifact_1", payload)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_distribution_requires_completed_review(monkeypatch: pytest.MonkeyPatch):
    artifact = SimpleNamespace(id="artifact_1", created_by="alice", review_snapshot={"status": "pending"})
    monkeypatch.setattr(
        xiaohongshu_service,
        "ContentRepository",
        lambda db: SimpleNamespace(get_artifact=lambda artifact_id: _async_value(artifact)),
    )
    payload = XiaohongshuDistributionCreate(
        request_id="request-123",
        account_ids=["xha_1"],
    )

    with pytest.raises(HTTPException) as exc_info:
        await xiaohongshu_service.create_distribution(object(), SimpleNamespace(uid="alice"), "artifact_1", payload)

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_direct_publish_requires_service_level_confirmation(monkeypatch: pytest.MonkeyPatch):
    artifact = SimpleNamespace(id="artifact_1", created_by="alice", review_snapshot={"status": "passed"})
    monkeypatch.setattr(
        xiaohongshu_service,
        "ContentRepository",
        lambda db: SimpleNamespace(get_artifact=lambda artifact_id: _async_value(artifact)),
    )
    payload = XiaohongshuDistributionCreate(
        request_id="request-123",
        account_ids=["xha_1"],
        mode="publish",
        confirm_publish=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await xiaohongshu_service.create_distribution(object(), SimpleNamespace(uid="alice"), "artifact_1", payload)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error"]["code"] == "XHS_PUBLISH_CONFIRM_REQUIRED"


@pytest.mark.asyncio
async def test_login_and_status_check_use_central_browser_gateway(monkeypatch: pytest.MonkeyPatch):
    account = SimpleNamespace(id="xha_1", owner_uid="alice", to_dict=lambda: {"id": "xha_1"})
    monkeypatch.setattr(
        xiaohongshu_service,
        "XiaohongshuRepository",
        lambda db: SimpleNamespace(get_account=lambda account_id, owner_uid: _async_value(account)),
    )
    calls = []

    async def open_browser(db, user, account_id):
        calls.append((db, user.uid, account_id))
        return {
            "session": {"id": "session-1", "status": "ready"},
            "browser": {"logged_in": True},
        }

    monkeypatch.setattr(xiaohongshu_service, "open_browser_session", open_browser)
    user = SimpleNamespace(uid="alice")

    login = await xiaohongshu_service.start_account_login("db", user, "xha_1")
    status = await xiaohongshu_service.check_account_login("db", user, "xha_1")

    assert calls == [("db", "alice", "xha_1"), ("db", "alice", "xha_1")]
    assert login["session"]["id"] == "session-1"
    assert login["reused"] is True
    assert status["completed"] is True
    assert status["accepted"] is False
    assert status["account"] == {"id": "xha_1"}


@pytest.mark.asyncio
async def test_gateway_distribution_receives_immutable_cover_reference(monkeypatch: pytest.MonkeyPatch):
    captured = {}
    session = SimpleNamespace(id="session-1", status="ready")

    async def open_gateway_browser(db, repo, account):
        del db, repo, account
        return session, {}

    async def gateway_request(method, path, **kwargs):
        captured.update({"method": method, "path": path, **kwargs})
        return SimpleNamespace(json=lambda: {"screenshot_path": "/tmp/result.png"})

    class FakeDb:
        async def commit(self):
            return None

    monkeypatch.setattr(xiaohongshu_worker, "_open_gateway_browser", open_gateway_browser)
    monkeypatch.setattr(xiaohongshu_worker, "_gateway_request", gateway_request)
    account = SimpleNamespace(id="account-1", owner_uid="alice")
    job = SimpleNamespace(id="distribution-1", mode="draft")
    payload = {
        "title": "标题",
        "body": "正文",
        "topics": ["封面"],
        "cover": {
            "type": "asset",
            "bucket_name": "content-covers",
            "object_name": "alice/cover.png",
            "sha256": "a" * 64,
        },
    }

    outcome = await xiaohongshu_worker._distribute_via_browser_gateway(
        FakeDb(),
        SimpleNamespace(),
        account,
        job,
        payload,
    )

    assert captured["json"]["cover_bucket_name"] == "content-covers"
    assert captured["json"]["cover_object_name"] == "alice/cover.png"
    assert captured["json"]["cover_sha256"] == "a" * 64
    assert outcome["browser_session_id"] == "session-1"


@pytest.mark.asyncio
async def test_interrupted_publish_is_not_automatically_retried(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(XiaohongshuAccount.__table__.create)
        await connection.run_sync(ContentDistributionJob.__table__.create)
        await connection.run_sync(ContentDistributionResult.__table__.create)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as db:
        repo = XiaohongshuRepository(db)
        account = await repo.create_account(owner_uid="alice", display_name="主账号")
        job = await repo.create_distribution_job(
            owner_uid="alice",
            artifact_id="artifact_1",
            artifact_version=1,
            mode="publish",
            payload_snapshot={"title": "标题", "body": "正文", "topics": []},
            idempotency_key="request-key",
            dedupe_key="dedupe-key",
            accounts=[account],
        )
        result = (await repo.list_distribution_results(job.id))[0]
        job.status = "running"
        result.status = "running"
        await db.commit()

        @asynccontextmanager
        async def session_context():
            yield db

        runtime_calls = []

        class FakeRuntime:
            async def distribute(self, *args, **kwargs):
                runtime_calls.append((args, kwargs))
                return {}

        monkeypatch.setattr(
            xiaohongshu_worker,
            "pg_manager",
            SimpleNamespace(get_async_session_context=session_context),
        )
        monkeypatch.setattr(xiaohongshu_worker, "XiaohongshuRuntime", FakeRuntime)

        await xiaohongshu_worker.process_xiaohongshu_distribution({}, job.id)

        assert runtime_calls == []
        assert result.status == "uncertain"
        assert result.uncertain is True
        assert result.error_code == "XHS_PREVIOUS_ATTEMPT_INTERRUPTED"
        assert "不会自动重发" in result.error_message
        assert job.status == "uncertain"
        assert job.error_code == "XHS_PUBLISH_RESULT_UNCERTAIN"

    await engine.dispose()


@pytest.mark.asyncio
async def test_multi_account_failure_does_not_block_other_accounts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(XiaohongshuAccount.__table__.create)
        await connection.run_sync(XiaohongshuBrowserSession.__table__.create)
        await connection.run_sync(ContentDistributionJob.__table__.create)
        await connection.run_sync(ContentDistributionResult.__table__.create)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as db:
        repo = XiaohongshuRepository(db)
        failed_account = await repo.create_account(owner_uid="alice", display_name="异常账号")
        healthy_account = await repo.create_account(owner_uid="alice", display_name="正常账号")
        job = await repo.create_distribution_job(
            owner_uid="alice",
            artifact_id="artifact_1",
            artifact_version=1,
            mode="draft",
            payload_snapshot={"title": "标题", "body": "正文", "topics": []},
            idempotency_key="multi-account-request",
            dedupe_key=None,
            accounts=[failed_account, healthy_account],
        )
        await db.commit()

        @asynccontextmanager
        async def session_context():
            yield db

        @asynccontextmanager
        async def account_slot(owner_uid, account_id):
            del owner_uid, account_id
            yield

        async def distribute_via_gateway(db, repo, account, job, payload):
            del db, repo, job, payload
            if account.id == failed_account.id:
                raise RuntimeError("sensitive profile path: /app/saves/xiaohongshu/account/profile")
            return {
                "browser_session_id": "session-healthy",
                "screenshot_path": "/tmp/result.png",
                "note_url": "",
            }

        monkeypatch.setenv("XHS_BROWSER_GATEWAY_URL", "http://gateway.test")
        monkeypatch.setenv("XHS_RUNTIME_ROOT", str(tmp_path / "xiaohongshu"))
        monkeypatch.setattr(
            xiaohongshu_worker,
            "pg_manager",
            SimpleNamespace(get_async_session_context=session_context),
        )
        monkeypatch.setattr(xiaohongshu_worker, "_browser_account_slot", account_slot)
        monkeypatch.setattr(xiaohongshu_worker, "_distribute_via_browser_gateway", distribute_via_gateway)

        await xiaohongshu_worker.process_xiaohongshu_distribution({}, job.id)

        results = {item.account_id: item for item in await repo.list_distribution_results(job.id)}
        assert results[failed_account.id].status == "failed"
        assert results[failed_account.id].error_code == "XHS_BROWSER_ERROR"
        assert results[failed_account.id].error_message == "分发任务执行异常，请联系管理员核对"
        assert "/app/saves" not in results[failed_account.id].error_message
        assert results[healthy_account.id].status == "draft_saved"
        assert results[healthy_account.id].browser_session_id == "session-healthy"
        assert job.status == "partial_failed"
        assert job.error_code == "XHS_PARTIAL_FAILURE"

    await engine.dispose()


@pytest.mark.asyncio
async def test_runtime_generic_browser_error_does_not_expose_internal_path(tmp_path: Path):
    class FailingPage:
        async def goto(self, *args, **kwargs):
            del args, kwargs
            raise RuntimeError("sensitive profile path: /app/saves/xiaohongshu/account/profile")

        async def screenshot(self, *args, **kwargs):
            del args, kwargs

    runtime = XiaohongshuRuntime(root=tmp_path)

    with pytest.raises(XiaohongshuRuntimeError) as exc_info:
        await runtime.distribute_on_page(
            FailingPage(),
            "alice",
            "account-1",
            "job-12345678",
            title="标题",
            body="正文",
            topics=[],
            mode="draft",
            cover_bytes=b"cover",
        )

    assert exc_info.value.code == "XHS_BROWSER_ERROR"
    assert str(exc_info.value) == "小红书页面操作失败，请稍后重试"
    assert "/app/saves" not in str(exc_info.value)


async def _async_value(value):
    return value
