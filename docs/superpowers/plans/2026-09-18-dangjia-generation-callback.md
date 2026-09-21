# Dangjia Generation Callback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Notify Dangjia exactly once from the existing content ARQ worker when an accepted Dangjia content run reaches success, failure, cancellation, or review-blocked termination.

**Architecture:** Add a focused outbound callback service that loads the committed Dangjia task/artifact/cover, builds the agreed JSON contract with a permanent public media URL backed by the existing private MinIO object, and performs one bounded `httpx` request. The existing content worker invokes it only after committing a terminal run state; the callback boundary catches and logs all delivery errors so generation status and ARQ retry behavior remain unchanged.

**Tech Stack:** Python 3.12+, FastAPI/Pydantic, SQLAlchemy async sessions, HTTPX, MinIO, ARQ, pytest.

**Spec:** `docs/vibe/2026-09-18-dangjia-generation-callback.md`

## Global Constraints

- Do not add an Outbox table, callback queue, new ARQ job, independent worker, automatic retry, compensation, or manual resend API.
- `images` is `string[]`; successful callbacks contain exactly one permanent media URL at `images[0]`.
- Failure callbacks omit success-only fields.
- Commit the local terminal state before calling Dangjia.
- Callback failures are logged and swallowed; they must not alter local task/run status or retry the content run.
- Never log or commit the API key, title, or body.
- Keep existing polling endpoints for diagnostics.

---

### Task 1: Callback Contract and Delivery Service

**Files:**
- Create: `backend/package/yuxi/services/dangjia_callback_service.py`
- Create: `backend/test/unit/content/test_dangjia_callback_service.py`
- Modify: `backend/package/yuxi/services/dangjia_service.py`
- Modify: `backend/test/unit/content/test_dangjia_service.py`

**Interfaces:**
- Produces: `async def notify_dangjia_content_result(*, task_id: str, run_id: str, terminal_status: Literal["completed", "failed", "cancelled"]) -> None`
- Reads: `ContentTask.brief_json`, `ContentArtifact`, `ContentCoverAsset`, `DANGJIA_CALLBACK_BASE_URL`, `DANGJIA_CALLBACK_API_KEY`, and `DANGJIA_MEDIA_PUBLIC_BASE_URL`.
- Calls: the public media route backed by `get_minio_client().adownload_file(...)` and one `httpx.AsyncClient.post(...)`.

- [x] **Step 1: Write failing callback contract tests**

Add tests that exercise the real payload-building behavior with literal expectations:

```python
def test_success_payload_uses_original_serial_and_cover_download_url():
    payload = build_success_payload(
        serial_no="202609141600",
        title="标题",
        body="正文",
        keywords=["旧房改造"],
        image_url="https://files.example.com/content-covers/cover.png?signature=test",
    )
    assert payload == {
        "serialNo": "202609141600",
        "generationStatus": 30,
        "title": "标题",
        "body": "正文",
        "keywords": ["旧房改造"],
        "images": ["https://files.example.com/content-covers/cover.png?signature=test"],
    }
```

Add table-driven failure assertions for generated failure, cancellation, and review blocking. Add an async test proving a non-Dangjia task performs no MinIO download and no HTTP request. Add an async test proving HTTP errors do not escape `notify_dangjia_content_result` and do not cause a second request.

- [x] **Step 2: Run the new tests and verify RED**

Run:

```bash
docker compose exec api uv run --group test pytest test/unit/content/test_dangjia_callback_service.py -q
```

Expected: collection/import failure because `dangjia_callback_service` does not exist.

- [x] **Step 3: Write minimal callback service implementation**

Implement:

```python
def build_success_payload(*, serial_no: str, title: str, body: str, keywords: list[str], image_url: str) -> dict:
    return {
        "serialNo": serial_no,
        "generationStatus": 30,
        "title": title,
        "body": body,
        "keywords": keywords,
        "images": [image_url],
    }
```

Load the task and committed artifact inside a short database session, close that session before HTTP I/O, and select failure reason from the terminal status plus `task.status == "review_blocked"`. Require a reviewed artifact and final cover asset for success. Build a permanent URL from the configured media base and cover asset ID; the public route verifies that the asset is the current final cover of a Dangjia task before returning bytes from private MinIO. Join the configured callback base URL with `/v1/callback/ai/gen/result/notify`; send one request with `Content-Type` and `x-api-key`; accept only HTTP 200 plus JSON `code == "200"`. Catch every configuration, media URL, network, response, and ACK error at the public function boundary and emit a sanitized structured log.

