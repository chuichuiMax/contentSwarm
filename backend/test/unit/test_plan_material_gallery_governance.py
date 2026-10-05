from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "plan_material_gallery_governance.py"
SPEC = importlib.util.spec_from_file_location("plan_material_gallery_governance", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_reviewed_global_personal_updates_skip_already_selected_rows():
    rows = [
        {"owner_uid": "admin", "id": "gallery", "deleted_at": None, "is_global_personal": False},
        {"owner_uid": "employee", "id": "gallery", "deleted_at": None, "is_global_personal": True},
    ]

    selected, already_selected = MODULE.reviewed_global_personal_updates(rows, {"admin:gallery", "employee:gallery"})

    assert selected == {"admin:gallery"}
    assert already_selected == {"employee:gallery"}


def test_reviewed_global_personal_updates_reject_deleted_or_unknown_rows():
    rows = [{"owner_uid": "admin", "id": "deleted", "deleted_at": "deleted", "is_global_personal": False}]

    with pytest.raises(ValueError, match="不是活动历史候选"):
        MODULE.reviewed_global_personal_updates(rows, {"admin:deleted"})
