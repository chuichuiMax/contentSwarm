from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


FIXTURE_DIR = Path(__file__).with_name("fixtures")
MATRIX_FIXTURE = FIXTURE_DIR / "decoration_matrix_v3.json"
SEMANTIC_LEXICON_FIXTURE = FIXTURE_DIR / "decoration_semantic_lexicons_v3.json"

COMBINATION_SIZES = {
    "single": 1,
    "double": 2,
    "triple": 3,
    "quadruple": 4,
}
TITLE_FORMULA_CODES = {f"T{index:02d}" for index in range(1, 8)}
BODY_FORMULA_CODES = {f"C{index:02d}" for index in range(1, 5)}
METHOD_CODES = {"M01", "M02", "M03", "M04", "S01"}


class FixtureValidationError(ValueError):
    """机器可读来源 fixture 与已确认原始数据不一致。"""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureValidationError(f"无法读取 fixture: {path.name}") from exc
    if not isinstance(payload, dict):
        raise FixtureValidationError(f"fixture 根节点必须是对象: {path.name}")
    return payload


def _content_hash(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_source(source: Any, content: Any) -> None:
    if not isinstance(source, dict):
        raise FixtureValidationError("fixture 缺少 source metadata")
    required = {"document", "url", "section", "source_revision", "captured_at", "content_hash"}
    missing = sorted(key for key in required if not source.get(key))
    if missing:
        raise FixtureValidationError(f"source metadata 缺少字段: {', '.join(missing)}")
    actual_hash = _content_hash(content)
    if source["content_hash"] != actual_hash:
        raise FixtureValidationError("fixture 内容 hash 与 source metadata 不一致")


def validate_decoration_matrix(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 3 or payload.get("industry_slug") != "decoration":
        raise FixtureValidationError("装修矩阵必须使用 schema_version=3 和 industry_slug=decoration")

    formulas = payload.get("formula_snapshots")
    groups = payload.get("groups")
    if not isinstance(formulas, dict) or not isinstance(groups, list):
        raise FixtureValidationError("装修矩阵缺少 formula_snapshots 或 groups")
    _validate_source(payload.get("source"), {"formula_snapshots": formulas, "groups": groups})

    title_formulas = formulas.get("title")
    body_formulas = formulas.get("body")
    if not isinstance(title_formulas, list) or not isinstance(body_formulas, list):
        raise FixtureValidationError("标题和正文公式快照必须是数组")
    if {item.get("code") for item in title_formulas} != TITLE_FORMULA_CODES:
        raise FixtureValidationError("标题公式必须完整覆盖 T01～T07")
    if {item.get("code") for item in body_formulas} != BODY_FORMULA_CODES:
        raise FixtureValidationError("正文公式必须完整覆盖 C01～C04")
    if any(not item.get("original_formula") for item in [*title_formulas, *body_formulas]):
        raise FixtureValidationError("公式原版结构不能为空")

    if len(groups) != 28:
        raise FixtureValidationError("装修矩阵必须正好包含 28 个组合组")
    group_codes = [group.get("code") for group in groups]
    if len(set(group_codes)) != 28 or any(not code for code in group_codes):
        raise FixtureValidationError("组合组 code 必须非空且唯一")

    direction_counts: dict[str, int] = {}
    title_references = 0
    body_references = 0
    formula_pairs = 0
    for group in groups:
        direction = group.get("content_direction")
        direction_code = direction.get("code") if isinstance(direction, dict) else None
        if not direction_code or not direction.get("name"):
            raise FixtureValidationError(f"组合组 {group.get('code')} 缺少内容方向")
        direction_counts[direction_code] = direction_counts.get(direction_code, 0) + 1

        members = group.get("method_members")
        combination_type = group.get("combination_type")
        if combination_type not in COMBINATION_SIZES or not isinstance(members, list):
            raise FixtureValidationError(f"组合组 {group.get('code')} 的组合类型无效")
        if len(members) != COMBINATION_SIZES[combination_type]:
            raise FixtureValidationError(f"组合组 {group.get('code')} 的手法数量与组合类型不一致")
        method_codes = [member.get("method_code") for member in members]
        if len(set(method_codes)) != len(method_codes) or any(code not in METHOD_CODES for code in method_codes):
            raise FixtureValidationError(f"组合组 {group.get('code')} 包含未知或重复手法")
        if any(member.get("order") != index for index, member in enumerate(members, start=1)):
            raise FixtureValidationError(f"组合组 {group.get('code')} 的手法顺序无效")

        titles = group.get("title_formula_candidate_codes")
        bodies = group.get("body_formula_candidate_codes")
        if not isinstance(titles, list) or not titles or any(code not in TITLE_FORMULA_CODES for code in titles):
            raise FixtureValidationError(f"组合组 {group.get('code')} 的标题公式候选无效")
        if not isinstance(bodies, list) or not bodies or any(code not in BODY_FORMULA_CODES for code in bodies):
            raise FixtureValidationError(f"组合组 {group.get('code')} 的正文公式候选无效")
        if len(set(titles)) != len(titles) or len(set(bodies)) != len(bodies):
            raise FixtureValidationError(f"组合组 {group.get('code')} 的公式候选不能重复")
        if not group.get("scenario_description") or not group.get("source_metadata"):
            raise FixtureValidationError(f"组合组 {group.get('code')} 缺少场景或来源")

        title_references += len(titles)
        body_references += len(bodies)
        formula_pairs += len(titles) * len(bodies)

    if len(direction_counts) != 7 or set(direction_counts.values()) != {4}:
        raise FixtureValidationError("装修矩阵必须正好包含 7 个方向且每个方向 4 个组合组")
    if (title_references, body_references, formula_pairs) != (91, 48, 166):
        raise FixtureValidationError("装修矩阵必须满足 91/48/166 公式引用不变量")
    return payload


def validate_decoration_semantic_lexicons(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 3 or payload.get("industry_slug") != "decoration":
        raise FixtureValidationError("装修语义词库必须使用 schema_version=3 和 industry_slug=decoration")
    categories = payload.get("categories")
    if not isinstance(categories, list):
        raise FixtureValidationError("装修语义词库 categories 必须是数组")
    _validate_source(payload.get("source"), categories)
    if len(categories) != 34:
        raise FixtureValidationError("装修语义词库必须正好包含 34 类")

    codes = [category.get("code") for category in categories]
    if len(set(codes)) != 34 or any(not code for code in codes):
        raise FixtureValidationError("语义词库 code 必须非空且唯一")
    title_count = sum(category.get("scope") == "title" for category in categories)
    if title_count != 14 or len(categories) - title_count != 20:
        raise FixtureValidationError("语义词库必须包含标题侧 14 类、正文/人设/结尾侧 20 类")
    for category in categories:
        if category.get("domain") != "expression" or category.get("evidence_eligible") is not False:
            raise FixtureValidationError(f"语义词库 {category.get('code')} 不能作为事实证据")
        if not category.get("name") or not category.get("source_heading"):
            raise FixtureValidationError(f"语义词库 {category.get('code')} 缺少名称或来源章节")
    return payload


def load_decoration_matrix(path: Path = MATRIX_FIXTURE) -> dict[str, Any]:
    return validate_decoration_matrix(_load_json(path))


def load_decoration_semantic_lexicons(path: Path = SEMANTIC_LEXICON_FIXTURE) -> dict[str, Any]:
    return validate_decoration_semantic_lexicons(_load_json(path))
