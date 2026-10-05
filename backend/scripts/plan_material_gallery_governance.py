"""Review legacy gallery roles and visibility. Dry-run by default; never deletes data."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

APP_ROOT = Path(__file__).resolve().parents[1]
for path in (APP_ROOT, APP_ROOT / "package"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

INITIAL_ENTERPRISE = (("reference", "案例图库"), ("rough", "毛坯房图库"), ("generated", "生图图库"))


def reviewed_global_personal_updates(private: list[Mapping], requested: set[str]) -> tuple[set[str], set[str]]:
    active = {f"{row['owner_uid']}:{row['id']}" for row in private if row["deleted_at"] is None}
    invalid = requested - active
    if invalid:
        raise ValueError(f"指定的个人图库不是活动历史候选：{','.join(sorted(invalid))}")
    pending = {
        f"{row['owner_uid']}:{row['id']}"
        for row in private
        if row["deleted_at"] is None and not row["is_global_personal"]
    }
    return requested & pending, requested - pending


async def run(*, apply: bool, global_personal: list[str]) -> None:
    from yuxi.storage.postgres.manager import pg_manager

    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        columns = set(
            (
                await db.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='content_material_categories'"
                    )
                )
            ).scalars()
        )
        if "image_design_role" not in columns:
            raise RuntimeError("图库角色字段尚不存在；先在隔离数据库验证 schema")
        if apply and "is_global_personal" not in columns:
            raise RuntimeError("全员可见字段尚不存在；禁止在未验证 schema 的数据库执行 --apply")

        enterprise = list(
            (
                await db.execute(
                    text(
                        "SELECT owner_uid,id,name,image_design_role,deleted_at FROM content_material_categories "
                        "WHERE material_type='image' AND visibility='enterprise' AND parent_id IS NULL"
                    )
                )
            ).mappings()
        )
        global_column = "c.is_global_personal" if "is_global_personal" in columns else "FALSE AS is_global_personal"
        private = list(
            (
                await db.execute(
                    text(
                        f"SELECT c.owner_uid,c.id,c.name,c.deleted_at,u.role,{global_column} "
                        "FROM content_material_categories c "
                        "LEFT JOIN users u ON u.uid=c.owner_uid "
                        "WHERE c.material_type='image' AND c.visibility='private' "
                        "AND c.parent_id IS NULL AND c.id LIKE 'mlc_%'"
                    )
                )
            ).mappings()
        )

        proposals = []
        ambiguous = []
        preserved = 0
        for index, (role, default_name) in enumerate(INITIAL_ENTERPRISE, 1):
            marked = [row for row in enterprise if row["image_design_role"] == role]
            if len(marked) > 1:
                ambiguous.append((role, [f"{row['owner_uid']}:{row['id']}" for row in marked]))
                continue
            if marked:
                row = marked[0]
                state = "deleted" if row["deleted_at"] is not None else "active"
                print(f"PRESERVE role={role} key={row['owner_uid']}:{row['id']} name={row['name']} state={state}")
                preserved += 1
                continue
            candidates = [
                row
                for row in enterprise
                if row["id"] != "enterprise-root"
                and row["deleted_at"] is None
                and row["name"].casefold() == default_name.casefold()
            ]
            if len(candidates) > 1:
                ambiguous.append((role, [f"{row['owner_uid']}:{row['id']}" for row in candidates]))
            elif candidates:
                row = candidates[0]
                proposals.append(("tag", role, default_name, row["owner_uid"], row["id"], index))
            else:
                proposals.append(
                    ("add", role, default_name, "system:material-library", f"enterprise-initial-{role}", index)
                )

        for action, role, name, owner, category_id, _ in proposals:
            print(f"PLAN_{action.upper()} role={role} key={owner}:{category_id} name={name}")
        for role, keys in ambiguous:
            print(f"REVIEW_AMBIGUOUS role={role} candidates={','.join(keys)}")

        review_private = [row for row in private if row["deleted_at"] is None and not row["is_global_personal"]]
        for row in review_private:
            print(
                f"REVIEW_GLOBAL_PERSONAL key={row['owner_uid']}:{row['id']} "
                f"name={row['name']} current_role={row['role']}"
            )

        requested = set(global_personal)
        selected, already_selected = reviewed_global_personal_updates(private, requested)
        if apply and ambiguous:
            raise RuntimeError("存在企业图库角色歧义，保持 review-only，未执行任何写入")

        if apply:
            for action, role, name, owner, category_id, index in proposals:
                if action == "tag":
                    await db.execute(
                        text(
                            "UPDATE content_material_categories SET image_design_role=:role "
                            "WHERE owner_uid=:owner AND material_type='image' AND id=:id "
                            "AND image_design_role IS NULL AND deleted_at IS NULL"
                        ),
                        {"role": role, "owner": owner, "id": category_id},
                    )
                else:
                    await db.execute(
                        text(
                            "INSERT INTO content_material_categories "
                            "(owner_uid,material_type,id,tenant_id,visibility,parent_id,industry_slug,"
                            "image_design_role,is_global_personal,name,description,sort_order,is_system,"
                            "created_at,updated_at) "
                            "VALUES (:owner,'image',:id,NULL,'enterprise',NULL,'uncategorized',"
                            ":role,FALSE,:name,'企业共享初始图库',:sort_order,FALSE,now(),now())"
                        ),
                        {"owner": owner, "id": category_id, "role": role, "name": name, "sort_order": index * 10},
                    )
            for key in sorted(selected):
                owner, category_id = key.split(":", 1)
                await db.execute(
                    text(
                        "UPDATE content_material_categories SET is_global_personal=TRUE "
                        "WHERE owner_uid=:owner AND material_type='image' AND id=:id "
                        "AND visibility='private' AND deleted_at IS NULL AND is_global_personal=FALSE"
                    ),
                    {"owner": owner, "id": category_id},
                )
            await db.commit()
        else:
            await db.rollback()

        counts = {}
        for label, table in (
            ("categories", "content_material_categories"),
            ("items", "content_material_library_items"),
            ("assets", "content_cover_assets"),
            ("shares", "content_material_shares"),
        ):
            counts[label] = (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
        print(
            f"SUMMARY apply={apply} proposed_add={sum(action == 'add' for action, *_ in proposals)} "
            f"proposed_role_tag={sum(action == 'tag' for action, *_ in proposals)} "
            f"proposed_rename=0 proposed_soft_delete=0 ambiguous={len(ambiguous)} "
            f"review_global_personal={len(review_private)} explicitly_selected={len(selected)} "
            f"already_selected={len(already_selected)} "
            f"preserved={preserved} failed=0 " + " ".join(f"{key}={value}" for key, value in counts.items())
        )
    await pg_manager.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write a reviewed plan; default is read-only dry-run")
    parser.add_argument("--global-personal", action="append", default=[], metavar="OWNER_UID:CATEGORY_ID")
    args = parser.parse_args()
    load_dotenv(APP_ROOT.parent / ".env", override=False)
    if not os.getenv("POSTGRES_URL"):
        parser.error("POSTGRES_URL is required")
    asyncio.run(run(apply=args.apply, global_personal=args.global_personal))
