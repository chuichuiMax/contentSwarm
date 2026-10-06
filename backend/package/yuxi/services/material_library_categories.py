from __future__ import annotations

from typing import Literal

MaterialType = Literal["image", "cover_template"]

MATERIAL_CATEGORIES: dict[MaterialType, tuple[dict[str, str], ...]] = {
    "image": (
        {"code": "product", "name": "产品商品", "description": "商品主体、产品细节、包装和展示图"},
        {"code": "people", "name": "人物", "description": "人物肖像、模特、团队和动作姿态"},
        {"code": "scene", "name": "场景", "description": "室内、户外、工作和生活场景"},
        {"code": "background", "name": "背景", "description": "纯色、纹理、渐变和环境底图"},
        {"code": "decoration", "name": "装饰", "description": "贴纸、图标、边框、光效和点缀元素"},
        {"code": "brand", "name": "品牌", "description": "Logo、品牌标准图形和品牌专属素材"},
        {"code": "uncategorized", "name": "未分类", "description": "待整理或无法判断用途的历史素材"},
    ),
    "cover_template": (
        {"code": "product_promotion", "name": "产品推广", "description": "新品发布、卖点介绍和商品主视觉"},
        {"code": "marketing", "name": "营销促销", "description": "折扣、限时、优惠券和转化活动"},
        {"code": "knowledge", "name": "知识科普", "description": "知识分享、教程要点和专业解读"},
        {"code": "guide", "name": "攻略清单", "description": "步骤指南、方法清单和实用建议"},
        {"code": "comparison", "name": "测评对比", "description": "产品测评、方案对比和前后效果"},
        {"code": "event", "name": "活动招募", "description": "会议、直播、课程和线下活动"},
        {"code": "festival", "name": "节日节气", "description": "节日营销、节气和纪念日内容"},
        {"code": "brand", "name": "品牌宣传", "description": "品牌故事、企业形象和价值主张"},
        {"code": "lifestyle", "name": "生活方式", "description": "穿搭、美食、旅行、家居和日常分享"},
        {"code": "other", "name": "其他", "description": "明确用途但不属于以上标准分类的模板"},
        {"code": "uncategorized", "name": "未分类", "description": "待整理或无法判断用途的历史模板"},
    ),
}

# Legacy PC defaults remain valid storage categories, but the material-library UI
# now exposes the four fixed personal entries instead of these old cards.
DEFAULT_IMAGE_CATEGORY_IDS = frozenset(item["code"] for item in MATERIAL_CATEGORIES["image"])
RETIRED_PRIVATE_IMAGE_CATEGORY_IDS = DEFAULT_IMAGE_CATEGORY_IDS - {"product"}

PERSONAL_IMAGE_FOLDERS = {
    "rough": ("mp-rough-private", "毛坯房图库"),
    "uploads": ("mp-uploads-private", "我的上传"),
    "generated": ("mp-generated-private", "AI生图图库"),
    "works": ("mp-works-private", "我的作品"),
}


def personal_folder_key(category) -> str | None:
    if category.material_type != "image" or category.visibility != "private" or category.parent_id:
        return None
    for key, (category_id, name) in PERSONAL_IMAGE_FOLDERS.items():
        if category.id == category_id or category.name in {name, f"{name}（个人）"}:
            return key
    if category.name in {"生图图库", "毛胚房图库"}:
        return "generated" if category.name == "生图图库" else "rough"
    return None


def _aliases(material_type: MaterialType) -> dict[str, str]:
    aliases = {item["name"]: item["code"] for item in MATERIAL_CATEGORIES[material_type]}
    aliases.update({item["code"]: item["code"] for item in MATERIAL_CATEGORIES[material_type]})
    if material_type == "image":
        aliases.update({"商品": "product", "产品": "product", "封面素材": "uncategorized"})
    else:
        aliases.update({"产品": "product_promotion", "促销": "marketing", "封面素材": "uncategorized"})
    return aliases


def normalize_material_category(material_type: MaterialType, value: str | None) -> str:
    normalized = (value or "").strip()
    if not normalized:
        return "uncategorized"
    return _aliases(material_type).get(normalized, "uncategorized")


def resolve_legacy_category(material_type: MaterialType, value: str | None) -> str | None:
    return _aliases(material_type).get((value or "").strip())


def validate_material_category(material_type: MaterialType, value: str | None) -> str:
    normalized = (value or "").strip()
    code = _aliases(material_type).get(normalized)
    if code is None or code == "uncategorized":
        raise ValueError("请选择一个有效的素材分类")
    return code


def category_definition(material_type: MaterialType, value: str | None) -> dict[str, str]:
    code = normalize_material_category(material_type, value)
    return next(item for item in MATERIAL_CATEGORIES[material_type] if item["code"] == code)


def list_material_categories(material_type: MaterialType) -> list[dict[str, str]]:
    return [dict(item) for item in MATERIAL_CATEGORIES[material_type]]


def category_filter_values(material_type: MaterialType, code: str) -> tuple[str, ...]:
    return tuple(value for value, mapped in _aliases(material_type).items() if mapped == code)


def known_category_values(material_type: MaterialType) -> tuple[str, ...]:
    return tuple(_aliases(material_type))
