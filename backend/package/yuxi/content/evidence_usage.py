from __future__ import annotations

from typing import Any


def build_evidence_usage_snapshot(
    *,
    selected_title: dict[str, Any],
    content_draft: dict[str, Any],
) -> dict[str, Any]:
    usages_by_evidence: dict[str, list[dict[str, Any]]] = {}

    def add_usage(evidence_id: Any, usage: dict[str, Any]) -> None:
        normalized_id = str(evidence_id or "").strip()
        if not normalized_id:
            return
        usages = usages_by_evidence.setdefault(normalized_id, [])
        if usage not in usages:
            usages.append(usage)

    for evidence_id in selected_title.get("evidence_ids") or []:
        add_usage(evidence_id, {"target": "title", "location": "标题"})

    for index, paragraph in enumerate(content_draft.get("paragraph_evidence") or [], start=1):
        if not isinstance(paragraph, dict):
            continue
        usage = {
            "target": "body",
            "location": f"正文第{index}段",
            "paragraph_id": str(paragraph.get("paragraph_id") or f"p{index}"),
        }
        for evidence_id in paragraph.get("evidence_ids") or []:
            add_usage(evidence_id, usage)

    return {
        "version": 1,
        "items": [{"evidence_id": evidence_id, "usages": usages} for evidence_id, usages in usages_by_evidence.items()],
    }
