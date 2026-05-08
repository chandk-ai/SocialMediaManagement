-- 009_source_items.sql
-- Persistent registry of every source item the system has ever seen.
--
-- Why this table exists
-- ─────────────────────
-- Pre-009, source items were ephemeral: each run called load_items(), which
-- pulled the first 10 items per source, fed the first 5 to the Planner, and
-- discarded the rest. The same RSS post could become a Twitter post on
-- Monday and again on Tuesday because nothing remembered.
--
-- This table gives every (source_id, external_id) a stable row that:
--   - tracks consumption status across runs (atomic claim-on-select)
--   - stores body_hash so we know when a row was edited upstream
--   - exposes `tags` so users can mark items "ready / hold / archived"
--   - records the run + post that consumed it (auditability)
--
-- The selection layer (see app/services/selection/) reads this table to
-- avoid re-using items, and writes to it as part of selection so concurrent
-- runs can't double-process.
--
-- UNIQUE (source_id, external_id) is the key invariant — every plugin's
-- fetch() yields a stable ``external_id`` (RSS guid, Notion page id, Drive
-- file id, etc.) and we rely on it for de-dup.
--
-- Idempotent.

CREATE TABLE IF NOT EXISTS smms.source_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES smms.organizations(id) ON DELETE CASCADE,
    source_id       UUID NOT NULL REFERENCES smms.sources(id) ON DELETE CASCADE,
    -- Stable identifier from the source plugin (RSS guid, Notion page id, …).
    external_id     TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    -- Hash of the item's body — set by the upsert path. When a re-fetch
    -- yields the same external_id but a different body, we know the source
    -- of truth was edited, so consumed status doesn't make sense any more.
    body_hash       TEXT,
    url             TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Consumption status — drives the selection layer's filter logic.
    status          TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'consumed', 'skipped', 'expired')),
    skipped_reason  TEXT,

    -- Provenance: who consumed it.
    consumed_by_post_id UUID REFERENCES smms.posts(id) ON DELETE SET NULL,
    consumed_by_run_id  UUID REFERENCES smms.workflow_runs(id) ON DELETE SET NULL,
    consumed_at         TIMESTAMPTZ,

    -- Lifecycle timestamps.
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Optional published-at from the upstream system (RSS pubDate, Notion
    -- last_edited, …). Used by freshness windows.
    item_published_at TIMESTAMPTZ,

    -- User-applied labels: 'ready', 'hold', 'pinned', 'reviewed', etc.
    tags            TEXT[] NOT NULL DEFAULT '{}'::text[],

    -- Same row at the same source = same item. The de-dup invariant.
    UNIQUE (source_id, external_id)
);

-- Selection's hot query: "what are the unseen items for this source, newest
-- first?". A single composite index covers it.
CREATE INDEX IF NOT EXISTS source_items_unseen_idx
    ON smms.source_items (source_id, status, item_published_at DESC NULLS LAST)
    WHERE status = 'new';

-- Per-org rollup queries (audit page, source detail).
CREATE INDEX IF NOT EXISTS source_items_org_recent_idx
    ON smms.source_items (org_id, last_seen_at DESC);

-- RLS: organizations only see their own rows.
ALTER TABLE smms.source_items ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE policyname = 'source_items_tenant_isolation'
    ) THEN
        CREATE POLICY source_items_tenant_isolation ON smms.source_items
            USING (org_id::text = current_setting('app.current_org_id', true));
    END IF;
END $$;
