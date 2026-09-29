from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backfill_personal_material_folders.py"
SPEC = importlib.util.spec_from_file_location("backfill_personal_material_folders", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_historical_sources_route_pc_outputs_and_mp_inputs_with_existing_items():
    item = SimpleNamespace(metadata_json={})
    pc_task = SimpleNamespace(brief_json={"form_values": {}})
    mp_task = SimpleNamespace(brief_json={"form_values": {"mp_content_code": "MP-1"}})
    old_mp_task = SimpleNamespace(brief_json={"form_values": {"mp_service_entry": "装修家居"}})

    assert MODULE.historical_destination(SimpleNamespace(role="output", metadata_json={}), item, pc_task, None) == (
        "generated",
        "pc",
    )
    assert MODULE.historical_destination(SimpleNamespace(role="output", metadata_json={}), item, mp_task, None) is None
    assert (
        MODULE.historical_destination(SimpleNamespace(role="output", metadata_json={}), item, old_mp_task, None) is None
    )
    assert MODULE.historical_destination(
        SimpleNamespace(role="image_design_input", metadata_json={}), item, None, None
    ) == ("uploads", "mp")


def test_historical_private_photos_are_not_shared_without_source_evidence():
    asset = SimpleNamespace(role="library_image", metadata_json={})
    item = SimpleNamespace(metadata_json={})
    unknown = SimpleNamespace(id="custom", visibility="private", name="客厅案例", image_design_role=None)
    shared_custom = SimpleNamespace(
        id="shared-custom", visibility="enterprise", name="企业案例", image_design_role=None
    )
    pc_root = SimpleNamespace(id="private-root", visibility="private", name="我的素材", image_design_role=None)

    assert MODULE.historical_destination(asset, item, None, unknown) == ("review", None)
    assert MODULE.historical_destination(asset, item, None, pc_root) == ("uploads", "pc")
    assert MODULE.historical_destination(
        asset, SimpleNamespace(metadata_json={"source_channel": "pc", "source_folder": None}), None, unknown
    ) == ("review", None)
    assert (
        MODULE.historical_destination(
            asset, SimpleNamespace(metadata_json={"source_channel": "pc", "source_folder": None}), None, shared_custom
        )
        is None
    )
    assert MODULE.historical_destination(
        asset, SimpleNamespace(metadata_json={"source_channel": "pc", "source_folder": "rough"}), None, unknown
    ) == ("rough", "pc")
    assert (
        MODULE.historical_destination(asset, SimpleNamespace(metadata_json={"source": "image_design"}), None, unknown)
        is None
    )
    mp_job = SimpleNamespace(request_json={"mp_fixed_target": True})
    assert MODULE.historical_destination(
        asset, SimpleNamespace(metadata_json={"source": "image_design"}), None, unknown, mp_job
    ) == ("generated", "mp")
