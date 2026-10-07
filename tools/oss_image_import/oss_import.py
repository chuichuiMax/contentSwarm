"""通过生产环境素材库接口批量导入图片，并确认文件已写入 OSS。"""

from __future__ import annotations

import mimetypes
from pathlib import Path

import requests

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".heif"}
MAX_IMAGE_BYTES = 100 * 1024 * 1024
BATCH_SIZE = 10
DEFAULT_BASE_URL = "https://ai.hi-run.net"
DESIGN_STYLES = (
    "复合写意",
    "写意木构",
    "江南印象",
    "东方古雅",
    "轻欧简美",
    "欧美香颂",
    "欧式田园",
    "异域风情",
    "新装饰主义",
    "北欧之光",
    "意境东方",
    "雅致现代",
    "工业再造",
    "优雅缤纷",
    "极简侘寂",
    "仿生未来",
    "复古风潮",
    "艺术室界",
)


class ImportError(RuntimeError):
    pass


def api_message(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return text[:300] or f"HTTP {response.status_code}"
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, dict):
        error = detail.get("error") if isinstance(detail.get("error"), dict) else detail
        message = error.get("message") if isinstance(error, dict) else None
        if message:
            return str(message)
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    return f"HTTP {response.status_code}"


def chunks(items: list, size: int = BATCH_SIZE) -> list[list]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def collect_images(folder: Path) -> tuple[list[Path], list[str]]:
    images: list[Path] = []
    skipped: list[str] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        size = path.stat().st_size
        if size <= 0 or size > MAX_IMAGE_BYTES:
            skipped.append(f"{path.name} 超过 100MB 或为空，已跳过")
            continue
        images.append(path)
    return images, skipped


def category_label(category: dict, by_id: dict[str, dict]) -> str:
    parent = by_id.get(category.get("parent_id") or "")
    scope = "企业" if category.get("visibility") == "enterprise" else "个人"
    name = category.get("name") or category.get("id")
    if parent and parent.get("name"):
        return f"{scope} / {parent['name']} / {name}"
    return f"{scope} / {name}"


def needs_design_style(category: dict, by_id: dict[str, dict]) -> bool:
    if category.get("visibility") != "enterprise":
        return False
    parent = by_id.get(category.get("parent_id") or "")
    root = parent or category
    return root.get("image_design_role") == "reference" and not (category.get("design_style") or "").strip()


def uploadable_categories(payload: dict) -> list[dict]:
    categories = payload.get("categories") or []
    by_id = {item.get("id"): item for item in categories if item.get("id")}
    selected = [item for item in categories if item.get("can_upload") and item.get("id")]
    selected.sort(key=lambda item: category_label(item, by_id))
    return selected


class ProductionClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}{path if path.startswith('/') else '/' + path}"

    def _check(self, response: requests.Response) -> dict:
        if response.status_code >= 400:
            raise ImportError(api_message(response))
        if not response.content:
            return {}
        return response.json()

    def login(self, account: str, password: str) -> dict:
        response = self.session.post(
            self._url("/api/auth/token"),
            data={"username": account.strip(), "password": password},
            timeout=30,
        )
        body = self._check(response)
        token = body.get("access_token")
        if not token:
            raise ImportError("登录成功但没有返回访问令牌")
        self.session.headers["Authorization"] = f"Bearer {token}"
        return body

    def list_categories(self) -> dict:
        response = self.session.get(
            self._url("/api/material-library/categories"),
            params={"material_type": "image"},
            timeout=60,
        )
        return self._check(response)

    def upload_batch(self, paths: list[Path], category_id: str, design_style: str | None) -> dict:
        handles = []
        files = []
        try:
            for path in paths:
                handle = path.open("rb")
                handles.append(handle)
                mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                files.append(("files", (path.name, handle, mime)))
            data = {"category": category_id}
            if design_style:
                data["design_style"] = design_style
            response = self.session.post(
                self._url("/api/material-library/images/import"),
                data=data,
                files=files,
                timeout=600,
            )
            return self._check(response)
        finally:
            for handle in handles:
                handle.close()

    def list_items(self, category_id: str, page: int) -> dict:
        response = self.session.get(
            self._url("/api/material-library/items"),
            params={
                "material_type": "image",
                "category": category_id,
                "page": page,
                "page_size": 100,
                "sort": "newest",
            },
            timeout=60,
        )
        return self._check(response)

    def find_items(self, category_id: str, item_ids: set[str]) -> dict[str, dict]:
        found: dict[str, dict] = {}
        for page in range(1, 6):
            body = self.list_items(category_id, page)
            items = body.get("items") or []
            for item in items:
                item_id = item.get("id")
                if item_id in item_ids:
                    found[item_id] = item
            if item_ids <= found.keys() or len(items) < 100:
                break
        return found

    def read_file_header(self, item: dict) -> bytes:
        path = item.get("file_url") or f"/api/material-library/items/{item['id']}/file"
        response = self.session.get(self._url(path), stream=True, timeout=(15, 60))
        try:
            if response.status_code >= 400:
                raise ImportError(api_message(response))
            header = next(response.iter_content(12), b"")
        finally:
            response.close()
        if not _looks_like_image(header):
            raise ImportError("OSS 返回的内容不是图片")
        return header


def _looks_like_image(header: bytes) -> bool:
    return (
        header.startswith(b"\xff\xd8")
        or header.startswith(b"\x89PNG")
        or header.startswith(b"GIF8")
        or (header.startswith(b"RIFF") and len(header) >= 12 and header[8:12] == b"WEBP")
        or header.startswith(b"BM")
    )
