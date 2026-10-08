from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from yuxi.services import material_library_service as service
from yuxi.storage.postgres.models_content import ContentEmployee, ContentMaterialShare


def _share(**contact):
    return ContentMaterialShare(
        id="mls_contact_test",
        token="contact-test",
        owner_uid="share-creator",
        category_id="enterprise-case",
        title="案例",
        building_name="洋湖天序",
        area="120",
        design_style="复古写意",
        **contact,
    )


def test_public_share_renders_black_dial_link_and_escapes_employee_name():
    share = _share(sharer_name='张<&"淑琪', sharer_phone="19900000001")

    page = service.render_public_material_share_page(share, [], "https://share.example.test")
    public = service.serialize_public_material_share(share, [])["share"]

    assert '<a class="share-phone" href="tel:19900000001">电话：19900000001 张&lt;&amp;&quot;淑琪</a>' in page
    assert ".share-phone{color:#000;" in page
    assert '<span class="project-style">风格：复古写意</span><a class="share-phone"' in page
    assert public["sharer_name"] == share.sharer_name
    assert public["sharer_phone"] == "19900000001"
    assert "owner_uid" not in public


def test_legacy_share_has_no_dial_link_and_keeps_full_width_style():
    share = _share()

    page = service.render_public_material_share_page(share, [], "https://share.example.test")
    public = service.serialize_public_material_share(share, [])["share"]

    assert 'href="tel:' not in page
    assert '<span class="project-style-wide">风格：复古写意</span>' in page
    assert public["sharer_phone"] is None
    assert public["sharer_name"] is None


def test_share_without_project_metadata_still_displays_sharer_contact():
    share = _share(sharer_name="测试分享员工", sharer_phone="19900000001")
    share.building_name = share.area = share.design_style = None

    page = service.render_public_material_share_page(share, [], "https://share.example.test")

    assert '<section class="project-info-card"><a class="share-phone" href="tel:19900000001">' in page
    assert "楼盘：" not in page


@pytest.mark.asyncio
@pytest.mark.parametrize("pc_actor", [False, True], ids=["mini-program-employee", "pc-sharing-actor"])
async def test_same_case_shares_snapshot_each_sharers_contact(monkeypatch, pc_actor):
    owner = SimpleNamespace(uid="material-owner", id=1, role="superadmin")
    actor = SimpleNamespace(uid="sharing-actor", id=2, role="superadmin")
    category = SimpleNamespace(
        id="enterprise-case",
        parent_id="case-root",
        industry_slug="decoration",
        name="案例",
        building_name="洋湖天序",
        area="120",
        design_style="复古写意",
    )
    asset = SimpleNamespace(
        bucket_name="image", object_name="source.png", sha256="source-hash", metadata_json={},
        content_type="image/png",
        original_file_name="case.png",
        file_size=100,
        image_width=48,
        image_height=36,
    )
    repo = SimpleNamespace(
        list_image_items_with_assets_and_categories=AsyncMock(
            return_value=[(SimpleNamespace(id="case-image"), asset, category)]
        ),
        create_share=AsyncMock(),
    )
    storage = SimpleNamespace(
        aupload_file=AsyncMock(return_value=SimpleNamespace(bucket_name="image", object_name="test-snapshot")),
        adelete_file=AsyncMock(),
        acopy_file=AsyncMock(return_value=SimpleNamespace(bucket_name="image", object_name="test-snapshot")),
        adownload_file=AsyncMock(return_value=b"prepared-cover"),
    )
    image = io.BytesIO()
    Image.new("RGB", (48, 36), "white").save(image, format="PNG")
    resolver = AsyncMock()
    monkeypatch.setattr(service, "MaterialLibraryRepository", lambda *_args, **_kwargs: repo)
    monkeypatch.setattr(service, "get_minio_client", lambda: storage)
    monkeypatch.setattr(service, "read_material_bytes", AsyncMock(return_value=image.getvalue()))
    monkeypatch.setattr(service, "ensure_material_share_images", AsyncMock(return_value=("display.webp", "card.jpg")))
    monkeypatch.setattr(service, "write_share_image_cache", AsyncMock())
    monkeypatch.setattr(service, "resolve_employee_for_user", resolver)
    monkeypatch.setattr(service, "_audit_material", lambda *_args, **_kwargs: None)
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    employees = [
        ContentEmployee(name="测试分享人甲", login_account="19900000001"),
        ContentEmployee(name="测试分享人乙", login_account="19900000002"),
    ]

    for employee in employees:
        resolver.return_value = employee
        await service.create_material_share(
            db,
            owner,
            service.MaterialShareCreate(item_ids=["case-image"]),
            actor_user=actor if pc_actor else None,
            sharer_employee=None if pc_actor else employee,
        )
    snapshots = [call.args[0] for call in repo.create_share.call_args_list]
    employees[0].name = "修改后的姓名"
    employees[0].login_account = "19900000003"

    assert [(share.sharer_name, share.sharer_phone) for share in snapshots] == [
        ("测试分享人甲", "19900000001"),
        ("测试分享人乙", "19900000002"),
    ]
    assert snapshots[0].token != snapshots[1].token
    assert all(share.owner_uid == owner.uid for share in snapshots)
    if pc_actor:
        assert resolver.await_count == 2
        resolver.assert_awaited_with(db, actor)
    else:
        resolver.assert_not_awaited()
