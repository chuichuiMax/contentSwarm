from sqlalchemy import inspect

from yuxi.storage.postgres.models_content import ImageDesignLibraryItem, ImageDesignMpDraft


def test_mp_draft_owns_three_workflow_json_slots():
    row = ImageDesignMpDraft(owner_uid="u-1", drafts_json={"redesign": {}, "adapt": {}, "transfer": {}})

    assert set(row.drafts_json) == {"redesign", "adapt", "transfer"}


def test_design_library_has_owner_asset_unique_constraint():
    names = {item.name for item in inspect(ImageDesignLibraryItem).local_table.constraints}

    assert "uq_image_design_library_owner_asset" in names
