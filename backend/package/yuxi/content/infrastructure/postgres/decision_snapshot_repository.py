from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content.model.formulas.selector import FormulaSelectionDecision
from yuxi.content.model.rules.engine import MatchDecision
from yuxi.storage.postgres.models_business import AgentRun
from yuxi.storage.postgres.models_content import (
    ContentFormulaSelectionSnapshot,
    ContentMatchDecisionSnapshot,
)


class PostgresDecisionSnapshotRepository:
    """在同一内容 Run 内以追加方式保存 active/superseded 决策。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def save_match_decision(
        self,
        *,
        task_id: str,
        content_run_id: str,
        node_run_id: str | None,
        rule_version_id: str,
        industry_pack_version_id: str | None,
        channel_profile_version_id: str | None,
        decision: MatchDecision,
        selected_by: str,
    ) -> ContentMatchDecisionSnapshot:
        await self._lock_run(content_run_id)
        previous = (
            await self.db.execute(
                select(ContentMatchDecisionSnapshot)
                .where(
                    ContentMatchDecisionSnapshot.content_run_id == content_run_id,
                    ContentMatchDecisionSnapshot.status == "active",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if previous is not None:
            previous.status = "superseded"
        snapshot = ContentMatchDecisionSnapshot(
            id=f"cmds_{uuid.uuid4().hex}",
            task_id=task_id,
            content_run_id=content_run_id,
            node_run_id=node_run_id,
            rule_version_id=rule_version_id,
            industry_pack_version_id=industry_pack_version_id,
            channel_profile_version_id=channel_profile_version_id,
            content_direction=decision.content_direction_code,
            eligible_group_ids=[item.group_code for item in decision.eligible_groups],
            rejected_groups=[item.to_dict() for item in decision.rejected_groups],
            score_details={item.group_code: item.score_details for item in decision.eligible_groups},
            selected_group_id=decision.selected_group_code,
            selection_mode=decision.selection_mode,
            selected_by=selected_by,
            status="active",
            supersedes_id=previous.id if previous is not None else None,
        )
        self.db.add(snapshot)
        await self.db.flush()
        return snapshot

    async def save_formula_selection(
        self,
        *,
        task_id: str,
        content_run_id: str,
        node_run_id: str | None,
        match_snapshot_id: str,
        rule_version_id: str,
        evidence_bundle_hash: str,
        decision: FormulaSelectionDecision,
        selected_by: str,
        delegated_agent_run_id: str | None = None,
    ) -> ContentFormulaSelectionSnapshot:
        await self._lock_run(content_run_id)
        previous = (
            await self.db.execute(
                select(ContentFormulaSelectionSnapshot)
                .where(
                    ContentFormulaSelectionSnapshot.content_run_id == content_run_id,
                    ContentFormulaSelectionSnapshot.status == "active",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if previous is not None:
            previous.status = "superseded"
        snapshot = ContentFormulaSelectionSnapshot(
            id=f"cfss_{uuid.uuid4().hex}",
            task_id=task_id,
            content_run_id=content_run_id,
            node_run_id=node_run_id,
            match_snapshot_id=match_snapshot_id,
            combination_group_id=decision.combination_group_id,
            eligible_title_formula_codes=[item.formula_code for item in decision.eligible_title_formulas],
            eligible_body_formula_codes=[item.formula_code for item in decision.eligible_body_formulas],
            title_score_details={item.formula_code: item.score_details for item in decision.eligible_title_formulas},
            body_score_details={item.formula_code: item.score_details for item in decision.eligible_body_formulas},
            selected_title_formula_code=decision.selected_title_formula_code,
            selected_body_formula_code=decision.selected_body_formula_code,
            title_selection_reason=decision.title_selection_reason,
            body_selection_reason=decision.body_selection_reason,
            selection_mode=decision.selection_mode,
            selected_by=selected_by,
            delegated_agent_run_id=delegated_agent_run_id,
            rule_version_id=rule_version_id,
            evidence_bundle_hash=evidence_bundle_hash,
            status="active",
            supersedes_id=previous.id if previous is not None else None,
        )
        self.db.add(snapshot)
        await self.db.flush()
        return snapshot

    async def _lock_run(self, content_run_id: str) -> None:
        run_id = (
            await self.db.execute(select(AgentRun.id).where(AgentRun.id == content_run_id).with_for_update())
        ).scalar_one_or_none()
        if run_id is None:
            raise ValueError("内容 Run 不存在")
