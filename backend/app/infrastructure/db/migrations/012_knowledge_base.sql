-- 012_knowledge_base.sql
-- Per-org knowledge base for retrieval-augmented generation.
--
-- Why this exists
-- ───────────────
-- The brand-voice fingerprint task (#38) lives in Postgres as a single
-- compressed embedding per org — useful, but coarse. The Critique
-- agent and the Tailor stage want a *library* of brand examples so
-- they can pull "5 most relevant past examples" rather than "the avg
-- vibe of the corpus".
--
-- This table holds chunked documents:
--   * past high-performing posts (auto-ingested when engagement > threshold)
--   * brand style guides (manually uploaded)
--   * compliance docs (regulated industry pillar)
--   * founder bios / product one-pagers
--
-- Vector type: ``vector(1536)`` for OpenAI ada-002 sized embeddings.
-- The pgvector extension is required on the Supabase project; the
-- migration installs it idempotently.
--
-- The retrieval path uses cosine distance; the index choice (ivfflat
-- with lists=100) is tuned for ~10k–100k chunks per org. For larger
-- catalogs, swap to hnsw via a manual ALTER.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS smms.knowledge_documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES smms.organizations(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    source_kind     TEXT NOT NULL,        -- 'manual' | 'past_post' | 'style_guide' | 'compliance'
    source_ref      TEXT,                  -- e.g. post id when source_kind='past_post'
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kb_doc_org ON smms.knowledge_documents (org_id);


CREATE TABLE IF NOT EXISTS smms.knowledge_chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES smms.organizations(id) ON DELETE CASCADE,
    doc_id          UUID NOT NULL REFERENCES smms.knowledge_documents(id) ON DELETE CASCADE,
    chunk_idx       INT NOT NULL,
    content         TEXT NOT NULL,
    -- 1536 = OpenAI ada-002 / text-embedding-3-small. For other
    -- providers the column is wider than needed — that's fine; the
    -- left-hand bytes are zero-padded.
    embedding       vector(1536),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (doc_id, chunk_idx)
);
CREATE INDEX IF NOT EXISTS idx_kb_chunk_org ON smms.knowledge_chunks (org_id);
-- Cosine ANN — for SELECT ... ORDER BY embedding <=> :q LIMIT k. lists=100
-- is reasonable up to ~100k chunks per org; tune up for larger catalogs.
CREATE INDEX IF NOT EXISTS idx_kb_chunk_vec
    ON smms.knowledge_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Auto-touch updated_at on knowledge_documents.
CREATE OR REPLACE FUNCTION smms.tg_kb_doc_touch_updated() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS kb_doc_touch_updated ON smms.knowledge_documents;
CREATE TRIGGER kb_doc_touch_updated BEFORE UPDATE ON smms.knowledge_documents
    FOR EACH ROW EXECUTE FUNCTION smms.tg_kb_doc_touch_updated();
