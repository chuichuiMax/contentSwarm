from __future__ import annotations

from typing import Any

_CRUD = (("view_list", "列表查看"), ("create", "新增"), ("view", "查看"), ("delete", "删除"))


def _actions(list_key: str, items: tuple[tuple[str, str], ...]) -> list[dict[str, str]]:
    return [{"key": f"{list_key}.{action_key}", "label": label} for action_key, label in items]


def _entry(list_key: str, label: str, items: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    return {"list_key": list_key, "list": label, "actions": _actions(list_key, items)}


PERMISSION_CATALOG: list[dict[str, Any]] = [
    {
        "module_key": "chat",
        "module": "创建新对话",
        "lists": [_entry("chat", "创建新对话", (("access", "对话"),))],
    },
    {
        "module_key": "content",
        "module": "内容生产",
        "lists": [_entry("content", "内容生产", (("generate", "内容生成"), ("copy", "复制")))],
    },
    {
        "module_key": "workspace",
        "module": "工作区",
        "lists": [_entry("workspace", "工作区", (("view_list", "列表查看"),))],
    },
    {
        "module_key": "extensions",
        "module": "智能体扩展",
        "lists": [_entry("extensions", "智能体扩展", (("view_list", "列表查看"),))],
    },
    {
        "module_key": "agent",
        "module": "智能体管理",
        "lists": [_entry("agent", "智能体管理", _CRUD)],
    },
    {
        "module_key": "account",
        "module": "账号管理",
        "lists": [_entry("account", "账号管理", _CRUD)],
    },
    {
        "module_key": "employee",
        "module": "员工管理",
        "lists": [_entry("employee", "员工管理", _CRUD)],
    },
    {
        "module_key": "config",
        "module": "配置管理",
        "lists": [
            _entry("persona", "人设管理", _CRUD),
            _entry("permission", "权限配置", _CRUD),
            _entry("content_type", "内容类型配置", _CRUD),
            _entry("variable", "变量配置", _CRUD),
        ],
    },
]


def all_permission_keys() -> set[str]:
    return {
        action["key"]
        for module in PERMISSION_CATALOG
        for item in module["lists"]
        for action in item["actions"]
    }


def normalize_grants(grants: list[str] | None, *, strict: bool = True) -> list[str]:
    allowed = all_permission_keys()
    normalized: list[str] = []
    seen: set[str] = set()
    for key in grants or []:
        if key not in allowed:
            if strict:
                raise ValueError(key)
            continue
        if key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    return normalized
