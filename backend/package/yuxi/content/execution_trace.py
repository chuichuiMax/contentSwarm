from __future__ import annotations

from typing import Any

_SENSITIVE_KEY_PARTS = ("api_key", "authorization", "cookie", "password", "secret", "token")


def build_execution_preview(value: Any, *, depth: int = 0) -> Any:
    """Build a bounded, user-visible preview for persisted run events."""

    if depth >= 5:
        return "…"
    if isinstance(value, str):
        return value if len(value) <= 600 else f"{value[:600]}…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        preview: dict[str, Any] = {}
        for key, item in list(value.items())[:16]:
            normalized_key = str(key)
            if any(part in normalized_key.lower() for part in _SENSITIVE_KEY_PARTS):
                continue
            preview[normalized_key] = build_execution_preview(item, depth=depth + 1)
        if len(value) > 16:
            preview["more_fields"] = len(value) - 16
        return preview
    if isinstance(value, (list, tuple)):
        preview = [build_execution_preview(item, depth=depth + 1) for item in value[:6]]
        if len(value) > 6:
            preview.append(f"另有 {len(value) - 6} 项")
        return preview
    return build_execution_preview(str(value), depth=depth + 1)


def build_knowledge_result_preview(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source_id": str(item.get("id") or item.get("file_id") or ""),
            "file_id": str(item.get("file_id") or ""),
            "content": build_execution_preview(str(item.get("content") or "")),
            "score": (item.get("metadata") or {}).get("score"),
            "file_name": (item.get("metadata") or {}).get("file_name") or (item.get("metadata") or {}).get("filename"),
        }
        for item in results[:5]
        if isinstance(item, dict)
    ]
