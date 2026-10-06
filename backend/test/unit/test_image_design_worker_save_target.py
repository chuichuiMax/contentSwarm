from __future__ import annotations

import asyncio
import hashlib
import io
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from PIL import Image

from yuxi.content_cover.image2_client import Image2Error
from yuxi.content_cover.schemas import Image2Input
from yuxi.image_design import save_targets, service, worker
from yuxi.image_design.schemas import ImageDesignGenerateCreate, ImageDesignSaveTarget
from yuxi.image_design.worker import _normalize_output
from yuxi.services import material_library_service
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
    ImageDesignJob,
    ImageDesignLibraryItem,
)


class MemoryRepository:
    def __init__(self, db, **_kwargs):
        self.db = db

    async def migrate_generated_private_root(self, owner_uid, root_id):
        pass

    async def list_categories(self, owner_uid, material_type):
        return [
            c
            for c in self.db.categories
            if c.material_type == material_type
            and c.deleted_at is None
            and (c.owner_uid == owner_uid or c.visibility == "enterprise")
        ]

    async def ensure_default_categories(self, values):
        for value in values:
            if not any(c.id == value["id"] and c.owner_uid == value["owner_uid"] for c in self.db.categories):
                self.db.categories.append(ContentMaterialCategory(**value))

    async def sync_system_categories(self, values):
        for value in values:
            category = next(
                (
                    item
                    for item in self.db.categories
                    if item.id == value["id"] and item.owner_uid == value["owner_uid"]
                ),
                None,
            )
            if category is None:
                self.db.categories.append(ContentMaterialCategory(**value))
                continue
            for field, field_value in value.items():
                setattr(category, field, field_value)
            category.deleted_at = None

    async def get_category_exact(
        self, *, requester_uid, material_type, category_id, category_owner_uid=None, visibility=None, for_update=False
    ):
        return next(
            (
                c
                for c in self.db.categories
                if c.id == category_id
                and c.material_type == material_type
                and c.deleted_at is None
                and (c.owner_uid == requester_uid or c.visibility == "enterprise")
                and (category_owner_uid is None or c.owner_uid == category_owner_uid)
                and (visibility is None or c.visibility == visibility)
            ),
            None,
        )

    async def get_category(self, owner_uid, material_type, category_id):
        return await self.get_category_exact(
            requester_uid=owner_uid, material_type=material_type, category_id=category_id
        )

    async def get_item_by_asset(self, asset_id):
        return next((item for item in self.db.items if item.asset_id == asset_id and item.deleted_at is None), None)

    async def create_item(self, **values):
        item = ContentMaterialLibraryItem(**values)
        self.db.items.append(item)
        return item


class MemorySession:
    def __init__(self):
        self.categories = []
        self.items = []
        self.assets = []
        self.design_library_items = []
        self.user = User(uid="employee", department_id=None, is_deleted=0)
        self.job = ImageDesignJob(
            id="idj_test",
            owner_uid="employee",
            workflow="room_adapt",
            status="queued",
            request_json={
                "material_ids": ["reference"],
                "prompt": "客厅",
                "size": "1024x1024",
                "gen_count": 2,
                "requested_save_target": {"scope": "enterprise", "gallery_id": None},
            },
            result_json={"asset_ids": []},
        )

    async def scalar(self, query):
        model = query.column_descriptions[0]["entity"]
        if model is User:
            assert "is_deleted" in str(query) and "deleted_at" in str(query)
            return self.user if self.user and not self.user.is_deleted and self.user.deleted_at is None else None
        if model is ImageDesignJob:
            return self.job
        if model is ContentCoverAsset:
            asset_id = query.compile().params.get("id_1")
            return next((a for a in self.assets if a.id == asset_id), None)
        if model is ContentMaterialCategory:
            category_id = query.compile().params.get("id_1")
            return next((c for c in self.categories if c.id == category_id and c.deleted_at is None), None)
        if model is ImageDesignLibraryItem:
            params = query.compile().params
            owner_uid = params.get("owner_uid_1")
            asset_id = params.get("asset_id_1")
            return next(
                (
                    item
                    for item in self.design_library_items
                    if item.owner_uid == owner_uid and item.asset_id == asset_id
                ),
                None,
            )
        raise AssertionError(model)

    def add(self, obj):
        if isinstance(obj, ContentCoverAsset):
            self.assets.append(obj)
        elif isinstance(obj, ImageDesignLibraryItem):
            self.design_library_items.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        pass


