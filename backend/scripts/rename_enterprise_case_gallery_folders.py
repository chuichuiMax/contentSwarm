#!/usr/bin/env python3
"""Rename enterprise 案例图库 secondary folders to 楼盘-风格-面积 (strip 楼栋房号)."""

from __future__ import annotations

import argparse
import asyncio

from dotenv import load_dotenv
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from yuxi.services.material_library_service import (
    format_enterprise_case_gallery_display_name,
    is_enterprise_case_root,
    resolve_enterprise_case_gallery_fields,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import ContentMaterialCategory


async def run(*, apply: bool) -> int:
    async with pg_manager.get_async_session_context() as db:
        roots = (
            await db.execute(
                select(ContentMaterialCategory).where(
                    ContentMaterialCategory.material_type == "image",
                    ContentMaterialCategory.visibility == "enterprise",
                    ContentMaterialCategory.parent_id.is_(None),
                    ContentMaterialCategory.deleted_at.is_(None),
                )
            )
        ).scalars().all()
        case_root_ids = {row.id for row in roots if is_enterprise_case_root(row)}
        if not case_root_ids:
            print("NO_CASE_ROOT")
            return 1

        children = (
            await db.execute(
                select(ContentMaterialCategory).where(
                    ContentMaterialCategory.material_type == "image",
                    ContentMaterialCategory.parent_id.in_(case_root_ids),
                    ContentMaterialCategory.deleted_at.is_(None),
                )
            )
        ).scalars().all()

        planned: list[tuple[str, str, str, str, str, str | None, str | None]] = []
        for category in children:
            style = (category.design_style or "").strip()
            if not style:
                print(f"SKIP_NO_STYLE\t{category.id}\t{category.name}")
                continue
            building, area = resolve_enterprise_case_gallery_fields(
                category.name, style, category.building_name, category.area
            )
            new_name = format_enterprise_case_gallery_display_name(building, style, area)
            if not new_name or new_name == category.name:
                continue
            planned.append(
                (
                    category.id,
                    category.owner_uid,
                    category.material_type,
                    category.name,
                    new_name,
                    building,
                    area,
                )
            )

        print(f"PLANNED\t{len(planned)}")
        for cat_id, _owner, _mt, old_name, new_name, _building, _area in planned:
            print(f"RENAME\t{cat_id}\t{old_name}\t=>\t{new_name}")

        if not apply:
            print("DRY_RUN")
            return 0

        renamed = 0
        for cat_id, owner_uid, material_type, _old_name, new_name, building, area in planned:
            try:
                await db.execute(
                    update(ContentMaterialCategory)
                    .where(
                        ContentMaterialCategory.id == cat_id,
                        ContentMaterialCategory.owner_uid == owner_uid,
                        ContentMaterialCategory.material_type == material_type,
                    )
                    .values(name=new_name, building_name=building, area=area)
                )
                await db.commit()
                renamed += 1
            except IntegrityError:
                await db.rollback()
                print(f"CONFLICT\t{cat_id}\t{new_name}")
        print(f"DONE\t{renamed}")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="写入数据库（默认仅预览）")
    args = parser.parse_args()
    load_dotenv("/app/.env", override=False)
    raise SystemExit(asyncio.run(run(apply=args.apply)))


if __name__ == "__main__":
    main()
