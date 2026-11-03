from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from pydantic import ValidationError
from fastapi import HTTPException

from yuxi.services import material_library_service as service
from yuxi.services.material_share_jobs import MaterialShareRequest, check_same_request
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


def test_public_share_separates_contact_and_blue_dial_link_and_escapes_employee_name():
    share = _share(sharer_name='张<&"淑琪', sharer_phone="19900000001")

    page = service.render_public_material_share_page(share, [], "https://share.example.test")
    public = service.serialize_public_material_share(share, [])["share"]

    fields = [
        "楼盘：洋湖天序", "面积：120㎡", "联系人：张&lt;&amp;&quot;淑琪", "风格：复古写意", "电话：19900000001",
    ]
    assert [page.index(field) for field in fields] == sorted(page.index(field) for field in fields)
    assert '<span class="project-contact">联系人：张&lt;&amp;&quot;淑琪</span>' in page
    assert '<a class="share-phone" href="tel:19900000001">电话：19900000001</a>' in page
    assert "--share-phone-color:#5b7cff;" in page
    assert ".share-phone{grid-column:1 / -1;color:var(--share-phone-color);" in page
    assert public["sharer_name"] == share.sharer_name
    assert public["sharer_phone"] == "19900000001"
    assert "owner_uid" not in public


def test_legacy_share_has_no_dial_link_and_keeps_full_width_style():
    share = _share()

    page = service.render_public_material_share_page(share, [], "https://share.example.test")
    public = service.serialize_public_material_share(share, [])["share"]

    assert 'href="tel:' not in page
    assert "联系人：" not in page
    assert '<span class="project-style-wide">风格：复古写意</span>' in page
    assert public["sharer_phone"] is None
    assert public["sharer_name"] is None


def test_share_without_project_metadata_still_displays_sharer_contact():
    share = _share(sharer_name="测试分享员工", sharer_phone="19900000001")
    share.building_name = share.area = share.design_style = None

    page = service.render_public_material_share_page(share, [], "https://share.example.test")

    assert '<section class="project-info-card"><span class="project-contact">联系人：测试分享员工</span>' in page
    assert '<a class="share-phone" href="tel:19900000001">电话：19900000001</a>' in page
    assert "楼盘：" not in page


@pytest.mark.parametrize("name,phone", [("测试分享员工", None), (None, "19900000001")])
def test_partial_sharer_contact_displays_only_the_available_field(name, phone):
    share = _share(sharer_name=name, sharer_phone=phone)

    page = service.render_public_material_share_page(share, [], "https://share.example.test")

    assert ("联系人：" in page) is bool(name)
    assert ('href="tel:' in page) is bool(phone)
    assert "None" not in page
    assert ("project-style-wide\">风格" in page) is (not name)


@pytest.mark.asyncio
@pytest.mark.parametrize("pc_actor", [False, True], ids=["mini-program-employee", "pc-sharing-actor"])
@pytest.mark.parametrize("custom_contact", [False, True], ids=["employee-default", "custom-contact"])
async def test_same_case_shares_snapshot_each_sharers_contact(monkeypatch, pc_actor, custom_contact):
    owner = SimpleNamespace(uid="material-owner", id=1, role="superadmin", department_id=1)
    actor = SimpleNamespace(uid="sharing-actor", id=2, role="superadmin", department_id=2)
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
        astat_file=AsyncMock(return_value=object()),
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
        ContentEmployee(
            name="测试分享人甲", employee_code="0001", current_branch="原门店", login_account="19900000001"
        ),
        ContentEmployee(
            name="测试分享人乙", employee_code="0002", current_branch="乙门店", login_account="19900000002"
        ),
    ]

    for employee in employees:
        resolver.return_value = employee
        await service.create_material_share(
            db,
            owner,
            service.MaterialShareCreate(
                item_ids=["case-image"],
                **({"contact_name": " 联系人乙 ", "contact_phone": " 13800000003 "} if custom_contact else {}),
            ),
            actor_user=actor if pc_actor else None,
            sharer_employee=None if pc_actor else employee,
            source_platform="pc" if pc_actor else "mp",
        )
    snapshots = [call.args[0] for call in repo.create_share.call_args_list]
    assert all(share.sharer_uid == (actor.uid if pc_actor else owner.uid) for share in snapshots)
    assert all(share.sharer_department_id == (2 if pc_actor else 1) for share in snapshots)
    employees[0].name = "修改后的姓名"
    employees[0].login_account = "19900000003"
    employees[0].current_branch = "新门店"

    assert [(share.sharer_employee_name, share.sharer_employee_code, share.sharer_branch) for share in snapshots] == [
        ("测试分享人甲", "0001", "原门店"), ("测试分享人乙", "0002", "乙门店"),
    ]

    assert [(share.sharer_name, share.sharer_phone) for share in snapshots] == ([
        ("联系人乙", "13800000003"), ("联系人乙", "13800000003"),
    ] if custom_contact else [
        ("测试分享人甲", "19900000001"),
        ("测试分享人乙", "19900000002"),
    ])
    assert snapshots[0].token != snapshots[1].token
    assert all(share.owner_uid == owner.uid for share in snapshots)
    assert all(share.source_platform == ("pc" if pc_actor else "mp") for share in snapshots)
    assert all(len(share.mp_entry_code) == 24 for share in snapshots)
    if pc_actor:
        assert resolver.await_count == 2
        resolver.assert_awaited_with(db, actor)
    else:
        resolver.assert_not_awaited()


@pytest.mark.parametrize("contact", [
    {"contact_name": " ", "contact_phone": "13800000003"},
    {"contact_name": "联系人"},
    {"contact_phone": "13800000003"},
    {"contact_name": "联系人", "contact_phone": ""},
    {"contact_name": "联系人", "contact_phone": "12800000003"},
    {"contact_name": "联系人", "contact_phone": "1380000000"},
    {"contact_name": "联系人", "contact_phone": "138000000030"},
    {"contact_name": "联系人", "contact_phone": "１３８０００００００３"},
    {"contact_name": "人" * 81, "contact_phone": "13800000003"},
])
def test_invalid_or_partial_share_contact_rejected(contact):
    with pytest.raises(ValidationError):
        service.MaterialShareCreate(item_ids=["image"], **contact)


def test_share_contact_retry_must_match_frozen_contact():
    user = SimpleNamespace(uid="employee-a")
    job = SimpleNamespace(requester_uid=user.uid, item_ids=["image"], share_json={
        "sharer_name": "联系人乙", "sharer_phone": "13800000003",
    })
    original = MaterialShareRequest(item_ids=["image"], contact_name=" 联系人乙 ", contact_phone="13800000003")
    check_same_request(job, user, original)
    # Omitted contact remains compatible with old clients and old queued jobs.
    check_same_request(job, user, MaterialShareRequest(item_ids=["image"]))
    for change in [{"contact_name": "另一联系人"}, {"contact_phone": "13900000004"}]:
        changed = original.model_copy(update=change)
        with pytest.raises(HTTPException) as error:
            check_same_request(job, user, changed)
        assert error.value.status_code == 409
