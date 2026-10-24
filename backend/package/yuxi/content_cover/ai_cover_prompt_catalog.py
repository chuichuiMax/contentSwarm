from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from yuxi.content_cover.ai_cover_prompt import ai_cover_prompt_body_for_catalog, is_case_share_content_type

AI_COVER_OSS_LOGICAL_BUCKET = "image"
AI_COVER_OSS_PREFIX = (os.getenv("AI_COVER_OSS_PREFIX") or "AI-cover/").strip()
if AI_COVER_OSS_PREFIX and not AI_COVER_OSS_PREFIX.endswith("/"):
    AI_COVER_OSS_PREFIX = f"{AI_COVER_OSS_PREFIX}/"

# OSS「提示词N」→ 变体（无 p6：六→p7 … 八→p14，九→p9，十→p10，十一→p11，十二→p12，十三→p13）
_VARIANT_FROM_STEM: dict[str, str] = {
    "提示词一": "p1",
    "提示词二": "p2",
    "提示词三": "p3",
    "提示词四": "p4",
    "提示词五": "p5",
    "提示词六": "p7",
    "提示词七": "p8",
    "提示词八": "p14",
    "提示词九": "p9",
    "提示词十": "p10",
    "提示词十一": "p11",
    "提示词十二": "p12",
    "提示词十三": "p13",
    "智能随机适配": "smart-random",
    "提示词十四": "p15",
    "提示词十五": "p16",
    "提示词十六": "p17",
    "p1": "p1",
    "p2": "p2",
    "p3": "p3",
    "p4": "p4",
    "p5": "p5",
    "p7": "p7",
    "p8": "p8",
    "p9": "p9",
    "p10": "p10",
    "p11": "p11",
    "p12": "p12",
    "p13": "p13",
    "p14": "p14",
    "p15": "p15",
    "p16": "p16",
    "p17": "p17",
    "smart-random": "smart-random",
}


@dataclass(frozen=True)
class AiCoverPromptSpec:
    variant: str
    slot_index: int
    label: str
    title: str
    description: str
    fallback_preview_path: str

    def oss_preview_basenames(self) -> tuple[str, ...]:
        names = (f"{self.label}.png", f"{self.label}.jpg", f"{self.label}.webp")
        code = (f"{self.variant}.png", f"{self.variant}.jpg", f"{self.variant}.webp")
        extra: tuple[str, ...] = ()
        if self.variant == "p11":
            extra = ("提示词十一.png", "提示词十一.jpg", "提示词十一.webp")
        numeric = _OSS_BASENAMES_BY_VARIANT.get(self.variant, ())
        return tuple(dict.fromkeys(names + extra + numeric + code))


_OSS_CN_NUMERALS: dict[int, str] = {
    1: "一",
    2: "二",
    3: "三",
    4: "四",
    5: "五",
    6: "六",
    7: "七",
    8: "八",
    9: "九",
    10: "十",
    11: "十一",
    12: "十二",
    13: "十三",
    14: "十四",
    15: "十五",
    16: "十六",
}


def _basenames_for_prompt_number(n: int) -> tuple[str, ...]:
    cn = _OSS_CN_NUMERALS.get(n)
    if not cn:
        return ()
    stem = f"提示词{cn}"
    return (f"{stem}.png", f"{stem}.jpg", f"{stem}.webp")


_OSS_BASENAMES_BY_VARIANT: dict[str, tuple[str, ...]] = {
    f"p{n}": _basenames_for_prompt_number(n) for n in range(1, 14) if n != 6
}


AI_COVER_SMART_RANDOM_VARIANT = "smart-random"

AI_COVER_SMART_RANDOM_SPEC = AiCoverPromptSpec(
    AI_COVER_SMART_RANDOM_VARIANT,
    0,
    "智能随机适配",
    "随机匹配提示词风格",
    "只使用封面原图；由封面 Agent 提供标题与副标题后，从当前内容类型可用提示词中随机选用一套。",
    "/ai-cover-prompts/smart-random.png",
)

