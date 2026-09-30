"""真实模型回放自动选型与八种手法，检查资料覆盖、报价和记录，样稿另做文风复核。"""

import json
import os
import re
from copy import deepcopy
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from yuxi.agents.models import load_chat_model
from yuxi.agents.skills.buildin import BUILTIN_SKILLS
from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
from yuxi.content.model.raw_reference import assemble_article, project_input, topic_validation_checks
from yuxi.content.v3.modular_rules import build_modular_rule_bundle

SKILL = next(skill for skill in BUILTIN_SKILLS if skill.slug == "viral-body-author")
CASES = json.loads((SKILL.source_dir / "tests/cases.yaml").read_text())["cases"]
QUOTE_LINES = [
    "拆除卫生间：1000 元 / 间×2/间=2000元",
    "挖回填层（4㎡内）：450 元 / 间×2/间=900元",
    "铲墙：20 元 /㎡ ×30㎡ =600 元",
    "水电改造：1200 元 / 间×2/间=2400元",
    "马桶安装（非壁挂式）：60 元 / 个",
    "暗装淋浴花洒预埋：120 元 / 套×2套=240元",
    "泥瓦全包：2500 元 / 间×2间=5000元",
    "石膏板吊顶（平顶）：70 元 /㎡ ×10㎡ =280 元",
    "整套人工合计：12060元",
]


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
async def test_real_model_keeps_quote_and_topics_with_each_writing_method(case, tmp_path):
    model_spec = os.getenv("VIRAL_BODY_EVAL_MODEL")
    if not model_spec:
        pytest.skip("需指定可用的真实模型 VIRAL_BODY_EVAL_MODEL")
    business = {
        "serialNo": "005",
        "persona": {
            "age": "35",
            "workYears": "10",
            "serviceCity": "长沙市",
            "introduction": "干活认真负责，师傅手艺好，服务过的业主特别满意，把装修需求告诉我，给你梦想中的家",
            "skills": ["工长", "水电", "泥瓦"],
            "honors": {"ownerRecommendCount": "10", "servedSiteCount": "10"},
            "serviceAdvantages": ["装修经验丰富", "自有工人无转包"],
            "tone": "有耐心",
        },
        "requirementType": {
            "typeName": "施工报价",
            "houseInfo": {
                "mySite": "湖南省长沙市岳麓区天顶街道长房云时代",
                "houseArea": "120平",
                "houseType": "三室二厅",
            },
            "prices": [{"content": "；".join(QUOTE_LINES), "titlePrice": {"displayText": "1.206w"}}],
        },
        "tags": ["旧房改造", "水电", "现代风格"],
        **case["business_details"],
    }
    original = deepcopy(business)
    pack = {
        "reference_snapshot": {
            "title": "深圳老小区翻新，4.2w爸妈觉得值",
            "body": (
                "我是小陈，在深圳做装修十几年。这是罗湖笋岗刚完工的一套65平老两居，"
                "业主是刚结婚的小夫妻。之前问了两家装修公司，半包报价快7w了。"
                "上门后我看好多基层还能用，没必要全拆。所有项目提前列清，中途一分钱不加。"
                "拆除清运3200元，水电改造4100元，整套半包合计42800元。"
                "每天我都拍现场照片同步进度。小夫妻说比预想好看。"
                "我不搞花里胡哨的营销，活踏踏实实做，有啥问题都可以问。"
            ),
        },
        "content_rule_bundle": build_modular_rule_bundle({}, single_blueprint=True),
        "strategy_snapshot": {"title_formula": {"code": "FRT07"}, "body_formula": {"code": "FRB06"}},
        "writing_request": (f"使用{case['method']}仿写。" if case.get("method") else "AI 自行选择最合适的手法。")
        + "资料全部融入，全文保持一个组合，亲切真诚。"
        + case.get("writing_request_extra", ""),
    }
    view = project_input({"production_pack": pack, "raw_business_json": business})
    model = load_chat_model(model_spec, reasoning_effort="medium", timeout=120, max_retries=0, streaming=True)

    response = await model.ainvoke([HumanMessage(content=json.dumps(view, ensure_ascii=False))])
    output_dir = Path(os.getenv("VIRAL_BODY_EVAL_OUTPUT_DIR", str(tmp_path)))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{case['id']}-raw.txt").write_text(response.text)
    article = assemble_article(response.text, pack)
    state = {
        "production_pack": pack,
        "selected_title": article["title"],
        "content_draft": article["draft"],
        "runtime_config_snapshot": {"raw_business_json": business},
        "content_brief": {},
        "evidence_bundle": {"items": [{"value": business}]},
        "strategy_snapshot": {"creation_methods": ["FRM07"], **pack["strategy_snapshot"]},
    }
    state.update(await V3DeterministicNodeHandler._adapt_to_channel(db=None, state=state, node_run_id="style-eval"))
    validation = await V3DeterministicNodeHandler._deterministic_validate(
        db=None, state=state, node_run_id="style-eval"
    )
    (output_dir / f"{case['id']}.json").write_text(
        json.dumps(
            {
                "case": case,
                "input": view,
                "article": article,
                **validation,
                "response_metadata": response.response_metadata,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    assert business == original == view["原始业务JSON"]
    assert validation["validation_report"]["status"] in {"passed", "warning"}, validation
    choice = article["draft"]["writing_choice"]
    if case.get("method"):
        assert choice == {"method": case["method"], "tone": case["tone"]}
    assert article["outline"]["writing_choice"] == choice
    assert article["outline"]["creative_additions"] == article["draft"]["creative_additions"]
    assert topic_validation_checks(article["draft"]["topics"]) == []
    body_without_spaces = "".join(article["draft"]["body"].split())
    for line in QUOTE_LINES:
        assert "".join(line.split()) in body_without_spaces, f"原始报价被改动或遗漏：{line}"
    for information in ("35", "10年", "120平", "三室二厅", "岳麓区", "天顶街道", "长房云时代", "现代", "工长"):
        assert information in body_without_spaces, f"遗漏已有业务信息：{information}"
    assert re.search(r"10[位个次]业主|业主[^，。；\n]{0,8}10[位个次]?", body_without_spaces)
    assert re.search(r"10[个处]工地|工地[^，。；\n]{0,8}10[个处]?", body_without_spaces)
    assert "005" not in article["draft"]["body"]
    assert "创作补充" not in article["draft"]["body"]
    for reference_only in ("小陈", "深圳", "罗湖", "笋岗", "65平", "7w", "42800"):
        assert reference_only not in article["title"]["text"] + article["draft"]["body"]
