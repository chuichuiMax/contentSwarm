"""当家外部内容接入 API 的集成测试。

happy path 通过本地 HTTP 服务器提供图片下载（pytest 在 api 容器内运行，
API 进程与测试进程同网络命名空间，127.0.0.1 可达）；创建 run 后立即取消，
避免真实消耗 LLM 调用。
"""

from __future__ import annotations

import io
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import anyio
import pytest
from PIL import Image

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_RUN_SETTLED_STATUSES = {"completed", "failed", "cancelled", "interrupt"}


async def _wait_run_settled(test_client, headers: dict, run_id: str, timeout: float = 10.0) -> str | None:
    """等待 run 进入终态，避免删除任务时 worker 仍在收尾取消流程。"""
    for _ in range(int(timeout / 0.5)):
        response = await test_client.get(f"/api/dangjia/content/runs/{run_id}", headers=headers)
        if response.status_code == 200 and response.json()["status"] in _RUN_SETTLED_STATUSES:
            return response.json()["status"]
        await anyio.sleep(0.5)
    return None


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (48, 36), "#2f6f4f").save(output, format="PNG")
    return output.getvalue()


@pytest.fixture(scope="function")
def image_server():
    png = _png()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _image(url: str, template_id: str = "") -> dict:
    return {"templateId": template_id, "objectKey": "", "objectUrl": url}


def _payload(serial_no: str, images: list[dict], *, type_name: str = "施工报价") -> dict:
    return {
        "serialNo": serial_no,
        "persona": {
            "age": "30",
            "workYears": "5",
            "serviceCity": "长沙市",
            "introduction": "我是当家平台的装修工长，专注长沙本地整装施工。",
            "skills": ["水电改造", "泥工贴砖"],
            "honors": {"ownerRecommendCount": "10", "servedSiteCount": "12"},
            "tone": "有耐心",
            "serviceAdvantages": ["报价透明", "工地可看"],
        },
        "requirementType": {
            "typeName": type_name,
            "quotationInfo": {"houseArea": "120平", "houseType": "三室两厅"},
            "prices": [
                {"format": "半包", "content": "人工+辅材 6.8万"},
                {"format": "全包", "content": "人工+辅材+主材 12.8万"},
            ],
            "mySite": "长沙市雨花区某某小区",
        },
        "tags": ["报价透明", "长沙装修"],
        "images": images,
    }


def _fake_images(count: int, *, cover_indexes: tuple[int, ...] = (0,)) -> list[dict]:
    images = []
    for index in range(count):
        template_id = "b585947e-8413-4dd5-a7b5-d1486ec81882" if index in cover_indexes else ""
        images.append(_image(f"https://obs.example.com/img-{index}.jpg", template_id))
    return images


async def test_dangjia_endpoints_require_authentication(test_client):
    payload = _payload(f"pytest-{uuid.uuid4().hex[:12]}", _fake_images(1))
    create = await test_client.post("/api/dangjia/content/tasks", json=payload)
    assert create.status_code == 401

    random_id = uuid.uuid4().hex
    assert (await test_client.get(f"/api/dangjia/content/tasks/{random_id}")).status_code == 401
    assert (await test_client.get(f"/api/dangjia/content/runs/{random_id}")).status_code == 401


async def test_dangjia_rejects_unknown_type_name(test_client, admin_headers):
    payload = _payload(f"pytest-{uuid.uuid4().hex[:12]}", _fake_images(1), type_name="未知类型")
    response = await test_client.post("/api/dangjia/content/tasks", json=payload, headers=admin_headers)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["error"]["code"] == "DANGJIA_TYPE_NAME_UNMAPPED"


async def test_dangjia_requires_exactly_one_cover_image(test_client, admin_headers):
    no_cover = _payload(f"pytest-{uuid.uuid4().hex[:12]}", _fake_images(2, cover_indexes=()))
    response = await test_client.post("/api/dangjia/content/tasks", json=no_cover, headers=admin_headers)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["error"]["code"] == "DANGJIA_COVER_IMAGE_INVALID"

    two_covers = _payload(f"pytest-{uuid.uuid4().hex[:12]}", _fake_images(2, cover_indexes=(0, 1)))
    response = await test_client.post("/api/dangjia/content/tasks", json=two_covers, headers=admin_headers)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["error"]["code"] == "DANGJIA_COVER_IMAGE_INVALID"


async def test_dangjia_rejects_unsupported_image_count(test_client, admin_headers):
    for count in (5, 7, 8):
        payload = _payload(f"pytest-{uuid.uuid4().hex[:12]}", _fake_images(count))
        response = await test_client.post("/api/dangjia/content/tasks", json=payload, headers=admin_headers)
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["error"]["code"] == "DANGJIA_IMAGE_COUNT_UNSUPPORTED"


