from yuxi.content.execution_trace import build_execution_preview, build_knowledge_result_preview


def test_execution_preview_bounds_content_and_removes_sensitive_fields():
    preview = build_execution_preview(
        {
            "title": "测试标题",
            "api_key": "should-not-appear",
            "items": list(range(10)),
            "body": "长" * 700,
        }
    )

    assert preview["title"] == "测试标题"
    assert "api_key" not in preview
    assert preview["items"][-1] == "另有 4 项"
    assert preview["body"].endswith("…")
    assert len(preview["body"]) == 601


def test_knowledge_preview_exposes_bounded_user_visible_chunks():
    preview = build_knowledge_result_preview(
        [
            {
                "id": "chunk-1",
                "file_id": "file-1",
                "content": "89㎡三居改造案例",
                "metadata": {"filename": "案例库.md", "score": 0.87},
            }
        ]
    )

    assert preview == [
        {
            "source_id": "chunk-1",
            "file_id": "file-1",
            "content": "89㎡三居改造案例",
            "score": 0.87,
            "file_name": "案例库.md",
        }
    ]
