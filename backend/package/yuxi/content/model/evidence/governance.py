"""V3 Evidence 领域模型：来源、用途、冻结和版本都是强约束。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EvidenceSourceType = Literal[
    "manual_input",
    "business_record",
    "media",
    "knowledge_base",
    "human_confirmation",
]
EvidenceVerifiedStatus = Literal["retrieved", "confirmed", "user_confirmed", "rejected"]
EvidenceUsage = Literal["title", "body", "visual", "style_reference"]
EvidenceRiskLevel = Literal["normal", "sensitive", "high_risk"]


class EvidenceGovernanceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class EvidenceBundleFrozenError(EvidenceGovernanceError):
    def __init__(self):
        super().__init__("evidence_bundle_frozen", "EvidenceBundle 冻结后不可修改，必须创建新版本")


class EvidenceItemV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=64)
    variable_codes: tuple[str, ...] = ()
    value: Any
    source_type: EvidenceSourceType
    source_id: str = Field(min_length=1, max_length=255)
    source_version: str = Field(min_length=1, max_length=128)
    verified_status: EvidenceVerifiedStatus
    allowed_usage: tuple[EvidenceUsage, ...]
    risk_level: EvidenceRiskLevel = "normal"
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_hash: str = Field(min_length=8, max_length=128)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("id")
    @classmethod
    def reject_lexicon_ids(cls, value: str) -> str:
        if value.startswith(("lex_", "lexicon_")):
            raise ValueError("词库值不是事实证据，不能作为 EvidenceItem")
        return value

    @field_validator("allowed_usage")
    @classmethod
    def require_unique_usage(cls, value: tuple[EvidenceUsage, ...]) -> tuple[EvidenceUsage, ...]:
        if not value or len(value) != len(set(value)):
            raise ValueError("EvidenceItem 必须声明不重复的 allowed_usage")
        return value


class EvidenceBundleV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    task_id: str
    version: int = Field(ge=1)
    status: Literal["frozen"] = "frozen"
    items: tuple[EvidenceItemV1, ...]
    source_counts: dict[str, int]
    citations: tuple[dict[str, Any], ...]
    bundle_hash: str
    supersedes_id: str | None = None
    frozen_at: datetime

    def add_item(self, item: EvidenceItemV1) -> None:
        del item
        raise EvidenceBundleFrozenError


def _canonical_bundle_payload(
    task_id: str,
    version: int,
    items: tuple[EvidenceItemV1, ...],
    citations: tuple[dict[str, Any], ...],
    supersedes_id: str | None,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "version": version,
        "items": [item.model_dump(mode="json") for item in sorted(items, key=lambda value: value.id)],
        "citations": list(citations),
        "supersedes_id": supersedes_id,
    }


def freeze_evidence_bundle(
    *,
    task_id: str,
    version: int,
    items: tuple[EvidenceItemV1, ...] | list[EvidenceItemV1],
    citations: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    supersedes_id: str | None = None,
    frozen_at: datetime | None = None,
) -> EvidenceBundleV1:
    frozen_items = tuple(items)
    ids = [item.id for item in frozen_items]
    if len(ids) != len(set(ids)):
        raise EvidenceGovernanceError("evidence_id_duplicate", "EvidenceBundle 中的 Evidence ID 不能重复")
    rejected = [item.id for item in frozen_items if item.verified_status == "rejected"]
    if rejected:
        raise EvidenceGovernanceError(
            "rejected_evidence_in_bundle",
            f"已拒绝 Evidence 不得进入冻结包: {', '.join(rejected)}",
        )
    unconfirmed_high_risk = [
        item.id for item in frozen_items if item.risk_level == "high_risk" and item.verified_status != "user_confirmed"
    ]
    if unconfirmed_high_risk:
        raise EvidenceGovernanceError(
            "high_risk_evidence_unconfirmed",
            f"高风险 Evidence 必须经人工确认: {', '.join(unconfirmed_high_risk)}",
        )
    frozen_citations = tuple(dict(item) for item in citations)
    canonical = _canonical_bundle_payload(task_id, version, frozen_items, frozen_citations, supersedes_id)
    bundle_hash = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    source_counts: dict[str, int] = {}
    for item in frozen_items:
        source_counts[item.source_type] = source_counts.get(item.source_type, 0) + 1
    return EvidenceBundleV1(
        id=f"ceb_{bundle_hash[:24]}",
        task_id=task_id,
        version=version,
        items=frozen_items,
        source_counts=source_counts,
        citations=frozen_citations,
        bundle_hash=bundle_hash,
        supersedes_id=supersedes_id,
        frozen_at=frozen_at or datetime.now(UTC),
    )


def next_evidence_bundle_version(
    current: EvidenceBundleV1,
    *,
    additions: tuple[EvidenceItemV1, ...] | list[EvidenceItemV1] = (),
    rejected_ids: frozenset[str] = frozenset(),
    citations: tuple[dict[str, Any], ...] | list[dict[str, Any]] | None = None,
) -> EvidenceBundleV1:
    retained = [item for item in current.items if item.id not in rejected_ids]
    return freeze_evidence_bundle(
        task_id=current.task_id,
        version=current.version + 1,
        items=tuple([*retained, *additions]),
        citations=current.citations if citations is None else citations,
        supersedes_id=current.id,
    )


def validate_evidence_references(
    bundle: EvidenceBundleV1,
    evidence_ids: tuple[str, ...] | list[str],
    *,
    usage: EvidenceUsage,
) -> None:
    allowed = {item.id for item in bundle.items if usage in item.allowed_usage and item.verified_status != "rejected"}
    unknown = sorted(set(evidence_ids) - allowed)
    if unknown:
        raise EvidenceGovernanceError(
            "evidence_reference_forbidden",
            f"存在不允许用于 {usage} 的 Evidence ID: {', '.join(unknown)}",
        )
