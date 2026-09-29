from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.image_design.mp_schemas import MpPolishCreate, MpTaskCreate, SaveTarget
from yuxi.image_design.mp_service import create_task, refinement_payload
from yuxi.image_design.prompt_compiler import build_prompt_plan, compile_prompt
from yuxi.image_design.save_targets import can_contribute_to_category, is_storage_root, resolve_writable_save_target
from yuxi.storage.postgres.models_content import ContentMaterialCategory


def transfer_payload(**changes):
    return {
        "workflow": "transfer",
        "images": [{"role": "reference", "library_item_id": "input-reference"}],
        "description": "暖色自然光",
        "target_space": "客厅",
        "layout_type": "一字型沙发墙",
        **changes,
    }


def test_cover_template_uncategorized_is_not_an_image_storage_root():
    category = ContentMaterialCategory(
        owner_uid="employee",
        material_type="cover_template",
        id="uncategorized",
        visibility="private",
        is_system=True,
    )
    assert not is_storage_root(category)


@pytest.mark.asyncio
async def test_retry_revalidates_fixed_target_before_enqueue_and_marks_legacy_request(monkeypatch):
    from yuxi.image_design import mp_service
    from yuxi.storage.postgres.models_content import ImageDesignJob

    job = ImageDesignJob(id="idj_retry", owner_uid="employee", workflow="room_adapt", status="failed",
        request_json={"gen_count": 2, "requested_save_target": {"scope": "private", "gallery_id": None}},
        result_json={"asset_ids": []})
    db = SimpleNamespace(scalar=AsyncMock(return_value=job), commit=AsyncMock())
    validate = AsyncMock(side_effect=HTTPException(status_code=422, detail="unavailable"))
    queue = SimpleNamespace(enqueue_job=AsyncMock(return_value=object()))
    get_queue = AsyncMock(return_value=queue)
    monkeypatch.setattr(mp_service, "validate_mp_save_target", validate)
    monkeypatch.setattr(mp_service, "get_arq_pool", get_queue)
    with pytest.raises(HTTPException):
        await mp_service.retry_task(db, SimpleNamespace(uid="employee"), job.id)
    get_queue.assert_not_awaited()
    assert job.status == "failed"
    validate.side_effect = None
    result = await mp_service.retry_task(db, SimpleNamespace(uid="employee"), job.id)
    assert result["task"]["status"] == "queued" and job.request_json["mp_fixed_target"] is True


@pytest.mark.parametrize("space,prefix", [("客厅", ""), ("书房", "mp_")])
@pytest.mark.parametrize(
    "elements,ids",
    [
        ([], []),
        (["落地窗旁休闲躺椅"], ["lounge_chair"]),
        (["落地窗旁休闲躺椅", "壁炉居中"], ["lounge_chair", "fireplace"]),
        ([" 壁炉居中 ", "壁炉居中"], ["fireplace"]),
    ],
)
def test_transfer_addon_arrays_reach_compiled_prompt(space, prefix, elements, ids):
    payload = MpPolishCreate(**transfer_payload(target_space=space, extra_element=elements))
    converted = refinement_payload(payload)
    assert converted.addons == [prefix + value for value in ids]
    plan = build_prompt_plan(converted, {"cross_space_style": {"style": "现代简约"}})
    prompt = compile_prompt(plan)
    for element in set(value.strip() for value in elements):
        assert element in prompt


def test_transfer_missing_addons_defaults_to_empty_list():
    assert MpPolishCreate(**transfer_payload()).extra_element == []


@pytest.mark.parametrize("elements", ["壁炉居中", None, [42], [" "], ["壁炉居中"] * 3])
def test_transfer_rejects_non_array_invalid_items_and_more_than_two(elements):
    with pytest.raises(ValidationError):
        MpPolishCreate(**transfer_payload(extra_element=elements))


def test_transfer_rejects_unknown_addon_without_dropping_it():
    payload = MpPolishCreate(**transfer_payload(extra_element=["壁炉居中", "未知选项"]))
    with pytest.raises(HTTPException) as error:
        refinement_payload(payload)
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_changed_second_addon_invalidates_refinement_before_queueing():
    original = MpPolishCreate(**transfer_payload(extra_element=["壁炉居中", "落地窗旁休闲躺椅"]))
    semantic = refinement_payload(original).model_dump(mode="json", exclude={"parent_refinement_id", "edited_prompt"})
    semantic["effective_prompt"] = original.description
    db = SimpleNamespace(
        scalar=AsyncMock(
            return_value=SimpleNamespace(
                status="completed", workflow="cross_space", request_json={"semantic": semantic}
            )
        )
    )
    changed = MpTaskCreate(
        **transfer_payload(
            extra_element=["壁炉居中", "开放式层板展示架"],
            refinement_id="idr_owned",
            save_target={"scope": "private"},
        )
    )
    with pytest.raises(HTTPException) as error:
        await create_task(db, SimpleNamespace(uid="employee"), changed)
    assert error.value.status_code == 409
    assert error.value.detail["error"]["code"] == "IMAGE_DESIGN_REFINEMENT_STALE"


