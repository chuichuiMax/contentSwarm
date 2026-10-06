"""小程序内容工作流专用分支：好评笔记跳过封面链路。"""

from __future__ import annotations

from typing import Any

MP_REVIEW_NOTES_ENTRY = "好评笔记"

MP_REVIEW_NOTES_UI = {
    "requires_cover": False,
    "requires_photo_upload": False,
    "save_title_body_only": True,
    "hidden_sections": ["cover", "photos", "cover_upload", "ai_cover"],
}

_MP_SKIP_COVER_NODE_IDS = frozenset(
    {
        "plan_visuals",
        "submit_cover_job",
        "wait_cover_job",
        "visual_review",
    }
)


def mp_service_entry_from_brief(brief_json: dict[str, Any] | None) -> str:
    brief = brief_json or {}
    form_values = brief.get("form_values") or {}
    return str(form_values.get("mp_service_entry") or "").strip()


def mp_service_entry_from_state(state: dict[str, Any]) -> str:
    brief = state.get("content_brief") or {}
    if not brief:
        runtime = state.get("runtime_config_snapshot") or {}
        brief = runtime.get("brief") or {}
    form_values = brief.get("form_values") or {}
    return str(form_values.get("mp_service_entry") or "").strip()


def mp_skip_cover_pipeline(state: dict[str, Any]) -> bool:
    return mp_service_entry_from_state(state) == MP_REVIEW_NOTES_ENTRY


def mp_cover_pipeline_skip_result(node_id: str, state: dict[str, Any]) -> dict[str, Any] | None:
    if not mp_skip_cover_pipeline(state):
        return None
    if node_id not in _MP_SKIP_COVER_NODE_IDS:
        return None
    if node_id == "plan_visuals":
        return {"visual_plan": {"status": "skipped", "mp_skip_cover": True}}
    if node_id == "submit_cover_job":
        return {
            "cover_job": {
                "status": "skipped",
                "cover_job_id": None,
                "mp_skip_cover": True,
            }
        }
    if node_id == "wait_cover_job":
        return {
            "cover_job": {
                **(state.get("cover_job") or {}),
                "status": "skipped",
                "asset_ids": [],
                "mp_skip_cover": True,
            },
            "cover_assets": [],
            "resume_parent_run_id": None,
        }
    if node_id == "visual_review":
        return {
            "visual_review": {
                "status": "skipped",
                "assets": [],
                "recommended_asset_id": None,
                "mp_skip_cover": True,
            }
        }
    return None


def mp_skip_cover_selection(state: dict[str, Any]) -> bool:
    return mp_skip_cover_pipeline(state)


__all__ = [
    "MP_REVIEW_NOTES_ENTRY",
    "MP_REVIEW_NOTES_UI",
    "mp_cover_pipeline_skip_result",
    "mp_service_entry_from_brief",
    "mp_service_entry_from_state",
    "mp_skip_cover_pipeline",
    "mp_skip_cover_selection",
]
