"""删除专业正解词库中的独立词条；默认预览，--apply 同步原文、解析文本及索引。

docker compose exec -T api uv run python scripts/remove_national_standard_lexicon_term.py --apply
知识库重名时用 --kb-id 指定；不会覆盖其他词条或修改历史任务快照。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

KB_NAME = "正文词库"
FILENAME = "工艺干货资料库-专业正解词库.txt"
TERM = "国标施工规范"


def remove_term(data: bytes) -> bytes:
    """仅删除整行词条，保留其他内容、换行及用户追加词条。"""
    return "".join(line for line in data.decode("utf-8").splitlines(keepends=True) if line.strip() != TERM).encode(
        "utf-8"
    )


async def migrate_file(kb, kb_id: str, file_id: str, *, apply: bool, backup_dir: Path) -> str:
    from yuxi.knowledge.utils.kb_utils import calculate_content_hash, parse_minio_url
    from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
    from yuxi.storage.minio import get_minio_client

    meta = kb.files_meta[file_id]
    original_path = meta.get("minio_url") or meta["path"]
    original = await kb._read_minio_bytes(original_path)
    parsed = await kb._read_minio_bytes(meta["markdown_file"])
    cleaned_original, cleaned_parsed = remove_term(original), remove_term(parsed)
    chunks = await KnowledgeChunkRepository().list_by_file_id(file_id)
    collection = await kb._get_milvus_collection(kb_id)
    expr = f"file_id == {json.dumps(file_id)}"
    rows = collection.query(expr=expr, output_fields=["content"], consistency_level="Strong")
    stale_index = any(remove_term(c.content.encode()) != c.content.encode() for c in chunks) or any(
        remove_term(row["content"].encode()) != row["content"].encode() for row in rows
    )
    if (original, parsed) == (cleaned_original, cleaned_parsed) and not stale_index:
        if not chunks or not rows:
            raise ValueError("词库索引缺失，请先完成入库")
        return "already_clean"
    if not apply:
        return "planned"

    backup = backup_dir / kb_id / file_id
    backup.mkdir(parents=True, exist_ok=True)
    # 保留首次备份，失败后的重试不覆盖它。
    for name, data in {
        "original.txt": original,
        "parsed.md": parsed,
        "metadata.json": json.dumps(meta, ensure_ascii=False, default=str, indent=2).encode(),
    }.items():
        if not (backup / name).exists():
            (backup / name).write_bytes(data)

    # 索引服务会先删除旧分块，因此在改动前确认嵌入服务可用。
    embed = kb._get_embedding_function(kb.databases_meta[kb_id].get("embedding_model_spec"))
    await embed([cleaned_parsed.decode("utf-8")])
    storage = get_minio_client()
    for path, data in ((original_path, cleaned_original), (meta["markdown_file"], cleaned_parsed)):
        bucket, obj = parse_minio_url(path)
        await storage.aupload_file(bucket_name=bucket, object_name=obj, data=data)
    meta["content_hash"] = await calculate_content_hash(cleaned_original)
    meta["size"] = len(cleaned_original)
    await kb.index_file(kb_id, file_id)

    if await kb._read_minio_bytes(original_path) != cleaned_original:
        raise ValueError("原文件回读不一致")
    if await kb._read_minio_bytes(meta["markdown_file"]) != cleaned_parsed:
        raise ValueError("解析文本回读不一致")
    chunks = await KnowledgeChunkRepository().list_by_file_id(file_id)
    collection.flush()
    rows = collection.query(expr=expr, output_fields=["content"], consistency_level="Strong")
    if (
        not chunks
        or not rows
        or any(remove_term(c.content.encode()) != c.content.encode() for c in chunks)
        or any(remove_term(row["content"].encode()) != row["content"].encode() for row in rows)
    ):
        raise ValueError("索引仍含目标词条或重建不完整，请检查后重试；原文件已备份")
    return "migrated"


async def main(args) -> None:
    from yuxi import knowledge_base
    from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
    from yuxi.storage.postgres.manager import pg_manager

    pg_manager.initialize()
    try:
        bases = await KnowledgeBaseRepository().get_all()
        matches = [b for b in bases if b.kb_id == args.kb_id] if args.kb_id else [b for b in bases if b.name == KB_NAME]
        if len(matches) != 1 or matches[0].kb_type != "milvus":
            raise ValueError("必须找到唯一 Milvus 正文词库；重名时请使用 --kb-id 指定")
        kb_id = matches[0].kb_id
        files = await KnowledgeFileRepository().list_by_kb_id(kb_id)
        targets = [f for f in files if f.filename == FILENAME]
        if len(targets) != 1:
            raise ValueError(f"必须找到唯一文件：{FILENAME}，实际 {len(targets)} 个")
        kb = await knowledge_base.aget_kb(kb_id)
        await kb._load_metadata()
        status = await migrate_file(kb, kb_id, targets[0].file_id, apply=args.apply, backup_dir=args.backup_dir)
        print(json.dumps({"status": status, "kb_id": kb_id, "file_id": targets[0].file_id}, ensure_ascii=False))
    finally:
        await pg_manager.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="执行删除；默认仅预览")
    parser.add_argument("--kb-id", help="目标环境的知识库 ID；默认按名称定位")
    parser.add_argument("--backup-dir", type=Path, default=Path("saves/migrations/remove-national-standard-term"))
    asyncio.run(main(parser.parse_args()))
