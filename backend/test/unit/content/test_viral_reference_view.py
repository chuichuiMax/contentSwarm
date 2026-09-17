from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.services import content_service
from yuxi.services import content_viral_assets


@pytest.mark.asyncio
async def test_get_artifact_viral_reference_returns_selected_candidate(monkeypatch):
    artifact = SimpleNamespace(
        id="artifact-1",
        task_id="task-1",
        runtime_config_snapshot={"creation_mode": "viral_rewrite"},
        evidence_snapshot={
            "items": [
                {
                    "id": "candidate-2",
                    "metadata": {"selected_reference": True},
                }
            ]
        },
    )
    node_run = SimpleNamespace(
        output_snapshot={
            "result": {
                "viral_candidate_collection": {
                    "evidence_items": [
                        {
                            "id": "candidate-1",
                            "value": "未选原文",
                            "metadata": {"document_name": "爆款库.xlsx"},
                        },
                        {
                            "id": "candidate-2",
                            "value": "本次实际选中的爆款原文",
                            "metadata": {
                                "document_name": "爆款库.xlsx",
                                "knowledge_base_name": "爆款库",
                            },
                        },
                    ]
                }
            }
        }
    )

    class FakeRepository:
        def __init__(self, _db):
            pass

        async def get_artifact_for_user(self, artifact_id, _user):
            return artifact if artifact_id == artifact.id else None

        async def get_latest_completed_node_run(self, task_id, node_id):
            assert task_id == artifact.task_id
            assert node_id == "collect_viral_candidates"
            return node_run

    monkeypatch.setattr(content_service, "ContentRepository", FakeRepository)

    result = await content_service.get_artifact_viral_reference(
        SimpleNamespace(), SimpleNamespace(uid="user-1"), artifact.id
    )

    assert result == {
        "reference": {
            "id": "candidate-2",
            "content": "本次实际选中的爆款原文",
            "source_name": "爆款库.xlsx",
            "knowledge_base_name": "爆款库",
        }
    }


@pytest.mark.asyncio
async def test_get_artifact_viral_reference_rejects_original_mode(monkeypatch):
    artifact = SimpleNamespace(
        id="artifact-1",
        runtime_config_snapshot={"creation_mode": "original"},
        evidence_snapshot={"items": []},
    )

    class FakeRepository:
        def __init__(self, _db):
            pass

        async def get_artifact_for_user(self, _artifact_id, _user):
            return artifact

    monkeypatch.setattr(content_service, "ContentRepository", FakeRepository)

    with pytest.raises(Exception) as exc_info:
        await content_service.get_artifact_viral_reference(
            SimpleNamespace(), SimpleNamespace(uid="user-1"), artifact.id
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "VIRAL_REFERENCE_NOT_AVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("accessible", [True, False])
async def test_get_artifact_viral_reference_reads_selected_asset_version(monkeypatch, accessible):
    artifact = SimpleNamespace(
        id="artifact-1",
        task_id="task-1",
        runtime_config_snapshot={"creation_mode": "viral_rewrite"},
        evidence_snapshot={
            "items": [
                {
                    "id": "asset-version-1",
                    "value": "已选爆款的抽象结构参考",
                    "metadata": {"selected_reference": True, "asset_id": "asset-version-1"},
                }
            ]
        },
    )
    user = SimpleNamespace(uid="user-1")

    class FakeRepository:
        def __init__(self, _db):
            pass

        async def get_artifact_for_user(self, artifact_id, actor):
            assert artifact_id == artifact.id
            assert actor is user
            return artifact

        async def get_latest_completed_node_run(self, *_args):
            return None

    async def require_asset(db, actor, asset_id):
        assert actor is user
        assert asset_id == "asset-version-1"
        if not accessible:
            raise HTTPException(404, "爆款资产不存在或无权访问")
        # 生成后源文件可能更新，历史内容仍应读取当时选中的不可变原文版本。
        return SimpleNamespace(
            id=asset_id,
            status="invalidated",
            source_json={"title": "原文标题", "body": "第一段\n\n第二段"},
        )

    monkeypatch.setattr(content_service, "ContentRepository", FakeRepository)
    monkeypatch.setattr(content_viral_assets, "require_asset", require_asset)

    if not accessible:
        with pytest.raises(HTTPException) as exc_info:
            await content_service.get_artifact_viral_reference(SimpleNamespace(), user, artifact.id)
        assert exc_info.value.status_code == 404
        return

    result = await content_service.get_artifact_viral_reference(SimpleNamespace(), user, artifact.id)
    assert result == {
        "reference": {
            "id": "asset-version-1",
            "content": "原文标题\n\n第一段\n\n第二段",
            "source_name": "原文标题",
            "knowledge_base_name": "",
        }
    }


@pytest.mark.asyncio
async def test_get_artifact_viral_reference_missing_legacy_node_returns_not_found(monkeypatch):
    class FakeRepository:
        def __init__(self, _db):
            pass

        async def get_artifact_for_user(self, *_args):
            return SimpleNamespace(
                task_id="task-1",
                runtime_config_snapshot={"creation_mode": "viral_rewrite"},
                evidence_snapshot={"items": [{"id": "candidate-1", "metadata": {"selected_reference": True}}]},
            )

        async def get_latest_completed_node_run(self, *_args):
            return None

    monkeypatch.setattr(content_service, "ContentRepository", FakeRepository)
    with pytest.raises(HTTPException) as exc_info:
        await content_service.get_artifact_viral_reference(SimpleNamespace(), SimpleNamespace(), "artifact-1")
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "VIRAL_REFERENCE_SOURCE_NOT_FOUND"
