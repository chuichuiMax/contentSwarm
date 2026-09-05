from __future__ import annotations

import base64
import io
import os
from urllib.parse import quote, urlencode

import httpx
from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from yuxi.content_cover.schemas import HyCanvasDesignCreate
from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.repositories.content_repository import ContentRepository
from yuxi.services.content_cover_service import create_cover_asset, get_cover_asset_file
from yuxi.storage.postgres.models_business import User


class HyCanvasClient:
    def __init__(
        self,
        *,
        base_url: str,
        public_url: str,
        api_key: str,
        workspace_id: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.public_url = public_url.rstrip("/")
        self.api_key = api_key
        self.workspace_id = workspace_id
        self.transport = transport

    @classmethod
    def from_env(cls) -> HyCanvasClient:
        base_url = (os.getenv("HYCANVAS_BASE_URL") or "").strip()
        public_url = (
            os.getenv("HYCANVAS_DEV_PUBLIC_URL") or os.getenv("HYCANVAS_PUBLIC_URL") or base_url
        ).strip()
        api_key = (os.getenv("HYCANVAS_API_KEY") or "").strip()
        workspace_id = (os.getenv("HYCANVAS_WORKSPACE_ID") or "").strip()
        if not all((base_url, public_url, api_key, workspace_id)):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "hycanvas_not_configured",
                    "message": "HyCanvas 尚未配置，请设置服务地址、API Key 和工作区。",
                },
            )
        return cls(
            base_url=base_url,
            public_url=public_url,
            api_key=api_key,
            workspace_id=workspace_id,
        )

    async def list_xiaohongshu_templates(self) -> dict:
        data = await self._request("GET", "/api/v1/templates")
        templates = []
        for item in data:
            if "小红书" not in (item.get("tags") or []):
                continue
            fillable_fields = item.get("fillableFields") or []
            format_ = item.get("format") or {}
            width = float(format_.get("width") or 0)
            height = float(format_.get("height") or 0)
            if height <= 0 or abs(width / height - 0.75) > 0.02:
                continue
            preview_urls = []
            for preview_url in item.get("previewUrls") or []:
                if preview_url.startswith("/template-previews/"):
                    preview_urls.append(f"/hycanvas-template-previews/{preview_url.removeprefix('/template-previews/')}")
                else:
                    preview_urls.append(preview_url)
            if not preview_urls:
                template_id = quote(item["id"], safe="")
                preview_urls = [f"/api/content/covers/hycanvas/templates/{template_id}/render.png"]
            templates.append(
                {
                    "id": item["id"],
                    "title": item["title"],
                    "format": format_,
                    "fillable_fields": fillable_fields,
                    "preview_urls": preview_urls,
                }
            )
        return {"configured": True, "templates": templates, "total": len(templates)}

    async def create_design(
        self,
        payload: HyCanvasDesignCreate,
        *,
        image: tuple[bytes, str, str] | None = None,
        image_field_label: str | None = None,
    ) -> dict:
        images = {}
        background_image = None
        if image is not None:
            content, content_type, file_name = image
            background_image = {
                "filename": file_name,
                "contentType": content_type,
                "dataBase64": base64.b64encode(content).decode("ascii"),
            }
            if image_field_label:
                images[image_field_label] = background_image
        data = await self._request(
            "POST",
            f"/api/v1/templates/{quote(payload.template_id, safe='')}/instantiate",
            json={
                "workspaceId": self.workspace_id,
                "title": payload.title,
                "fields": payload.fields,
                "images": images,
                "backgroundImage": background_image,
            },
        )
        design_id = data["designId"]
        return {
            "design_id": design_id,
            "editor_url": f"{self.public_url}/editor/?id={quote(design_id, safe='')}",
            "render_url": f"/api/content/covers/hycanvas/designs/{quote(design_id, safe='')}/render.png",
        }

    async def create_and_bind(self, db: AsyncSession, user: User, payload: HyCanvasDesignCreate) -> dict:
        artifact = await ContentRepository(db).get_artifact_for_user(payload.artifact_id, user, for_update=True)
        if artifact is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "content_artifact_not_found", "message": "内容版本不存在。"},
            )

        image = None
        if payload.image_asset_id:
            content, content_type, file_name = await get_cover_asset_file(db, user, payload.image_asset_id)
            image = (content, content_type, file_name)
        design = await self.create_design(payload, image=image)
        png, content_type = await self.render_png(design["design_id"])
        upload = UploadFile(
            file=io.BytesIO(png),
            filename=f"hycanvas-{design['design_id']}.png",
            headers=Headers({"content-type": content_type}),
        )
        created = await create_cover_asset(
            db,
            user,
            upload,
            role="source",
            content_task_id=artifact.task_id,
        )
        asset = await ContentCoverRepository(db).get_asset_for_user(created["asset"]["id"], str(user.uid))
        if asset is None:
            raise RuntimeError("HyCanvas 导出封面保存失败")
        snapshot = {
            **design,
            "template_id": payload.template_id,
            "title": payload.title,
            "fields": payload.fields,
            "source_image_asset_id": payload.image_asset_id,
            "cover_asset_id": asset.id,
        }
        version = await ContentCoverRepository(db).bind_hycanvas_design(
            artifact=artifact,
            asset=asset,
            snapshot=snapshot,
            owner_uid=str(user.uid),
        )
        await db.commit()
        return {
            **snapshot,
            "artifact_id": artifact.id,
            "artifact_version": version.version,
            "cover_file_url": f"/api/content/covers/assets/{asset.id}/file",
            "artifact": artifact.to_dict(),
        }

    async def sync_and_bind(self, db: AsyncSession, user: User, artifact_id: str, design_id: str) -> dict:
        artifact = await ContentRepository(db).get_artifact_for_user(artifact_id, user, for_update=True)
        if artifact is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "content_artifact_not_found", "message": "内容版本不存在。"},
            )
        current_snapshot = artifact.hycanvas_design_snapshot or {}
        if current_snapshot.get("design_id") != design_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "hycanvas_design_mismatch", "message": "该 HyCanvas 设计稿未绑定到当前内容版本。"},
            )

        png, content_type = await self.render_png(design_id)
        upload = UploadFile(
            file=io.BytesIO(png),
            filename=f"hycanvas-{design_id}.png",
            headers=Headers({"content-type": content_type}),
        )
        created = await create_cover_asset(
            db,
            user,
            upload,
            role="source",
            content_task_id=artifact.task_id,
        )
        asset = await ContentCoverRepository(db).get_asset_for_user(created["asset"]["id"], str(user.uid))
        if asset is None:
            raise RuntimeError("HyCanvas 导出封面保存失败")
        snapshot = {**current_snapshot, "cover_asset_id": asset.id}
        version = await ContentCoverRepository(db).bind_hycanvas_design(
            artifact=artifact,
            asset=asset,
            snapshot=snapshot,
            owner_uid=str(user.uid),
        )
        await db.commit()
        return {
            **snapshot,
            "artifact_id": artifact.id,
            "artifact_version": version.version,
            "cover_file_url": f"/api/content/covers/assets/{asset.id}/file",
            "artifact": artifact.to_dict(),
        }

    async def create_editor_session(
        self,
        db: AsyncSession,
        user: User,
        artifact_id: str,
        design_id: str,
        return_url: str,
        return_label: str,
    ) -> dict:
        artifact = await ContentRepository(db).get_artifact_for_user(artifact_id, user)
        if artifact is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "content_artifact_not_found", "message": "内容版本不存在。"},
            )
        snapshot = artifact.hycanvas_design_snapshot or {}
        if snapshot.get("design_id") != design_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "hycanvas_design_mismatch", "message": "该 HyCanvas 设计稿未绑定到当前内容版本。"},
            )
        issued = await self._request(
            "POST",
            f"/api/v1/auth/integration-ticket/{quote(design_id, safe='')}",
        )
        next_path = "/editor/?" + urlencode(
            {
                "id": design_id,
                "returnUrl": return_url,
                "returnLabel": return_label,
            }
        )
        editor_url = f"{self.public_url}/api/v1/auth/integration?" + urlencode(
            {
                "ticket": issued["ticket"],
                "designId": design_id,
                "next": next_path,
            }
        )
        return {"editor_url": editor_url}

    async def create_workspace_session(self) -> dict:
        issued = await self._request(
            "POST",
            f"/api/v1/auth/integration-ticket/workspace/{quote(self.workspace_id, safe='')}",
        )
        editor_url = f"{self.public_url}/api/v1/auth/integration?" + urlencode(
            {
                "ticket": issued["ticket"],
                "workspaceId": self.workspace_id,
                "next": "/dashboard/",
            }
        )
        return {"editor_url": editor_url}

    async def render_png(self, design_id: str) -> tuple[bytes, str]:
        response = await self._send("GET", f"/api/v1/designs/{quote(design_id, safe='')}/render.png")
        return response.content, response.headers.get("content-type", "image/png")

    async def render_template_png(self, template_id: str) -> tuple[bytes, str]:
        response = await self._send("GET", f"/api/v1/templates/{quote(template_id, safe='')}/render.png")
        return response.content, response.headers.get("content-type", "image/png")

    async def render_template_with_background_png(
        self,
        template_id: str,
        image: tuple[bytes, str, str],
    ) -> tuple[bytes, str]:
        content, content_type, file_name = image
        response = await self._send(
            "POST",
            f"/api/v1/templates/{quote(template_id, safe='')}/preview.png",
            json={
                "backgroundImage": {
                    "filename": file_name,
                    "contentType": content_type,
                    "dataBase64": base64.b64encode(content).decode("ascii"),
                }
            },
        )
        return response.content, response.headers.get("content-type", "image/png")

    async def _request(self, method: str, path: str, **kwargs):
        response = await self._send(method, path, **kwargs)
        return response.json()

    async def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30,
            transport=self.transport,
        ) as client:
            try:
                response = await client.request(method, path, **kwargs)
                response.raise_for_status()
                return response
            except httpx.HTTPError as exc:
                upstream_status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                upstream_detail = None
                if isinstance(exc, httpx.HTTPStatusError):
                    try:
                        body = exc.response.json()
                        upstream_detail = body.get("detail") or body.get("title") or body.get("code")
                    except (ValueError, AttributeError):
                        upstream_detail = None
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "hycanvas_request_failed",
                        "message": "HyCanvas 请求失败。",
                        "upstream_status": upstream_status,
                        **({"upstream_detail": str(upstream_detail)[:300]} if upstream_detail else {}),
                    },
                ) from exc
