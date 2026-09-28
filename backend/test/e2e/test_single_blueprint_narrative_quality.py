"""真实模型区分内容推进与字段拼接；含无故事正例，避免把叙事变成新公式。"""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from yuxi.agents.models import load_chat_model
from yuxi.content.model.contracts.content_nodes import StandardizedContentReviewResultV1
from yuxi.content.v3.modular_rules import build_modular_rule_bundle


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_live_reviewer_distinguishes_narrative_progress_from_padding():
    model_spec = os.getenv("BLUEPRINT_REVIEW_TEST_MODEL")
    output_dir = os.getenv("BLUEPRINT_REVIEW_TEST_OUTPUT")
    if not model_spec or not output_dir:
        pytest.skip("需配置真实模型及语义评测输出目录")

    from yuxi.models.providers.cache import model_cache
    from yuxi.models.providers.service import get_all_model_providers
    from yuxi.storage.postgres.manager import pg_manager

    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        model_cache.rebuild(await get_all_model_providers(db))
    await pg_manager.close()
    bundle = build_modular_rule_bundle({}, single_blueprint=True)
    instructions = next(m["instructions"] for m in bundle["modules"] if m["slug"] == "single-blueprint-reviewer")
    model = load_chat_model(model_spec, reasoning_effort="medium", timeout=120, max_retries=0)
    model = model.bind_tools([StandardizedContentReviewResultV1], tool_choice="StandardizedContentReviewResultV1")
    quote = "✅ 铲墙：20元/㎡；\n✅ 墙面开门洞：200元/个"
    reference = {
        "body": "翻新前最想知道钱花在哪儿？我和业主把报价单摊在桌上，从准备动的地方聊起。\n"
        "铲墙、开门洞，项目逐行列出。\n账看完了，业主接着问谁来做。我带的是自己的工人，接着聊施工安排。",
        "blocks": {"R1": "问题引入", "R2": "展示报价", "R3": "承接下一关注点"},
    }
    payload = {
        "stage": "review",
        "content_type": "CT02",
        "writing_requirements": {"request": "分享北京旧房改造项目单价", "required_terms": [], "forbidden_terms": []},
        "facts": [
            {"id": "F1", "value": "北京旧房改造", "variables": ["scene"], "allowed_usage": ["title", "body"]},
            {
                "id": "F2",
                "value": "自有工人、不转包、决策快",
                "variables": ["advantages"],
                "allowed_usage": ["body"],
            },
        ],
        "reference": reference,
        "narrative_policy": bundle["single_blueprint"]["narrative_policy"],
        "quote": {"id": "quote_block", "context": quote, "read_only": True},
        "required_review_codes": ["FACTUAL_ACCURACY", "WRITING_QUALITY"],
        "deterministic_checks": {"status": "passed", "checks": []},
    }
    # None 为冻结报价的位置；正反例使用相同事实与参考，改变的是信息组织。
    cases = {
        "repeated_reminders": (
            True,
            [
                "北京旧房改造，开工前一定要把施工范围和费用看明白。",
                None,
                "报价列出来，是为了让大家把费用核对清楚，心里更有数。",
                "准备装修的朋友，记得提前核对施工范围，费用弄清楚了才能放心。",
            ],
        ),
        "duplicate_advantages": (
            True,
            [
                "北京做旧改，钱花在哪些活上、谁来干，都是要聊的。我这边自有工人，不转包，决策快。",
                None,
                "说说我的优势：自己的工人，不往外转包，决定事情也快。",
            ],
        ),
        "decorative_scene": (
            True,
            [
                "今天在北京工地，我把本子放到桌上。旧房改造，要先把费用看清楚。",
                None,
                "本子翻到下一页，我又提醒了一遍：核对好报价，心里才能有数。",
                "合上本子，还是那句话，装修前把范围和费用弄清楚。",
            ],
        ),
        "disconnected_scene": (
            True,
            [
                "今天去北京工地的路上，想起昨晚那场球。落后的时候急得我站起来，比赛结束才坐下。",
                None,
                "自有工人，不转包。球赛就是这样，不到最后谁也说不准。",
            ],
        ),
        "unanswered_question_and_internal_rules": (
            True,
            [
                "聊北京旧房改造时，业主先问人工费落在哪些活上，我把清单摊开。",
                None,
                "他最后又看了一遍吊顶那行：‘石膏板平顶怎么只有4㎡？’"
                "我没有替报价补充未写明的范围，只提醒他按清单中的4㎡和70元/㎡核对；"
                "整套人工合计仍以11600元为准。",
            ],
        ),
        "connected_scene": (
            False,
            [
                "聊北京旧房改造时，业主指着报价单问：‘铲墙和开门洞，是不是都算一项拆除？’"
                "我把这两行指给他看，一项对应墙面处理，一项对应门洞，单子上是分别列的。",
                None,
                "看完这两行，他接着问：‘那这些活交给谁做？’我说工人是自己的，不往外转包。"
                "刚才还在聊纸上的项目，这会儿话题落到了做事的人。",
            ],
        ),
        "direct_share_without_story": (
            False,
            [
                "北京旧房改造，想查铲墙和开门洞的单价，可以直接看这两项：",
                None,
            ],
        ),
    }
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for label, (expected_blocked, paragraphs) in cases.items():
        current = deepcopy(payload)
        current_quote = quote
        if label == "unanswered_question_and_internal_rules":
            current_quote = "✅ 石膏板吊顶（平顶）：70元/㎡×4㎡=280元；\n✅ 整套人工合计：11600元"
            current["quote"]["context"] = current_quote
        if label == "direct_share_without_story":
            current["reference"] = {
                "body": "北京旧改，铲墙和开门洞的单价整理在这里：\n铲墙：20元/㎡；墙面开门洞：200元/个。",
                "blocks": {"R1": "直接点明分享对象", "R2": "展示报价"},
            }
        current["draft"] = {
            "title": {"text": "北京旧改项目单价分享", "facts": ["F1"]},
            "topics": [],
            "omissions": [],
            "blocks": [
                {
                    "id": "quote_block" if text is None else f"b{i}",
                    "kind": "quote_ref" if text is None else "text",
                    "text": current_quote if text is None else text,
                    "facts": [],
                    "blueprint_refs": ["R2" if text is None else "R1" if i == 1 else "R3"],
                }
                for i, text in enumerate(paragraphs, 1)
            ],
        }
        response = None
        async for chunk in model.astream(
            [SystemMessage(content=instructions), HumanMessage(content=json.dumps(current, ensure_ascii=False))]
        ):
            response = chunk if response is None else response + chunk
        review = StandardizedContentReviewResultV1.model_validate(response.tool_calls[0]["args"])
        records.append(
            {
                "case": label,
                "expected_blocked": expected_blocked,
                "input": current,
                "review": review.model_dump(mode="json"),
                "rules_hash": bundle["bundle_hash"],
                "usage": response.usage_metadata,
            }
        )
        (output / f"{label}.json").write_text(json.dumps(records[-1], ensure_ascii=False, indent=2))
    (output / "summary.json").write_text(json.dumps(records, ensure_ascii=False, indent=2))
    for record in records:
        checks = record["review"]["checks"]
        assert {c["code"] for c in checks} == {"FACTUAL_ACCURACY", "WRITING_QUALITY"}, record
        assert record["review"]["status"] == ("blocked" if record["expected_blocked"] else "passed"), record
        locations = {"content", "title", "topics", *(b["id"] for b in record["input"]["draft"]["blocks"])}
        assert all(c["location"] in locations for c in checks), record
        if record["expected_blocked"]:
            assert any(c["code"] == "WRITING_QUALITY" and c["status"] == "blocked" for c in checks), record
        if record["case"] == "duplicate_advantages":
            issue = next(c for c in checks if c["code"] == "WRITING_QUALITY" and c["status"] == "blocked")
            assert all(block_id in issue["message"] for block_id in ("b1", "b3")), issue
            assert issue["suggestion"], issue
