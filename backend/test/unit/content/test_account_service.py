from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from yuxi.services.account_service import AccountCreate, AccountUpdate, _normalize_text


def test_normalize_text_strips_and_rejects_blank():
    assert _normalize_text("  林间有风  ", field="账号名称") == "林间有风"
    with pytest.raises(HTTPException, match="不能为空"):
        _normalize_text("   ", field="ID")


def test_account_create_schema_rejects_negative_counts():
    with pytest.raises(ValidationError):
        AccountCreate(name="测试号", account_id="1", account_type="enterprise", follower_count=-1)


def test_account_update_schema_allows_partial_enabled():
    payload = AccountUpdate(enabled=False)
    assert payload.model_dump(exclude_unset=True) == {"enabled": False}


def test_account_id_length_is_bounded():
    with pytest.raises(ValidationError):
        AccountCreate(name="测试号", account_id="x" * 65, account_type="personal")
