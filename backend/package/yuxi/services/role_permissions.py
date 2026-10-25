from __future__ import annotations

from typing import Any

from yuxi.services.material_folder_permissions import is_valid_material_grant_key

_CRUD = (("view_list", "列表查看"), ("create", "新增"), ("view", "查看"), ("delete", "删除"))


def _actions(list_key: str, items: tuple[tuple[str, str], ...]) -> list[dict[str, str]]:
    return [{"key": f"{list_key}.{action_key}", "label": label} for action_key, label in items]


def _entry(list_key: str, label: str, items: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    return {"list_key": list_key, "list": label, "actions": _actions(list_key, items)}


ROUGH_UPLOAD_PERMISSION = "material_rough.upload"
IMAGE_DESIGN_PAGE_PERMISSION = "image_design.access"

PERMISSION_CATALOG: list[dict[str, Any]] = [
    {
        "module_key": "customer_lead",
        "module": "客资列表",
        "lists": [_entry("customer_lead", "客资列表", (("view_list", "列表查看"), ("delete", "删除")))],
    },
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
        "module_key": "visual",
        "module": "视觉创作",
        "lists": [],
    },
    {
        "module_key": "image_design",
        "module": "图片设计",
        "lists": [_entry("image_design", "PC端", (("access", "页面"),))],
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
            _entry("target_audience", "目标人群配置", _CRUD),
            _entry("resident_population", "居住人口配置", _CRUD),
            _entry("variable", "业务参数配置", _CRUD),
            _entry("business_variable", "业务变量配置", _CRUD),
            _entry("process_standard", "工艺类型列表", _CRUD),
        ],
    },
]


def all_permission_keys() -> set[str]:
    return {action["key"] for module in PERMISSION_CATALOG for item in module["lists"] for action in item["actions"]}


def normalize_grants(
    grants: list[str] | None,
    *,
    strict: bool = True,
    extra_allowed: set[str] | None = None,
) -> list[str]:
    allowed = all_permission_keys()
    if extra_allowed:
        allowed |= extra_allowed
    normalized: list[str] = []
    seen: set[str] = set()
    for key in grants or []:
        if key not in allowed and not is_valid_material_grant_key(key):
            if strict:
                raise ValueError(key)
            continue
        if key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    return normalized


async def permission_catalog(db) -> list[dict[str, Any]]:
    from yuxi.services.material_folder_permissions import list_material_folder_permission_entries

    folder_entries = await list_material_folder_permission_entries(db)
    catalog: list[dict[str, Any]] = []
    for module in PERMISSION_CATALOG:
        if module["module_key"] == "visual":
            catalog.append({**module, "lists": folder_entries})
        else:
            catalog.append(module)
    return catalog


def catalog_permission_keys(catalog: list[dict[str, Any]]) -> set[str]:
    keys = set(all_permission_keys())
    for module in catalog:
        for item in module.get("lists") or []:
            for action in item.get("actions") or []:
                keys.add(action["key"])
    return keys
