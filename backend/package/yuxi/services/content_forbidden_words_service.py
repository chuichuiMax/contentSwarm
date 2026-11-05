"""按当前用户权限全量读取封禁词库，并冻结来源及替换映射。"""

import hashlib
import json

from yuxi.content.model.forbidden_words import parse_replacement_table
from yuxi.knowledge.manager import DEFAULT_SHARE_CONFIG
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.user_repository import UserRepository


async def _parsed_file_texts(kb_id: str) -> dict[str, str]:
    """文件已解析但尚未切片时，读取解析后的 Markdown。不改知识库记录。"""

    from yuxi.knowledge.utils.kb_utils import is_minio_url, parse_minio_url
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
    from yuxi.storage.minio import get_minio_client

    records = await KnowledgeFileRepository().list_by_kb_id(kb_id)
    texts: dict[str, str] = {}
    client = None
    for record in records:
        if getattr(record, "is_folder", False):
            continue
        url = getattr(record, "markdown_file", None)
        if not url or not is_minio_url(url):
            continue
        if client is None:
            client = get_minio_client()
        bucket_name, object_name = parse_minio_url(url)
        data = await client.adownload_file(bucket_name, object_name)
        text = data.decode("utf-8").strip()
        if text:
            texts[record.file_id] = text
    return texts


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
    contents: list[tuple[str, str, list[str]]] = []
    if documents:
        for file_id, document_chunks in documents.items():
            contents.append(
                (
                    file_id,
                    "\n".join(chunk.content for chunk in document_chunks),
                    [chunk.chunk_id for chunk in document_chunks],
                )
            )
    else:
        for file_id, text in (await _parsed_file_texts(kb_id)).items():
            contents.append((file_id, text, []))
    rows_map = {}
    sources = []
    for file_id, content, chunk_ids in contents:
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
                "chunk_ids": chunk_ids,
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


async def load_optional_forbidden_words(uid: str, name: str) -> dict:
    """直接生成时缺少封禁词库不中断；词库有多份或表格损坏时仍报错。"""

    try:
        return await load_forbidden_words(uid, name)
    except ValueError as exc:
        if "当前找到 0 个" not in str(exc):
            raise
        return {
            "knowledge_base_id": "",
            "name": name,
            "sources": [],
            "alternatives": {},
            "snapshot_hash": "",
        }


async def lookup_knowledge_base_text(uid: str, name: str, *, limit: int = 12000) -> dict | None:
    """按名称查找唯一可访问知识库。没有或不唯一时返回空，不中断生成。"""

    from yuxi import knowledge_base

    user = await UserRepository().get_by_uid(uid)
    if not user:
        return None
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
        return None
    chunks = await KnowledgeChunkRepository().list_by_kb_id(matches[0].kb_id)
    text = "\n\n".join(
        chunk.content.strip()
        for chunk in sorted(chunks, key=lambda item: (item.file_id, item.chunk_index))
        if chunk.content and chunk.content.strip()
    ).strip()
    if not text:
        return None
    return {"kb_id": matches[0].kb_id, "name": name, "text": text[:limit]}


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
