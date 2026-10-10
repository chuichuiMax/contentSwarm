from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services import employee_service
from yuxi.storage.postgres.models_business import Base, User
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentEmployee,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
)


@pytest.fixture
def repository():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            ContentCoverAsset.__table__,
            ContentMaterialCategory.__table__,
            ContentMaterialLibraryItem.__table__,
        ],
    )
    with Session(engine) as db:
        categories = [
            ("alice", "private", "private", "rough", None),
            ("shared-owner", "shared", "enterprise", "rough", None),
            ("shared-owner", "child", "enterprise", None, "shared"),
            ("shared-owner", "generated", "enterprise", "generated", None),
            ("shared-owner", "deleted", "enterprise", "rough", None),
        ]
        for owner, key, visibility, role, parent in categories:
            db.add(
                ContentMaterialCategory(
                    owner_uid=owner,
                    id=key,
                    material_type="image",
                    name=key,
                    visibility=visibility,
                    image_design_role=role,
                    parent_id=parent,
                    deleted_at=datetime(2026, 1, 1) if key == "deleted" else None,
                )
            )
        specs = [
            ("a-private", "alice", "private", "enabled", False, False),
            ("a-shared-1", "alice", "shared", "enabled", False, False),
            ("a-shared-2", "alice", "shared", "enabled", False, False),
            ("b-shared", "bob", "shared", "enabled", False, False),
            ("disabled", "alice", "shared", "disabled", False, False),
            ("removed", "alice", "shared", "enabled", True, False),
            ("asset-removed", "alice", "shared", "enabled", False, True),
            ("generated", "alice", "generated", "enabled", False, False),
            ("child", "alice", "child", "enabled", False, False),
            ("gallery-removed", "alice", "deleted", "enabled", False, False),
        ]
        for key, owner, category, status, deleted, asset_deleted in specs:
            db.add(
                ContentCoverAsset(
                    id=key,
                    owner_uid=owner,
                    role="library_image",
                    original_file_name=f"{key}.png",
                    content_type="image/png",
                    file_size=1,
                    image_width=1,
                    image_height=1,
                    sha256=key,
                    bucket_name="test",
                    object_name=key,
                    created_at=datetime(2026, 10, 10),
                    deleted_at=datetime(2026, 1, 1) if asset_deleted else None,
                )
            )
            db.add(
                ContentMaterialLibraryItem(
                    id=key,
                    owner_uid=owner,
                    asset_id=key,
                    material_type="image",
                    display_name=key,
                    category=category,
                    category_owner_uid="alice" if category == "private" else "shared-owner",
                    status=status,
                    deleted_at=datetime(2026, 1, 1) if deleted else None,
                )
            )
        db.commit()

        async def execute(statement):
            return db.execute(statement)

        yield MaterialLibraryRepository(SimpleNamespace(execute=execute), include_shared=True)
    engine.dispose()


@pytest.mark.asyncio
async def test_counts_separate_uploaders_from_gallery_owner_and_private_images(repository):
    assert await repository.count_private_rough_images_by_owner(["alice", "bob", "shared-owner"]) == {"alice": 1}
    assert await repository.count_enterprise_rough_images_by_owner(["alice", "bob", "shared-owner"]) == {
        "alice": 2,
        "bob": 1,
    }
    assert await repository.count_enterprise_rough_images_by_owner([]) == {}


@pytest.mark.asyncio
async def test_enterprise_detail_total_and_pagination_match_employee_count(repository):
    params = dict(
        material_type="image",
        category=None,
        status="enabled",
        query_text=None,
        page=1,
        page_size=1,
        scope="enterprise",
        enterprise_rough_owner_uid="alice",
    )
    rows, total = await repository.list_items("administrator", **params)
    assert total == 2
    assert len(rows) == 1
    assert rows[0][0].owner_uid == "alice"
    params["page"] = 2
    second, second_total = await repository.list_items("administrator", **params)
    assert second_total == total
    assert rows[0][0].id != second[0][0].id
    params.update(page=1, query_text="a-shared-2")
    filtered, filtered_total = await repository.list_items("administrator", **params)
    assert filtered_total == 1
    assert filtered[0][0].id == "a-shared-2"
    params.update(query_text=None, uploaded_before=datetime(2026, 10, 10))
    assert (await repository.list_items("administrator", **params))[1] == 0


@pytest.mark.asyncio
async def test_employee_and_system_account_rows_receive_both_counts(monkeypatch):
    employee = ContentEmployee(
        id="staff",
        employee_code="E1",
        name="员工",
        login_account="123",
        gender="male",
        role="user",
        created_at=datetime(2026, 1, 1),
    )
    user = User(uid="pc-admin", username="管理员", role="admin", created_at=datetime(2026, 1, 2))
    uid = employee_service._platform_uid(employee)

    async def list_employees(**kwargs):
        return [employee]

    async def execute(statement):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [user]))

    async def private_counts(owners):
        assert set(owners) == {uid, "pc-admin"}
        return {uid: 4}

    async def shared_counts(owners):
        assert set(owners) == {uid, "pc-admin"}
        return {uid: 2, "pc-admin": 3}

    monkeypatch.setattr(
        employee_service, "EmployeeRepository", lambda db: SimpleNamespace(list_employees=list_employees)
    )
    monkeypatch.setattr(
        employee_service,
        "MaterialLibraryRepository",
        lambda db: SimpleNamespace(
            count_private_rough_images_by_owner=private_counts,
            count_enterprise_rough_images_by_owner=shared_counts,
        ),
    )
    listing = await employee_service.list_employees(SimpleNamespace(execute=execute))
    rows = {row["source"]: row for row in listing["employees"]}
    assert (rows["employee"]["rough_image_count"], rows["employee"]["enterprise_rough_image_count"]) == (4, 2)
    assert (rows["user"]["rough_image_count"], rows["user"]["enterprise_rough_image_count"]) == (0, 3)
