from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.process_standard_repository import ProcessStandardRepository
from yuxi.storage.postgres.models_business import User

# 鸿扬 15 大高标准工艺系统配置表；仅写入缺失的 (工艺名称, 工艺详情)，删除自定义项不受影响。
DEFAULT_PROCESS_STANDARDS: tuple[tuple[str, str, bool], ...] = (
    ("安全用电系统", "HYB-强电箱安全配置系统", True),
    ("安全用电系统", "HYB-强电箱内空开安装工艺", True),
    ("安全用电系统", "HYB-过欠压保护工艺", True),
    ("安全用电系统", "HYB-厨房管线耐用工艺", True),
    ("安全用电系统", "HYB-强弱电布管特色工艺", True),
    ("安全用电系统", "HYB-灯具防触电工艺", True),
    ("防渗漏系统", "HYB-厨卫及顶楼防漏吊顶工艺", True),
    ("防渗漏系统", "HYB-出水口限位工艺", True),
    ("防渗漏系统", "HYB-烟道防漏烟、开裂工艺", True),
    ("防渗漏系统", "HYB-厨卫顶面布管特色工艺", True),
    ("防渗漏系统", "HYB-水管上墙工艺（无水区到有水区）", True),
    ("防渗漏系统", "HYB-阳台地漏防返水工艺", True),
    ("防渗漏系统", "HYB-地漏安装工艺", True),
    ("防渗漏系统", "HYB-防漏监控工艺（选）", True),
    ("防堵防臭系统", "HYB-存水弯特色工艺", True),
    ("防堵防臭系统", "HYB-墙排特色工艺", True),
    ("防堵防臭系统", "HYB-排水通畅特色工艺", True),
    ("防堵防臭系统", "HYB-防污水返水工艺", True),
    ("降噪隔音系统", "HYB-排水管包管静音工艺", True),
    ("降噪隔音系统", "HYB-轻钢龙骨隔墙隔音工艺", True),
    ("降噪隔音系统", "HYB-房门隔音工艺", True),
    ("防松动/掉落系统", "HYB-完美拆除工艺", True),
    ("防松动/掉落系统", "HYB-烟道抹灰工艺", True),
    ("防松动/掉落系统", "HYB-轻质砌筑隔墙加固工艺", True),
    ("防松动/掉落系统", "HYB-OSB板打底工艺", True),
    ("防松动/掉落系统", "HYB-轻钢龙骨隔墙加固工艺", True),
    ("防松动/掉落系统", "HYB-墙砖薄贴工艺", True),
    ("防松动/掉落系统", "HYB-大板砖薄贴工艺", True),
    ("防松动/掉落系统", "HYB-木制品悬挂安装工艺", True),
    ("防松动/掉落系统", "HYB-灯具安装工艺", True),
    ("防开裂系统", "HYB-新旧砌体搭接工艺", True),
    ("防开裂系统", "HYB-全轻钢龙骨吊顶工艺", True),
    ("防开裂系统", "HYB-粉墙挂钢丝网工艺", True),
    ("防开裂系统", "HYB-腻子防开裂工艺（选）", True),
    ("防开裂系统", "HYB-门洞修正工艺", True),
    ("防变形系统", "HYB-全轻钢龙骨吊顶工艺", True),
    ("防变形系统", "HYB-OSB板打底工艺", True),
    ("防变形系统", "HYB-房门安装工艺", True),
    ("防碰撞系统", "HYB-可耐福金属阳角防碰耐用工艺", True),
    ("防碰撞系统", "HYB-木制品圆弧角工艺", True),
    ("防霉变系统", "HYB-墙面抗碱底漆工艺", True),
    ("防霉变系统", "HYB-厨卫门套防潮防霉变工艺", True),
    ("防霉变系统", "HYB-生活阳台木制品安装工艺", True),
    ("防霉变系统", "HYB-台面/洁具美缝工艺", True),
    ("精致收口系统", "HYB-基础装修精致收口工艺", True),
    ("精致收口系统", "HYB-橱柜精致收口工艺", True),
    ("精致收口系统", "HYB-木制精致收口工艺", True),
    ("精致收口系统", "HYB-门墙精致收口工艺", True),
    ("颜值美学系统", "HYB-内嵌/隐形产品实现工艺", True),
    ("颜值美学系统", "HYB-悬挑产品实现工艺", True),
    ("颜值美学系统", "HYB-墙面垂平标筋工艺", True),
    ("颜值美学系统", "HYB-洗墙灯灯照壁工艺", True),
    ("颜值美学系统", "HYB-瓷砖磨原边45度拼角工艺", True),
    ("颜值美学系统", "HYB-台面无挡水条工艺", True),
    ("颜值美学系统", "HYB-墙地砖对缝工艺", True),
    ("颜值美学系统", "HYB-加长风口工艺", True),
    ("功能舒适系统", "HYB-热水管保温工艺", True),
    ("功能舒适系统", "HYB-强电配电系统工艺", True),
    ("功能舒适系统", "HYB-地面不积水工艺", True),
    ("暖通舒适系统", "HYB-中央空调管线孔封闭工艺", True),
    ("暖通舒适系统", "HYB-中央空调管线孔封闭及排气口防护罩工艺", True),
    ("暖通舒适系统", "HYB-地暖高流地坪工艺", True),
    ("暖通舒适系统", "HYB-冷凝水管安装工艺", True),
    ("智能网络畅享系统", "HYB-智能线路预置工艺", True),
    ("智能网络畅享系统", "HYB-WIFI全屋覆盖工艺", True),
    ("智能网络畅享系统", "HYB-7类网线布置工艺", True),
    ("个性定制系统", "HYB-吊顶与背景墙造型实现工艺", True),
    ("个性定制系统", "HYB-木作造型/异型工艺", True),
    ("个性定制系统", "HYB-油漆调色定制工艺", True),
)


class ProcessStandardCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    detail: str = Field(min_length=1, max_length=255)
    enabled: bool = True


class ProcessStandardUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    detail: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})


def _normalize_text(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise _error(422, "PROCESS_STANDARD_INVALID_FIELD", f"{field} 不能为空")
    return normalized


async def ensure_default_process_standards(db: AsyncSession) -> None:
    """补齐导入表中缺失的 (工艺名称, 工艺详情)。表内已有同键则跳过；删除导入表条目后再次加载会补回。"""
    repo = ProcessStandardRepository(db)
    for name, detail, enabled in DEFAULT_PROCESS_STANDARDS:
        if await repo.get_by_name_detail(name, detail):
            continue
        await repo.create(
            {
                "id": str(uuid.uuid4()),
                "name": name,
                "detail": detail,
                "enabled": enabled,
                "created_by": "system",
            }
        )


async def list_process_standards(
    db: AsyncSession, keyword: str | None = None, name: str | None = None
) -> dict[str, Any]:
    await ensure_default_process_standards(db)
    repo = ProcessStandardRepository(db)
    items = await repo.list_items(
        keyword=keyword.strip() if keyword else None,
        name=name.strip() if name else None,
    )
    names = await repo.list_names()
    return {
        "process_standards": [item.to_dict() for item in items],
        "names": names,
        "total": len(items),
    }


async def list_enabled_process_type_names(db: AsyncSession) -> list[str]:
    await ensure_default_process_standards(db)
    return await ProcessStandardRepository(db).list_enabled_names()


async def list_enabled_process_names_by_type(db: AsyncSession) -> dict[str, list[str]]:
    await ensure_default_process_standards(db)
    return await ProcessStandardRepository(db).list_enabled_names_by_type()


async def create_process_standard(
    db: AsyncSession, user: User, payload: ProcessStandardCreate
) -> dict[str, Any]:
    await ensure_default_process_standards(db)
    repo = ProcessStandardRepository(db)
    name = _normalize_text(payload.name, field="工艺名称")
    detail = _normalize_text(payload.detail, field="工艺详情")
    if await repo.get_by_name_detail(name, detail):
        raise _error(409, "PROCESS_STANDARD_EXISTS", "该工艺标准已存在")
    try:
        item = await repo.create(
            {
                "id": str(uuid.uuid4()),
                "name": name,
                "detail": detail,
                "enabled": payload.enabled,
                "created_by": str(user.uid),
            }
        )
    except IntegrityError as exc:
        raise _error(409, "PROCESS_STANDARD_EXISTS", "该工艺标准已存在") from exc
    return {"process_standard": item.to_dict()}


async def update_process_standard(
    db: AsyncSession, item_id: str, payload: ProcessStandardUpdate
) -> dict[str, Any]:
    repo = ProcessStandardRepository(db)
    item = await repo.get(item_id)
    if item is None:
        raise _error(404, "PROCESS_STANDARD_NOT_FOUND", "工艺标准不存在")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        data["name"] = _normalize_text(data["name"], field="工艺名称")
    if "detail" in data:
        data["detail"] = _normalize_text(data["detail"], field="工艺详情")
    next_name = data.get("name", item.name)
    next_detail = data.get("detail", item.detail)
    if next_name != item.name or next_detail != item.detail:
        existing = await repo.get_by_name_detail(next_name, next_detail)
        if existing is not None and existing.id != item.id:
            raise _error(409, "PROCESS_STANDARD_EXISTS", "该工艺标准已存在")
    try:
        item = await repo.update(item, data)
    except IntegrityError as exc:
        raise _error(409, "PROCESS_STANDARD_EXISTS", "该工艺标准已存在") from exc
    return {"process_standard": item.to_dict()}


async def delete_process_standard(db: AsyncSession, item_id: str) -> dict[str, Any]:
    repo = ProcessStandardRepository(db)
    item = await repo.get(item_id)
    if item is None:
        raise _error(404, "PROCESS_STANDARD_NOT_FOUND", "工艺标准不存在")
    await repo.delete(item)
    return {"success": True, "id": item_id}
