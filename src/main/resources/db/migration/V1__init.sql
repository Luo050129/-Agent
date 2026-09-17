-- ============================================================
-- 白山面试辅助 Agent · 初始 Schema
-- 依赖：PostgreSQL 16 + pgvector 扩展（docker-compose 已内置）
-- 注意：knowledge_chunk.embedding 为 vector(1024)，
--      切换 embedding 模型（如 text-embedding-3-small=1536）时需同步修改。
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------- 简历 ----------
CREATE TABLE resume (
    id             UUID PRIMARY KEY,
    file_name      VARCHAR(255) NOT NULL,
    mime_type      VARCHAR(120),
    file_size      BIGINT,
    raw_text       TEXT,
    analysis_json  TEXT,
    overall_score  INT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 知识库文档 ----------
CREATE TABLE knowledge_document (
    id          UUID PRIMARY KEY,
    file_name   VARCHAR(255) NOT NULL,
    mime_type   VARCHAR(120),
    file_size   BIGINT,
    doc_type    VARCHAR(32)  NOT NULL DEFAULT 'KNOWLEDGE',
    chunk_count INT          NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ---------- 知识库分块（向量检索） ----------
CREATE TABLE knowledge_chunk (
    id           BIGSERIAL PRIMARY KEY,
    document_id  UUID NOT NULL REFERENCES knowledge_document (id) ON DELETE CASCADE,
    chunk_index  INT  NOT NULL,
    content      TEXT NOT NULL,
    embedding    vector(1024) NOT NULL
);

CREATE INDEX idx_knowledge_chunk_hnsw
    ON knowledge_chunk USING hnsw (embedding vector_cosine_ops);

-- ---------- 面试会话 ----------
CREATE TABLE interview_session (
    id            UUID PRIMARY KEY,
    resume_id     UUID NOT NULL REFERENCES resume (id) ON DELETE CASCADE,
    mode          VARCHAR(16) NOT NULL,           -- TECHNICAL / BEHAVIORAL / MIXED
    jd_text       TEXT,
    status        VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE / FINISHED
    question_count INT NOT NULL DEFAULT 0,
    context_json  TEXT,                            -- 面试官角色上下文（简历摘要+JD+知识库）
    summary_json  TEXT,                            -- 面试总结（FinishSummary）
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at      TIMESTAMPTZ
);

CREATE INDEX idx_session_resume ON interview_session (resume_id);

-- ---------- 面试消息 ----------
CREATE TABLE interview_message (
    id             BIGSERIAL PRIMARY KEY,
    session_id     UUID NOT NULL REFERENCES interview_session (id) ON DELETE CASCADE,
    role           VARCHAR(16) NOT NULL,           -- USER / ASSISTANT
    kind           VARCHAR(16) NOT NULL,           -- QUESTION / ANSWER / EVALUATION / SYSTEM
    content        TEXT,
    evaluation_json TEXT,                          -- EvaluationResult
    score          INT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_message_session ON interview_message (session_id, id);
