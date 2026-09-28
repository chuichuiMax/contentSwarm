"""真实模型语义验收：故事四种适配及越界反例；不代表端到端性能对照。"""

import asyncio
import json
import os
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from yuxi.agents.models import load_chat_model
from yuxi.content.model.contracts.content_nodes import StandardizedContentReviewResultV1
from yuxi.content.model.materials import (
    MaterialRequirementManifestV1,
    standardize_evidence_materials,
    validate_material_gate,
)
from yuxi.content.model.single_blueprint import project_input
from yuxi.content.v3.modular_rules import build_modular_rule_bundle


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_story_fact_boundaries_with_live_reviewer():
    model_spec = os.getenv("BLUEPRINT_REVIEW_TEST_MODEL")
    if not model_spec:
        pytest.skip("需显式启用真实模型语义评测")
    from yuxi.models.providers.cache import model_cache
    from yuxi.models.providers.service import get_all_model_providers
    from yuxi.storage.postgres.manager import pg_manager

    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        model_cache.rebuild(await get_all_model_providers(db))
    await pg_manager.close()
    cases = [
        (
            "full_story",
            "本篇现场记录：我上门检查确认基层完好，和业主确认后保留原墙面。",
            "这次上门检查，基层情况完好。我和业主确认后，把原墙面保留下来了。",
            False,
            True,
        ),
        (
            "partial_story",
            "本篇现场记录：我上门测量了墙面尺寸，尚无基层检查或施工结果。",
            "这次我先上门量了墙面的尺寸。旧墙是否需要全部铲除，还得检查基层情况再决定。",
            False,
            True,
        ),
        ("no_site_facts", None, "旧房墙面是否需要全部铲掉，要先检查基层情况，不能只看表面颜色下结论。", False, True),
        ("reference_without_story", None, "我在北京做工长，工人是自己的，不往外转包。", False, False),
        (
            "reference_story_disguised_as_own",
            None,
            "这次我上门检查北京业主的旧房，确认基层完好后保留墙面，帮业主省下2000元。",
            True,
            True,
        ),
        ("tone_as_fact", None, "我在北京做工长，做事有耐心，这是业主一直认可我的原因。", True, False),
        ("team_as_personal_experience", None, "我在北京做工长，水电和泥瓦一直都是我本人亲自施工。", True, False),
        ("unsupported_client_request", None, "这套115㎡旧房，业主要求我先把预算控制住，再安排工序。", True, True),
        (
            "unsupported_site_visit_offer",
            None,
            "北京准备装修的业主，可以预约来我的在建工地参观，带报价单来现场对比。",
            True,
            False,
        ),
        (
            "supported_personal_supervision",
            "我自己对接、自己盯场、自己带队施工。",
            "我自己盯场，带队施工。",
            False,
            False,
        ),
        ("persona_after_quote", None, "我在北京做工长，工人是自己的，不往外转包。", False, False),
        (
            "internal_instruction_leak",
            None,
            "具体项目以锁定报价明细为准，当前缺少完整分项内容，发布前需要补充确认，不能凭空补写。",
            True,
            False,
        ),
        (
            "generic_padding",
            None,
            "看清项目范围，预算更清楚。人工辅材分开看，预算更直观。结合自家户型核对范围，心里更有数。",
            True,
            False,
        ),
        ("false_management_adoption", None, "这份报价可以参考。", True, False),
        (
            "reader_question_without_client_quote",
            None,
            "看旧房报价时，会不会担心后面还有加项？先区分按面积计价和按项计价，单位不同，单价数字不能直接横比。",
            False,
            False,
        ),
    ]
    replay_cases = {}
    if replay_path := os.getenv("BLUEPRINT_REVIEW_REPLAY_CASES"):
        replay_cases = json.loads(Path(replay_path).read_text())
        cases.extend((name, None, "", item["expected_blocked"], False) for name, item in replay_cases.items())
    if selected_cases := os.getenv("BLUEPRINT_REVIEW_CASES"):
        selected = set(selected_cases.split(","))
        assert selected <= {case[0] for case in cases}
        cases = [case for case in cases if case[0] in selected]
    bundle = build_modular_rule_bundle({}, single_blueprint=True)
    instructions = next(m["instructions"] for m in bundle["modules"] if m["slug"] == "single-blueprint-reviewer")
    model = load_chat_model(model_spec, reasoning_effort="medium", timeout=120, max_retries=0)
    model = model.bind_tools([StandardizedContentReviewResultV1], tool_choice="StandardizedContentReviewResultV1")
    output = Path(os.environ["BLUEPRINT_REVIEW_TEST_OUTPUT"])
    output.mkdir(parents=True, exist_ok=True)
    limiter = asyncio.Semaphore(2)

    async def evaluate(case):
        label, site_fact, text, expected_blocked, story = case
        facts = [
            {
                "id": "F1",
                "variables": ["persona_fact"],
                "value": "北京工长，团队自有工人无转包",
                "allowed_usage": ["body"],
            }
        ]
        if site_fact:
            facts.append({"id": "F2", "variables": ["site_record"], "value": site_fact, "allowed_usage": ["body"]})
        if label == "unsupported_client_request":
            facts.append({"id": "F3", "variables": ["quantity"], "value": "115㎡旧房报价", "allowed_usage": ["body"]})
        refs = {"R1": "身份与团队特点"}
        if story:
            refs["R2"] = "上门检查旧墙，依据基层状况决定是否保留墙面，说明结果"
        if label == "unsupported_site_visit_offer":
            refs["R2"] = "邀请本地业主预约参观在建工地、携报价单实地对比"
        blocks = [
            {
                "id": "b1",
                "kind": "text",
                "text": "我在北京做工长，工人是自己的，不往外转包。",
                "facts": ["F1"],
                "blueprint_refs": ["R1"],
            },
            {
                "id": "b2",
                "kind": "text",
                "text": text,
                "facts": ["F2"] if site_fact else [],
                "blueprint_refs": ["R2"] if story else [],
            },
        ]
        if label == "reference_without_story":
            blocks = blocks[:1]
        if label == "persona_after_quote":
            blocks = [
                {"id": "quote_block", "kind": "quote_ref", "text": "拆除：40元/㎡", "facts": [], "blueprint_refs": []},
                blocks[0],
            ]
        payload = {
            "stage": "review",
            "facts": facts,
            "tone": "有耐心",
            "requirements": ["真实身份与有据服务特点"],
            "reference": {"blocks": refs, "style": {}, "slot_mapping": {}},
            "quote": {"id": "quote_block"} if label == "persona_after_quote" else None,
            "required_review_codes": [
                "PERSONA_OPENING",
                "PERSONA_GROUNDING",
                "COMPOSITION_ALIGNMENT",
                "NATURAL_EXPRESSION",
            ],
            "draft": {
                "title": {"text": "北京旧房墙面处理", "facts": []},
                "topics": [],
                "blocks": blocks,
                "omissions": [],
            },
            "deterministic_checks": {"status": "passed", "checks": []},
        }
        if label in replay_cases:
            payload = replay_cases[label]["input"]
        elif site_fact:
            # 正例经过真实物料标准化、审核绑定和模型投影，避免手造 F2 掩盖上游丢料。
            manifest = MaterialRequirementManifestV1.model_validate(
                {
                    "order_hash": "o" * 64,
                    "manifest_hash": "m" * 64,
                    "content_type_code": "CT03",
                    "title_formula_code": "T",
                    "body_formula_code": "B",
                    "reference_required": True,
                    "requirements": [
                        {
                            "requirement_id": f"variable:{code}",
                            "variable_code": code,
                            "material_types": ["business_fact"],
                            "value_type": "string",
                            "required": False,
                            "allowed_sources": ["manual_input"],
                            "allowed_usage": ["body"],
                            "review_policy": "user_confirmed",
                            "risk_level": "normal",
                            "fallback_policy": "block",
                        }
                        for code in ("persona_fact", "result")
                    ],
                }
            )
            evidence = {
                "items": [
                    {
                        "id": f"e{i}",
                        "variable_codes": [code],
                        "value": value,
                        "source_type": "manual_input",
                        "source_id": f"record-{i}",
                        "source_version": "1",
                        "verified_status": "user_confirmed",
                        "allowed_usage": ["body"],
                    }
                    for i, (code, value) in enumerate((("persona_fact", facts[0]["value"]), ("result", site_fact)), 1)
                ]
            }
            materials = standardize_evidence_materials(evidence_bundle=evidence, manifest=manifest)
            report = validate_material_gate(manifest=manifest, materials=materials)
            assert report.status == "passed"
            pack = {
                "materials": [m.model_dump(mode="json") for m in materials],
                "material_manifest": manifest.model_dump(mode="json"),
                "material_quality_report": report.model_dump(mode="json"),
                "content_rule_bundle": bundle,
                "strategy_snapshot": {"content_direction": "CT03", "title_formula": {}, "body_formula": {"code": "B"}},
                "reference_snapshot": {"reference_blueprint": {"content_block_sequence": list(refs.values())}},
                "channel_profile": {"title_constraints": {}, "body_constraints": {}},
            }
            view = project_input({"production_pack": pack, "evidence_bundle": evidence, "content_brief": {}})
            payload["facts"] = view["facts"]
            assert any(f["value"] == site_fact for f in payload["facts"])
        if label in {"generic_padding", "false_management_adoption", "reader_question_without_client_quote"}:
            payload["required_review_codes"].append("BODY_VALUE")
            payload["reference"]["blocks"]["R2"] = "提出读者问题并解释判断依据"
            payload["draft"]["blocks"][1]["blueprint_refs"] = ["R2"]
        if label == "false_management_adoption":
            payload["reference"]["blocks"]["R2"] = "现场管理做法"
            payload["draft"]["blocks"][1].update(
                kind="quote_ref", id="quote_block", text="拆除：40元/㎡；水电：50元/㎡"
            )
            payload["quote"] = {"id": "quote_block"}
        async with limiter:
            response = None
            async for chunk in model.astream(
                [SystemMessage(content=instructions), HumanMessage(content=json.dumps(payload, ensure_ascii=False))]
            ):
                response = chunk if response is None else response + chunk
        assert response is not None
        assert len(response.tool_calls) == 1
        report = StandardizedContentReviewResultV1.model_validate(response.tool_calls[0]["args"]).model_dump(
            mode="json"
        )
        record = {
            "case": label,
            "expected_blocked": expected_blocked,
            "input": payload,
            "review": report,
            "usage": response.usage_metadata,
            "rules_hash": bundle["bundle_hash"],
        }
        (output / f"{label}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        return record

    records = await asyncio.gather(*(evaluate(case) for case in cases))
    (output / "summary.json").write_text(json.dumps(records, ensure_ascii=False, indent=2))
    assert all((r["review"]["status"] == "blocked") == r["expected_blocked"] for r in records), [
        (r["case"], r["review"]) for r in records if (r["review"]["status"] == "blocked") != r["expected_blocked"]
    ]
