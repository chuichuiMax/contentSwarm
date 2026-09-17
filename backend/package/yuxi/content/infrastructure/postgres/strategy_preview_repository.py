from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content.control.errors import ContentApplicationError
from yuxi.content.control.strategy.recommend_v3 import StrategyPreviewActor, StrategyPreviewContext
from yuxi.content.model.rules.engine import CombinationGroup
from yuxi.content.v3.seed import DECORATION_INDUSTRY_PACK_V3_ID
from yuxi.repositories.content_repository import ContentRepository
from yuxi.storage.postgres.models_content import (
    ChannelProfile,
    ChannelProfileVersion,
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

    async def _load_strategy_source(self, task_id, actor):
        task = await ContentRepository(self.db).get_task(task_id)
        if task is None or task.deleted_at is not None or not self._can_read(task, actor):
            raise ContentApplicationError("CONTENT_TASK_NOT_FOUND", "内容任务不存在", "not_found")
        industry_slug, _ = await self._industry_context(task)
        bundle = await ContentRepository(self.db).get_rule_bundle(task.rule_version_id, include_disabled=True)
        if bundle is None:
            raise ContentApplicationError("CONTENT_RULE_VERSION_NOT_FOUND", "任务锁定规则版本不存在", "conflict")
        return task, industry_slug, bundle

    async def load_candidates(
        self, *, task_id: str, actor: StrategyPreviewActor, auto_direction: bool = False
    ) -> dict[str, Any]:
        """读取完整规则引用，装配手动方向或自动蓝图选择所需的候选。"""
        from yuxi.content.model.strategy import build_strategy_candidates

        task, industry_slug, bundle = await self._load_strategy_source(task_id, actor)
        from yuxi.content.v3.joint_workflow import BLUEPRINT_FIRST_WORKFLOW_IDS

        auto_direction = auto_direction or task.workflow_version_id in BLUEPRINT_FIRST_WORKFLOW_IDS
        try:
            candidates = build_strategy_candidates(
                bundle,
                industry_slug=industry_slug,
                direction_code=task.content_type_code,
                auto_direction=auto_direction,
                rule_version_id=task.rule_version_id,
                policy=(task.runtime_config_snapshot_json or {}).get("selection_policy_snapshot"),
            )
        except ValueError as exc:
            raise ContentApplicationError("CONTENT_STRATEGY_CANDIDATES_INVALID", str(exc), "conflict") from exc
        return {"task_id": task.id, "creates_run": False, "strategy_candidates": candidates}

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
        bundle = await ContentRepository(self.db).get_rule_bundle(task.rule_version_id)
        groups = tuple(
            CombinationGroup.from_mapping(
                {
                    **item,
                    "code": item["id"],
                    "content_direction_code": item["content_type_codes"][0],
                    "content_direction_name": item["source_metadata"].get(
                        "content_direction_name", item["content_type_codes"][0]
                    ),
                },
                rule_version_id=task.rule_version_id,
            )
            for item in bundle["combination_rules"]
        )
        selected_angle = task.selected_angle_json or {}
        content_direction_code = (
            requested_content_direction_code or task.content_type_code or selected_angle.get("content_type_code") or ""
        )
        return StrategyPreviewContext(
            task_id=task.id,
            rule_version_id=task.rule_version_id,
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
                return pack.slug, task.industry_pack_version_id
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
