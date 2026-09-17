"""把用户选图解析为可冻结的素材引用，不让模型决定图片组合。"""

from fastapi import HTTPException

from yuxi.content_cover.photo_composition import PhotoComposition
from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.repositories.material_library_repository import MaterialLibraryRepository


async def resolve_photo_composition(db, user, composition: PhotoComposition, primary_id: str, *, complete: bool):
    if complete:
        try:
            composition.require_complete(primary_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    repo = MaterialLibraryRepository(db, include_shared=True)
    slots = []
    for slot in composition.slots:
        resolved = slot.model_dump()
        if slot.image_item_id:
            item = await repo.get_item_for_user(slot.image_item_id, str(user.uid))
            if item is None or item.material_type != "image" or item.status != "enabled":
                raise HTTPException(status_code=422, detail="组合图片不存在、已停用或无权访问")
            asset = await repo.get_asset(item.asset_id, item.owner_uid)
            if asset is None or asset.role not in {"source", "library_image"}:
                raise HTTPException(status_code=422, detail="组合图片文件不可用")
            await ContentCoverRepository(db).retain_material_use([asset.id], str(user.uid))
            resolved.update(asset_id=asset.id, sha256=asset.sha256)
        slots.append(resolved)
    return {"layout_id": composition.layout_id, **composition.render_layout(), "slots": slots}
