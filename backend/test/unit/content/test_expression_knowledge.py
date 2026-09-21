import pytest

from yuxi.content.control.workflow import deterministic_node


@pytest.mark.unit
@pytest.mark.asyncio
async def test_expression_knowledge_separates_advantage_evidence_from_style_references(monkeypatch):
    from yuxi import knowledge_base

    sources = [
        {"name": "我的优势", "role": "brand_advantages", "usage": "body_evidence"},
        {"name": "表达语气库", "role": "tone_reference", "usage": "style_reference"},
        {"name": "具象表达", "role": "concrete_expression", "usage": "style_reference"},
    ]
    databases = [{"kb_id": f"kb_{index}", "name": source["name"]} for index, source in enumerate(sources, start=1)]
    queries = []

    async def get_databases_by_uid(_uid):
        return {"databases": databases}

    def make_retriever(index):
        async def retrieve(query):
            queries.append(query)
            return {
                "kb_id": f"kb_{index}",
                "results": [
                    {
                        "id": f"chunk_{index}",
                        "kb_id": f"kb_{index}",
                        "file_id": f"file_{index}",
                        "content": f"资料内容 {index}",
                        "metadata": {"source": f"资料{index}.md"},
                    }
                ],
            }

        return retrieve

    monkeypatch.setattr(knowledge_base, "get_databases_by_uid", get_databases_by_uid)
    monkeypatch.setattr(
        knowledge_base,
        "get_retrievers",
        lambda: {
            f"kb_{index}": {"name": source["name"], "retriever": make_retriever(index)}
            for index, source in enumerate(sources, start=1)
        },
    )

    async def ignore_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(deterministic_node, "append_run_stream_event", ignore_event)
    state = {
        "uid": "user-1",
        "task_id": "task-1",
        "run_id": "run-1",
        "content_brief": {"form_values": {"user_request": "长沙旧房装修报价"}},
        "strategy_snapshot": {
            "body_formula": {
                "code": "FRB05",
                "name": "工长自荐型",
                "structure_schema": ["真实工长身份", "相关人设优势", "具体行动引导"],
            },
            "title_formula": {"name": "城市身份型"},
        },
        "runtime_config_snapshot": {
            "expression_knowledge_policy": {
                "required": True,
                "sources": [
                    {**source, "query_terms": [source["name"]], "max_chunks": 2, "max_chars_per_chunk": 1200}
                    for source in sources
                ],
            }
        },
    }

    result = await deterministic_node.load_expression_knowledge(state)

    assert len(result["evidence_items"]) == 1
    advantage = result["evidence_items"][0]
    assert advantage["value"] == "资料内容 1"
    assert advantage["allowed_usage"] == ["body"]
    assert advantage["metadata"]["knowledge_base_name"] == "我的优势"
    assert advantage["metadata"]["body_formula_code"] == "FRB05"
    assert advantage["metadata"]["formula_section"] == "相关人设优势"
    assert advantage["metadata"]["persona_opening_layers"] == ["identity", "value", "evidence"]
    assert advantage["metadata"]["persona_opening_window_paragraphs"] == 2
    assert advantage["metadata"]["advantage_selection"] == {
        "min": 2,
        "max": 3,
        "match_current_pain": True,
    }
    assert "正文前两个自然段" in advantage["metadata"]["integration_instruction"]
    assert [item["role"] for item in result["expression_guidance"]["sources"]] == [
        "tone_reference",
        "concrete_expression",
    ]
    assert result["expression_guidance"]["snapshot_hash"]
    assert len(queries) == 3
    assert all("长沙旧房装修报价" in query for query in queries)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_expression_knowledge_fails_when_required_source_is_missing(monkeypatch):
    from yuxi import knowledge_base

    async def get_databases_by_uid(_uid):
        return {"databases": []}

    monkeypatch.setattr(knowledge_base, "get_databases_by_uid", get_databases_by_uid)
    monkeypatch.setattr(knowledge_base, "get_retrievers", lambda: {})
    state = {
        "uid": "user-1",
        "content_brief": {"form_values": {"user_request": "装修内容"}},
        "strategy_snapshot": {
            "body_formula": {"code": "FRB01", "structure_schema": ["相关人设优势"]},
            "title_formula": {},
        },
        "runtime_config_snapshot": {
            "expression_knowledge_policy": {
                "required": True,
                "sources": [{"name": "我的优势", "role": "brand_advantages", "usage": "body_evidence"}],
            }
        },
    }

    with pytest.raises(ValueError, match="我的优势"):
        await deterministic_node.load_expression_knowledge(state)
