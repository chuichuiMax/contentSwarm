from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content.control.workflow.content_node_input import ContentNodeInputAssembler
from yuxi.content.model.contracts import ContractDomainContext
from yuxi.services.agent_delegation_service import AgentDelegationRequest, AgentDelegationService
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentNodeRun, ContentTask

PROHIBITED_ACTIONS = {
    "select_creation_strategy": ("不提交规则库外的组合组、创作手法或公式", "不编造事实", "不生成正文"),
    "analyze_and_select_direction": ("不锁定组合组", "不选公式", "不修改工作流"),
    "analyze_content_value": ("不锁定组合组", "不选公式", "不修改工作流"),
    "select_content_direction": ("不选择候选集外方向", "不锁定组合组", "不选公式", "不修改工作流"),
    "explain_strategy": ("不改变固定匹配结果", "不扩大候选池"),
    "collect_missing_evidence": ("不直接生成文章", "不编造事实"),
    "collect_business_rule_evidence": ("不查询价格、封禁词或爆款资料", "不生成文章", "不编造事实"),
    "collect_price_evidence": ("不查询价格库之外的资料", "不改变价格口径", "不生成文章"),
    "collect_compliance_evidence": ("不查询封禁词库之外的资料", "不自行编造替换词", "不生成文章"),
    "collect_viral_candidates": ("不选择最终爆款", "不把爆款事实当作业务事实", "不生成文章"),
    "select_viral_reference": ("不查询知识库", "不复制爆款原句或事实", "不生成文章"),
    "collect_strategy_product_evidence": (
        "不生成标题或正文",
        "不修改锁定公式或创作手法",
        "爆款样例不得作为事实或数字来源",
        "不得把案例证明映射为爆款样例或 style_reference",
    ),
    "rank_formula_candidates": ("不提交候选池外公式", "不修改匹配组"),
    "generate_title_candidates": ("不生成正文", "不选最终标题", "不改公式"),
    "select_title": ("不选择未通过校验的候选", "不修改标题文本、公式或证据 ID", "不生成正文"),
    "build_outline": ("不生成新事实", "不改正文公式"),
    "generate_body": ("不改锁定标题", "不检索网页或知识库"),
    "generate_content": ("不修改锁定策略与公式", "不检索网页或知识库", "不引入冻结证据外的事实"),
    "persona_style_polish": ("不改数字、价格、承诺或证据 ID", "不改结构主干"),
    "semantic_review": ("不直接修改文稿", "不跳过确定性校验"),
    "plan_visuals": ("不新增无证据价格、人物或效果",),
    "submit_cover_job": ("不直接写 MinIO", "不轮询封面任务"),
    "visual_review": ("不直接修改或删除资产",),
}


