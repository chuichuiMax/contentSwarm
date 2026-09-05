from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content.control.errors import ContentApplicationError
from yuxi.content.control.strategy.recommend_v3 import StrategyPreviewActor, StrategyPreviewContext
from yuxi.content.model.rules.engine import CombinationGroup
from yuxi.content.v3.seed import DECORATION_INDUSTRY_PACK_V3_ID, PLATFORM_RULE_V3_ID
from yuxi.storage.postgres.models_content import (
    ChannelProfile,
    ChannelProfileVersion,
    ContentCombinationRule,
    ContentTask,
    IndustryContentPackVersion,
    IndustryTemplateVersion,
)


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _available_variables(brief: dict[str, Any]) -> frozenset[str]:
    available: set[str] = set()
    for section_name in ("form_values", "business_variables", "brand", "persona"):
        section = brief.get(section_name)
        if isinstance(section, dict):
            available.update(key for key, value in section.items() if _has_value(value))
    for key, value in brief.items():
        if not isinstance(value, dict) and _has_value(value):
            available.add(key)
    if _has_value(brief.get("audience")):
        available.add("audience")
    return frozenset(available)


def _available_evidence_types(evidence_bundle: dict[str, Any]) -> frozenset[str]:
    available: set[str] = set()
    for item in evidence_bundle.get("items") or []:
        if not isinstance(item, dict):
            continue
        for key in ("type", "evidence_type", "source_type", "variable_code"):
            value = item.get(key)
            if isinstance(value, str) and value:
                available.add(value)
    return frozenset(available)


class PostgresStrategyPreviewRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def load_context(
        self,
        *,
        task_id: str,
        actor: StrategyPreviewActor,
        requested_content_direction_code: str | None,
    ) -> StrategyPreviewContext | None:
        task = (
            await self.db.execute(
                select(ContentTask).where(ContentTask.id == task_id, ContentTask.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if task is None or not self._can_read(task, actor):
            return None
        if not task.brief_json:
            raise ContentApplicationError("CONTENT_BRIEF_REQUIRED", "请先完成业务简报", "conflict")

        industry_slug, industry_pack_version_id = await self._industry_context(task)
        channel_code = await self._channel_code(task.channel_profile_version_id)
        rows = list(
            (
                await self.db.execute(
                    select(ContentCombinationRule)
                    .where(
                        ContentCombinationRule.version_id == PLATFORM_RULE_V3_ID,
                        ContentCombinationRule.schema_version == 3,
                    )
                    .order_by(ContentCombinationRule.priority.desc(), ContentCombinationRule.id)
                )
            ).scalars()
        )
        groups = tuple(self._to_domain(row) for row in rows)
        selected_angle = task.selected_angle_json or {}
        content_direction_code = (
            requested_content_direction_code or task.content_type_code or selected_angle.get("content_type_code") or ""
        )
        return StrategyPreviewContext(
            task_id=task.id,
            rule_version_id=PLATFORM_RULE_V3_ID,
            industry_pack_version_id=industry_pack_version_id,
            channel_profile_version_id=task.channel_profile_version_id,
            content_direction_code=content_direction_code,
            industry_slug=industry_slug,
            channel_code=channel_code,
            content_goal_code=task.content_goal,
            narrative_axis_code=task.primary_narrative_axis,
            available_variable_codes=_available_variables(task.brief_json or {}),
            available_evidence_types=_available_evidence_types(task.evidence_json or {}),
            groups=groups,
        )

    @staticmethod
    def _can_read(task: ContentTask, actor: StrategyPreviewActor) -> bool:
        if actor.role == "superadmin" or task.created_by == actor.uid:
            return True
        return actor.role == "admin" and task.tenant_id == actor.tenant_id

    async def _industry_context(self, task: ContentTask) -> tuple[str, str | None]:
        if task.industry_pack_version_id:
            pack = await self.db.get(IndustryContentPackVersion, task.industry_pack_version_id)
            if pack is not None:
                return pack.slug, (
                    DECORATION_INDUSTRY_PACK_V3_ID if pack.slug == "decoration" else task.industry_pack_version_id
                )
        template = await self.db.get(IndustryTemplateVersion, task.industry_template_version_id)
        if template is not None:
            return template.slug, DECORATION_INDUSTRY_PACK_V3_ID if template.slug == "decoration" else None
        return "", None

    async def _channel_code(self, version_id: str | None) -> str | None:
        if not version_id:
            return None
        row = (
            await self.db.execute(
                select(ChannelProfile.code)
                .join(ChannelProfileVersion, ChannelProfileVersion.profile_id == ChannelProfile.id)
                .where(ChannelProfileVersion.id == version_id)
            )
        ).scalar_one_or_none()
        return row

    @staticmethod
    def _to_domain(row: ContentCombinationRule) -> CombinationGroup:
        direction_code = (row.content_type_codes or [""])[0]
        return CombinationGroup.from_mapping(
            {
                "code": row.id,
                "content_direction_code": direction_code,
                "content_direction_name": direction_code,
                "combination_type": row.combination_type,
                "method_members": row.method_members or [],
                "title_formula_candidate_codes": row.title_formula_candidate_codes or [],
                "body_formula_candidate_codes": row.body_formula_candidate_codes or [],
                "industry_scope": row.industry_scope or [],
                "channel_scope": row.channel_scope or [],
                "content_goal_codes": row.content_goal_codes or [],
                "narrative_axis_codes": row.narrative_axis_codes or [],
                "required_variable_codes": row.required_variable_codes or [],
                "required_evidence_types": row.required_evidence_types or [],
                "priority": row.priority,
                "enabled": row.compatibility != "disabled",
                "scenario_description": row.scenario_description,
                "source_metadata": row.source_metadata or {},
            },
            rule_version_id=row.version_id,
        )
