"""原文和原始业务数据直接写作；标题与正文的存储适配在模型返回后进行。"""

import re
from copy import deepcopy

TOPIC_INSTRUCTION = (
    "发布话题要求：根据本篇标题、正文和原始业务 JSON 生成恰好 10 个不重复、与内容相关的话题标签，"
    "每个标签为 1～20 个字符，不含空格；不得编造地点、数字或服务承诺，不使用固定标签凑数。"
    "在正文末尾另起一行，按 #标签 #标签 的格式输出全部标签，不把标签混入正文段落。"
)


def topic_validation_checks(topics: list[str]) -> list[dict]:
    """原文仿写也必须满足当前发布接口的十个有效话题要求。"""
    issues = []
    if len(topics) != 10:
        issues.append(("TOPIC_COUNT_MISMATCH", f"话题必须恰好 10 个，当前为 {len(topics)} 个"))
    if len(topics) != len(set(topics)):
        issues.append(("TOPIC_DUPLICATED", "话题存在重复项"))
    if any(not 1 <= len(topic) <= 20 or re.search(r"[#\s]", topic) for topic in topics):
        issues.append(("TOPIC_FORMAT_INVALID", "单个话题必须为 1～20 个字符，且不含空格或 #"))
    return [
        {"code": code, "level": "error", "location": "topics", "message": message, "evidence_ids": []}
        for code, message in issues
    ]


def is_raw_reference(pack: dict) -> bool:
    return (pack.get("content_rule_bundle", {}).get("single_blueprint") or {}).get(
        "writing_mode"
    ) == "raw_reference_text"


def project_input(payload: dict) -> dict:
    pack = payload["production_pack"]
    reference = pack["reference_snapshot"]
    if not reference.get("body"):
        raise ValueError("直接仿写缺少完整爆款原文")
    business = payload.get("raw_business_json")
    if not isinstance(business, dict) or not business:
        raise ValueError("直接仿写缺少原始业务 JSON，请重新提交业务资料")
    author = next(m for m in pack["content_rule_bundle"]["modules"] if m["slug"] == "single-blueprint-author")
    return {
        "仿写要求": f"{author['instructions']}\n\n{TOPIC_INSTRUCTION}",
        "爆款原文": {"标题": reference.get("title", ""), "正文": reference["body"]},
        "原始业务JSON": deepcopy(business),
    }


def assemble_article(text: str, pack: dict) -> dict:
    """仅分离第一行标题及文末独立话题行，保留正文措辞和报价排版。"""
    article = text.strip()
    lines = article.splitlines()
    if len(lines) < 2:
        raise ValueError("模型未返回标题及完整正文")
    title = re.sub(r"^#{1,6}\s+", "", lines[0]).strip().strip("*")
    title = re.sub(r"^标题[：:]\s*", "", title).strip()
    body = "\n".join(lines[1:]).strip()
    topics = []
    while body:
        head, separator, tail = body.rpartition("\n")
        tail = re.sub(r"^(?:话题标签|发布标签|话题|标签)[：:]\s*", "", tail.strip())
        if not re.fullmatch(r"(?:#[^#\s]+\s*#?\s*)+", tail):
            break
        topics = [topic.removesuffix("[话题]") for topic in re.findall(r"#([^#\s]+)", tail)] + topics
        body = head.rstrip() if separator else ""
    body = re.sub(r"^正文[：:]\s*", "", body).strip()
    if not title or not body:
        raise ValueError("模型未返回标题及完整正文")
    strategy = pack["strategy_snapshot"]
    return {
        "title": {
            "text": title,
            "formula_code": strategy["title_formula"]["code"],
            "evidence_ids": [],
            "lexicon_usage": [],
        },
        "outline": {"body_formula_code": strategy["body_formula"]["code"], "sections": []},
        "draft": {
            "body": body,
            "topics": topics,
            "body_formula_code": strategy["body_formula"]["code"],
            "paragraph_evidence": [],
            "lexicon_usage": [],
            "raw_model_text": text,
        },
    }