async def test_dangjia_rejects_invalid_payload_shape(test_client, admin_headers):
    payload = _payload(f"pytest-{uuid.uuid4().hex[:12]}", [])
    response = await test_client.post("/api/dangjia/content/tasks", json=payload, headers=admin_headers)
    assert response.status_code == 422, response.text


async def test_dangjia_missing_task_and_run_return_not_found(test_client, admin_headers):
    random_id = uuid.uuid4().hex
    task_response = await test_client.get(f"/api/dangjia/content/tasks/{random_id}", headers=admin_headers)
    assert task_response.status_code == 404

    run_response = await test_client.get(f"/api/dangjia/content/runs/{random_id}", headers=admin_headers)
    assert run_response.status_code == 404


async def test_dangjia_create_compile_run_and_idempotent_replay(test_client, admin_headers, image_server):
    templates_response = await test_client.get("/api/content/covers/hycanvas/templates", headers=admin_headers)
    assert templates_response.status_code == 200, templates_response.text
    templates = templates_response.json().get("templates") or []
    if not templates:
        pytest.skip("HyCanvas 未配置可用的小红书模板")
    template_id = templates[0]["id"]

    serial_no = f"pytest-{uuid.uuid4().hex[:12]}"
    images = [
        _image(f"{image_server}/cover.png", template_id),
        _image(f"{image_server}/site-1.png"),
        _image(f"{image_server}/site-2.png"),
    ]
    payload = _payload(serial_no, images)

    task_id = None
    material_item_ids: list[str] = []
    try:
        created = await test_client.post("/api/dangjia/content/tasks", json=payload, headers=admin_headers)
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["serial_no"] == serial_no
        assert body["idempotent"] is False
        assert body["task_id"]
        assert body["run_id"]
        task_id = body["task_id"]
        run_id = body["run_id"]

        # 立即取消 run，避免 worker 真实执行 LLM 节点
        cancelled = await test_client.post(f"/api/content/runs/{run_id}/cancel", headers=admin_headers)
        assert cancelled.status_code == 200, cancelled.text
        await _wait_run_settled(test_client, admin_headers, run_id)

        task_response = await test_client.get(f"/api/content/tasks/{task_id}", headers=admin_headers)
        assert task_response.status_code == 200, task_response.text
        task = task_response.json()["task"]
        form_values = task["brief"]["form_values"]
        assert form_values["external_serial_no"] == serial_no
        assert form_values["external_source"] == "dangjia"
        assert form_values["brand_name"] == "长沙市装修工长"
        assert form_values["audience"] == ["长沙市准备装修三室两厅的业主"]
        assert form_values["pain"] == ["想搞清楚120平三室两厅的施工报价明细"]
        assert form_values["project_type"] == "三室两厅"
        assert form_values["area"] == "120平"
        assert form_values["budget"] == "【半包】人工+辅材 6.8万\n【全包】人工+辅材+主材 12.8万"
        assert "水电改造" in form_values["craft_and_materials"]
        assert form_values["project_site"] == "长沙市雨花区某某小区"
        assert form_values["content_tags"] == ["报价透明", "长沙装修"]
        assert form_values["type_name"] == "施工报价"
        assert task["brief"]["persona"]["description"].startswith("30岁，5年装修工龄，服务城市长沙市。")

        visual = task["brief"]["visual_material"]
        assert visual["hycanvas_template_id"] == template_id
        assert visual["photo_composition"]["layout_id"] == "grid-3"
        assert len(visual["photo_composition"]["slots"]) == 3
        assert visual["photo_composition"]["slots"][0]["image_item_id"] == visual["image_item_id"]
        material_item_ids = [slot["image_item_id"] for slot in visual["photo_composition"]["slots"]]

        dangjia_task = await test_client.get(f"/api/dangjia/content/tasks/{task_id}", headers=admin_headers)
        assert dangjia_task.status_code == 200, dangjia_task.text
        assert dangjia_task.json()["serial_no"] == serial_no
        assert dangjia_task.json()["latest_run_id"] == run_id
        assert dangjia_task.json()["artifact"] is None

        dangjia_run = await test_client.get(f"/api/dangjia/content/runs/{run_id}", headers=admin_headers)
        assert dangjia_run.status_code == 200, dangjia_run.text
        assert dangjia_run.json()["run_id"] == run_id
        assert dangjia_run.json()["task_id"] == task_id
        assert dangjia_run.json()["status"]

        replayed = await test_client.post("/api/dangjia/content/tasks", json=payload, headers=admin_headers)
        assert replayed.status_code == 200, replayed.text
        replay_body = replayed.json()
        assert replay_body["idempotent"] is True
        assert replay_body["task_id"] == task_id
        assert replay_body["serial_no"] == serial_no
        assert replay_body["run_id"] == run_id
    finally:
        if task_id:
            deleted = await test_client.delete(f"/api/content/tasks/{task_id}", headers=admin_headers)
            assert deleted.status_code == 200, deleted.text
        for item_id in material_item_ids:
            await test_client.delete(f"/api/material-library/items/{item_id}", headers=admin_headers)