@pytest.fixture
def db(monkeypatch):
    session = MemorySession()
    from yuxi.services import personal_materials

    for module in (save_targets, worker, material_library_service, personal_materials):
        monkeypatch.setattr(module, "MaterialLibraryRepository", MemoryRepository)
    return session


def asset():
    return ContentCoverAsset(id="cca_generated", owner_uid="employee", original_file_name="result.png")


@pytest.mark.asyncio
async def test_mp_worker_does_not_fallback_when_generated_gallery_disappears(db):
    with pytest.raises(HTTPException):
        await worker.attach_generated_asset(
            db,
            user=db.user,
            asset=asset(),
            requested=ImageDesignSaveTarget(scope="enterprise", gallery_id="deleted"),
            job_id="idj_test",
            workflow="room_adapt",
            mp_fixed_target=True,
        )
    assert db.items == [] and db.design_library_items == []


@pytest.mark.asyncio
async def test_mp_worker_saves_enterprise_target_to_actual_enterprise_gallery(db):
    db.categories.append(
        ContentMaterialCategory(
            id="actual-generated-id",
            owner_uid="admin",
            material_type="image",
            name="生图图库",
            visibility="enterprise",
        )
    )
    resolved, item = await worker.attach_generated_asset(
        db,
        user=db.user,
        asset=asset(),
        requested=ImageDesignSaveTarget(scope="enterprise", gallery_id="actual-generated-id"),
        job_id="idj_test",
        workflow="room_adapt",
        mp_fixed_target=True,
    )
    assert resolved.public_target == {"scope": "enterprise", "gallery_id": "actual-generated-id"}
    assert item.category == "actual-generated-id"
    assert item.category_owner_uid == "admin"
    assert item.metadata_json["source_channel"] == "mp"
    assert item.metadata_json["source_folder"] == "generated"
    assert db.design_library_items[0].source_material_item_id == item.id

    db.design_library_items[0].hidden_at = worker.utc_now_naive()
    await worker.attach_generated_asset(
        db,
        user=db.user,
        asset=asset(),
        requested=ImageDesignSaveTarget(scope="enterprise", gallery_id="actual-generated-id"),
        job_id="idj_test",
        workflow="room_adapt",
        mp_fixed_target=True,
    )
    assert len(db.design_library_items) == 1 and db.design_library_items[0].hidden_at is not None


