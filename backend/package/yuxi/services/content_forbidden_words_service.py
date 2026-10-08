"""按当前用户权限全量读取封禁词库，并冻结来源及替换映射。"""

import hashlib
import json

from yuxi.content.model.forbidden_words import parse_replacement_table
from yuxi.knowledge.manager import DEFAULT_SHARE_CONFIG
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.user_repository import UserRepository


async def load_forbidden_words(uid: str, name: str) -> dict:
    """按 Postgres 知识库名解析唯一可访问词库，避免依赖进程内 Milvus 元数据缓存中的过期名称。"""

    from yuxi import knowledge_base

    user = await UserRepository().get_by_uid(uid)
    if not user:
        raise ValueError(f"必须能访问唯一的“{name}”，当前找到 0 个")
    user_info = {"uid": user.uid, "role": user.role, "department_id": user.department_id}
    rows = await KnowledgeBaseRepository().list_by_name(name)
    matches = [
        row
        for row in rows
        if knowledge_base._database_info_accessible(
            user_info,
            {
                "created_by": row.created_by,
                "share_config": row.share_config or DEFAULT_SHARE_CONFIG.copy(),
            },
        )
    ]
    if len(matches) != 1:
        raise ValueError(f"必须能访问唯一的“{name}”，当前找到 {len(matches)} 个")
    kb_id = matches[0].kb_id
    chunks = await KnowledgeChunkRepository().list_by_kb_id(kb_id)
    documents = {}
    for chunk in sorted(chunks, key=lambda item: (item.file_id, item.chunk_index)):
        documents.setdefault(chunk.file_id, []).append(chunk)
    rows_map = {}
    sources = []
    for file_id, document_chunks in documents.items():
        content = "\n".join(chunk.content for chunk in document_chunks)
        parsed = parse_replacement_table(content)
        if not parsed:
            raise ValueError(f"封禁词库文件 {file_id} 未读到完整的问题词—常用表达方式表")
        for term, alternatives in parsed.items():
            if term in rows_map and rows_map[term] != alternatives:
                raise ValueError(f"封禁词库存在冲突映射：{term}")
            rows_map[term] = alternatives
        sources.append(
            {
                "file_id": file_id,
                "chunk_ids": [chunk.chunk_id for chunk in document_chunks],
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            }
        )
    if not rows_map:
        raise ValueError("封禁词库没有可读取的替换表")
    snapshot = {"knowledge_base_id": kb_id, "name": name, "sources": sources, "alternatives": rows_map}
    snapshot["snapshot_hash"] = hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return snapshot


async def load_knowledge_base_text(uid: str, name: str, *, limit: int = 12000) -> str:
    """读取指定名称的唯一可访问知识库正文，按文件和切片顺序拼接。"""

    from yuxi import knowledge_base

    user = await UserRepository().get_by_uid(uid)
    if not user:
        raise ValueError(f"必须能访问唯一的“{name}”，当前找到 0 个")
    user_info = {"uid": user.uid, "role": user.role, "department_id": user.department_id}
    rows = await KnowledgeBaseRepository().list_by_name(name)
    matches = [
        row
        for row in rows
        if knowledge_base._database_info_accessible(
            user_info,
            {
                "created_by": row.created_by,
                "share_config": row.share_config or DEFAULT_SHARE_CONFIG.copy(),
            },
        )
    ]
    if len(matches) != 1:
        raise ValueError(f"必须能访问唯一的“{name}”，当前找到 {len(matches)} 个")
    chunks = await KnowledgeChunkRepository().list_by_kb_id(matches[0].kb_id)
    text = "\n\n".join(
        chunk.content.strip()
        for chunk in sorted(chunks, key=lambda item: (item.file_id, item.chunk_index))
        if chunk.content and chunk.content.strip()
    ).strip()
    if not text:
        raise ValueError(f"“{name}”没有可读取的内容")
    return text[:limit]
