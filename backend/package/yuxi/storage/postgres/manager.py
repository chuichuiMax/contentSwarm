"""PostgreSQL 数据库管理器 - 支持知识库和业务数据"""

import json
import os
from contextlib import asynccontextmanager

from psycopg_pool import AsyncConnectionPool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base
from yuxi.storage.postgres.models_business import Base as BusinessBase
from yuxi.storage.postgres.models_knowledge import Base as KnowledgeBase
from yuxi.storage.postgres import models_content as _models_content  # noqa: F401
from yuxi.utils import logger
from yuxi.utils.singleton import SingletonMeta

# 合并两个 Base
CombinedBase = declarative_base()

# 继承所有表
for module in [KnowledgeBase, BusinessBase]:
    for table_name in dir(module):
        table = getattr(module, table_name)
        if isinstance(table, type) and hasattr(table, "__tablename__"):
            setattr(CombinedBase, table_name, table)


class PostgresManager(metaclass=SingletonMeta):
    """PostgreSQL 数据库管理器 - 支持知识库和业务数据"""

    # 知识库 PostgreSQL URL 环境变量名
    KB_DATABASE_URL_ENV = "POSTGRES_URL"
    # API 与后台 Worker 可能同时启动。事务级 advisory lock 可避免空库首次
    # 初始化时两个进程并发执行 create_all，造成 PostgreSQL 类型/表创建冲突。
    SCHEMA_INIT_LOCK_ID = 2026081201

    def __init__(self):
        self.async_engine = None
        self.AsyncSession = None
        self.langgraph_pool = None
        self._initialized = False

    def initialize(self):
        """初始化数据库连接"""
        if self._initialized:
            return

        db_url = os.getenv(self.KB_DATABASE_URL_ENV)
        if not db_url:
            logger.error(
                f"环境变量 {self.KB_DATABASE_URL_ENV} 未设置，"
                "请在 docker-compose.yml 或 .env 中配置 PostgreSQL 连接字符串"
            )
            return

        try:
            # 创建异步 SQLAlchemy 引擎
            self.async_engine = create_async_engine(
                db_url,
                json_serializer=lambda obj: json.dumps(obj, ensure_ascii=False),
                json_deserializer=json.loads,
                pool_pre_ping=True,
                pool_recycle=1800,
                pool_size=10,
                max_overflow=20,
            )

            # 创建异步会话工厂
            self.AsyncSession = async_sessionmaker(
                bind=self.async_engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )

            # ==========================================
            # 2. 为 LangGraph 专门初始化一个原生 psycopg_pool
            # ==========================================
            # ⚠️ 注意：psycopg 不认识 "+asyncpg" 这样的 SQLAlchemy 方言标识。
            # 如果你的 db_url 是 "postgresql+asyncpg://user:pwd@host/db"，
            # 需要把它清洗成标准的 "postgresql://user:pwd@host/db"
            langgraph_db_url = db_url.replace("+asyncpg", "").replace("+psycopg", "")

            # 创建 LangGraph 专属连接池
            self.langgraph_pool = AsyncConnectionPool(
                conninfo=langgraph_db_url,
                max_size=10,  # 根据你的 Agent 并发情况设置，通常 5-10 足够了
                kwargs={"autocommit": True},  # LangGraph Checkpoint 强依赖 autocommit
            )

            self._initialized = True
            logger.info("PostgreSQL manager initialized")
        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL manager: {e}")
            # 不抛出异常，允许应用启动，但在使用时会报错

    def _check_initialized(self):
        """检查是否已初始化"""
        if not self._initialized:
            raise RuntimeError("PostgreSQL manager not initialized. Please check configuration.")

    async def create_tables(self):
        """创建所有表（知识库和业务表）"""
        self._check_initialized()
        async with self.async_engine.begin() as conn:
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": self.SCHEMA_INIT_LOCK_ID},
            )
            await conn.run_sync(KnowledgeBase.metadata.create_all)
            await conn.run_sync(BusinessBase.metadata.create_all)
        # create_all 只会创建新表，不会为既有表补列。内容平台 V2 必须在不
        # 重建 v1 数据库的情况下升级，因此显式执行幂等的兼容迁移。
        await self.ensure_content_schema()
        logger.info("PostgreSQL tables created/checked (knowledge + business)")

    async def create_business_tables(self):
        """创建所有业务数据表"""
        self._check_initialized()
        async with self.async_engine.begin() as conn:
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": self.SCHEMA_INIT_LOCK_ID},
            )
            await conn.run_sync(BusinessBase.metadata.create_all)
        logger.info("PostgreSQL business tables created/checked")

    async def drop_tables(self):
        """删除所有表（慎用！）"""
        self._check_initialized()
        async with self.async_engine.begin() as conn:
            await conn.run_sync(BusinessBase.metadata.drop_all)
            await conn.run_sync(KnowledgeBase.metadata.drop_all)
        logger.info("PostgreSQL tables dropped")

    async def ensure_knowledge_schema(self):
        """确保知识库 schema 包含所有必要字段"""
        self._check_initialized()
        stmts = [
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS embedding_model_spec VARCHAR(512)",
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS llm_model_spec VARCHAR(512)",
            "ALTER TABLE IF EXISTS knowledge_bases DROP COLUMN IF EXISTS embed_info",
            "ALTER TABLE IF EXISTS knowledge_bases DROP COLUMN IF EXISTS llm_info",
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS query_params JSONB",
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS additional_params JSONB",
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS share_config JSONB",
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS mindmap JSONB",
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS mindmap_file_ids JSONB",
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS mindmap_metadata JSONB",
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS sample_questions JSONB",
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS parent_id VARCHAR(64)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS original_filename VARCHAR(512)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS file_type VARCHAR(64)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS path VARCHAR(1024)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS minio_url VARCHAR(1024)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS markdown_file VARCHAR(1024)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS status VARCHAR(32)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS content_hash VARCHAR(128)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS file_size BIGINT",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS chunk_count INTEGER DEFAULT 0",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS token_count BIGINT DEFAULT 0",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS content_type VARCHAR(64)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS processing_params JSONB",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS is_folder BOOLEAN",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS error_message TEXT",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS created_by VARCHAR(64)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS updated_by VARCHAR(64)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
            "ALTER TABLE IF EXISTS evaluation_datasets ADD COLUMN IF NOT EXISTS created_by VARCHAR(64)",
            "ALTER TABLE IF EXISTS evaluation_datasets ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
            "ALTER TABLE IF EXISTS evaluation_datasets ADD COLUMN IF NOT EXISTS build_metadata JSONB",
            "ALTER TABLE IF EXISTS evaluation_runs ADD COLUMN IF NOT EXISTS name VARCHAR(255)",
            "ALTER TABLE IF EXISTS evaluation_runs ADD COLUMN IF NOT EXISTS metrics JSONB",
            "ALTER TABLE IF EXISTS evaluation_runs ADD COLUMN IF NOT EXISTS overall_score DOUBLE PRECISION",
            "ALTER TABLE IF EXISTS evaluation_runs ADD COLUMN IF NOT EXISTS total_items INTEGER",
            "ALTER TABLE IF EXISTS evaluation_runs ADD COLUMN IF NOT EXISTS completed_items INTEGER",
            "ALTER TABLE IF EXISTS evaluation_runs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ",
            "ALTER TABLE IF EXISTS evaluation_runs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ",
            "ALTER TABLE IF EXISTS evaluation_runs ADD COLUMN IF NOT EXISTS created_by VARCHAR(64)",
            "ALTER TABLE IF EXISTS evaluation_run_items ADD COLUMN IF NOT EXISTS gold_chunk_ids JSONB",
            "ALTER TABLE IF EXISTS evaluation_run_items ADD COLUMN IF NOT EXISTS gold_answer TEXT",
            "ALTER TABLE IF EXISTS evaluation_run_items ADD COLUMN IF NOT EXISTS generated_answer TEXT",
            "ALTER TABLE IF EXISTS evaluation_run_items ADD COLUMN IF NOT EXISTS retrieved_chunks JSONB",
            "ALTER TABLE IF EXISTS evaluation_run_items ADD COLUMN IF NOT EXISTS metrics JSONB",
            "ALTER TABLE IF EXISTS evaluation_run_items ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ",
            """
            CREATE TABLE IF NOT EXISTS evaluation_datasets (
                id SERIAL PRIMARY KEY,
                dataset_id VARCHAR(64) NOT NULL UNIQUE,
                kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                name VARCHAR(255) NOT NULL,
                description TEXT,
                item_count INTEGER DEFAULT 0,
                has_gold_chunks BOOLEAN DEFAULT FALSE,
                has_gold_answers BOOLEAN DEFAULT FALSE,
                build_metadata JSONB,
                created_by VARCHAR(64),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS evaluation_dataset_items (
                id SERIAL PRIMARY KEY,
                item_id VARCHAR(64) NOT NULL UNIQUE,
                dataset_id VARCHAR(64) NOT NULL REFERENCES evaluation_datasets(dataset_id) ON DELETE CASCADE,
                kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                item_index INTEGER NOT NULL,
                query_text TEXT NOT NULL,
                gold_chunk_ids JSONB,
                gold_answer TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_evaluation_dataset_items_dataset_index UNIQUE (dataset_id, item_index)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS evaluation_runs (
                id SERIAL PRIMARY KEY,
                run_id VARCHAR(64) NOT NULL UNIQUE,
                name VARCHAR(255) NOT NULL,
                kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                dataset_id VARCHAR(64) REFERENCES evaluation_datasets(dataset_id) ON DELETE SET NULL,
                status VARCHAR(32) DEFAULT 'running',
                retrieval_config JSONB,
                metrics JSONB,
                overall_score DOUBLE PRECISION,
                total_items INTEGER DEFAULT 0,
                completed_items INTEGER DEFAULT 0,
                started_at TIMESTAMPTZ DEFAULT NOW(),
                completed_at TIMESTAMPTZ,
                created_by VARCHAR(64)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS evaluation_run_items (
                id SERIAL PRIMARY KEY,
                run_id VARCHAR(64) NOT NULL REFERENCES evaluation_runs(run_id) ON DELETE CASCADE,
                dataset_item_id VARCHAR(64) REFERENCES evaluation_dataset_items(item_id) ON DELETE SET NULL,
                item_index INTEGER NOT NULL,
                query_text TEXT NOT NULL,
                gold_chunk_ids JSONB,
                gold_answer TEXT,
                generated_answer TEXT,
                retrieved_chunks JSONB,
                metrics JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_evaluation_run_items_run_index UNIQUE (run_id, item_index)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id SERIAL PRIMARY KEY,
                chunk_id VARCHAR(128) NOT NULL UNIQUE,
                file_id VARCHAR(64) NOT NULL REFERENCES knowledge_files(file_id) ON DELETE CASCADE,
                kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                start_char_pos INTEGER,
                end_char_pos INTEGER,
                start_token_pos INTEGER,
                end_token_pos INTEGER,
                graph_indexed BOOLEAN DEFAULT FALSE,
                ent_ids JSONB,
                tags JSONB,
                extraction_result JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            "ALTER TABLE IF EXISTS knowledge_chunks ADD COLUMN IF NOT EXISTS extraction_result JSONB",
            """
            CREATE TABLE IF NOT EXISTS knowledge_graph_entities (
                id SERIAL PRIMARY KEY,
                entity_id VARCHAR(64) NOT NULL UNIQUE,
                kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                normalized_name VARCHAR(512) NOT NULL,
                label VARCHAR(128) NOT NULL,
                name VARCHAR(512) NOT NULL,
                attributes JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_knowledge_graph_entities_identity UNIQUE (kb_id, normalized_name, label)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS knowledge_graph_entity_mentions (
                id SERIAL PRIMARY KEY,
                entity_id VARCHAR(64) NOT NULL REFERENCES knowledge_graph_entities(entity_id) ON DELETE CASCADE,
                kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                file_id VARCHAR(64) NOT NULL REFERENCES knowledge_files(file_id) ON DELETE CASCADE,
                chunk_id VARCHAR(128) NOT NULL REFERENCES knowledge_chunks(chunk_id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_knowledge_graph_entity_mentions_entity_chunk UNIQUE (entity_id, chunk_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS knowledge_graph_triples (
                id SERIAL PRIMARY KEY,
                triple_id VARCHAR(64) NOT NULL UNIQUE,
                kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                source_entity_id VARCHAR(64) NOT NULL REFERENCES knowledge_graph_entities(entity_id) ON DELETE CASCADE,
                target_entity_id VARCHAR(64) NOT NULL REFERENCES knowledge_graph_entities(entity_id) ON DELETE CASCADE,
                relation_type VARCHAR(256) NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS knowledge_graph_triple_mentions (
                id SERIAL PRIMARY KEY,
                triple_id VARCHAR(64) NOT NULL REFERENCES knowledge_graph_triples(triple_id) ON DELETE CASCADE,
                kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                file_id VARCHAR(64) NOT NULL REFERENCES knowledge_files(file_id) ON DELETE CASCADE,
                chunk_id VARCHAR(128) NOT NULL REFERENCES knowledge_chunks(chunk_id) ON DELETE CASCADE,
                text TEXT,
                extractor_type VARCHAR(128),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_knowledge_graph_triple_mentions_triple_chunk UNIQUE (triple_id, chunk_id)
            )
            """,
            "ALTER TABLE IF EXISTS knowledge_bases ALTER COLUMN kb_id TYPE VARCHAR(80)",
            "ALTER TABLE IF EXISTS knowledge_files ALTER COLUMN kb_id TYPE VARCHAR(80)",
            "ALTER TABLE IF EXISTS evaluation_datasets ALTER COLUMN kb_id TYPE VARCHAR(80)",
            "ALTER TABLE IF EXISTS evaluation_dataset_items ALTER COLUMN kb_id TYPE VARCHAR(80)",
            "ALTER TABLE IF EXISTS evaluation_runs ALTER COLUMN kb_id TYPE VARCHAR(80)",
            "CREATE INDEX IF NOT EXISTS idx_kb_type ON knowledge_bases(kb_type)",
            "CREATE INDEX IF NOT EXISTS idx_kb_name ON knowledge_bases(name)",
            "CREATE INDEX IF NOT EXISTS idx_kf_kb_id ON knowledge_files(kb_id)",
            "CREATE INDEX IF NOT EXISTS idx_kf_parent ON knowledge_files(parent_id)",
            "CREATE INDEX IF NOT EXISTS idx_kf_status ON knowledge_files(status)",
            "CREATE INDEX IF NOT EXISTS idx_kf_hash ON knowledge_files(content_hash)",
            "CREATE INDEX IF NOT EXISTS ix_evaluation_datasets_kb_id ON evaluation_datasets(kb_id)",
            (
                "CREATE INDEX IF NOT EXISTS ix_evaluation_dataset_items_dataset_index "
                "ON evaluation_dataset_items(dataset_id, item_index)"
            ),
            "CREATE INDEX IF NOT EXISTS ix_evaluation_dataset_items_kb_id ON evaluation_dataset_items(kb_id)",
            "CREATE INDEX IF NOT EXISTS ix_evaluation_runs_kb_id ON evaluation_runs(kb_id)",
            "CREATE INDEX IF NOT EXISTS ix_evaluation_runs_status ON evaluation_runs(status)",
            "CREATE INDEX IF NOT EXISTS ix_evaluation_runs_started ON evaluation_runs(started_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_evaluation_run_items_run_index ON evaluation_run_items(run_id, item_index)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_chunks_chunk_id ON knowledge_chunks(chunk_id)",
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_file_id ON knowledge_chunks(file_id)",
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_kb_id ON knowledge_chunks(kb_id)",
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_graph_indexed ON knowledge_chunks(graph_indexed)",
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_graph_entities_entity_id "
                "ON knowledge_graph_entities(entity_id)"
            ),
            "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_entities_kb_id ON knowledge_graph_entities(kb_id)",
            (
                "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_entity_mentions_kb_id "
                "ON knowledge_graph_entity_mentions(kb_id)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_entity_mentions_file_id "
                "ON knowledge_graph_entity_mentions(file_id)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_entity_mentions_chunk_id "
                "ON knowledge_graph_entity_mentions(chunk_id)"
            ),
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_graph_triples_triple_id "
                "ON knowledge_graph_triples(triple_id)"
            ),
            "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_triples_kb_id ON knowledge_graph_triples(kb_id)",
            (
                "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_triple_mentions_kb_id "
                "ON knowledge_graph_triple_mentions(kb_id)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_triple_mentions_file_id "
                "ON knowledge_graph_triple_mentions(file_id)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_triple_mentions_chunk_id "
                "ON knowledge_graph_triple_mentions(chunk_id)"
            ),
        ]

        async with self.async_engine.begin() as conn:
            for stmt in stmts:
                await conn.execute(text(stmt))

    async def ensure_business_schema(self):
        """确保业务 schema 包含后续新增字段（运行时 schema 演进）。"""
        self._check_initialized()
        stmts = [
            "ALTER TABLE IF EXISTS content_material_categories ADD COLUMN IF NOT EXISTS parent_id VARCHAR(64)",
            (
                "ALTER TABLE IF EXISTS content_material_categories ADD COLUMN IF NOT EXISTS "
                "industry_slug VARCHAR(80) NOT NULL DEFAULT 'uncategorized'"
            ),
            (
                "UPDATE content_material_categories SET industry_slug = 'uncategorized' "
                "WHERE industry_slug IS NULL"
            ),
            (
                "ALTER TABLE IF EXISTS content_material_categories "
                "ALTER COLUMN industry_slug SET DEFAULT 'uncategorized', "
                "ALTER COLUMN industry_slug SET NOT NULL"
            ),
            (
                "CREATE INDEX IF NOT EXISTS idx_content_material_category_owner_type_parent "
                "ON content_material_categories(owner_uid, material_type, parent_id)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS idx_content_material_category_owner_type_industry "
                "ON content_material_categories(owner_uid, material_type, industry_slug)"
            ),
            (
                "DO $$ BEGIN "
                "IF EXISTS ("
                "SELECT 1 FROM pg_constraint "
                "WHERE conrelid = 'content_material_categories'::regclass "
                "AND conname = 'content_material_categories_pkey' "
                "AND pg_get_constraintdef(oid) <> "
                "'PRIMARY KEY (owner_uid, material_type, id)'"
                ") THEN "
                "ALTER TABLE content_material_categories "
                "DROP CONSTRAINT content_material_categories_pkey; "
                "ALTER TABLE content_material_categories "
                "ADD CONSTRAINT content_material_categories_pkey "
                "PRIMARY KEY (owner_uid, material_type, id); "
                "END IF; END $$"
            ),
            (
                "ALTER TABLE IF EXISTS content_cover_image2_settings "
                "ADD COLUMN IF NOT EXISTS capabilities_json JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_cover_image2_settings "
                "ADD COLUMN IF NOT EXISTS verification_status VARCHAR(32) NOT NULL DEFAULT 'unverified'"
            ),
            ("ALTER TABLE IF EXISTS content_cover_image2_settings ADD COLUMN IF NOT EXISTS verified_at TIMESTAMP"),
            (
                "ALTER TABLE IF EXISTS content_cover_poster_templates "
                "DROP CONSTRAINT IF EXISTS uq_content_cover_poster_owner_checksum"
            ),
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_content_cover_poster_owner_checksum_active "
                "ON content_cover_poster_templates(owner_uid, checksum) WHERE deleted_at IS NULL"
            ),
            "ALTER TABLE IF EXISTS content_artifacts ADD COLUMN IF NOT EXISTS cover_asset_id VARCHAR(64)",
            "ALTER TABLE IF EXISTS content_artifacts ADD COLUMN IF NOT EXISTS cover_job_id VARCHAR(64)",
            "ALTER TABLE IF EXISTS content_artifact_versions ADD COLUMN IF NOT EXISTS cover_asset_id VARCHAR(64)",
            "ALTER TABLE IF EXISTS content_artifact_versions ADD COLUMN IF NOT EXISTS cover_job_id VARCHAR(64)",
            (
                "ALTER TABLE IF EXISTS content_artifacts ADD COLUMN IF NOT EXISTS "
                "hycanvas_design_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_artifact_versions ADD COLUMN IF NOT EXISTS "
                "hycanvas_design_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            "ALTER TABLE IF EXISTS content_distribution_jobs ADD COLUMN IF NOT EXISTS confirmed_by VARCHAR(255)",
            "ALTER TABLE IF EXISTS content_distribution_jobs ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMP",
            (
                "ALTER TABLE IF EXISTS content_distribution_results "
                "ADD COLUMN IF NOT EXISTS browser_session_id VARCHAR(80)"
            ),
            ("ALTER TABLE IF EXISTS content_distribution_results ADD COLUMN IF NOT EXISTS evidence_type VARCHAR(32)"),
            (
                "ALTER TABLE IF EXISTS content_distribution_results "
                "ADD COLUMN IF NOT EXISTS uncertain BOOLEAN NOT NULL DEFAULT FALSE"
            ),
            (
                "CREATE INDEX IF NOT EXISTS ix_content_distribution_results_browser_session_id "
                "ON content_distribution_results(browser_session_id)"
            ),
            "CREATE INDEX IF NOT EXISTS ix_content_artifacts_cover_asset_id ON content_artifacts(cover_asset_id)",
            "CREATE INDEX IF NOT EXISTS ix_content_artifacts_cover_job_id ON content_artifacts(cover_job_id)",
            (
                "CREATE INDEX IF NOT EXISTS ix_content_artifact_versions_cover_asset_id "
                "ON content_artifact_versions(cover_asset_id)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS ix_content_artifact_versions_cover_job_id "
                "ON content_artifact_versions(cover_job_id)"
            ),
            "ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS tool_dependencies JSONB DEFAULT '[]'::jsonb",
            "ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS mcp_dependencies JSONB DEFAULT '[]'::jsonb",
            "ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS skill_dependencies JSONB DEFAULT '[]'::jsonb",
            "ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS version VARCHAR(64)",
            "ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS source_type VARCHAR(32) NOT NULL DEFAULT 'upload'",
            (
                "ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS share_config JSONB NOT NULL "
                'DEFAULT \'{"access_level": "user", "department_ids": [], "user_uids": []}\'::jsonb'
            ),
            "ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS content_hash VARCHAR(128)",
            "ALTER TABLE IF EXISTS conversations ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE IF EXISTS mcp_servers ADD COLUMN IF NOT EXISTS env JSONB",
            """
            CREATE TABLE IF NOT EXISTS agent_envs (
                id SERIAL PRIMARY KEY,
                uid VARCHAR NOT NULL REFERENCES users(uid) ON DELETE CASCADE,
                env JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_agent_envs_uid UNIQUE (uid)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS agents (
                id SERIAL PRIMARY KEY,
                slug VARCHAR(80) NOT NULL UNIQUE,
                backend_id VARCHAR(64) NOT NULL,
                name VARCHAR(100) NOT NULL,
                description TEXT,
                icon VARCHAR(255),
                pics JSONB NOT NULL DEFAULT '[]'::jsonb,
                config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                share_config JSONB NOT NULL DEFAULT '{}'::jsonb,
                is_default BOOLEAN NOT NULL DEFAULT FALSE,
                is_subagent BOOLEAN NOT NULL DEFAULT FALSE,
                created_by VARCHAR(64),
                updated_by VARCHAR(64),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            "ALTER TABLE IF EXISTS agents ADD COLUMN IF NOT EXISTS backend_id VARCHAR(64)",
            "ALTER TABLE IF EXISTS agents ADD COLUMN IF NOT EXISTS share_config JSONB NOT NULL DEFAULT '{}'::jsonb",
            "ALTER TABLE IF EXISTS agents ADD COLUMN IF NOT EXISTS is_subagent BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE IF EXISTS agents ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE IF EXISTS agents ADD COLUMN IF NOT EXISTS config_version INTEGER NOT NULL DEFAULT 1",
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_agents_slug ON agents(slug)",
            "CREATE INDEX IF NOT EXISTS ix_agents_backend_id ON agents(backend_id)",
            "CREATE INDEX IF NOT EXISTS ix_agents_is_subagent ON agents(is_subagent)",
            "CREATE INDEX IF NOT EXISTS ix_agents_enabled ON agents(enabled)",
            "CREATE INDEX IF NOT EXISTS ix_agents_created_by ON agents(created_by)",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_agents_default
            ON agents(is_default)
            WHERE is_default IS TRUE
            """,
            """
            CREATE TABLE IF NOT EXISTS model_providers (
                id SERIAL PRIMARY KEY,
                provider_id VARCHAR(100) NOT NULL UNIQUE,
                display_name VARCHAR(100) NOT NULL,
                provider_type VARCHAR(32) NOT NULL DEFAULT 'openai',
                default_protocol VARCHAR(64),
                base_url VARCHAR(500) NOT NULL,
                embedding_base_url VARCHAR(500),
                rerank_base_url VARCHAR(500),
                models_endpoint VARCHAR(200),
                embedding_models_endpoint VARCHAR(200),
                rerank_models_endpoint VARCHAR(200),
                api_key_env VARCHAR(128),
                api_key VARCHAR(500),
                capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
                enabled_models JSONB NOT NULL DEFAULT '[]'::jsonb,
                headers_json JSONB,
                extra_json JSONB,
                is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                is_builtin BOOLEAN NOT NULL DEFAULT FALSE,
                created_by VARCHAR(100),
                updated_by VARCHAR(100),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            "ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS parent_agent_run_id VARCHAR(64)",
            "CREATE INDEX IF NOT EXISTS idx_agent_runs_uid_created ON agent_runs(uid, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_agent_runs_thread_created ON agent_runs(thread_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_agent_runs_status_updated ON agent_runs(status, updated_at)",
            """
            CREATE INDEX IF NOT EXISTS idx_agent_runs_parent_agent_run_created
            ON agent_runs(parent_agent_run_id, created_at DESC)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_agent_runs_subagent_lookup
            ON agent_runs(uid, thread_id, run_type, created_at DESC)
            """,
            "CREATE INDEX IF NOT EXISTS ix_conversations_is_pinned ON conversations(is_pinned)",
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_model_providers_provider_id ON model_providers(provider_id)",
            "CREATE INDEX IF NOT EXISTS ix_model_providers_is_enabled ON model_providers(is_enabled)",
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'content_employees'
                      AND column_name = 'login_port'
                      AND data_type = 'character varying'
                ) THEN
                    ALTER TABLE content_employees
                    ALTER COLUMN login_port TYPE JSONB
                    USING CASE
                        WHEN login_port IN ('pc_app', 'PC&APP') THEN '["pc","app"]'::jsonb
                        WHEN login_port IN ('app', 'APP') THEN '["app"]'::jsonb
                        WHEN login_port IN ('pc', 'PC') THEN '["pc"]'::jsonb
                        ELSE '["pc","app"]'::jsonb
                    END;
                END IF;
            END $$;
            """,
            (
                "ALTER TABLE IF EXISTS content_roles ADD COLUMN IF NOT EXISTS permissions "
                "JSONB NOT NULL DEFAULT '[]'::jsonb"
            ),
        ]
        async with self.async_engine.begin() as conn:
            for stmt in stmts:
                await conn.execute(text(stmt))

    async def ensure_content_schema(self):
        """为已存在的 contentSwarm v1 表补齐 V2 字段。

        新表由 ``BusinessBase.metadata.create_all`` 创建；这里仅维护既有表的
        ADD COLUMN/INDEX 语句。所有语句均幂等，API 与 Worker 重复启动安全。
        """

        self._check_initialized()
        stmts = [
            """
            ALTER TABLE IF EXISTS content_combination_rules
            ADD COLUMN IF NOT EXISTS schema_version INTEGER NOT NULL DEFAULT 2
            """,
            "ALTER TABLE IF EXISTS content_combination_rules ALTER COLUMN content_goal DROP NOT NULL",
            "ALTER TABLE IF EXISTS content_combination_rules ALTER COLUMN content_formula_code DROP NOT NULL",
            """
            ALTER TABLE IF EXISTS content_combination_rules
            ADD COLUMN IF NOT EXISTS combination_type VARCHAR(32)
            """,
            """
            ALTER TABLE IF EXISTS content_combination_rules
            ADD COLUMN IF NOT EXISTS method_members JSONB NOT NULL DEFAULT '[]'::jsonb
            """,
            """
            ALTER TABLE IF EXISTS content_combination_rules
            ADD COLUMN IF NOT EXISTS content_goal_codes JSONB NOT NULL DEFAULT '[]'::jsonb
            """,
            """
            ALTER TABLE IF EXISTS content_combination_rules
            ADD COLUMN IF NOT EXISTS scenario_description TEXT NOT NULL DEFAULT ''
            """,
            """
            ALTER TABLE IF EXISTS content_combination_rules
            ADD COLUMN IF NOT EXISTS required_variable_codes JSONB NOT NULL DEFAULT '[]'::jsonb
            """,
            """
            ALTER TABLE IF EXISTS content_combination_rules
            ADD COLUMN IF NOT EXISTS source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
            """,
            """
            ALTER TABLE IF EXISTS content_combination_rules
            ADD COLUMN IF NOT EXISTS title_formula_candidate_codes JSONB NOT NULL DEFAULT '[]'::jsonb
            """,
            """
            ALTER TABLE IF EXISTS content_combination_rules
            ADD COLUMN IF NOT EXISTS body_formula_candidate_codes JSONB NOT NULL DEFAULT '[]'::jsonb
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_content_combination_rules_schema_version
            ON content_combination_rules(schema_version)
            """,
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_content_combination_rule_schema_version'
                ) THEN
                    ALTER TABLE content_combination_rules
                    ADD CONSTRAINT ck_content_combination_rule_schema_version
                    CHECK (schema_version IN (2, 3));
                END IF;
            END $$
            """,
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_content_combination_rule_v2_required_fields'
                ) THEN
                    ALTER TABLE content_combination_rules
                    ADD CONSTRAINT ck_content_combination_rule_v2_required_fields
                    CHECK (
                        schema_version <> 2
                        OR (content_goal IS NOT NULL AND content_formula_code IS NOT NULL)
                    );
                END IF;
            END $$
            """,
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_content_combination_rule_v3_required_fields'
                ) THEN
                    ALTER TABLE content_combination_rules
                    ADD CONSTRAINT ck_content_combination_rule_v3_required_fields
                    CHECK (
                        schema_version <> 3
                        OR (
                            combination_type IN ('single', 'double', 'triple', 'quadruple')
                            AND jsonb_array_length(method_members) > 0
                            AND jsonb_array_length(title_formula_candidate_codes) > 0
                            AND jsonb_array_length(body_formula_candidate_codes) > 0
                        )
                    );
                END IF;
            END $$
            """,
            (
                "ALTER TABLE IF EXISTS content_combination_rules "
                "ADD COLUMN IF NOT EXISTS content_type_codes JSONB NOT NULL DEFAULT '[]'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_combination_rules "
                "ADD COLUMN IF NOT EXISTS industry_scope JSONB NOT NULL DEFAULT '[]'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_combination_rules "
                "ADD COLUMN IF NOT EXISTS channel_scope JSONB NOT NULL DEFAULT '[]'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_combination_rules "
                "ADD COLUMN IF NOT EXISTS narrative_axis_codes JSONB NOT NULL DEFAULT '[]'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_combination_rules "
                "ADD COLUMN IF NOT EXISTS title_pattern_codes JSONB NOT NULL DEFAULT '[]'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_combination_rules "
                "ADD COLUMN IF NOT EXISTS body_pattern_codes JSONB NOT NULL DEFAULT '[]'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_combination_rules "
                "ADD COLUMN IF NOT EXISTS required_evidence_types JSONB NOT NULL DEFAULT '[]'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_combination_rules "
                "ADD COLUMN IF NOT EXISTS hard_conditions JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_combination_rules "
                "ADD COLUMN IF NOT EXISTS score_weights JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            "ALTER TABLE IF EXISTS content_combination_rules ADD COLUMN IF NOT EXISTS fallback_rule_id VARCHAR(64)",
            "ALTER TABLE IF EXISTS content_tasks ADD COLUMN IF NOT EXISTS content_type_code VARCHAR(32)",
            (
                "ALTER TABLE IF EXISTS content_workflow_versions "
                "ADD COLUMN IF NOT EXISTS schema_version INTEGER NOT NULL DEFAULT 2"
            ),
            (
                "UPDATE content_workflow_versions SET schema_version = 3 "
                "WHERE version >= 3 AND schema_version = 2 "
                "AND COALESCE(definition_json->>'schema_version', '') = '3'"
            ),
            (
                "CREATE INDEX IF NOT EXISTS ix_content_workflow_versions_schema_version "
                "ON content_workflow_versions(schema_version)"
            ),
            "ALTER TABLE IF EXISTS content_workflow_versions ADD COLUMN IF NOT EXISTS definition_hash VARCHAR(64)",
            (
                "CREATE INDEX IF NOT EXISTS ix_content_workflow_versions_definition_hash "
                "ON content_workflow_versions(definition_hash)"
            ),
            "ALTER TABLE IF EXISTS content_tasks ADD COLUMN IF NOT EXISTS workflow_definition_hash VARCHAR(64)",
            "ALTER TABLE IF EXISTS content_tasks ADD COLUMN IF NOT EXISTS active_evidence_bundle_id VARCHAR(64)",
            (
                "CREATE INDEX IF NOT EXISTS ix_content_tasks_active_evidence_bundle_id "
                "ON content_tasks(active_evidence_bundle_id)"
            ),
            (
                "ALTER TABLE IF EXISTS content_media_evidence_items "
                "ADD COLUMN IF NOT EXISTS parser_version VARCHAR(128) NOT NULL DEFAULT 'unknown'"
            ),
            "ALTER TABLE IF EXISTS content_node_runs ADD COLUMN IF NOT EXISTS delegated_agent_run_id VARCHAR(64)",
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_content_node_runs_delegated_agent_run_id "
                "ON content_node_runs(delegated_agent_run_id)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS idx_content_node_runs_parent_node_attempt "
                "ON content_node_runs(agent_run_id, node_id, attempt)"
            ),
            "ALTER TABLE IF EXISTS content_tasks ADD COLUMN IF NOT EXISTS industry_pack_version_id VARCHAR(64)",
            "ALTER TABLE IF EXISTS content_tasks ADD COLUMN IF NOT EXISTS persona_profile_version_id VARCHAR(64)",
            "ALTER TABLE IF EXISTS content_tasks ADD COLUMN IF NOT EXISTS channel_profile_version_id VARCHAR(64)",
            "ALTER TABLE IF EXISTS content_tasks ADD COLUMN IF NOT EXISTS primary_narrative_axis VARCHAR(80)",
            (
                "ALTER TABLE IF EXISTS content_tasks "
                "ADD COLUMN IF NOT EXISTS selected_angle_json JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_tasks "
                "ADD COLUMN IF NOT EXISTS runtime_config_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            "ALTER TABLE IF EXISTS content_tasks ADD COLUMN IF NOT EXISTS selected_image_item_id VARCHAR(64)",
            "ALTER TABLE IF EXISTS content_tasks ADD COLUMN IF NOT EXISTS selected_poster_template_id VARCHAR(64)",
            (
                "CREATE INDEX IF NOT EXISTS ix_content_tasks_selected_image_item_id "
                "ON content_tasks(selected_image_item_id)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS ix_content_tasks_selected_poster_template_id "
                "ON content_tasks(selected_poster_template_id)"
            ),
            (
                "ALTER TABLE IF EXISTS content_industry_pack_versions "
                "ADD COLUMN IF NOT EXISTS schema_version INTEGER NOT NULL DEFAULT 2"
            ),
            ("UPDATE content_industry_pack_versions SET schema_version = 3 WHERE version >= 3 AND schema_version = 2"),
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_content_global_industry_pack_version "
                "ON content_industry_pack_versions(slug, version) WHERE tenant_id IS NULL"
            ),
            (
                "ALTER TABLE IF EXISTS content_industry_pack_versions "
                "ADD COLUMN IF NOT EXISTS compliance_policy JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_industry_pack_versions "
                "ADD COLUMN IF NOT EXISTS visual_policy JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_industry_pack_versions "
                "ADD COLUMN IF NOT EXISTS golden_samples JSONB NOT NULL DEFAULT '[]'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_industry_pack_versions "
                "ADD COLUMN IF NOT EXISTS negative_examples JSONB NOT NULL DEFAULT '[]'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_industry_pack_versions "
                "ADD COLUMN IF NOT EXISTS minimum_coverage DOUBLE PRECISION NOT NULL DEFAULT 1.0"
            ),
            (
                "ALTER TABLE IF EXISTS content_industry_pack_versions "
                "ADD COLUMN IF NOT EXISTS source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_industry_pack_versions "
                "ADD COLUMN IF NOT EXISTS changelog TEXT NOT NULL DEFAULT ''"
            ),
            (
                "ALTER TABLE IF EXISTS content_industry_pack_versions "
                "ADD COLUMN IF NOT EXISTS rollback_target_version_id VARCHAR(64)"
            ),
            (
                "ALTER TABLE IF EXISTS content_industry_pack_versions "
                "ADD COLUMN IF NOT EXISTS evaluation_report JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            "CREATE INDEX IF NOT EXISTS ix_content_tasks_content_type_code ON content_tasks(content_type_code)",
            (
                "CREATE INDEX IF NOT EXISTS ix_content_tasks_industry_pack_version_id "
                "ON content_tasks(industry_pack_version_id)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS ix_content_tasks_persona_profile_version_id "
                "ON content_tasks(persona_profile_version_id)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS ix_content_tasks_channel_profile_version_id "
                "ON content_tasks(channel_profile_version_id)"
            ),
            (
                "ALTER TABLE IF EXISTS content_artifacts "
                "ADD COLUMN IF NOT EXISTS content_type_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_artifacts "
                "ADD COLUMN IF NOT EXISTS angle_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_artifacts "
                "ADD COLUMN IF NOT EXISTS pattern_slot_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_artifacts "
                "ADD COLUMN IF NOT EXISTS persona_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_artifacts "
                "ADD COLUMN IF NOT EXISTS channel_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_artifacts "
                "ADD COLUMN IF NOT EXISTS compliance_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_artifacts "
                "ADD COLUMN IF NOT EXISTS runtime_config_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_artifacts "
                "ADD COLUMN IF NOT EXISTS edit_diff_snapshot JSONB NOT NULL DEFAULT '[]'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_artifacts "
                "ADD COLUMN IF NOT EXISTS evidence_usage_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_artifact_versions "
                "ADD COLUMN IF NOT EXISTS content_type_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_artifact_versions "
                "ADD COLUMN IF NOT EXISTS angle_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_artifact_versions "
                "ADD COLUMN IF NOT EXISTS pattern_slot_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_artifact_versions "
                "ADD COLUMN IF NOT EXISTS persona_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_artifact_versions "
                "ADD COLUMN IF NOT EXISTS channel_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_artifact_versions "
                "ADD COLUMN IF NOT EXISTS compliance_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_artifact_versions "
                "ADD COLUMN IF NOT EXISTS runtime_config_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_artifact_versions "
                "ADD COLUMN IF NOT EXISTS edit_diff_snapshot JSONB NOT NULL DEFAULT '[]'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS content_artifact_versions "
                "ADD COLUMN IF NOT EXISTS evidence_usage_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            "ALTER TABLE IF EXISTS content_employees ADD COLUMN IF NOT EXISTS avatar VARCHAR(1024)",
            "ALTER TABLE IF EXISTS content_employees ADD COLUMN IF NOT EXISTS bio TEXT",
            "ALTER TABLE IF EXISTS content_employees ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP",
            (
                "ALTER TABLE IF EXISTS content_variables "
                'ADD COLUMN IF NOT EXISTS ports JSONB NOT NULL DEFAULT \'["pc","app"]\'::jsonb'
            ),
            (
                "ALTER TABLE IF EXISTS content_variables "
                'ADD COLUMN IF NOT EXISTS editions JSONB NOT NULL DEFAULT \'["quick","pro"]\'::jsonb'
            ),
        ]
        async with self.async_engine.begin() as conn:
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": self.SCHEMA_INIT_LOCK_ID},
            )
            for stmt in stmts:
                await conn.execute(text(stmt))

    @property
    def is_postgresql(self) -> bool:
        """检查是否是 PostgreSQL 数据库"""
        if not self._initialized:
            return False
        return self.async_engine.dialect.name == "postgresql"

    async def get_async_session(self) -> AsyncSession:
        """获取异步数据库会话"""
        self.initialize()  # 确保已初始化
        return self.AsyncSession()

    @asynccontextmanager
    async def get_async_session_context(self):
        """获取异步数据库会话的上下文管理器"""
        self.initialize()  # 确保已初始化
        session = self.AsyncSession()
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"PostgreSQL async operation failed: {e}")
            raise
        finally:
            await session.close()

    async def close(self):
        """关闭引擎"""
        if self.async_engine:
            await self.async_engine.dispose()

        if self.langgraph_pool:
            await self.langgraph_pool.close()
        self.async_engine = None
        self.AsyncSession = None
        self.langgraph_pool = None
        self._initialized = False

    async def async_check_first_run(self):
        """检查是否首次运行（异步版本）- 检查用户表是否有数据"""
        from sqlalchemy import func, select

        self._check_initialized()
        async with self.get_async_session_context() as session:
            from yuxi.storage.postgres.models_business import User

            result = await session.execute(select(func.count(User.id)))
            count = result.scalar()
            return count == 0

    async def commit(self):
        """提交当前会话"""
        self._check_initialized()
        async with self.get_async_session_context():
            pass  # commit is automatic in context manager


# 创建全局 PostgreSQL 管理器实例
pg_manager = PostgresManager()