@pytest.mark.asyncio
async def test_mp_worker_saves_private_option_to_personal_generated_gallery(db):
    resolved, item = await worker.attach_generated_asset(
        db,
        user=db.user,
        asset=asset(),
        requested=ImageDesignSaveTarget(scope="private"),
        job_id="idj_test",
        workflow="room_adapt",
        mp_fixed_target=True,
    )
    assert resolved.public_target == {"scope": "private", "gallery_id": "mp-generated-private"}
    assert item.category == "mp-generated-private"


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [{"scope": "enterprise"}, None, {"scope": "private", "gallery_id": "missing"}])
async def test_create_job_stores_only_canonical_validated_target(db, monkeypatch, target):
    refinement = SimpleNamespace(
        status="completed",
        workflow="style_transfer",
        request_json={"materials": []},
        input_fingerprint="verified",
        analysis_ids_json=[],
    )
    monkeypatch.setattr(db, "scalar", AsyncMock(side_effect=[refinement, None]))
    created = []
    monkeypatch.setattr(db, "add", created.append)
    monkeypatch.setattr(service, "get_image2_config_state", AsyncMock(return_value={"configured": True}))
    monkeypatch.setattr(
        service,
        "validate_refinement_integrity",
        lambda *args: SimpleNamespace(plan_version=1, image_roles=[{"material_id": "source"}]),
    )
    monkeypatch.setattr(service, "compile_prompt", lambda *args: "verified prompt")
    monkeypatch.setattr(
        service, "get_arq_pool", AsyncMock(return_value=SimpleNamespace(enqueue_job=AsyncMock(return_value=object())))
    )
    payload = ImageDesignGenerateCreate(refinement_id="idr_verified", save_target=target)
    if target and target.get("gallery_id"):
        with pytest.raises(HTTPException):
            await service.create_generate_job(db, db.user, payload)
        assert not created
    else:
        await service.create_generate_job(db, db.user, payload)
        assert len(created) == 1
        assert created[0].request_json["requested_save_target"] == (
            {"scope": "enterprise", "gallery_id": None} if target else None
        )
        assert "save_target" not in created[0].request_json


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope,gallery_id",
    [
        ("private", None),
        ("enterprise", None),
        ("private", "folder"),
        ("enterprise", "folder"),
    ],
)
async def test_attach_preserves_scope_owner_and_is_idempotent(db, scope, gallery_id):
    if scope == "enterprise" and gallery_id is None:
        db.categories.append(
            ContentMaterialCategory(
                id="enterprise-root",
                owner_uid="employee",
                material_type="image",
                visibility="private",
                name="Colliding folder",
                is_system=False,
            )
        )
    if gallery_id:
        db.categories.append(
            ContentMaterialCategory(
                id=gallery_id,
                owner_uid="employee" if scope == "private" else "admin",
                material_type="image",
                visibility=scope,
                name="Folder",
                is_system=False,
            )
        )
    requested = ImageDesignSaveTarget(scope=scope, gallery_id=gallery_id)
    generated = asset()
    first, item = await worker.attach_generated_asset(
        db, user=db.user, asset=generated, requested=requested, job_id="idj_test", workflow="room_adapt"
    )
    _, repeated = await worker.attach_generated_asset(
        db, user=db.user, asset=generated, requested=requested, job_id="idj_test", workflow="room_adapt"
    )
    assert first.public_target == {"scope": scope, "gallery_id": gallery_id}
    assert first.warning is None
    assert repeated.id == item.id and len(db.items) == 1
    assert len(db.design_library_items) == 1
    assert db.design_library_items[0].source_material_item_id == item.id
    assert db.design_library_items[0].source_role == "generated"
    assert item.owner_uid == "employee"
    assert item.category_owner_uid == (
        "employee" if scope == "private" else "admin" if gallery_id else "system:material-library"
    )
    assert item.metadata_json["source"] == "image_design"
    assert item.metadata_json["requested_save_target"] == {"scope": scope, "gallery_id": gallery_id}
    assert item.metadata_json["resolved_save_target"] == {"scope": scope, "gallery_id": gallery_id}


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["private", "enterprise"])
async def test_deleted_folder_falls_back_only_to_requested_scope(db, scope):
    resolved, item = await worker.attach_generated_asset(
        db,
        user=db.user,
        asset=asset(),
        requested=ImageDesignSaveTarget(scope=scope, gallery_id="deleted"),
        job_id="idj_test",
        workflow="room_adapt",
    )
    assert resolved.public_target == {"scope": scope, "gallery_id": None}
    assert resolved.warning == "SAVE_TARGET_FALLBACK_TO_ROOT"
    assert item.metadata_json["requested_save_target"]["gallery_id"] == "deleted"