class AgentNodeResultMapper:
    @staticmethod
    def to_state(node_id: str, result: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        if node_id == "select_creation_strategy":
            return {
                "selected_angle": {
                    "direction_code": result["selected_direction_code"],
                    "reason": result["reason"],
                    "evidence_ids": result["evidence_ids"],
                    "selected_by": "agent",
                },
                "strategy_selection": result,
            }
        if node_id == "analyze_and_select_direction":
            selected = next(
                (
                    item
                    for item in result["direction_candidates"]
                    if item.get("direction_code") == result["selected_direction_code"]
                ),
                None,
            )
            if selected is None:
                raise ValueError("Agent 选定内容方向不在价值分析候选集中")
            return {
                "value_analysis": {
                    "value_points": result["value_points"],
                    "direction_candidates": result["direction_candidates"],
                    "reasoning": result["reasoning"],
                    "evidence_ids": result["evidence_ids"],
                },
                "content_angles": result["direction_candidates"],
                "selected_angle": {
                    **selected,
                    "reason": result["selection_reason"],
                    "evidence_ids": result["selection_evidence_ids"],
                    "selected_by": "agent",
                },
            }
        if node_id == "analyze_content_value":
            return {
                "value_analysis": result,
                "content_angles": result["direction_candidates"],
            }
        if node_id == "select_content_direction":
            selected = next(
                (
                    item
                    for item in state.get("content_angles") or []
                    if item.get("direction_code") == result["direction_code"]
                ),
                None,
            )
            if selected is None:
                raise ValueError("Agent 选定内容方向不在价值分析候选集中")
            return {
                "selected_angle": {
                    **selected,
                    "reason": result["reason"],
                    "evidence_ids": result["evidence_ids"],
                    "selected_by": "agent",
                }
            }
        if node_id == "explain_strategy":
            return {"strategy_explanation": result}
        if node_id == "collect_missing_evidence":
            return {"evidence_collection": result}
        if node_id == "collect_business_rule_evidence":
            return {"business_rule_evidence_collection": result}
        if node_id == "collect_price_evidence":
            return {"price_evidence_collection": result}
        if node_id == "collect_compliance_evidence":
            return {"compliance_evidence_collection": result}
        if node_id == "collect_viral_candidates":
            return {"viral_candidate_collection": result}
        if node_id == "select_viral_reference":
            return {"viral_reference_selection": result}
        if node_id == "collect_strategy_product_evidence":
            return {"product_evidence_collection": result}
        if node_id == "rank_formula_candidates":
            return {"formula_rankings": result}
        if node_id == "generate_title_candidates":
            return {"title_candidates": result["candidates"]}
        if node_id == "select_title":
            selected = next(
                (
                    item
                    for item in state.get("title_candidates") or []
                    if item.get("id") == result["selected_title_id"] and item.get("selectable") is True
                ),
                None,
            )
            if selected is None:
                raise ValueError("标题 Agent 只能选择通过确定性校验的候选")
            return {
                "selected_title": {
                    **selected,
                    "selection_reason": result["reason"],
                    "selected_by": "agent",
                }
            }
        if node_id == "build_outline":
            return {"content_outline": result}
        if node_id == "generate_body":
            return {"content_draft": result}
        if node_id == "generate_content":
            return {
                "selected_title": {
                    "id": "agent-selected-title",
                    **result["title"],
                    "selected_by": "agent",
                },
                "content_outline": result["outline"],
                "content_draft": result["draft"],
            }
        if node_id == "persona_style_polish":
            draft = dict(state.get("content_draft") or {})
            draft["body"] = result["polished_body"]
            return {
                "content_draft": draft,
                "persona_diff": {
                    "change_summary": result["change_summary"],
                    "preserved_fact_checks": result["preserved_fact_checks"],
                },
            }
        if node_id == "semantic_review":
            return {"review_report": result}
        if node_id == "plan_visuals":
            canonical = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            return {
                "visual_plan": {
                    **result,
                    "plan_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                }
            }
        if node_id == "submit_cover_job":
            return {"cover_job": result}
        if node_id == "visual_review":
            return {"visual_review": result}
        raise ValueError(f"未注册的 V3 Agent 节点: {node_id}")


class AgentNodeHandler:
    async def execute(
        self,
        *,
        db: AsyncSession,
        node: dict[str, Any],
        state: dict[str, Any],
        node_run_id: str,
    ) -> dict[str, Any]:
        node_run = await db.get(ContentNodeRun, node_run_id)
        task = await db.get(ContentTask, state["task_id"])
        user = (
            await db.execute(select(User).where(User.uid == state["uid"], User.is_deleted == 0))
        ).scalar_one_or_none()
        if node_run is None or task is None or user is None:
            raise ValueError("Agent 节点缺少任务、用户或节点 Run")

        research_result_fields = {
            "collect_business_rule_evidence": "business_rule_evidence_collection",
            "collect_price_evidence": "price_evidence_collection",
            "collect_compliance_evidence": "compliance_evidence_collection",
        }
        research_result_field = research_result_fields.get(node["id"])
        if (
            research_result_field
            and not bool((state.get("evidence_gap_analysis") or {}).get("has_missing"))
            and not bool((state.get("runtime_config_snapshot") or {}).get("force_evidence_research"))
        ):
            return {
                research_result_field: {
                    "evidence_items": [],
                    "citations": [],
                    "unresolved_questions": [],
                    "skipped": True,
                    "skip_reason": "当前策略所需变量与证据已完整，无需重复调研",
                }
            }

        if node["id"] == "semantic_review" and not bool(
            (state.get("runtime_config_snapshot") or {}).get("strict_semantic_review")
        ):
            return {
                "review_report": {
                    "status": "passed",
                    "checks": [],
                    "evidence_conflicts": [],
                    "skipped": True,
                    "skip_reason": "普通首稿已通过确定性校验，语义审核仅在严格审核模式下执行",
                }
            }

        if node["id"] == "rank_formula_candidates":
            valid_pairs = (state.get("formula_candidate_pool") or {}).get("valid_formula_pairs") or []
            if len(valid_pairs) == 1:
                pair = valid_pairs[0]
                return {
                    "formula_rankings": {
                        "title_rankings": [{"formula_code": pair["title_formula_code"], "reason": "唯一有效公式对"}],
                        "body_rankings": [{"formula_code": pair["body_formula_code"], "reason": "唯一有效公式对"}],
                        "skipped": True,
                        "skip_reason": "固定规则已得到唯一有效公式对",
                    }
                }

        creation_mode = (state.get("runtime_config_snapshot") or {}).get("creation_mode", "original")
        if node["id"] == "collect_viral_candidates" and creation_mode != "viral_rewrite":
            return {
                "viral_candidate_collection": {
                    "evidence_items": [],
                    "citations": [],
                    "unresolved_questions": [],
                }
            }
        if node["id"] == "select_viral_reference" and creation_mode != "viral_rewrite":
            return {
                "viral_reference_selection": {
                    "selected_candidate_id": None,
                    "selection_reason": "原创模式跳过爆款参考选择",
                    "selection_basis": {},
                    "reference_blueprint": None,
                    "unresolved_questions": [],
                }
            }

        evidence_bundle = state.get("evidence_bundle") or {"items": []}
        evidence_hash = str(evidence_bundle.get("bundle_hash") or "")
        if not evidence_hash:
            canonical = json.dumps(evidence_bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            evidence_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        locked_versions = {
            "industry_pack_version_id": task.industry_pack_version_id or "",
            "channel_profile_version_id": task.channel_profile_version_id or "",
            "persona_profile_version_id": task.persona_profile_version_id,
            "rule_version_id": task.rule_version_id,
            "title_formula_code": (state.get("formula_selection_snapshot") or {}).get("selected_title_formula_code"),
            "body_formula_code": (state.get("formula_selection_snapshot") or {}).get("selected_body_formula_code"),
            "artifact_version_id": (state.get("artifact_version") or {}).get("id"),
        }
        media = state.get("media_evidence_items") or []
        cover_asset_ids = list((state.get("cover_job") or {}).get("asset_ids") or [])
        visual_material = (state.get("runtime_config_snapshot") or {}).get("visual_material") or {}
        required_source_asset_ids = [visual_material["image_asset_id"]] if visual_material.get("image_asset_id") else []
        locked_values = {
            "creation_mode": (state.get("runtime_config_snapshot") or {}).get("creation_mode", "original"),
            "selected_title": (state.get("selected_title") or {}).get("text"),
            "source_asset_ids": [
                *[item["id"] for item in media if item.get("id")],
                *cover_asset_ids,
            ],
            "required_source_asset_ids": required_source_asset_ids,
            "visual_plan_hash": (state.get("visual_plan") or {}).get("plan_hash"),
            "state_version": int(state.get("state_version") or 0),
        }
        if node["id"] == "plan_visuals":
            limits: dict[str, int] = {}
            for field in visual_material.get("hycanvas_fillable_fields") or []:
                role = str(field.get("semanticRole") or "")
                if role == "label" or role not in {"title", "subtitle", "body_excerpt"}:
                    continue
                max_chars = (field.get("constraints") or {}).get("maxChars")
                if isinstance(max_chars, int) and max_chars > 0:
                    limits[role] = min(limits.get(role, max_chars), max_chars)
            locked_values["visual_text_max_chars"] = limits
        if node["id"] == "submit_cover_job":
            locked_values["visual_plan"] = state.get("visual_plan") or {}
        assembly = ContentNodeInputAssembler.build(node=node, state=state)
        domain_context = ContractDomainContext.from_governance(
            match_decision_snapshot=state.get("match_decision_snapshot") or {},
            formula_selection_snapshot=state.get("formula_selection_snapshot") or {},
            evidence_bundle=evidence_bundle,
            locked_versions=locked_versions,
            locked_values=locked_values,
            product_material_requirements=state.get("product_material_requirements") or {},
            strategy_snapshot=state.get("strategy_snapshot") or {},
            viral_candidate_collection=state.get("viral_candidate_collection") or {},
        )
        delegation = AgentDelegationService(db)
        delegated = await delegation.execute(
            AgentDelegationRequest(
                task_id=task.id,
                parent_content_run_id=state["run_id"],
                node_run=node_run,
                user=user,
                agent_slug=node["agent_slug"],
                required_skills=tuple(node["required_skills"]),
                input_contract=assembly.contract_name,
                input_payload=assembly.payload,
                input_snapshot_hash=assembly.snapshot_hash,
                domain_context=domain_context,
                governance_values={
                    "evidence_bundle_hash": evidence_hash,
                    "match_decision_snapshot": state.get("match_decision_snapshot") or {},
                    "formula_selection_snapshot": state.get("formula_selection_snapshot") or {},
                    "locked_versions": locked_versions,
                    "locked_values": locked_values,
                    "product_material_requirements": state.get("product_material_requirements") or {},
                },
                prompt=f"执行内容工作流节点 {node['id']} 的唯一职责",
                output_contract=node["output_contract"],
                result_tool_name=node["result_tool_name"],
                knowledge_policy=node["knowledge_policy"],
                timeout_seconds=node["timeout_seconds"],
                max_execution_steps=node["max_execution_steps"],
                max_tool_calls=node["max_tool_calls"],
                token_budget=node["token_budget"],
                max_retrieval_rounds=int(node.get("max_retrieval_rounds") or 0),
                max_knowledge_bases=int(node.get("max_knowledge_bases") or 0),
                max_chunks_per_knowledge_base=int(node.get("max_chunks_per_knowledge_base") or 0),
                max_chars_per_knowledge_chunk=int(node.get("max_chars_per_knowledge_chunk") or 0),
                prohibited_actions=PROHIBITED_ACTIONS.get(node["id"], ()),
            )
        )
        mapped = AgentNodeResultMapper.to_state(node["id"], delegated.output, state)
        delegated_runs = dict(state.get("delegated_agent_runs") or {})
        delegated_runs[node["id"]] = delegated.delegated_agent_run_id
        return {**mapped, "delegated_agent_runs": delegated_runs}


__all__ = ["AgentNodeHandler", "AgentNodeResultMapper", "PROHIBITED_ACTIONS"]
