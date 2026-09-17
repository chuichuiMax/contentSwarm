"""从本次决策输入快照提取展示信息，不修改原始评分或证据路径。"""

from yuxi.content.model.contracts.strategy import resolve_input_path


def build_decision_presentation(decision, inputs):
    candidates = inputs.get("strategy_candidates") or {}
    names = {
        section: {item["code"]: item["name"] for item in candidates.get(section, []) if item.get("name")}
        for section in ("title_formulas", "content_formulas", "methods")
    }
    names["references"] = {
        item["id"]: item["title"] for item in inputs.get("reference_candidates", []) if item.get("title")
    }
    strategy = decision["strategy"]
    assessments = [
        *strategy["title_assessments"],
        *strategy["body_assessments"],
        *strategy["method_assessments"],
        *decision["reference"]["assessments"],
    ]
    evidence = {}
    for path in dict.fromkeys(path for item in assessments for path in item["input_paths"]):
        try:
            value = resolve_input_path(inputs, path)
        except ValueError:
            # 部分旧记录没有保存完整输入；明确展示未留存，不引用当前资料冒充历史证据。
            evidence[path] = None
            continue
        parts = path.split(".")
        if len(parts) >= 3 and parts[:2] == ["evidence_bundle", "items"] and parts[2].isdigit():
            item = inputs["evidence_bundle"]["items"][int(parts[2])]
            evidence[path] = {
                "variable_codes": item.get("variable_codes") or [],
                "source_type": item.get("source_type"),
                "metadata": item.get("metadata") or {},
                "value": value,
            }
        else:
            key = parts[2] if len(parts) > 2 and parts[1] in {"form_values", "business_variables"} else parts[1]
            evidence[path] = {"key": key, "value": value}
    return {"candidate_names": names, "input_evidence": evidence}