@pytest.mark.asyncio
@pytest.mark.parametrize("visibility,owner", [("private", "admin"), ("private", "employee")])
async def test_existing_folder_scope_change_must_not_fall_back(db, visibility, owner):
    db.categories.append(
        ContentMaterialCategory(
            id="folder", owner_uid=owner, material_type="image", visibility=visibility, name="Folder"
        )
    )
    with pytest.raises(HTTPException):
        await worker.attach_generated_asset(
            db,
            user=db.user,
            asset=asset(),
            requested=ImageDesignSaveTarget(scope="enterprise", gallery_id="folder"),
            job_id="idj_test",
            workflow="room_adapt",
        )
    assert not db.items


@pytest.fixture
def run_worker(db, monkeypatch):
    @asynccontextmanager
    async def session_context():
        yield db

    monkeypatch.setattr(worker.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(
        worker,
        "_load_material_input",
        AsyncMock(return_value=Image2Input(data=b"input", content_type="image/png", file_name="input.png")),
    )
    monkeypatch.setattr(worker, "resolve_image2_config", AsyncMock(return_value=object()))
    monkeypatch.setattr(worker, "clear_cancel_signal", AsyncMock())
    monkeypatch.setattr(worker, "_normalize_output", lambda *args: (b"png", 1024, 1024))
    monkeypatch.setattr(
        worker,
        "get_minio_client",
        lambda: SimpleNamespace(
            aupload_file=AsyncMock(return_value=SimpleNamespace(bucket_name="results", object_name="output.png"))
        ),
    )

    class Client:
        calls = 0
        fail_on = None

        def __init__(self, _config):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def submit(self, request, *, idempotency_key):
            self.__class__.calls += 1
            if self.calls == self.fail_on:
                raise Image2Error("IMAGE2_TEMPORARY", "temporary")
            return SimpleNamespace(status="completed", provider_task_id=None, images=["result"])

        async def read_output(self, _image):
            return b"image", "image/png"

    monkeypatch.setattr(worker, "Image2Client", Client)
    return Client


@pytest.mark.asyncio
async def test_worker_saves_every_output_and_retry_does_not_duplicate(db, run_worker):
    run_worker.fail_on = 2
    await worker.process_image_design_job({}, db.job.id)
    assert db.job.status == "failed"
    assert len(db.assets) == len(db.items) == 1
    first_id = db.assets[0].id
    run_worker.fail_on = None
    await worker.process_image_design_job({}, db.job.id)
    assert db.job.status == "succeeded"
    assert len(db.assets) == len(db.items) == 2
    assert db.job.result_json["asset_ids"][0] == first_id
    assert len(db.job.result_json["library_item_ids"]) == 2
    assert db.job.result_json["requested_save_target"] == {"scope": "enterprise", "gallery_id": None}
    assert db.job.result_json["resolved_save_target"] == {"scope": "enterprise", "gallery_id": None}
    assert db.job.result_json["save_warning"] is None
    assert db.job.error_code is None
    assert run_worker.calls == 3


@pytest.mark.asyncio
async def test_worker_replays_completed_job_without_creating_assets(db, run_worker):
    await worker.process_image_design_job({}, db.job.id)
    await worker.process_image_design_job({}, db.job.id)
    assert len(db.items) == len(db.assets) == 2
    assert run_worker.calls == 2


@pytest.mark.asyncio
async def test_overlapping_delivery_cannot_overwrite_retained_asset_bytes(db, run_worker, monkeypatch):
    db.job.request_json["gen_count"] = 1
    provider_images = []
    for color in ((255, 0, 0), (0, 0, 255)):
        output = io.BytesIO()
        Image.new("RGB", (1024, 1024), color).save(output, format="PNG")
        provider_images.append(output.getvalue())

    async def read_output(_client, _image):
        return provider_images[run_worker.calls - 1], "image/png"

    monkeypatch.setattr(run_worker, "read_output", read_output)
    monkeypatch.setattr(worker, "_normalize_output", _normalize_output)
    first_upload_started = asyncio.Event()
    second_upload_started = asyncio.Event()
    first_delivery_finished = asyncio.Event()
    uploads = []
    objects = {}

    async def upload(*, bucket_name, object_name, data, content_type):
        uploads.append((bucket_name, object_name, data))
        if len(uploads) == 1:
            first_upload_started.set()
            # Both deliveries must snapshot the empty checkpoint before A commits.
            await second_upload_started.wait()
        else:
            second_upload_started.set()
            # B writes only after A's asset/item/checkpoint transaction commits.
            await first_delivery_finished.wait()
        objects[(bucket_name, object_name)] = data
        return SimpleNamespace(bucket_name=bucket_name, object_name=object_name)

    monkeypatch.setattr(worker, "get_minio_client", lambda: SimpleNamespace(aupload_file=upload))

    async def first_delivery():
        try:
            await worker.process_image_design_job({}, db.job.id)
        finally:
            first_delivery_finished.set()

    first = asyncio.create_task(first_delivery())
    await asyncio.wait_for(first_upload_started.wait(), timeout=5)
    second = asyncio.create_task(worker.process_image_design_job({}, db.job.id))
    await asyncio.wait_for(asyncio.gather(first, second), timeout=5)

    assert db.job.status == "succeeded"
    assert run_worker.calls == 2 and len(uploads) == 2
    assert uploads[0][2] != uploads[1][2]
    assert len(db.assets) == len(db.items) == 1
    retained = db.assets[0]
    stored = objects[(retained.bucket_name, retained.object_name)]
    assert hashlib.sha256(stored).hexdigest() == retained.sha256
    assert stored == uploads[0][2]
    assert len(stored) == retained.file_size
    assert len(objects) == 2
    assert db.job.result_json["asset_ids"] == [retained.id]
    assert db.job.result_json["library_item_ids"] == [db.items[0].id]


@pytest.mark.asyncio
@pytest.mark.parametrize("deleted", [False, True])
async def test_worker_rejects_missing_or_deleted_owner(db, run_worker, deleted):
    if deleted:
        db.user.is_deleted = 1
    else:
        db.user = None
    await worker.process_image_design_job({}, db.job.id)
    assert db.job.status == "failed"
    assert db.job.error_code == "IMAGE_DESIGN_OWNER_NOT_FOUND"
    assert not db.items
    assert run_worker.calls == 0


@pytest.mark.asyncio
async def test_worker_exposes_fallback_in_job_and_asset_metadata(db, run_worker):
    db.job.request_json["requested_save_target"]["gallery_id"] = "deleted"
    await worker.process_image_design_job({}, db.job.id)
    assert db.job.status == "succeeded"
    assert db.job.result_json["save_warning"] == "SAVE_TARGET_FALLBACK_TO_ROOT"
    assert db.job.result_json["resolved_save_target"] == {"scope": "enterprise", "gallery_id": None}
    for generated in db.assets:
        assert generated.metadata_json["requested_save_target"]["gallery_id"] == "deleted"
        assert generated.metadata_json["resolved_save_target"] == {"scope": "enterprise", "gallery_id": None}


@pytest.mark.asyncio
async def test_worker_does_not_complete_output_when_attachment_fails(db, run_worker, monkeypatch):
    monkeypatch.setattr(MemoryRepository, "create_item", AsyncMock(side_effect=RuntimeError("database unavailable")))
    await worker.process_image_design_job({}, db.job.id)
    assert db.job.status == "failed"
    assert db.job.result_json["asset_ids"] == []
    assert not db.items


@pytest.mark.asyncio
async def test_worker_legacy_pc_job_does_not_attach(db, run_worker):
    db.job.request_json.pop("requested_save_target")
    db.user = None
    await worker.process_image_design_job({}, db.job.id)
    assert db.job.status == "succeeded"
    assert len(db.assets) == 2 and not db.items
    assert set(db.job.result_json) == {"asset_ids"}
