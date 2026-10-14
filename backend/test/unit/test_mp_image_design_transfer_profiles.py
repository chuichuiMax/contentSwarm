from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from server.routers.mp_image_design_router import mp_image_design
from server.utils.auth_middleware import get_db, get_mp_context
from yuxi.image_design import service
from yuxi.image_design.analysis import configured_image_design_vision_model_spec
from yuxi.image_design.mp_schemas import MpPolishCreate
from yuxi.image_design.mp_service import refinement_payload
from yuxi.image_design.prompt_compiler import build_prompt_plan, compile_prompt
from yuxi.image_design.workflow_profiles import public_profiles


@pytest.mark.parametrize("space", public_profiles()["spaces"], ids=lambda space: space["id"])
def test_mp_transfer_compiles_the_selected_space_options(space):
    payload = MpPolishCreate(
        workflow="transfer",
        images=[{"role": "reference", "library_item_id": "reference"}],
        description="自然光",
        target_space=space["id"],
        layout_type=space["layouts"][0]["id"],
        extra_element=[item["id"] for item in space["addons"]],
    )
    converted = refinement_payload(payload)
    plan = build_prompt_plan(converted, {"cross_space_style": {"style": "现代简约"}})
    prompt = compile_prompt(plan)
    assert f"目标空间：{space['label']}" in prompt
    assert f"布局要求：{space['layouts'][0]['label']}" in prompt
    for addon in space["addons"]:
        assert addon["label"] in prompt
    assert not converted.layout.startswith("mp_")


@pytest.mark.parametrize(
    "layout,addons",
    [("sofa_wall", []), ("bed_center", ["fireplace"]), ("一字型沙发墙", ["壁炉居中"])],
)
def test_mp_transfer_rejects_options_from_another_space(layout, addons):
    payload = MpPolishCreate(
        workflow="transfer",
        images=[{"role": "reference", "library_item_id": "reference"}],
        target_space="主卧",
        layout_type=layout,
        extra_element=addons,
    )
    with pytest.raises(HTTPException) as error:
        refinement_payload(payload)
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_mp_profiles_polish_and_generation_use_the_same_space_selection(monkeypatch):
    row = SimpleNamespace(id="idr_transfer", status="completed", workflow="cross_space")
    db = SimpleNamespace(scalar=AsyncMock(return_value=row))
    prompts = []

    async def refine(_db, _user, payload):
        plan = build_prompt_plan(payload, {"cross_space_style": {"style": "现代简约"}})
        semantic = payload.model_dump(mode="json", exclude={"parent_refinement_id", "edited_prompt"})
        row.request_json = {"semantic": {**semantic, "effective_prompt": payload.user_prompt}}
        prompts.append(compile_prompt(plan))
        return {"refinement": {"id": row.id, "compiled_prompt": prompts[-1]}}

    generate = AsyncMock(
        return_value={
            "job": {"id": "idj_transfer", "workflow": "cross_space", "status": "queued", "request": {"gen_count": 1}},
            "reused": False,
        }
    )
    monkeypatch.setattr(service, "create_refinement", refine)
    monkeypatch.setattr(service, "create_generate_job", generate)
    monkeypatch.setattr("yuxi.image_design.mp_service.validate_mp_save_target", AsyncMock())
    app = FastAPI()
    app.include_router(mp_image_design, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_mp_context] = lambda: SimpleNamespace(user=SimpleNamespace(uid="employee"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        profiles = await client.get("/api/mp/image-design/profiles")
        assert profiles.status_code == 200
        body = profiles.json()
        assert body["spaces"] == public_profiles()["spaces"]
        assert body["vision_model_spec"] == configured_image_design_vision_model_spec()
        assert body["prompt_compiler_spec"].startswith("deterministic-prompt-compiler:")
        assert "image2" not in body
        bedroom = next(space for space in profiles.json()["spaces"] if space["id"] == "master_bedroom")
        payload = {
            "workflow": "transfer",
            "images": [{"role": "reference", "library_item_id": "reference"}],
            "description": "自然光",
            "target_space": bedroom["id"],
            "layout_type": bedroom["layouts"][0]["id"],
            "extra_element": [bedroom["addons"][0]["id"]],
        }
        polished = await client.post("/api/mp/image-design/polish", json=payload)
        assert polished.status_code == 200, polished.text
        assert "床居中对称" in polished.json()["polished_prompt"]
        assert "床尾凳" in polished.json()["polished_prompt"]
        task = {**payload, "refinement_id": row.id, "save_target": {"scope": "private"}}
        created = await client.post("/api/mp/image-design/tasks", json=task)
        assert created.status_code == 200, created.text
        task["layout_type"] = bedroom["layouts"][1]["id"]
        stale = await client.post("/api/mp/image-design/tasks", json=task)
        assert stale.status_code == 409
        assert generate.await_count == 1
