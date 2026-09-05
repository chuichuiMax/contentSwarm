from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from yuxi.content.control.errors import ContentApplicationError
from yuxi.content.model.rules.engine import CombinationGroup, CombinationMatcher, MatchRequest


@dataclass(frozen=True, slots=True)
class StrategyPreviewActor:
    uid: str
    role: str
    tenant_id: str | None


@dataclass(frozen=True, slots=True)
class StrategyPreviewContext:
    task_id: str
    rule_version_id: str
    industry_pack_version_id: str | None
    channel_profile_version_id: str | None
    content_direction_code: str
    industry_slug: str
    channel_code: str | None
    content_goal_code: str | None
    narrative_axis_code: str | None
    available_variable_codes: frozenset[str]
    available_evidence_types: frozenset[str]
    groups: tuple[CombinationGroup, ...]


class StrategyPreviewPort(Protocol):
    async def load_context(
        self,
        *,
        task_id: str,
        actor: StrategyPreviewActor,
        requested_content_direction_code: str | None,
    ) -> StrategyPreviewContext | None: ...


@dataclass(frozen=True, slots=True)
class PreviewV3StrategyCommand:
    task_id: str
    actor: StrategyPreviewActor
    content_direction_code: str | None = None
    limit: int = 28


class PreviewV3StrategyHandler:
    def __init__(self, port: StrategyPreviewPort, matcher: CombinationMatcher | None = None):
        self.port = port
        self.matcher = matcher or CombinationMatcher()

    async def execute(self, command: PreviewV3StrategyCommand) -> dict[str, object]:
        context = await self.port.load_context(
            task_id=command.task_id,
            actor=command.actor,
            requested_content_direction_code=command.content_direction_code,
        )
        if context is None:
            raise ContentApplicationError("CONTENT_TASK_NOT_FOUND", "内容任务不存在", "not_found")
        if not context.content_direction_code:
            raise ContentApplicationError("CONTENT_DIRECTION_REQUIRED", "请先选择内容方向", "conflict")

        decision = self.matcher.match(
            list(context.groups),
            MatchRequest(
                content_direction_code=context.content_direction_code,
                industry_slug=context.industry_slug,
                channel_code=context.channel_code,
                content_goal_code=context.content_goal_code,
                narrative_axis_code=context.narrative_axis_code,
                available_variable_codes=context.available_variable_codes,
                available_evidence_types=context.available_evidence_types,
                limit=command.limit,
            ),
        )
        return {
            "preview": True,
            "creates_run": False,
            "task_id": context.task_id,
            "rule_version_id": context.rule_version_id,
            "industry_pack_version_id": context.industry_pack_version_id,
            "channel_profile_version_id": context.channel_profile_version_id,
            "decision": decision.to_dict(),
        }