@pytest.fixture
async def category_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(ContentMaterialCategory.__table__.create)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        db.add_all(
            [
                ContentMaterialCategory(
                    owner_uid=owner, material_type="image", id="same-id", name=f"{owner}图库", visibility=scope
                )
                for owner, scope in [("employee", "private"), ("admin", "enterprise"), ("other", "private")]
            ]
        )
        await db.commit()
        yield db
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,owner", [("private", "employee"), ("enterprise", "admin")])
async def test_real_repository_resolves_colliding_ids_by_scope_and_owner(category_db, scope, owner):
    resolved = await resolve_writable_save_target(
        category_db, SimpleNamespace(uid="employee"), SaveTarget(scope=scope, gallery_id="same-id")
    )
    assert resolved.category_owner_uid == owner
    assert resolved.public_target == {"scope": scope, "gallery_id": "same-id"}


@pytest.mark.asyncio
async def test_ambiguous_enterprise_id_returns_validation_error(category_db):
    category_db.add(
        ContentMaterialCategory(
            owner_uid="admin2", material_type="image", id="same-id", name="重复企业图库", visibility="enterprise"
        )
    )
    await category_db.commit()
    with pytest.raises(HTTPException) as error:
        await resolve_writable_save_target(
            category_db, SimpleNamespace(uid="employee"), SaveTarget(scope="enterprise", gallery_id="same-id")
        )
    assert error.value.status_code == 422
    assert error.value.detail["error"]["code"] == "IMAGE_DESIGN_SAVE_TARGET_AMBIGUOUS"


@pytest.mark.parametrize("role", ["employee", "admin"])
@pytest.mark.parametrize(
    "scope,owner,allowed",
    [
        ("private", "employee", True),
        ("private", "other", False),
        ("enterprise", "other", True),
    ],
)
def test_contribution_policy_preserves_private_ownership_and_employee_enterprise_access(role, scope, owner, allowed):
    category = ContentMaterialCategory(owner_uid=owner, visibility=scope)
    assert can_contribute_to_category(SimpleNamespace(uid="employee", role=role), category) is allowed


@pytest.mark.asyncio
async def test_upload_uses_same_contribution_policy(monkeypatch):
    from yuxi.services import material_library_service as materials

    category = ContentMaterialCategory(owner_uid="other", visibility="enterprise", name="Shared")
    monkeypatch.setattr(materials, "resolve_material_category", AsyncMock(return_value=category))
    user = SimpleNamespace(uid="employee", department_id=None)
    resolved, _ = await materials._resolve_upload_category(object(), user, "folder", None)
    assert resolved is category
    category.visibility = "private"
    with pytest.raises(HTTPException) as error:
        await materials._resolve_upload_category(object(), user, "folder", None)
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_polish_and_task_http_contract_accepts_same_array_and_rejects_stale_inputs(monkeypatch):
    monkeypatch.setattr("yuxi.image_design.mp_service.validate_mp_save_target", AsyncMock())
    import httpx
    from fastapi import FastAPI
    from server.routers.mp_image_design_router import mp_image_design
    from server.utils.auth_middleware import get_db, get_mp_context
    from yuxi.image_design import service

    row = SimpleNamespace(id="idr_contract", status="completed", workflow="cross_space")
    db = SimpleNamespace(scalar=AsyncMock(return_value=row))

    async def refine(_db, _user, payload):
        semantic = payload.model_dump(mode="json", exclude={"parent_refinement_id", "edited_prompt"})
        semantic["effective_prompt"] = payload.user_prompt
        row.request_json = {"semantic": semantic}
        return {"refinement": {"id": row.id, "compiled_prompt": "已润色"}}

    generate = AsyncMock(
        return_value={
            "job": {"id": "idj_contract", "status": "queued", "workflow": "cross_space", "request": {"gen_count": 2}},
            "reused": False,
        }
    )
    monkeypatch.setattr(service, "create_refinement", refine)
    monkeypatch.setattr(service, "create_generate_job", generate)
    app = FastAPI()
    app.include_router(mp_image_design, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_mp_context] = lambda: SimpleNamespace(user=SimpleNamespace(uid="employee"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        payload = transfer_payload(extra_element=["落地窗旁休闲躺椅", "壁炉居中"])
        polished = await client.post("/api/mp/image-design/polish", json=payload)
        assert polished.status_code == 200, polished.text
        task = {**payload, "refinement_id": polished.json()["refinement_id"], "save_target": {"scope": "private"}}
        created = await client.post("/api/mp/image-design/tasks", json=task)
        assert created.status_code == 200, created.text
        assert created.json()["task"]["status"] == "queued"
        assert generate.await_args.args[2].save_target.scope == "private"
        assert generate.await_args.kwargs["mp_fixed_target"] is True
        task["extra_element"][1] = "开放式层板展示架"
        stale = await client.post("/api/mp/image-design/tasks", json=task)
        assert stale.status_code == 409
        invalid = await client.post("/api/mp/image-design/polish", json={**payload, "extra_element": ["壁炉居中"] * 3})
        assert invalid.status_code == 422
        assert generate.await_count == 1
