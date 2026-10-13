"""Real storage and authenticated PC API checks for content-cover archival."""

import importlib.util
import io
import secrets
import uuid
from pathlib import Path

import httpx
import pytest
from PIL import Image
from sqlalchemy import delete, select

from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.services.content_cover_worker import _store_outputs
from yuxi.services.material_library_service import create_library_item_for_asset
from yuxi.services.personal_materials import folder_categories
from yuxi.storage.minio import get_minio_client
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, OperationLog, User
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset, ContentCoverJob, ContentMaterialCategory, ContentMaterialLibraryItem,
    ContentRuleVersion, ContentTask, ContentWorkflowVersion, IndustryTemplateVersion,
)
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_pc_and_mp_covers_only_appear_in_works_and_legacy_migration_keeps_files(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "cover_archive_e2e_migration",
        Path(__file__).resolve().parents[2] / "scripts/migrate_content_cover_archives.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    owner_uid = f"cover-archive-e2e-{uuid.uuid4().hex}"
    foreign_uid = f"{owner_uid}-foreign"
    objects, asset_ids = set(), []
    pg_manager.initialize()
    output = io.BytesIO()
    Image.new("RGB", (64, 64), "#3566CC").save(output, format="PNG")
    try:
        async with pg_manager.get_async_session_context() as db:
            department = Department(name=owner_uid)
            db.add(department)
            await db.flush()
            users = [User(uid=uid, username=uid, password_hash=AuthUtils.hash_password(secrets.token_urlsafe(24)),
                          role="user", department_id=department.id) for uid in (owner_uid, foreign_uid)]
            db.add_all(users)
            await db.flush()
            token = AuthUtils.create_access_token({"sub": str(users[0].id)})
            foreign_token = AuthUtils.create_access_token({"sub": str(users[1].id)})
            template = await db.scalar(select(IndustryTemplateVersion.id).limit(1))
            workflow = await db.scalar(select(ContentWorkflowVersion.id).limit(1))
            rule = await db.scalar(select(ContentRuleVersion.id).limit(1))
            assert template and workflow and rule, "Running API must have content seed versions"
            for channel in ("pc", "mp"):
                task_id = f"ct_{uuid.uuid4().hex}"
                db.add(ContentTask(id=task_id, name=f"{channel} cover archival", created_by=owner_uid,
                                   updated_by=owner_uid, industry_template_version_id=template,
                                   workflow_version_id=workflow, rule_version_id=rule,
                                   brief_json={"form_values": {
                                       "mp_content_code": "MP-e2e",
                                   } if channel == "mp" else {}}))
                await db.flush()
                db.add(ContentCoverJob(id=f"{owner_uid}-{channel}", owner_uid=owner_uid, content_task_id=task_id,
                                       mode="compose", status="running", idempotency_key=channel,
                                       request_json={}, result_json={}))
        for channel in ("pc", "mp"):
            async with pg_manager.get_async_session_context() as db:
                job = await db.get(ContentCoverJob, f"{owner_uid}-{channel}")
            ids = await _store_outputs(job, [output.getvalue()])
            asset_ids.extend(ids)
            async with pg_manager.get_async_session_context() as db:
                saved_job = await db.get(ContentCoverJob, job.id)
                saved_job.status = "succeeded"
                saved_job.result_json = {"asset_ids": ids}
                assert await db.scalar(select(ContentMaterialLibraryItem.id).where(
                    ContentMaterialLibraryItem.asset_id.in_(ids),
                )) is None
        async with pg_manager.get_async_session_context() as db:
            user = await db.scalar(select(User).where(User.uid == owner_uid))
            mapping = await folder_categories(db, user)
            generated_id, works_id = mapping["generated"][0].id, mapping["works"][0].id
            cover = await db.get(ContentCoverAsset, asset_ids[0])
            # Simulate a historical PC-cover attachment without duplicating its object.
            legacy = await create_library_item_for_asset(db, asset=cover, material_type="image", name="legacy cover",
                category=generated_id, category_owner_uid=owner_uid,
                metadata={"source": "content_production", "source_channel": "pc", "source_folder": "generated"})
            legacy_id = legacy.id
            design = await ContentCoverRepository(db).create_asset(
                id=f"cca_{uuid.uuid4().hex}", owner_uid=owner_uid, role="output", original_file_name="design.png",
                content_type="image/png", file_size=cover.file_size, image_width=cover.image_width,
                image_height=cover.image_height, sha256=cover.sha256, bucket_name=cover.bucket_name,
                object_name=cover.object_name, metadata_json={"domain": "image_design"},
            )
            design_id = design.id
            await create_library_item_for_asset(db, asset=design, material_type="image", name="AI design",
                category=generated_id, category_owner_uid=owner_uid, metadata={"source": "image_design"})
        async with httpx.AsyncClient(base_url="http://127.0.0.1:5050", trust_env=False, timeout=60) as client:
            headers = {"Authorization": f"Bearer {token}"}
            for apply in (False, True):
                async with pg_manager.get_async_session_context() as db:
                    report = await migration.migrate_archives(db, [owner_uid], apply=apply,
                                                             manifest_path=tmp_path / "before.json")
                    assert report["count"] == 1
                    if not apply:
                        await db.rollback()
                galleries = await client.get("/api/material-library/galleries", headers=headers)
                assert galleries.status_code == 200, galleries.text
                cards = {g["personal_folder"]: g for g in galleries.json()["galleries"] if g.get("personal_folder")}
                assert cards["generated"]["count"] == 1
                assert cards["works"]["count"] == 2
                for category_id, expected in ((generated_id, {design_id}), (works_id, set(asset_ids))):
                    result = await client.get("/api/material-library/items", headers=headers,
                        params={"material_type": "image", "scope": "private", "category": category_id})
                    assert result.status_code == 200, result.text
                    assert {row["asset_id"] for row in result.json()["items"]} == expected
                    assert result.json()["total"] == len(expected)
                for asset_id in asset_ids:
                    result = await client.get(f"/api/content/covers/assets/{asset_id}/file", headers=headers)
                    assert result.status_code == 200, result.text
                    assert Image.open(io.BytesIO(result.content)).size == (64, 64)
            denied = await client.get(f"/api/content/covers/assets/{asset_ids[0]}/file",
                                     headers={"Authorization": f"Bearer {foreign_token}"})
            assert denied.status_code == 404
        async with pg_manager.get_async_session_context() as db:
            legacy = await db.get(ContentMaterialLibraryItem, legacy_id)
            assert (legacy.category, legacy.asset_id) == (works_id, asset_ids[0])
            assert (await migration.migrate_archives(db, [owner_uid]))["count"] == 0
    finally:
        async with pg_manager.get_async_session_context() as db:
            assets = list(await db.scalars(select(ContentCoverAsset).where(ContentCoverAsset.owner_uid == owner_uid)))
            objects.update((a.bucket_name, a.object_name) for a in assets)
            await db.execute(delete(ContentMaterialLibraryItem).where(
                ContentMaterialLibraryItem.owner_uid == owner_uid,
            ))
            await db.execute(delete(ContentCoverJob).where(ContentCoverJob.owner_uid == owner_uid))
            await db.execute(delete(ContentCoverAsset).where(ContentCoverAsset.owner_uid == owner_uid))
            await db.execute(delete(ContentTask).where(ContentTask.created_by == owner_uid))
            await db.execute(delete(ContentMaterialCategory).where(ContentMaterialCategory.owner_uid == owner_uid))
            ids = select(User.id).where(User.uid.in_([owner_uid, foreign_uid]))
            await db.execute(delete(OperationLog).where(OperationLog.user_id.in_(ids)))
            await db.execute(delete(User).where(User.uid.in_([owner_uid, foreign_uid])))
            await db.execute(delete(Department).where(Department.name == owner_uid))
        for bucket, name in objects:
            await get_minio_client().adelete_file(bucket, name)
        await pg_manager.close()
