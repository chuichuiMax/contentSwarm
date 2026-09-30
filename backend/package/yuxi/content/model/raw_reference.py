"""原文和原始业务数据直接写作；标题与正文的存储适配在模型返回后进行。"""

import json
import re
from copy import deepcopy

TOPIC_INSTRUCTION = (
    "发布话题要求：根据本篇标题、正文和原始业务 JSON 生成恰好 10 个不重复、与内容相关的话题标签，"
    "每个标签为 1～20 个字符，不含空格；不得编造地点、数字或服务承诺，不使用固定标签凑数。"
    "在正文末尾另起一行，按 #标签 #标签 的格式输出全部标签，不把标签混入正文段落。"
)

PLAN_INSTRUCTION = (
    '输出格式：先输出一个 JSON 对象，格式为 {"method":"选定手法","tone":"对应固定语气",'
    '"creative_additions":["创作补充的原文片段，不带句末标点"]}，不得使用代码围栏。'
    "method 和 tone 必须是正文 Skill 表中的唯一配对。creative_additions 逐字登记你补写的经历、反馈、"
    "对比价格及节省金额所在片段，文字须与接下来标题或正文完全一致；只登记含新增内容的片段。"
    "没有补写则用空数组。JSON 对象结束后换行输出标题、完整正文及文末话题。"
    "元数据由程序单独保存；发布正文只讲故事，不出现手法、语气、创作补充等内部说明。"
)


def has_writing_plan(pack: dict) -> bool:
    return (pack.get("content_rule_bundle", {}).get("single_blueprint") or {}).get(
        "article_output_format"
    ) == "planned_article_text_v1"


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
    view = {
        "仿写要求": f"{author['instructions']}\n\n{TOPIC_INSTRUCTION}",
        "爆款原文": {"标题": reference.get("title", ""), "正文": reference["body"]},
        "原始业务JSON": deepcopy(business),
        "审核规则": build_review_rules(payload),
    }
    # 历史任务没有正文 Skill 快照，继续使用当时冻结的要求，不读取当前磁盘规则。
    body_author = next((m for m in pack["content_rule_bundle"]["modules"] if m["slug"] == "viral-body-author"), None)
    if body_author:
        view["仿写要求"] = f"{author['instructions']}\n\n{body_author['instructions']}\n\n{TOPIC_INSTRUCTION}"
        if pack.get("writing_request"):
            view["本篇写作要求"] = pack["writing_request"]
    if has_writing_plan(pack):
        view["仿写要求"] += f"\n\n{PLAN_INSTRUCTION}"
    return view


def assemble_article(text: str, pack: dict) -> dict:
    """分离内部创作记录、标题和标签；历史任务保持原纯文本契约。"""
    article = text.strip()
    plan = None
    if has_writing_plan(pack):
        try:
            plan, end = json.JSONDecoder().raw_decode(article)
        except json.JSONDecodeError as exc:
            raise ValueError("缺少有效的手法、语气与创作补充记录") from exc
        if not isinstance(plan, dict):
            raise ValueError("缺少有效的手法、语气与创作补充记录")
        article = article[end:].strip()
        styles = pack["content_rule_bundle"]["runtime_rules"]["viral-body-author"]["writing_styles"]
        method, tone = plan.get("method"), plan.get("tone")
        if not isinstance(method, str) or method not in styles or styles[method] != tone:
            raise ValueError("写作手法与语气必须是冻结规则中的唯一配对")
        additions = plan.get("creative_additions")
        if not isinstance(additions, list) or any(not isinstance(s, str) or not s.strip() for s in additions):
            raise ValueError("创作补充必须是原文片段列表")
        # 句末标点承担段落衔接，不属于用于核对文字和数字的创作片段。
        plan["creative_additions"] = [s.strip().rstrip("。！？；：.!?;:") for s in additions]
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
    metadata = {}
    if plan is not None:
        if any(not s or (s not in title and s not in body) for s in plan["creative_additions"]):
            raise ValueError("创作补充记录与成稿不一致")
        metadata = {
            "writing_choice": {"method": plan["method"], "tone": plan["tone"]},
            "creative_additions": plan["creative_additions"],
        }
    strategy = pack["strategy_snapshot"]
    return {
        "title": {
            "text": title,
            "formula_code": strategy["title_formula"]["code"],
            "evidence_ids": [],
            "lexicon_usage": [],
        },
        "outline": {"body_formula_code": strategy["body_formula"]["code"], "sections": [], **deepcopy(metadata)},
        "draft": {
            "body": body,
            "topics": topics,
            "body_formula_code": strategy["body_formula"]["code"],
            "paragraph_evidence": [],
            "lexicon_usage": [],
            "raw_model_text": text,
            **metadata,
        },
    }