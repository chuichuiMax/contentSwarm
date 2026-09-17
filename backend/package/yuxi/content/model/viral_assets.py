"""文章级爆款资产及准备结果，完整原文与结构引用可独立核验。"""

from __future__ import annotations

import hashlib
import json
import csv
import html
import io
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ViralContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ViralArticleSource(ViralContract):
    kb_id: str = Field(min_length=1, max_length=80)
    file_id: str = Field(min_length=1, max_length=64)
    locator: str = Field(min_length=1, max_length=512)
    industry_slug: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=1000)
    body: str = Field(min_length=1, max_length=100_000)
    full_source_hash: str = Field(min_length=64, max_length=64)
    source_file_version: str = Field(min_length=1, max_length=128)
    completeness: Literal["complete", "unverified"]
    viral_basis: str = Field(min_length=1, max_length=4000)

    @property
    def article_id(self) -> str:
        identity = json.dumps([self.kb_id, self.file_id, self.locator], ensure_ascii=False)
        return "va_" + hashlib.sha256(identity.encode()).hexdigest()[:40]

    @property
    def source_hash(self) -> str:
        content = json.dumps(self.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(content.encode()).hexdigest()


class SourceAnchor(ViralContract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    section: Literal["title", "body"]
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, gt=0)
    quote: str = Field(min_length=1)


class ReferenceSlot(ViralContract):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    required: bool
    anchor: SourceAnchor


class ReferenceCard(ViralContract):
    content_type_code: Literal["CT01", "CT02", "CT03", "CT04", "CT05", "CT06", "CT07"] | None = None
    content_type_reason: str = ""
    audience: str = Field(min_length=1)
    scene: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=1000)
    required_slots: list[ReferenceSlot]
    anchors: list[SourceAnchor] = Field(min_length=1)


class ViralAssetPreparationInputV1(ViralContract):
    source: ViralArticleSource
    source_hash: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def verify_source_hash(self):
        if self.source_hash != self.source.source_hash:
            raise ValueError("输入原文哈希不一致")
        return self


class ViralAssetImport(ViralContract):
    kb_id: str = Field(min_length=1, max_length=80)
    file_id: str = Field(min_length=1, max_length=64)
    industry_slug: str = Field(min_length=1, max_length=80)
    layout: Literal["single", "markdown_table", "csv"]
    title_column: str | None = None
    body_column: str | None = None
    viral_basis: str = Field(min_length=1, max_length=3000)


class ViralAssetPreparationResultV1(ViralContract):
    status: Literal["prepared", "needs_review"]
    source_hash: str = Field(min_length=64, max_length=64)
    reference_card: ReferenceCard | None = None
    reference_blueprint: dict[str, Any] | None = None
    blueprint_anchors: dict[str, list[SourceAnchor]] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_consistent_status(self):
        if self.status == "prepared":
            if not self.reference_card or not self.reference_blueprint or self.issues:
                raise ValueError("准备成功必须有参考卡、蓝图且不存在未解决问题")
        elif not self.issues or self.reference_card or self.reference_blueprint or self.blueprint_anchors:
            raise ValueError("待核验结果只提交明确问题，不发布未核验画像")
        return self


BLUEPRINT_FIELDS = frozenset(
    {
        "title_pattern",
        "title_slot_sequence",
        "opening_hook",
        "content_block_sequence",
        "narrative_structure",
        "paragraph_rhythm",
        "list_pattern",
        "emoji_pattern",
        "interaction_style",
    }
)


