"""用隔离测试文件验证原文、解析结果、双索引和重复执行，不改运营文件。"""

import importlib.util
import hashlib
from pathlib import Path
import uuid

import pytest

from yuxi import knowledge_base
from yuxi.knowledge.utils.kb_utils import parse_minio_url
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.storage.minio import get_minio_client
from yuxi.storage.postgres.manager import pg_manager

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/remove_national_standard_lexicon_term.py"
SPEC = importlib.util.spec_from_file_location("remove_national_standard_lexicon_term", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_lexicon_deletion_updates_all_stores_and_second_apply_is_noop(tmp_path):
    pg_manager.initialize()
    storage = get_minio_client()
    file_id = None
    objects = []
    try:
        bases = [b for b in await KnowledgeBaseRepository().get_all() if b.name == MODULE.KB_NAME]
        assert len(bases) == 1
        kb_id = bases[0].kb_id
        kb = await knowledge_base.aget_kb(kb_id)
        await kb._load_metadata()
        source = "标准化施工工艺\n国标施工规范\n材料验收标准\n测试新增词条\n".encode()
        expected = "标准化施工工艺\n材料验收标准\n测试新增词条\n".encode()
        object_name = f"{kb_id}/migration-test/{uuid.uuid4().hex}.txt"
        uploaded = await storage.aupload_file("knowledgebases", object_name, source)
        objects.append((uploaded.bucket_name, uploaded.object_name))
        meta = await kb.add_file_record(
            kb_id, uploaded.url, params={"content_hashes": {uploaded.url: hashlib.sha256(source).hexdigest()}}
        )
        file_id = meta["file_id"]
        # 已知 UTF-8 文本无需 OCR；仍通过实际服务建分块和向量索引。
        meta["markdown_file"] = await kb._save_markdown_to_minio(kb_id, file_id, source.decode())
        objects.append(parse_minio_url(meta["markdown_file"]))
        meta["status"] = "parsed"
        await kb._persist_file(file_id)
        await kb.index_file(kb_id, file_id)
        collection = await kb._get_milvus_collection(kb_id)
        collection.flush()

        assert await MODULE.migrate_file(kb, kb_id, file_id, apply=False, backup_dir=tmp_path) == "planned"
        assert await kb._read_minio_bytes(uploaded.url) == source
        assert not list(tmp_path.iterdir())
        assert await MODULE.migrate_file(kb, kb_id, file_id, apply=True, backup_dir=tmp_path) == "migrated"
        assert await kb._read_minio_bytes(uploaded.url) == expected
        assert await kb._read_minio_bytes(meta["markdown_file"]) == expected
        chunks = await KnowledgeChunkRepository().list_by_file_id(file_id)
        assert "测试新增词条" in "\n".join(c.content for c in chunks)
        assert (tmp_path / kb_id / file_id / "original.txt").read_bytes() == source
        updated_at = meta["updated_at"]
        assert await MODULE.migrate_file(kb, kb_id, file_id, apply=True, backup_dir=tmp_path) == "already_clean"
        assert meta["updated_at"] == updated_at
        assert (tmp_path / kb_id / file_id / "original.txt").read_bytes() == source
    finally:
        if file_id:
            await kb.delete_file(kb_id, file_id)
        for bucket, obj in objects:
            await storage.adelete_file(bucket, obj)
        await pg_manager.close()
