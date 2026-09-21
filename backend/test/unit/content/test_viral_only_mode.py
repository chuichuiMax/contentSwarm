from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from yuxi.content.schemas import ContentTaskCreate
from yuxi.services.content_service import _require_v3_task


def test_new_content_task_defaults_to_viral_rewrite_and_rejects_original():
    payload = ContentTaskCreate(industry_template_id="industry-decoration-v3")
    assert payload.creation_mode == "viral_rewrite"

    with pytest.raises(ValidationError):
        ContentTaskCreate(industry_template_id="industry-decoration-v3", creation_mode="original")


def test_historical_original_task_is_read_only():
    task = SimpleNamespace(runtime_config_snapshot_json={"schema_version": 3, "creation_mode": "original"})

    with pytest.raises(HTTPException) as captured:
        _require_v3_task(task)

    assert captured.value.status_code == 409
    assert captured.value.detail["error"]["code"] == "CONTENT_CREATION_MODE_UNSUPPORTED"
