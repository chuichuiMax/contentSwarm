"""原文和原始业务数据直接写作；标题与正文的存储适配在模型返回后进行。"""

import re
from copy import deepcopy


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
        "仿写要求": author["instructions"],
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
        if not re.fullmatch(r"(?:#[^#\s]+\s*)+", tail.strip()):
            break
        topics = re.findall(r"#([^#\s]+)", tail) + topics
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
