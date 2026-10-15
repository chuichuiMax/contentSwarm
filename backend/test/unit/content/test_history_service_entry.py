from yuxi.content.mp_workflow import history_service_entry


def test_history_service_entry_keeps_review_notes():
    brief = {"form_values": {"mp_service_entry": "好评笔记"}}
    assert history_service_entry(brief) == "好评笔记"


def test_history_service_entry_defaults_unmarked_tasks_to_decoration():
    assert history_service_entry({}) == "装修家居"
    assert history_service_entry(None) == "装修家居"
    assert history_service_entry({"form_values": {"mp_service_entry": "装修家居"}}) == "装修家居"
    assert history_service_entry({"form_values": {"mp_service_entry": "  "}}) == "装修家居"
