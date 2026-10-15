"""Batch PC image-design generation for enterprise shared rough-room photos.

Uses the same HTTP contract as the PC UI:
  POST /api/image-design/refinements  (style_transfer + AI 深度优化)
  POST /api/image-design/generate
  GET  /api/image-design/generate/status

Sources: enterprise 毛坯房图库 (root + secondary folders).
Saves:   enterprise secondary gallery under 生图图库 (default name 毛坯批量生图图库).

Run inside api-dev/worker-dev or any host that can reach the API:

  uv run --no-sync python scripts/batch_rough_image_design_generate.py \\
    --base-url https://ai.hi-run.net \\
    --username tiechui --password '***' \\
    --dry-run --limit 3

  uv run --no-sync python scripts/batch_rough_image_design_generate.py \\
    --base-url https://ai.hi-run.net \\
    --username tiechui --password '***' \\
    --apply --create-output-gallery
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

import httpx

APP_ROOT = Path(__file__).resolve().parents[1]
for path in (APP_ROOT, APP_ROOT / "package"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from yuxi.image_design.workflow_profiles import STYLE_PROFILES  # noqa: E402

PC_STYLE_LABELS = list(STYLE_PROFILES.keys())

# PC defaults (web/src/data/imageDesignPromptKeywords.js)
LOCKED_IMAGE_DESIGN_PROMPT_LINE = "描述你希望生成的空间风格、材质、色彩和氛围…"
DEFAULT_IMAGE_DESIGN_PROMPT = (
    "以毛坯图为唯一结构底图，保持原机位、构图、透视和画幅比例；墙、柱、梁、门窗洞口的位置、数量、尺寸、轮廓及空间遮挡关系全部不变，"
    "不增删、移动或重塑结构。参考图仅用于风格、配色、材质和灯光。所有饰面贴合原结构，全面替换毛坯材质，消除水泥、砖块、孔洞及施工纹理，"
    "覆盖远近景、侧面、转角和洞口内侧，无毛坯遗漏。结构一致优先于装饰造型，主要结构边缘与原图对齐，生成完整、自然、高清的写实装修效果图。\n"
    "移除毛坯图中的临时裸灯泡、悬吊电线及临时施工灯具，改用符合装修风格的正式照明。临时施工物品不属于需要保留的空间结构。"
)
DEFAULT_IMAGE_DESIGN_KEYWORDS = [
    "顶面地面墙面也需要装修",
    "原房杂物不要显示",
    "符合实景装修后实景图",
    "专业空间摄影构图",
    "空间合理",
    "高级质感",
]

OUTPUT_GALLERY_NAME = "毛坯批量生图图库"
ROUGH_GALLERY_NAMES = frozenset({"毛坯房图库", "毛胚房图库"})
GENERATED_GALLERY_NAMES = frozenset({"生图图库"})
TERMINAL_JOB_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


def compose_image_design_prompt(body: str, keywords: list[str]) -> str:
    selected: list[str] = []
    for item in keywords:
        keyword = str(item or "").strip()
        if keyword and keyword not in selected:
            selected.append(keyword)
    keyword_line = f"关键词：{'；'.join(selected)}" if selected else ""
    parts = [LOCKED_IMAGE_DESIGN_PROMPT_LINE, body.strip(), keyword_line]
    return "\n".join(part for part in parts if part)


def api_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return response.text[:500]
    detail = payload.get("detail")
    if isinstance(detail, dict):
        err = detail.get("error") or {}
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    if isinstance(detail, str):
        return detail
    return json.dumps(payload, ensure_ascii=False)[:500]


class ImageDesignBatchClient:
    def __init__(self, base_url: str, *, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._headers: dict[str, str] = {}

    async def login(self, username: str, password: str) -> None:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            response = await client.post(
                "/api/auth/token",
                data={"username": username, "password": password},
            )
        if response.status_code != 200:
            raise RuntimeError(f"登录失败 ({response.status_code}): {api_error_message(response)}")
        token = response.json()["access_token"]
        self._headers = {"Authorization": f"Bearer {token}"}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, headers=self._headers)

    async def bootstrap(self) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.get("/api/image-design/bootstrap")
        if response.status_code != 200:
            raise RuntimeError(f"bootstrap 失败: {api_error_message(response)}")
        payload = response.json()
        image2 = payload.get("image2") or {}
        if not image2.get("configured"):
            raise RuntimeError("image2 未配置，请先在系统里验证中转站后再批量生图")
        return payload

    async def list_galleries(self) -> list[dict[str, Any]]:
        async with self._client() as client:
            response = await client.get("/api/material-library/galleries")
        if response.status_code != 200:
            raise RuntimeError(f"读取图库失败: {api_error_message(response)}")
        return list(response.json().get("galleries") or [])

    async def create_output_gallery(self, parent_id: str, name: str) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.post(
                "/api/material-library/categories",
                json={
                    "material_type": "image",
                    "visibility": "enterprise",
                    "name": name,
                    "parent_id": parent_id,
                },
            )
        if response.status_code not in {200, 201}:
            raise RuntimeError(f"创建输出图库失败: {api_error_message(response)}")
        payload = response.json()
        category = payload.get("category")
        if not isinstance(category, dict):
            raise RuntimeError(f"创建输出图库返回异常: {payload}")
        return category

    async def list_items(self, *, category_id: str, page: int, page_size: int) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.get(
                "/api/material-library/items",
                params={
                    "material_type": "image",
                    "scope": "enterprise",
                    "category": category_id,
                    "status": "enabled",
                    "page": page,
                    "page_size": page_size,
                    "sort": "newest",
                },
            )
        if response.status_code != 200:
            raise RuntimeError(f"列出素材失败 ({category_id}): {api_error_message(response)}")
        return response.json()

    async def create_refinement(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.post("/api/image-design/refinements", json=payload)
        if response.status_code != 201:
            raise RuntimeError(f"AI 深度优化失败: {api_error_message(response)}")
        return response.json()["refinement"]

    async def generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.post("/api/image-design/generate", json=payload)
        if response.status_code != 202:
            raise RuntimeError(f"提交生图失败: {api_error_message(response)}")
        return response.json()["job"]

    async def wait_job(self, job_id: str, *, poll_seconds: float, deadline: float) -> dict[str, Any]:
        while time.monotonic() < deadline:
            async with self._client() as client:
                response = await client.get(
                    "/api/image-design/generate/status",
                    params={"job_id": job_id},
                )
            if response.status_code != 200:
                raise RuntimeError(f"查询任务失败: {api_error_message(response)}")
            job = response.json()["job"]
            if job.get("status") in TERMINAL_JOB_STATUSES:
                return job
            await asyncio.sleep(poll_seconds)
        raise TimeoutError(f"任务 {job_id} 在时限内未完成")


def find_rough_gallery_ids(galleries: list[dict[str, Any]]) -> tuple[str, list[str]]:
    rough_roots = [
        gallery
        for gallery in galleries
        if gallery.get("image_design_role") == "rough"
        or gallery.get("name") in ROUGH_GALLERY_NAMES
    ]
    if not rough_roots:
        raise RuntimeError("未找到企业共享毛坯房图库")
    if len(rough_roots) > 1:
        names = [f"{item.get('name')}({item.get('id')})" for item in rough_roots]
        raise RuntimeError(f"存在多个毛坯房图库根目录，请人工指定: {', '.join(names)}")
    root = rough_roots[0]
    root_id = str(root["id"])
    child_ids = [str(item["id"]) for item in galleries if item.get("parent_id") == root_id]
    return root_id, child_ids


def find_generated_root_id(galleries: list[dict[str, Any]]) -> str:
    candidates = [
        gallery
        for gallery in galleries
        if gallery.get("parent_id") is None
        and gallery.get("visibility") == "enterprise"
        and (
            gallery.get("image_design_role") == "generated"
            or gallery.get("name") in GENERATED_GALLERY_NAMES
        )
    ]
    if not candidates:
        raise RuntimeError("未找到企业共享生图图库")
    if len(candidates) > 1:
        raise RuntimeError("企业生图图库存在多个候选，请人工核对")
    return str(candidates[0]["id"])


def resolve_output_gallery_id(
    galleries: list[dict[str, Any]],
    *,
    generated_root_id: str,
    output_name: str,
) -> str | None:
    enterprise_named = [
        gallery
        for gallery in galleries
        if gallery.get("visibility") == "enterprise" and gallery.get("name") == output_name
    ]
    if len(enterprise_named) == 1:
        return str(enterprise_named[0]["id"])
    if len(enterprise_named) > 1:
        raise RuntimeError(f"存在多个名为 {output_name!r} 的企业图库，请人工指定 gallery_id")
    for gallery in galleries:
        if gallery.get("parent_id") == generated_root_id and gallery.get("name") == output_name:
            return str(gallery["id"])
    return None


async def collect_rough_items(
    client: ImageDesignBatchClient,
    *,
    rough_root_id: str,
    child_category_ids: list[str],
    page_size: int,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for category_id in [rough_root_id, *child_category_ids]:
        page = 1
        while True:
            payload = await client.list_items(category_id=category_id, page=page, page_size=page_size)
            batch = list(payload.get("items") or [])
            for item in batch:
                item_id = str(item.get("id") or "")
                if not item_id or item_id in seen:
                    continue
                seen.add(item_id)
                items.append(item)
            total = int(payload.get("total") or 0)
            if page * page_size >= total or not batch:
                break
            page += 1
    return items


def _resolve_style_pool(fixed_label: str | None) -> list[str]:
    if fixed_label:
        if fixed_label not in STYLE_PROFILES:
            raise RuntimeError(f"未知风格 {fixed_label!r}，可选: {', '.join(PC_STYLE_LABELS)}")
        return [fixed_label]
    return list(PC_STYLE_LABELS)


def load_completed_ids(log_path: Path) -> set[str]:
    if not log_path.is_file():
        return set()
    done: set[str] = set()
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") == "succeeded" and row.get("source_item_id"):
            done.add(str(row["source_item_id"]))
    return done


def load_completed_styles(log_path: Path) -> set[str]:
    if not log_path.is_file():
        return set()
    done: set[str] = set()
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") == "succeeded" and row.get("style_label"):
            done.add(str(row["style_label"]))
    return done


def build_work_queue(
    items: list[dict[str, Any]],
    style_pool: list[str],
    *,
    style_mode: str,
    one_per_style: bool,
    fixed_style: str | None,
    completed_items: set[str],
    completed_styles: set[str],
    rng: random.Random,
    limit: int | None,
) -> list[tuple[dict[str, Any], str]]:
    if fixed_style:
        pool = [fixed_style]
    else:
        pool = style_pool

    pending_items = [item for item in items if str(item.get("id") or "") not in completed_items]

    if one_per_style and not fixed_style:
        queue: list[tuple[dict[str, Any], str]] = []
        item_iter = iter(pending_items)
        for style in pool:
            if style in completed_styles:
                continue
            item = next(item_iter, None)
            if item is None:
                break
            queue.append((item, style))
        return queue

    queue = []
    for index, item in enumerate(pending_items):
        if limit is not None and len(queue) >= limit:
            break
        if fixed_style:
            style = fixed_style
        elif style_mode == "random":
            style = rng.choice(pool)
        else:
            style = pool[index % len(pool)]
        queue.append((item, style))
    return queue


def append_log(log_path: Path, row: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


async def run_batch(args: argparse.Namespace) -> int:
    password = args.password or os.getenv("YUXI_ADMIN_PASSWORD") or os.getenv("ADMIN_PASSWORD") or ""
    username = args.username or os.getenv("YUXI_ADMIN_USERNAME") or os.getenv("ADMIN_USERNAME") or ""
    if not username or not password:
        raise RuntimeError("请提供 --username / --password 或环境变量 YUXI_ADMIN_USERNAME / YUXI_ADMIN_PASSWORD")

    client = ImageDesignBatchClient(args.base_url, timeout=args.http_timeout)
    await client.login(username, password)
    await client.bootstrap()

    galleries = await client.list_galleries()
    rough_root_id, rough_child_ids = find_rough_gallery_ids(galleries)
    generated_root_id = find_generated_root_id(galleries)

    output_gallery_id = resolve_output_gallery_id(
        galleries,
        generated_root_id=generated_root_id,
        output_name=args.output_gallery_name,
    )
    if output_gallery_id is None:
        if not args.create_output_gallery:
            raise RuntimeError(
                f"未找到输出图库 {args.output_gallery_name!r}，请先在 PC 素材库创建，或加 --create-output-gallery（需管理员）"
            )
        if args.dry_run:
            print(f"[dry-run] 将创建二级图库 {args.output_gallery_name!r}，父级生图图库 {generated_root_id}")
            output_gallery_id = "dry-run-gallery"
        else:
            created = await client.create_output_gallery(generated_root_id, args.output_gallery_name)
            output_gallery_id = str(created["id"])
            print(f"已创建输出图库 {args.output_gallery_name}: {output_gallery_id}")

    items = await collect_rough_items(
        client,
        rough_root_id=rough_root_id,
        child_category_ids=rough_child_ids,
        page_size=args.page_size,
    )
    if args.offset:
        items = items[args.offset :]

    completed_items = load_completed_ids(args.log_file) if args.skip_done else set()
    completed_styles = load_completed_styles(args.log_file) if args.skip_done and args.one_per_style else set()
    user_prompt = compose_image_design_prompt(args.prompt_body, list(args.extra_keyword or DEFAULT_IMAGE_DESIGN_KEYWORDS))
    rng = random.Random(args.random_seed)
    style_pool = _resolve_style_pool(args.style_label)
    style_mode = "fixed" if args.style_label else args.style_mode
    work_queue = build_work_queue(
        items,
        style_pool,
        style_mode=style_mode,
        one_per_style=args.one_per_style and not args.style_label,
        fixed_style=args.style_label,
        completed_items=completed_items,
        completed_styles=completed_styles,
        rng=rng,
        limit=args.limit,
    )

    plan = {
        "rough_root_id": rough_root_id,
        "rough_child_ids": rough_child_ids,
        "output_gallery_id": output_gallery_id,
        "style_mode": style_mode,
        "one_per_style": args.one_per_style and not args.style_label,
        "style_label": args.style_label,
        "style_pool": style_pool if not args.style_label else [args.style_label],
        "random_seed": args.random_seed,
        "aspect_ratio": args.aspect_ratio,
        "clarity": args.clarity,
        "gen_count": args.gen_count,
        "rough_items_available": len(items),
        "jobs_planned": len(work_queue),
        "dry_run": args.dry_run,
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))

    failures = 0
    total = len(work_queue)
    for index, (item, style_label) in enumerate(work_queue, start=1):
        item_id = str(item["id"])
        label = item.get("name") or item.get("file_name") or item_id
        print(f"[{index}/{total}] {label} ({item_id}) · 风格 {style_label}")

        refinement_payload = {
            "workflow": "style_transfer",
            "source_material_id": item_id,
            "style_label": style_label,
            "use_prompt_as_style": False,
            "user_prompt": user_prompt,
        }
        if args.dry_run:
            append_log(
                args.log_file,
                {
                    "status": "dry_run",
                    "source_item_id": item_id,
                    "refinement_payload": refinement_payload,
                },
            )
            continue

        try:
            refinement = await client.create_refinement(refinement_payload)
            idempotency_key = hashlib.sha256(f"rough-batch:{item_id}:{refinement['input_fingerprint']}".encode()).hexdigest()
            generate_payload = {
                "refinement_id": refinement["id"],
                "aspect_ratio": args.aspect_ratio,
                "gen_count": args.gen_count,
                "clarity": args.clarity,
                "idempotency_key": idempotency_key,
                "save_target": {"scope": "enterprise", "gallery_id": output_gallery_id},
            }
            job = await client.generate(generate_payload)
            job_id = str(job["id"])
            finished = await client.wait_job(
                job_id,
                poll_seconds=args.poll_seconds,
                deadline=time.monotonic() + args.job_timeout_seconds,
            )
            row = {
                "status": finished.get("status"),
                "source_item_id": item_id,
                "source_name": label,
                "style_label": style_label,
                "refinement_id": refinement["id"],
                "job_id": job_id,
                "result_assets": finished.get("result_assets") or [],
                "error_message": finished.get("error_message"),
            }
            append_log(args.log_file, row)
            if finished.get("status") != "succeeded":
                failures += 1
                print(f"  failed: {finished.get('error_message') or finished.get('status')}")
            else:
                print(f"  succeeded: job={job_id}")
        except Exception as exc:
            failures += 1
            append_log(
                args.log_file,
                {"status": "error", "source_item_id": item_id, "source_name": label, "error": str(exc)},
            )
            print(f"  error: {exc}")
            if not args.continue_on_error:
                break

    if failures:
        print(f"完成，失败 {failures} 条，日志: {args.log_file}")
        return 1
    print(f"完成，日志: {args.log_file}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量：企业毛坯房图库 → PC 图片设计生图 → 指定生图二级图库")
    default_base = os.getenv("IMAGE_DESIGN_BATCH_BASE_URL")
    if not default_base:
        default_base = "http://127.0.0.1:5050" if os.getenv("RUNNING_IN_DOCKER") == "true" else "http://localhost:5050"
    parser.add_argument("--base-url", default=default_base)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--apply", action="store_true", help="实际提交优化与生图（默认仅 dry-run）")
    parser.add_argument("--dry-run", action="store_true", help="只列出计划（与未加 --apply 等价）")
    parser.add_argument("--create-output-gallery", action="store_true", help="缺少输出图库时自动创建（需管理员）")
    parser.add_argument("--output-gallery-name", default=OUTPUT_GALLERY_NAME)
    parser.add_argument(
        "--style-label",
        default=None,
        help="固定 PC 原房换装风格；指定后忽略轮询/每种一张",
    )
    parser.add_argument(
        "--style-mode",
        choices=["round_robin", "random"],
        default="round_robin",
        help="未固定风格时的分配方式（默认轮询）",
    )
    parser.add_argument(
        "--one-per-style",
        action="store_true",
        default=True,
        help="每种 PC 风格各生成一张（默认开启，从毛坯库依次取图）",
    )
    parser.add_argument(
        "--no-one-per-style",
        action="store_false",
        dest="one_per_style",
        help="关闭「每种风格一张」，按 --limit 与 --style-mode 批量跑",
    )
    parser.add_argument("--random-seed", type=int, default=None, help="style-mode=random 时的随机种子")
    parser.add_argument("--prompt-body", default=DEFAULT_IMAGE_DESIGN_PROMPT, help="补充描述正文（不含锁定首行）")
    parser.add_argument(
        "--extra-keyword",
        action="append",
        dest="extra_keyword",
        help="追加关键词；可多次指定。未指定时使用 PC 默认六个关键词",
    )
    parser.add_argument("--aspect-ratio", choices=["3:4", "4:3", "1:1"], default="3:4")
    parser.add_argument("--clarity", choices=["1K", "2K"], default="1K")
    parser.add_argument("--gen-count", type=int, choices=[1, 2, 4], default=1)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--job-timeout-seconds", type=float, default=3600.0)
    parser.add_argument("--http-timeout", type=float, default=900.0, help="单次 HTTP 超时（深度优化含识图较慢）")
    parser.add_argument("--log-file", type=Path, default=Path("saves/logs/rough-batch-image-design.jsonl"))
    parser.add_argument("--skip-done", action="store_true", default=True, help="跳过日志里已成功的 source_item_id")
    parser.add_argument("--no-skip-done", action="store_false", dest="skip_done")
    parser.add_argument("--continue-on-error", action="store_true", default=True)
    parser.add_argument("--halt-on-error", action="store_false", dest="continue_on_error")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not args.apply:
        args.dry_run = True
    if args.page_size < 1 or args.page_size > 100:
        raise SystemExit("--page-size 须在 1–100 之间")
    exit_code = asyncio.run(run_batch(args))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
