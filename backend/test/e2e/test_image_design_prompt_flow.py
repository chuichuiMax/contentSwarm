from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]

WORKFLOWS = {"style_transfer", "room_adapt", "cross_space"}
TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled"}


def _load_benchmark_manifest() -> list[dict[str, Any]]:
    manifest_value = os.getenv("IMAGE_DESIGN_EVAL_MANIFEST", "").strip()
    if not manifest_value:
        pytest.skip("IMAGE_DESIGN_EVAL_MANIFEST is required for the fixed image-design benchmark.")
    manifest_path = Path(manifest_value)
    if not manifest_path.is_file():
        pytest.fail(f"Image-design benchmark manifest does not exist: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list):
        pytest.fail("Image-design benchmark manifest must contain a cases array.")
    if len(cases) != 30:
        pytest.fail(f"Image-design benchmark must contain exactly 30 cases, found {len(cases)}.")
    ids = [str(case.get("id") or "") for case in cases]
    if any(not case_id for case_id in ids) or len(set(ids)) != len(ids):
        pytest.fail("Every benchmark case must have a non-empty unique id.")
    counts = Counter(str((case.get("refinement") or {}).get("workflow") or "") for case in cases)
    if counts != Counter({workflow: 10 for workflow in WORKFLOWS}):
        pytest.fail(f"Benchmark must contain 10 cases per workflow, found {dict(counts)}.")
    return cases


async def _wait_for_job(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    job_id: str,
    *,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(
            "/api/image-design/generate/status",
            params={"job_id": job_id},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        job = response.json()["job"]
        if job["status"] in TERMINAL_JOB_STATUSES:
            return job
        await asyncio.sleep(3)
    pytest.fail(f"Image-design job {job_id} did not finish within {timeout_seconds} seconds.")


def _assert_refinement(case: dict[str, Any], refinement: dict[str, Any]) -> None:
    case_id = case["id"]
    expected = case.get("expected") or {}
    assert refinement["coverage"] == {"missing": [], "unexpected": []}, case_id
    actual_roles = [item["role"] for item in refinement["plan"]["image_roles"]]
    assert actual_roles == expected.get("roles"), case_id
    prompt = refinement["compiled_prompt"]
    for phrase in expected.get("prompt_contains") or []:
        assert phrase in prompt, f"{case_id}: compiled prompt missing {phrase!r}"
    for phrase in expected.get("prompt_excludes") or []:
        assert phrase not in prompt, f"{case_id}: compiled prompt leaked {phrase!r}"


async def test_fixed_benchmark_generates_traceable_results(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
) -> None:
    if os.getenv("IMAGE_DESIGN_EVAL_RUN", "").strip() != "1":
        pytest.skip("Set IMAGE_DESIGN_EVAL_RUN=1 to authorize the 30-case live image2 benchmark.")
    cases = _load_benchmark_manifest()
    records: list[dict[str, Any]] = []

    for case in cases:
        refine_response = await e2e_client.post(
            "/api/image-design/refinements",
            json=case["refinement"],
            headers=e2e_headers,
        )
        assert refine_response.status_code == 201, f"{case['id']}: {refine_response.text}"
        refinement = refine_response.json()["refinement"]
        _assert_refinement(case, refinement)

        render = {"aspect_ratio": "3:4", "gen_count": 1, "clarity": "1K", **(case.get("render") or {})}
        generate_response = await e2e_client.post(
            "/api/image-design/generate",
            json={"refinement_id": refinement["id"], **render},
            headers=e2e_headers,
        )
        assert generate_response.status_code == 202, f"{case['id']}: {generate_response.text}"
        records.append(
            {
                "case_id": case["id"],
                "workflow": case["refinement"]["workflow"],
                "refinement_id": refinement["id"],
                "input_fingerprint": refinement["input_fingerprint"],
                "analysis_ids": refinement["analysis_ids"],
                "plan": refinement["plan"],
                "compiled_prompt": refinement["compiled_prompts"][render["aspect_ratio"]],
                "job_id": generate_response.json()["job"]["id"],
            }
        )

    for record in records:
        job = await _wait_for_job(e2e_client, e2e_headers, record["job_id"])
        assert job["status"] == "succeeded", f"{record['case_id']}: {job.get('error_message') or job['status']}"
        assert job["result_assets"], f"{record['case_id']}: succeeded job has no result assets"
        record["result_assets"] = job["result_assets"]
        record["hard_constraints_pass"] = None
        record["workflow_reversal"] = None
        record["review_notes"] = ""

    report_path = Path(os.getenv("IMAGE_DESIGN_EVAL_REPORT", "saves/evaluations/image-design-prompt-flow.json"))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps({"cases": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def test_scored_benchmark_meets_quality_gate() -> None:
    report_value = os.getenv("IMAGE_DESIGN_EVAL_SCORED_REPORT", "").strip()
    if not report_value:
        pytest.skip("IMAGE_DESIGN_EVAL_SCORED_REPORT is required for the human-reviewed quality gate.")
    report_path = Path(report_value)
    if not report_path.is_file():
        pytest.fail(f"Scored image-design benchmark report does not exist: {report_path}")
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list) or len(cases) != 30:
        pytest.fail("Scored image-design benchmark report must contain exactly 30 cases.")
    if any(not isinstance(case.get("hard_constraints_pass"), bool) for case in cases):
        pytest.fail("Every scored benchmark case must set hard_constraints_pass to true or false.")
    if any(not isinstance(case.get("workflow_reversal"), bool) for case in cases):
        pytest.fail("Every scored benchmark case must set workflow_reversal to true or false.")

    passed = sum(case["hard_constraints_pass"] for case in cases)
    reversals = [case["case_id"] for case in cases if case["workflow_reversal"]]
    assert passed / len(cases) >= 0.9, f"Hard-constraint pass rate is {passed}/{len(cases)}, below 90%."
    assert not reversals, f"Workflow reversal detected in benchmark cases: {', '.join(reversals)}"
