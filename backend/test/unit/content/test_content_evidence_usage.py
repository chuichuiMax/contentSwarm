from yuxi.content.evidence_usage import build_evidence_usage_snapshot


def test_build_evidence_usage_snapshot_tracks_only_final_title_and_body_references():
    snapshot = build_evidence_usage_snapshot(
        selected_title={"evidence_ids": ["ev-title", "ev-shared"]},
        content_draft={
            "paragraph_evidence": [
                {"paragraph_id": "intro", "evidence_ids": ["ev-shared", "ev-body-1"]},
                {"paragraph_id": "result", "evidence_ids": ["ev-body-2"]},
            ]
        },
    )

    assert snapshot == {
        "version": 1,
        "items": [
            {"evidence_id": "ev-title", "usages": [{"target": "title", "location": "标题"}]},
            {
                "evidence_id": "ev-shared",
                "usages": [
                    {"target": "title", "location": "标题"},
                    {"target": "body", "location": "正文第1段", "paragraph_id": "intro"},
                ],
            },
            {
                "evidence_id": "ev-body-1",
                "usages": [{"target": "body", "location": "正文第1段", "paragraph_id": "intro"}],
            },
            {
                "evidence_id": "ev-body-2",
                "usages": [{"target": "body", "location": "正文第2段", "paragraph_id": "result"}],
            },
        ],
    }


def test_build_evidence_usage_snapshot_deduplicates_repeated_usage():
    snapshot = build_evidence_usage_snapshot(
        selected_title={"evidence_ids": ["ev-1", "ev-1"]},
        content_draft={
            "paragraph_evidence": [
                {"paragraph_id": "p1", "evidence_ids": ["ev-1", "ev-1"]},
            ]
        },
    )

    assert snapshot["items"] == [
        {
            "evidence_id": "ev-1",
            "usages": [
                {"target": "title", "location": "标题"},
                {"target": "body", "location": "正文第1段", "paragraph_id": "p1"},
            ],
        }
    ]
