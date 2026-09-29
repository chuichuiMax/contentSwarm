from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def visible_work_items(jobs: Iterable[Any], assets_by_id: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Flatten successful cover-job outputs while retaining only visible generated assets."""
    items: list[dict[str, Any]] = []
    seen_asset_ids: set[str] = set()
    for job in jobs:
        if _value(job, "status") != "succeeded":
            continue
        result = _value(job, "result_json") or _value(job, "result") or {}
        for asset_id in result.get("asset_ids") or []:
            asset = assets_by_id.get(asset_id)
            if (
                asset_id in seen_asset_ids
                or not asset
                or _value(asset, "role") != "output"
                or _value(asset, "hidden_from_works_at")
            ):
                continue
            seen_asset_ids.add(asset_id)
            items.append(
                {
                    "id": asset_id,
                    "job_id": _value(job, "id"),
                    "created_at": _value(job, "completed_at") or _value(job, "created_at"),
                    "uploaded_at": _value(asset, "created_at"),
                }
            )
    return items
