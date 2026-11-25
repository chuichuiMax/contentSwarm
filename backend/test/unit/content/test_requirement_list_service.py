from datetime import datetime
import asyncio

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from yuxi.services.requirement_list_service import (
    MpSharedSubmissionCreate,
    SubmissionCreate,
    _guest_submitter_label,
    SubmissionFilters,
    _can_manage,
    _filtered,
    _owns_submission,
    _range_bounds,
    _visible,
    completion_percent,
    create_shared_submission,
    display_stamp,
    mask_phone,
)
from yuxi.utils.datetime_utils import SHANGHAI_TZ


def test_mask_phone_keeps_prefix_and_suffix():
    assert mask_phone("13812344221") == "138****4221"


def test_display_stamp_uses_shanghai_time():
    assert display_stamp(datetime(2026, 10, 15, 6, 11)) == "2026-10-15 14:11"


def test_today_range_covers_one_shanghai_day(monkeypatch):
    monkeypatch.setattr(
        "yuxi.services.requirement_list_service.shanghai_now",
        lambda: datetime(2026, 10, 15, 14, 11, tzinfo=SHANGHAI_TZ),
    )
    start, end = _range_bounds("today")
    assert start == datetime(2026, 10, 14, 16, 0)
    assert end == datetime(2026, 10, 15, 16, 0)


def test_submission_create_rejects_unknown_type():
    payload = SubmissionCreate(requirement_type="其他", customer_name="刘洋", address="长沙·芙蓉区")
    with pytest.raises(HTTPException) as caught:
        payload.normalized()
    assert caught.value.status_code == 422
    assert caught.value.detail == "需求清单类型不正确"


def test_submission_create_requires_one_customer_field():
    payload = SubmissionCreate(requirement_type="毛坯房")
    with pytest.raises(HTTPException) as caught:
        payload.normalized()
    assert caught.value.detail == "请先填写客户姓名、手机号码或楼盘地址"


def test_submission_answers_reject_oversized_payload():
    with pytest.raises(ValidationError):
        SubmissionCreate(requirement_type="毛坯房", customer_name="刘洋", answers={"note": "x" * 20001})


def test_guest_submitter_label_joins_nickname_and_phone():
    assert _guest_submitter_label(" 微信用户 ", "13812344221") == "微信用户 · 13812344221"
    assert _guest_submitter_label("", "13812344221") == "13812344221"


def test_mp_shared_submission_requires_wechat_fields():
    with pytest.raises(ValidationError):
        MpSharedSubmissionCreate(
            requirement_type="毛坯房",
            customer_name="刘洋",
            address="长沙",
            staff_code="H001",
            visitor_session_id="",
            phone_code="",
            submitter_nickname="",
        )


def test_shared_submission_requires_staff_code():
    payload = SubmissionCreate(requirement_type="毛坯房", customer_name="刘洋", staff_code="  ")
    with pytest.raises(HTTPException) as caught:
        asyncio.run(create_shared_submission(None, payload))
    assert caught.value.status_code == 422
    assert caught.value.detail == "分享链接缺少员工编码"


def test_completion_percent_uses_filled_fields_over_total():
    assert completion_percent({}, name="", phone="", address="") == 0
    assert completion_percent({}, name="刘洋", phone="", address="") == 4
    answers = {
        "q1": "平层",
        "q2_area": "120",
        "q2_layout": "三室",
        "q3": "婚房",
        "q4": "1个月内",
        "q5": "15万以内",
        "q6": "男性",
        "q7": ["简约现代"],
        "q8": "全部保留",
        "q9": ["夫(妻)"],
        "q10": "医生",
        "q11": "满顶",
        "q12": ["书房"],
        "q13": "封闭式",
        "q14": "未养宠",
        "q15": ["喝茶"],
        "q16": "全屋分体空调",
        "q17_0": "洗地机",
        "q17_1": "管线机",
        "q17_2": "全屋智能",
        "q17_3": "AP",
        "q18": "一般",
    }
    assert completion_percent(answers, name="刘洋", phone="13800001111", address="长沙") == 100
    assert completion_percent({"q3_other": "备注"}, name="", phone="", address="") == 4


def test_only_owner_or_superadmin_can_manage_submission():
    from types import SimpleNamespace

    own = SimpleNamespace(created_by="user-1")
    other = SimpleNamespace(created_by="other")
    user = SimpleNamespace(role="user", uid="user-1")
    admin = SimpleNamespace(role="admin", uid="admin-1")
    root = SimpleNamespace(role="superadmin", uid="root")
    assert _owns_submission(own, user)
    assert _can_manage(own, user)
    assert not _can_manage(other, user)
    assert not _can_manage(other, admin)
    assert _can_manage(other, root)


def test_visible_query_limits_non_superadmin():
    from types import SimpleNamespace

    user = SimpleNamespace(role="user", uid="user-1")
    root = SimpleNamespace(role="superadmin", uid="root")
    scoped = str(_visible(_filtered(SubmissionFilters()), user).compile(compile_kwargs={"literal_binds": True}))
    open_query = str(_visible(_filtered(SubmissionFilters()), root).compile(compile_kwargs={"literal_binds": True}))
    assert "created_by = 'user-1'" in scoped
    assert "requirement_list_submission_shares" in scoped
    assert "created_by =" not in open_query


def test_visible_query_includes_staff_code_records():
    from types import SimpleNamespace

    user = SimpleNamespace(role="user", uid="user-1")
    scoped = str(
        _visible(_filtered(SubmissionFilters()), user, employee_code="H04454").compile(
            compile_kwargs={"literal_binds": True}
        )
    )
    assert "employee_code = 'H04454'" in scoped
