"""知识库映射必须单值且不能把普通库用于爆款准备。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from yuxi.knowledge.base import KnowledgeBase
from yuxi.services.content_viral_assets import require_viral_kb_type


@pytest.mark.parametrize("value", [None, *[f"CT{i:02d}" for i in range(1, 8)]])
def test_single_creation_type_is_persistable(value):
    params = KnowledgeBase.normalize_additional_params({"viral_content_type": value, "auto_generate_questions": False})
    assert params["viral_content_type"] == value
    assert params["auto_generate_questions"] is False


@pytest.mark.parametrize("value", ["", "CT08", "工种总价", ["CT03", "CT04"], {"type": "CT04"}, 4])
def test_invalid_or_multiple_creation_types_are_rejected(value):
    with pytest.raises(ValueError, match="一个有效创作类型"):
        KnowledgeBase.normalize_additional_params({"viral_content_type": value})


@pytest.mark.asyncio
async def test_unmapped_database_cannot_prepare_viral_files():
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    with pytest.raises(HTTPException, match="绑定一个爆款创作类型") as exc:
        await require_viral_kb_type(db, "ordinary_kb")
    assert exc.value.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kb_ids,expected", [(None, True), ([], False), (["visible", "private"], True), (["private"], False)]
)
async def test_automatic_discovery_keeps_permissions_and_explicit_scope(monkeypatch, kb_ids, expected):
    from sqlalchemy.dialects import postgresql
    from yuxi.services import content_viral_assets

    monkeypatch.setattr(content_viral_assets, "accessible_asset_kbs", AsyncMock(return_value=["visible"]))
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: [])))
    await content_viral_assets.search_ready_viral_assets(
        db,
        SimpleNamespace(uid="user"),
        industry_slug="decoration",
        query="",
        kb_ids=kb_ids,
        content_type_code="CT04",
        limit=5,
    )
    if not expected:
        db.execute.assert_not_called()
        return
    query = db.execute.call_args.args[0]
    sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    where = sql.split("WHERE", 1)[1].split("ORDER BY", 1)[0]
    assert "'visible'" in where and "'private'" not in where
    assert "viral_content_type" in where and "content_type_code" in where and "'CT04'" in where
