"""按当前用户权限全量读取封禁词库，并冻结来源及替换映射。"""

import hashlib
import json

from yuxi.content.model.forbidden_words import parse_replacement_table
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository


async def load_forbidden_words(uid: str, name: str) -> dict:
    from yuxi import knowledge_base

    accessible = await knowledge_base.get_databases_by_uid(uid)
    matches = [kb for kb in accessible["databases"] if kb["name"] == name]
    if len(matches) != 1:
        raise ValueError(f"必须能访问唯一的“{name}”，当前找到 {len(matches)} 个")
    kb_id = matches[0]["kb_id"]
    chunks = await KnowledgeChunkRepository().list_by_kb_id(kb_id)
    documents = {}
    for chunk in sorted(chunks, key=lambda item: (item.file_id, item.chunk_index)):
        documents.setdefault(chunk.file_id, []).append(chunk)
    rows = {}
    sources = []
    for file_id, document_chunks in documents.items():
        content = "\n".join(chunk.content for chunk in document_chunks)
        parsed = parse_replacement_table(content)
        if not parsed:
            raise ValueError(f"封禁词库文件 {file_id} 未读到完整的问题词—常用表达方式表")
        for term, alternatives in parsed.items():
            if term in rows and rows[term] != alternatives:
                raise ValueError(f"封禁词库存在冲突映射：{term}")
            rows[term] = alternatives
        sources.append(
            {
                "file_id": file_id,
                "chunk_ids": [chunk.chunk_id for chunk in document_chunks],
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            }
        )
    if not rows:
        raise ValueError("封禁词库没有可读取的替换表")
    snapshot = {"knowledge_base_id": kb_id, "name": name, "sources": sources, "alternatives": rows}
    snapshot["snapshot_hash"] = hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return snapshot
