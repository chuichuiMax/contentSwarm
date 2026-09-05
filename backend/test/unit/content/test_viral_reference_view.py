from types import SimpleNamespace

import pytest

from yuxi.services import content_service


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