Change `DangjiaContentCreate.serialNo` from `max_length=64` to `max_length=32` so an accepted serial can always satisfy the callback contract.

- [x] **Step 4: Run callback and request-validation tests and verify GREEN**

Run:

```bash
docker compose exec api uv run --group test pytest \
  test/unit/content/test_dangjia_callback_service.py \
  test/unit/content/test_dangjia_service.py -q
```

Expected: all selected tests pass.

### Task 2: Invoke Callback After Terminal Commit

**Files:**
- Modify: `backend/package/yuxi/services/content_run_worker.py`
- Modify: `backend/test/unit/content/test_worker_retry.py`

**Interfaces:**
- Consumes: `notify_dangjia_content_result(task_id=..., run_id=..., terminal_status=...)` from Task 1.
- Produces: one callback attempt after each applicable final worker outcome; retryable intermediate exceptions do not call it.

- [x] **Step 1: Write failing terminal-hook tests**

Extend worker tests with a notifier fake that records `(task_id, run_id, terminal_status)`. Assert:

```python
assert notifications == [(task.id, run.id, "completed")]
```

for normal graph completion, `"failed"` only after the final ARQ attempt, and `"cancelled"` for explicit cancellation. Assert the existing first-attempt retry test records no notification.

- [x] **Step 2: Run focused worker tests and verify RED**

Run:

```bash
docker compose exec api uv run --group test pytest test/unit/content/test_worker_retry.py -q
```

Expected: new notification assertions fail because the worker has no callback hook.

- [x] **Step 3: Add the minimal terminal hook**

Import the callback service function and call it only after `_set_content_run_status(...)` and the final stream event have completed. Cover successful completion, final failure, explicit cancellation, unexpected worker interruption, and early configuration/legacy failures where a task is available. Do not call it when a retryable error is re-raised to ARQ or while waiting for external/human input.

- [x] **Step 4: Run focused worker tests and verify GREEN**

Run:

```bash
docker compose exec api uv run --group test pytest test/unit/content/test_worker_retry.py -q
```

Expected: all selected worker tests pass without changing existing state/event assertions.

### Task 3: Deployment Configuration, Documentation, and Regression Verification

**Files:**
- Modify: `.env.template`
- Modify: `docs/develop-guides/changelog.md`
- Modify: `docs/vibe/2026-09-18-dangjia-generation-callback.md` only if implementation details require clarification.

**Interfaces:**
- Consumes: `DANGJIA_CALLBACK_BASE_URL` and `DANGJIA_CALLBACK_API_KEY` from Task 1.
- Produces: deployable configuration placeholders without committing any secret.

- [x] **Step 1: Document environment variables**

Add commented placeholders to `.env.template`:

```dotenv
# DANGJIA_CALLBACK_BASE_URL=http://mgr.dev.dangjia.com:8001
# DANGJIA_CALLBACK_API_KEY=
# DANGJIA_MEDIA_PUBLIC_BASE_URL=https://content.example.com/api/dangjia/content/media
```

Record the one-shot callback behavior and `images[0]` permanent media URL contract in the changelog. Do not add the real API key.

- [x] **Step 2: Run relevant unit and integration regression tests**

Run:

```bash
docker compose exec api uv run --group test pytest \
  test/unit/content/test_dangjia_callback_service.py \
  test/unit/content/test_dangjia_service.py \
  test/unit/content/test_worker_retry.py \
  test/integration/api/test_dangjia_router.py -q
```

Expected: all selected tests pass.

- [x] **Step 3: Format and lint touched Python files**

Run:

```bash
docker compose exec api uv run ruff format \
  package/yuxi/services/dangjia_callback_service.py \
  package/yuxi/services/dangjia_service.py \
  package/yuxi/services/content_run_worker.py \
  test/unit/content/test_dangjia_callback_service.py \
  test/unit/content/test_dangjia_service.py \
  test/unit/content/test_worker_retry.py
docker compose exec api uv run ruff check \
  package/yuxi/services/dangjia_callback_service.py \
  package/yuxi/services/dangjia_service.py \
  package/yuxi/services/content_run_worker.py \
  test/unit/content/test_dangjia_callback_service.py \
  test/unit/content/test_dangjia_service.py \
  test/unit/content/test_worker_retry.py
```

Expected: both commands exit successfully with no lint errors.

- [x] **Step 4: Inspect the final diff and requirements checklist**

Confirm every changed production line maps to the approved callback scope, the real key is absent, no unrelated HyCanvas changes were touched, and every acceptance checkbox in the spec has either automated evidence or an explicitly documented environment dependency.
