from __future__ import annotations

import os
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content_cover.image2_client import Image2Client, Image2Config, Image2Error
from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.utils.datetime_utils import utc_now_naive

IMAGE2_DEFAULT_MODEL = "gpt-image-2"


def _model(setting=None) -> str:
    return str((setting.model if setting else None) or os.getenv("IMAGE2_MODEL") or IMAGE2_DEFAULT_MODEL).strip()


def _effective_api_key(setting=None) -> str:
    return str((setting.api_key if setting else None) or os.getenv("IMAGE2_API_KEY") or "").strip()


async def resolve_image2_config(db: AsyncSession, *, owner_uid: str) -> Image2Config:
    setting = await ContentCoverRepository(db).get_image2_setting(owner_uid)
    if setting:
        return Image2Config.from_values(
            base_url=setting.base_url,
            api_key=setting.api_key,
            model=_model(setting),
        )
    return Image2Config.from_env()


async def get_image2_config_state(db: AsyncSession, *, owner_uid: str) -> dict[str, Any]:
    setting = await ContentCoverRepository(db).get_image2_setting(owner_uid)
    source = "database" if setting else "environment"
    try:
        config = await resolve_image2_config(db, owner_uid=owner_uid)
    except Image2Error:
        return {
            "configured": False,
            "base_url": setting.base_url if setting else None,
            "api_key_configured": bool(setting and setting.api_key),
            "model": _model(setting),
            "source": source,
            "can_manage": True,
            "quality": "high",
            "verification_status": getattr(setting, "verification_status", "unverified") if setting else "unverified",
            "verified_at": getattr(setting, "verified_at", None) if setting else None,
            "capabilities": getattr(setting, "capabilities_json", {}) if setting else {},
        }
    return {
        "configured": True,
        "base_url": config.base_url,
        "api_key_configured": bool(config.api_key),
        "model": config.model,
        "source": source,
        "can_manage": True,
        "quality": "high",
        "verification_status": getattr(setting, "verification_status", "unverified") if setting else "unverified",
        "verified_at": getattr(setting, "verified_at", None) if setting else None,
        "capabilities": getattr(setting, "capabilities_json", {}) if setting else {},
    }


async def save_image2_config(
    db: AsyncSession,
    *,
    base_url: str,
    api_key: str | None,
    model: str = IMAGE2_DEFAULT_MODEL,
    owner_uid: str,
) -> None:
    repo = ContentCoverRepository(db)
    setting = await repo.get_image2_setting(owner_uid, for_update=True)
    effective_api_key = api_key or _effective_api_key(setting)
    validated = Image2Config.from_values(
        base_url=base_url,
        api_key=effective_api_key,
        model=model,
    )
    await repo.upsert_image2_setting(
        owner_uid=owner_uid,
        base_url=validated.base_url,
        api_key=validated.api_key,
        model=validated.model,
        capabilities_json={},
        verification_status="unverified",
        verified_at=None,
    )
    await db.commit()


async def verify_image2_config(
    db: AsyncSession,
    *,
    base_url: str,
    api_key: str | None,
    model: str,
    owner_uid: str,
) -> dict[str, Any]:
    """Probe a draft config and persist only a successful, reachable profile."""
    repo = ContentCoverRepository(db)
    setting = await repo.get_image2_setting(owner_uid, for_update=True)
    effective_api_key = api_key or _effective_api_key(setting)
    config = Image2Config.from_values(base_url=base_url, api_key=effective_api_key, model=model)
    async with Image2Client(config) as client:
        profile = await client.probe_capabilities()
    values = profile.model_dump(mode="json")
    verified_at = utc_now_naive()
    await repo.upsert_image2_setting(
        owner_uid=owner_uid,
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        capabilities_json=values,
        verification_status="verified" if profile.model_discovered is not False else "warning",
        verified_at=verified_at,
    )
    await db.commit()
    return {
        "profile": values,
        "state": await get_image2_config_state(db, owner_uid=owner_uid),
    }