def validate_prepared_asset(payload: dict[str, Any], source: ViralArticleSource) -> ViralAssetPreparationResultV1:
    result = ViralAssetPreparationResultV1.model_validate(payload)
    if result.source_hash != source.source_hash:
        raise ValueError("准备结果必须对应同一原文版本")
    if result.status == "needs_review":
        return result
    if source.completeness != "complete":
        raise ValueError("未核验完整性的原文不能发布画像")
    blueprint = result.reference_blueprint
    if not BLUEPRINT_FIELDS.issubset(blueprint) or not BLUEPRINT_FIELDS.issubset(result.blueprint_anchors):
        raise ValueError("结构蓝图缺少必需字段或原文依据")
    if not blueprint["title_slot_sequence"] or not blueprint["content_block_sequence"]:
        raise ValueError("结构蓝图缺少标题槽位或信息块顺序")
    if not isinstance(blueprint["list_pattern"], dict) or blueprint["list_pattern"].get("type") not in {
        "none",
        "numbered",
        "emoji",
        "bulleted",
        "mixed",
    }:
        raise ValueError("蓝图列表类型无效")
    card = result.reference_card
    if source.industry_slug == "decoration" and (not card.content_type_code or not card.content_type_reason):
        raise ValueError("装修爆款必须标注创作类型及原文分类依据")
    names = [slot.name for slot in card.required_slots]
    if len(names) != len(set(names)):
        raise ValueError("参考事实槽位名称不能重复")
    anchors = [*card.anchors, *(slot.anchor for slot in card.required_slots)]
    for name in BLUEPRINT_FIELDS:
        if not result.blueprint_anchors[name]:
            raise ValueError("蓝图各字段均需原文依据，未出现的样式也需引用检查范围")
        anchors.extend(result.blueprint_anchors[name])
    for anchor in anchors:
        text = getattr(source, anchor.section)
        if anchor.start is None and anchor.end is None:
            if text.count(anchor.quote) != 1:
                raise ValueError("原文引用不存在或存在多处匹配，请提供明确位置")
            anchor.start = text.index(anchor.quote)
            anchor.end = anchor.start + len(anchor.quote)
        if anchor.start is None or anchor.end is None:
            raise ValueError("原文引用位置必须同时提供 start/end")
        if anchor.end <= anchor.start or text[anchor.start : anchor.end] != anchor.quote:
            raise ValueError("画像引用与原文位置不一致")
    return result


def extract_article_records(
    content: str,
    *,
    layout: str,
    title_column: str | None = None,
    body_column: str | None = None,
) -> list[dict[str, str]]:
    """根据明确的源记录结构拆文章；不从向量分块猜测完整原文。"""
    if layout == "single":
        lines = content.strip().splitlines()
        if len(lines) < 2 or not lines[0].startswith("# "):
            raise ValueError("单篇格式需以一级标题开始；无法确定边界时请使用明确的文章表格")
        if any(line.startswith("# ") for line in lines[1:]):
            raise ValueError("文档包含多个一级标题，不能作为一篇原文")
        body = "\n".join(lines[1:]).strip()
        if not body:
            raise ValueError("原文正文为空")
        return [{"locator": "article:1", "title": lines[0][2:].strip(), "body": body}]
    if not title_column or not body_column or title_column == body_column:
        raise ValueError("请明确指定不同的标题列与正文列")
    records = []
    if layout == "csv":
        reader = csv.DictReader(io.StringIO(content))
        if title_column not in (reader.fieldnames or []) or body_column not in (reader.fieldnames or []):
            raise ValueError("原文表格缺少指定标题列或正文列")
        rows = [(f"csv:record:{index}", row) for index, row in enumerate(reader, 1)]
    elif layout == "markdown_table":
        rows = []
        header = None
        table = 0
        record = 0
        for line in content.splitlines():
            if not line.strip().startswith("|"):
                header = None
                continue
            cells = next(csv.reader([line.strip().strip("|")], delimiter="|", escapechar="\\", quoting=csv.QUOTE_NONE))
            cells = [html.unescape(re.sub(r"<br\s*/?>", "\n", cell, flags=re.I)).strip() for cell in cells]
            if title_column in cells and body_column in cells:
                header, table, record = cells, table + 1, 0
                continue
            if header is None or all(re.fullmatch(r":?-+:?", cell.replace(" ", "")) for cell in cells):
                continue
            if len(cells) != len(header):
                raise ValueError("文章表格行列不完整，不能发布截断记录")
            record += 1
            rows.append((f"table:{table}/record:{record}", dict(zip(header, cells, strict=True))))
    else:
        raise ValueError("不支持的原文记录格式")
    for locator, row in rows:
        title, body = str(row.get(title_column) or "").strip(), str(row.get(body_column) or "").strip()
        if not title and not body:
            continue
        if not title or not body or None in row:
            raise ValueError(f"原文记录 {locator} 的标题或正文不完整")
        records.append({"locator": locator, "title": title, "body": body})
    if not records:
        raise ValueError("没有找到完整的标题与正文记录")
    return records
