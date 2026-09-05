from pydantic import ValidationError

import pytest

from yuxi.services.content_type_service import ContentTypeCreate, ContentTypeUpdate, next_type_code


def test_content_type_create_schema_requires_name():
    with pytest.raises(ValidationError):
        ContentTypeCreate(name="")


def test_content_type_update_schema_allows_partial_enabled():
    payload = ContentTypeUpdate(enabled=False)
    assert payload.model_dump(exclude_unset=True) == {"enabled": False}


def test_next_type_code_increments_nrlx_sequence():
    assert next_type_code([]) == "NRLX0001"
    assert next_type_code(["NRLX0001", "NRLX0007", "OTHER01"]) == "NRLX0008"
