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


def _extract_trailing_topics(body: str) -> tuple[str, list[str]]:
    """从正文末尾剥离话题行或行尾话题标签。"""

    text = body.strip()
    topics: list[str] = []
    while text:
        head, separator, tail = text.rpartition("\n")
        if not re.fullmatch(r"(?:#[^#\s]+\s*)+", tail.strip()):
            break
        topics = re.findall(r"#([^#\s]+)", tail) + topics
        text = head.rstrip() if separator else ""
    if topics:
        return text, topics
    # 兼容最后一行正文后紧跟话题：……聊聊。 #北京装修 #旧房改造
    lines = text.splitlines()
    if not lines:
        return text, []
    last = lines[-1]
    matched = re.search(r"(?:(?:^|\s)#[^#\s]+)+\s*$", last)
    if matched is None:
        return text, []
    topics = re.findall(r"#([^#\s]+)", matched.group(0))
    remainder = last[: matched.start()].rstrip()
    if remainder:
        lines[-1] = remainder
    else:
        lines = lines[:-1]
    return "\n".join(lines).strip(), topics


def build_review_rules(payload: dict) -> dict:
    """输出原文仿写侧全部审核规则，供模型自觉遵守；程序侧不再硬拦。"""

    pack = payload["production_pack"]
    bundle = pack["content_rule_bundle"]
    policy = bundle.get("single_blueprint") or {}
    platform = (bundle.get("runtime_rules") or {}).get("viral-platform-expression") or {}
    lexicon = platform.get("forbidden_lexicon") or {}
    brief = payload.get("content_brief") or {}
    alternatives = lexicon.get("alternatives")
    if alternatives is None:
        replacements = platform.get("forbidden_replacements") or {}
        alternatives = {term: [value] for term, value in replacements.items() if value}
    reference_body = str((pack.get("reference_snapshot") or {}).get("body") or "")
    _, reference_topics = _extract_trailing_topics(reference_body)
    return {
        "程序硬拦": "已关闭；下列规则仅供写作与后处理参考，不因命中而阻断任务",
        "叙述边界": policy.get("narrative_policy") or "",
        "布局限制": deepcopy(policy.get("layout") or {}),
        "平台封禁词及常用替换": deepcopy(alternatives or {}),
        "用户禁止表达": list(brief.get("forbidden_terms") or []),
        "用户希望包含": list(brief.get("required_terms") or []),
        "话题标签": (
            "必须在文末单独一行输出 6～10 个小红书话题，格式如 #北京装修 #旧房改造；"
            "不要写进正文段落；可参考爆款原文话题并结合本篇城市、工种、项目自然改写"
        ),
        "参考话题": reference_topics,
        "违禁词处理": (
            "写作时优先规避封禁词；若仍出现，后处理先按替换表改写，"
            "残留项再由模型按常用表达自然替换，不得改动接口数字、项目参数与报价"
        ),
    }


def project_input(payload: dict) -> dict:
    pack = payload["production_pack"]
    reference = pack["reference_snapshot"]
    if not reference.get("body"):
        raise ValueError("直接仿写缺少完整爆款原文")
    business = payload.get("raw_business_json")
    if not isinstance(business, dict) or not business:
        raise ValueError("直接仿写缺少原始业务 JSON，请重新提交业务资料")
    author = next(m for m in pack["content_rule_bundle"]["modules"] if m["slug"] == "single-blueprint-author")
    reference_body = reference["body"]
    body_without_topics, reference_topics = _extract_trailing_topics(reference_body)
    original = {"标题": reference.get("title", ""), "正文": body_without_topics or reference_body}
    if reference_topics:
        original["话题"] = reference_topics
    return {
        "仿写要求": f"{author['instructions']}\n\n{TOPIC_INSTRUCTION}",
        "爆款原文": {"标题": reference.get("title", ""), "正文": reference["body"]},
        "原始业务JSON": deepcopy(business),
        "审核规则": build_review_rules(payload),
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
    body, topics = _extract_trailing_topics(body)
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