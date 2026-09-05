from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "migrate_material_library_to_image.py"
SPEC = importlib.util.spec_from_file_location("migrate_material_library_to_image", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_target_object_name_is_user_isolated_and_deterministic():
    assert set(MODULE.ELIGIBLE_ROLES) == {"source", "template", "mask", "poster_template"}
    assert MODULE.VISIBLE_LIBRARY_ROLES == {"source", "template", "poster_template"}
    assert "mask" not in MODULE.VISIBLE_LIBRARY_ROLES
    assert "output" not in MODULE.ELIGIBLE_ROLES
    assert MODULE.target_object_name("user-1", "asset-1", "image") == (
        "material-library/user-1/images/asset-1/image.png"
    )
    assert MODULE.target_object_name("user-1", "asset-1", "cover_template") == (
        "material-library/user-1/cover-templates/asset-1/image.png"
    )


def test_manifest_round_trip(tmp_path):
    path = tmp_path / "migration.jsonl"
    record = {
        "asset_id": "asset-1",
        "old_bucket": "content-covers",
        "old_object": "old.png",
        "new_bucket": "image",
        "new_object": "material-library/user-1/images/asset-1/image.png",
        "sha256": "abc",
    }

    MODULE.append_manifest(path, record)

    assert MODULE.read_manifest(path) == [record]
