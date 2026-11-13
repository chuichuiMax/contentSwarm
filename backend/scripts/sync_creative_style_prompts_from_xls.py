"""按 Excel 更新创作风格文案提示词：提示词为空则跳过；非空且库中不存在时可新增。"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid
from pathlib import Path

from sqlalchemy import select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import ContentType, CreativeStyle

PAREN_SUFFIX = re.compile(r"[（(].*$")


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _base_name(value: str) -> str:
    return _norm(PAREN_SUFFIX.sub("", value))


def load_excel_rows(path: Path) -> list[dict[str, str]]:
    rows = json.loads(path.read_text(encoding="utf-8-sig"))
    return [
        {
            "content_type_name": str(item.get("content_type_name") or "").strip(),
            "name": str(item.get("name") or "").strip(),
            "prompt": str(item.get("prompt") or "").strip(),
        }
        for item in rows
        if str(item.get("name") or "").strip()
    ]


def match_style(
    excel_name: str,
    styles: list[CreativeStyle],
) -> CreativeStyle | None:
    exact = [item for item in styles if _norm(item.name) == _norm(excel_name)]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None
    base = _base_name(excel_name)
    soft = [item for item in styles if _base_name(item.name) == base or _norm(item.name) == base]
    if len(soft) == 1:
        return soft[0]
    return None


async def main(*, source: Path, apply: bool, create_missing: bool) -> None:
    rows = load_excel_rows(source)
    todo = [row for row in rows if row["prompt"]]
    skipped_empty = [row for row in rows if not row["prompt"]]
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        type_rows = (await db.execute(select(ContentType))).scalars().all()
        types_by_name = {_norm(item.name): item for item in type_rows}
        style_rows = (await db.execute(select(CreativeStyle))).scalars().all()
        styles_by_type: dict[str, list[CreativeStyle]] = {}
        for item in style_rows:
            styles_by_type.setdefault(item.content_type_id, []).append(item)

        updated = []
        created = []
        missing_type = []
        unmatched = []
        unchanged = []
        dirty = False
        for row in todo:
            content_type = types_by_name.get(_norm(row["content_type_name"]))
            if content_type is None:
                missing_type.append(row)
                continue
            bucket = styles_by_type.setdefault(content_type.id, [])
            style = match_style(row["name"], bucket)
            if style is None:
                if not create_missing:
                    unmatched.append(row)
                    continue
                style_name = row["name"]
                created.append((content_type.name, style_name, len(row["prompt"])))
                if apply:
                    style = CreativeStyle(
                        id=str(uuid.uuid4()),
                        content_type_id=content_type.id,
                        name=style_name,
                        description="",
                        prompt=row["prompt"],
                        enabled=True,
                        sort_order=len(bucket),
                        created_by="system",
                    )
                    db.add(style)
                    bucket.append(style)
                    dirty = True
                continue
            if (style.prompt or "").strip() == row["prompt"]:
                unchanged.append((content_type.name, style.name, style.id))
                continue
            updated.append((content_type.name, style.name, style.id, len(style.prompt or ""), len(row["prompt"])))
            if apply:
                style.prompt = row["prompt"]
                dirty = True
        if apply and dirty:
            await db.commit()

    print(f"excel_rows={len(rows)} with_prompt={len(todo)} empty_skipped={len(skipped_empty)}")
    print(
        f"would_update={len(updated)} would_create={len(created)} unchanged={len(unchanged)} "
        f"missing_type={len(missing_type)} unmatched={len(unmatched)} apply={apply} create_missing={create_missing}"
    )
    for content_type_name, style_name, style_id, old_len, new_len in updated:
        print(f"UPDATE {content_type_name} / {style_name} ({style_id}) {old_len}->{new_len}")
    for content_type_name, style_name, prompt_len in created:
        print(f"CREATE {content_type_name} / {style_name} prompt_len={prompt_len}")
    for content_type_name, style_name, style_id in unchanged:
        print(f"SAME {content_type_name} / {style_name}")
    for row in missing_type:
        print(f"MISSING_TYPE {row['content_type_name']} / {row['name']}")
    for row in unmatched:
        print(f"UNMATCHED {row['content_type_name']} / {row['name']}")
    print("EMPTY_SKIPPED")
    for row in skipped_empty:
        print(f"  {row['content_type_name']} / {row['name']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--create-missing", action="store_true", help="提示词非空且库中无该风格时新增")
    args = parser.parse_args()
    asyncio.run(main(source=args.source, apply=args.apply, create_missing=args.create_missing))