AI_COVER_PROMPT_SPECS: tuple[AiCoverPromptSpec, ...] = (
    AiCoverPromptSpec("p1", 1, "提示词一", "手写风双行标题", "标题/副标题手写笔触，两行叠字", "/ai-cover-prompts/p1.png"),
    AiCoverPromptSpec("p2", 2, "提示词二", "3D 鎏金促销字", "立体白字鎏金边 + 底部红条副标题", "/ai-cover-prompts/p2.png"),
    AiCoverPromptSpec("p3", 3, "提示词三", "暖棕行草双行", "顶部居中行草标题与副标题", "/ai-cover-prompts/p3.png"),
    AiCoverPromptSpec("p4", 4, "提示词四", "速食 POP 迷因风", "夸张手绘 POP 字，漫画迷因感", "/ai-cover-prompts/p4.png"),
    AiCoverPromptSpec("p5", 5, "提示词五", "科技风白字", "白色几何科技字体，两行", "/ai-cover-prompts/p5.png"),
    AiCoverPromptSpec("p7", 6, "提示词六", "书法狂野字", "书法字，枯笔飞白，墨色浓淡", "/ai-cover-prompts/p7.png"),
    AiCoverPromptSpec("p8", 7, "提示词七", "动漫体", "动漫风粗笔画，活泼醒目", "/ai-cover-prompts/p8.png"),
    AiCoverPromptSpec(
        "p14",
        8,
        "提示词八",
        "漫画综艺体",
        "马克笔漫画风，一头粗一头窄，镂空线条",
        "/ai-cover-prompts/p14.png",
    ),
    AiCoverPromptSpec("p9", 9, "提示词九", "大笔触手写", "手写风长短粗细变化", "/ai-cover-prompts/p9.png"),
    AiCoverPromptSpec("p10", 10, "提示词十", "现代宋体广告字", "强对比信息排版，三种以内配色", "/ai-cover-prompts/p10.png"),
    AiCoverPromptSpec("p11", 11, "提示词十一", "杂志宋体排版", "极简建筑杂志风，中英文层级", "/ai-cover-prompts/p11.png"),
    AiCoverPromptSpec("p13", 12, "提示词十三", "秀丽体手写", "秀丽笔两行，飞白粗细对比", "/ai-cover-prompts/p13.png"),
    AiCoverPromptSpec(
        "p15",
        13,
        "提示词十四",
        "杂志粗黑三行",
        "无衬线粗黑 + 英文数字，建筑杂志式左对齐",
        "/ai-cover-prompts/p15.png",
    ),
    AiCoverPromptSpec(
        "p16",
        14,
        "提示词十五",
        "智能排版封面",
        "保留原图 + 标题/副标题，沿用 AI_COVER 排版规则",
        "/ai-cover-prompts/p16.png",
    ),
    AiCoverPromptSpec(
        "p17",
        15,
        "提示词十六",
        "无文案和谐画面",
        "不显示封面文案，仅优化构图光影与整体和谐感",
        "/ai-cover-prompts/p17.png",
    ),
)

AI_COVER_CASE_SHARE_P12_SPEC = AiCoverPromptSpec(
    "p12",
    11,
    "提示词十二",
    "案例顶栏黄白黑体",
    "装修案例分享：工地名 + 面积风格实景",
    "/ai-cover-prompts/p12.png",
)

_VARIANT_TO_SPEC: dict[str, AiCoverPromptSpec] = {spec.variant: spec for spec in AI_COVER_PROMPT_SPECS}
_VARIANT_TO_SPEC["p12"] = AI_COVER_CASE_SHARE_P12_SPEC
_VARIANT_TO_SPEC[AI_COVER_SMART_RANDOM_VARIANT] = AI_COVER_SMART_RANDOM_SPEC


def variant_from_oss_basename(filename: str) -> str | None:
    stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0].strip()
    if not stem:
        return None
    if stem in _VARIANT_FROM_STEM:
        return _VARIANT_FROM_STEM[stem]
    if re.fullmatch(r"p\d+", stem, flags=re.IGNORECASE):
        return stem.lower()
    return None


def spec_for_variant(variant: str, *, content_type_code: str = "", content_type_name: str = "") -> AiCoverPromptSpec | None:
    normalized = (variant or "").strip().lower()
    if normalized == "p12":
        if is_case_share_content_type(content_type_code=content_type_code, content_type_name=content_type_name):
            return AI_COVER_CASE_SHARE_P12_SPEC
        return None
    return _VARIANT_TO_SPEC.get(normalized)


def specs_for_content_type(*, content_type_code: str = "", content_type_name: str = "") -> tuple[AiCoverPromptSpec, ...]:
    specs = [AI_COVER_SMART_RANDOM_SPEC, *AI_COVER_PROMPT_SPECS]
    if is_case_share_content_type(content_type_code=content_type_code, content_type_name=content_type_name):
        insert_at = next(i for i, spec in enumerate(specs) if spec.variant == "p13")
        specs.insert(insert_at, AI_COVER_CASE_SHARE_P12_SPEC)
    return tuple(specs)


def api_preview_url_for_variant(variant: str) -> str:
    return f"/api/content/covers/ai-cover-prompts/{variant}/preview.png"


def spec_to_dict(
    spec: AiCoverPromptSpec,
    *,
    preview_url: str,
    content_type_code: str = "",
    content_type_name: str = "",
) -> dict[str, Any]:
    case_share = is_case_share_content_type(content_type_code=content_type_code, content_type_name=content_type_name)
    return {
        "variant": spec.variant,
        "slot_index": spec.slot_index,
        "label": spec.label,
        "title": spec.title,
        "description": spec.description,
        "preview_url": preview_url,
        "selectable": spec.variant != "p12" or case_share,
        "prompt_body": (
            ""
            if spec.variant == AI_COVER_SMART_RANDOM_VARIANT
            else ai_cover_prompt_body_for_catalog(spec.variant)
        ),
        "is_smart_random": spec.variant == AI_COVER_SMART_RANDOM_VARIANT,
    }
