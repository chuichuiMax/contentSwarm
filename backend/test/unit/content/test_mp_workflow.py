from yuxi.content.mp_workflow import (
    mp_cover_pipeline_skip_result,
    mp_skip_cover_pipeline,
    mp_skip_cover_selection,
)


def _review_notes_state() -> dict:
    return {
        "content_brief": {
            "form_values": {"mp_service_entry": "好评笔记"},
        },
    }


def test_mp_skip_cover_pipeline_for_review_notes():
    state = _review_notes_state()
    assert mp_skip_cover_pipeline(state) is True
    assert mp_skip_cover_selection(state) is True


def test_mp_cover_pipeline_skip_result_covers_visual_nodes():
    state = _review_notes_state()
    assert mp_cover_pipeline_skip_result("plan_visuals", state) == {
        "visual_plan": {"status": "skipped", "mp_skip_cover": True},
    }
    submit = mp_cover_pipeline_skip_result("submit_cover_job", state)
    assert submit["cover_job"]["status"] == "skipped"
    wait = mp_cover_pipeline_skip_result("wait_cover_job", state)
    assert wait["cover_assets"] == []
    assert wait["cover_job"]["status"] == "skipped"


def test_mp_cover_pipeline_skip_ignored_for_decoration():
    state = {
        "content_brief": {
            "form_values": {"mp_service_entry": "装修家居"},
        },
    }
    assert mp_skip_cover_pipeline(state) is False
    assert mp_cover_pipeline_skip_result("plan_visuals", state) is None
