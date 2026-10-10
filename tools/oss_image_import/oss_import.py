"""通过生产环境素材库接口批量导入图片，并确认文件已写入 OSS。"""

from __future__ import annotations

import io
import re
from pathlib import Path

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".heif"}
MAX_IMAGE_BYTES = 100 * 1024 * 1024
MAX_EDGE = 8192
MAX_PIXELS = 40_000_000
WEBP_QUALITY = 80
BATCH_SIZE = 10
_CASE_FOLDER_NAME = re.compile(
    r"^(?P<building>.+?)[\s\-·・｜|_]+(?P<area>\d+(?:\.\d+)?)(?:\s*(?:㎡|m2|m²))?$",
    re.IGNORECASE,
)
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


def group_images_by_folder(folder: Path) -> tuple[list[tuple[str, list[Path]]], list[str]]:
    """一级目录名对应图库下要新建的文件夹；目录里直接放的图片归到所选文件夹名。"""
    images, skipped = collect_images(folder)
    grouped: dict[str, list[Path]] = {}
    for path in images:
        relative = path.relative_to(folder)
        name = relative.parts[0] if len(relative.parts) > 1 else folder.name
        grouped.setdefault(name, []).append(path)
    return [(name, grouped[name]) for name in sorted(grouped)], skipped


def child_needs_project_details(category: dict) -> bool:
    if category.get("parent_id"):
        return False
    if category.get("industry_slug") == "decoration":
        return True
    return category.get("visibility") == "enterprise" and (
        category.get("image_design_role") == "reference" or category.get("name") == "案例图库"
    )


def case_project_fields(folder_name: str) -> tuple[str | None, str | None]:
    """名称能识别出楼盘和面积时附带写入；识别不了也按原名建文件夹。"""
    match = _CASE_FOLDER_NAME.fullmatch(folder_name.strip())
    building = match.group("building").strip() if match else ""
    if not match or not building:
        return None, None
    return building, match.group("area")


def encode_webp(path: Path) -> tuple[str, bytes]:
    try:
        try:
            from pillow_heif import register_heif_opener

            register_heif_opener()
        except Exception:
            pass
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source)
            image.load()
            width, height = image.size
            if width < 2 or height < 2:
                raise ImportError(f"{path.name} 尺寸过小，无法导入")
            scale = 1.0
            longest = max(width, height)
            if longest > MAX_EDGE:
                scale = min(scale, MAX_EDGE / longest)
            if width * height > MAX_PIXELS:
                scale = min(scale, (MAX_PIXELS / (width * height)) ** 0.5)
            if scale < 1:
                image = image.resize((max(2, int(width * scale)), max(2, int(height * scale))), Image.Resampling.LANCZOS)
            if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
                image = image.convert("RGBA")
            else:
                image = image.convert("RGB")
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=WEBP_QUALITY, method=4)
    except ImportError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImportError(f"{path.name} 无法转成 WebP") from exc
    data = output.getvalue()
    if len(data) > MAX_IMAGE_BYTES:
        raise ImportError(f"{path.name} 转成 WebP 后仍超过 100 MB")
    return f"{path.stem}.webp", data


def prepare_webp_files(paths: list[Path]) -> list[tuple[str, bytes]]:
    used: set[str] = set()
    prepared: list[tuple[str, bytes]] = []
    for path in paths:
        name, data = encode_webp(path)
        suffix = 2
        while name in used:
            name = f"{path.stem}-{suffix}.webp"
            suffix += 1
        used.add(name)
        prepared.append((name, data))
    return prepared


def uploadable_categories(payload: dict) -> list[dict]:
    categories = payload.get("categories") or []
    by_id = {item.get("id"): item for item in categories if item.get("id")}
    selected = [
        item
        for item in categories
        if item.get("id") and (item.get("can_upload") or (not item.get("parent_id") and item.get("can_manage")))
    ]
    selected.sort(key=lambda item: category_label(item, by_id))
    return selected


class ProductionClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.categories: list[dict] = []

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
        body = self._check(response)
        self.categories = body.get("categories") or []
        return body

    def find_child(self, parent_id: str, name: str) -> dict | None:
        for item in self.categories:
            if item.get("parent_id") == parent_id and item.get("name") == name:
                return item
        return None

    def create_child_gallery(self, parent: dict, name: str, design_style: str | None) -> dict:
        existing = self.find_child(parent["id"], name)
        if existing:
            return existing
        body = {
            "material_type": "image",
            "parent_id": parent["id"],
            "name": name,
            "description": "",
        }
        if child_needs_project_details(parent):
            if not (design_style or "").strip():
                raise ImportError("案例图库上传需要选择设计风格")
            body["design_style"] = design_style.strip()
            building_name, area = case_project_fields(name)
            if building_name:
                body["building_name"] = building_name
            if area:
                body["area"] = area
        response = self.session.post(self._url("/api/material-library/categories"), json=body, timeout=60)
        if response.status_code == 409:
            self.list_categories()
            existing = self.find_child(parent["id"], name)
            if existing:
                return existing
        created = self._check(response).get("category") or {}
        if created.get("id"):
            self.categories.append(created)
        return created

    def upload_batch(self, files: list[tuple[str, bytes]], category_id: str, design_style: str | None) -> dict:
        data = {"category": category_id}
        if design_style:
            data["design_style"] = design_style
        response = self.session.post(
            self._url("/api/material-library/images/import"),
            data=data,
            files=[("files", (name, content, "image/webp")) for name, content in files],
            timeout=600,
        )
        return self._check(response)

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
