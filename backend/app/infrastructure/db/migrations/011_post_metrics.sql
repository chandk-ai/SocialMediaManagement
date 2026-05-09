-- 011_post_metrics.sql
-- Per-post engagement snapshots — the raw data behind the analytics
-- dashboard and the learning loop that ranks selection strategies.
--
-- Why snapshots, not totals
-- ─────────────────────────
-- Engagement is time-resolved: a tweet does most of its work in the
-- first 90 minutes, a LinkedIn post takes 24-48h, a YouTube video can
-- still pick up views weeks out. We snapshot at T+1h and T+24h (job
-- queue schedules these via run.publish), and again on demand. The
-- analytics page can compute deltas between snapshots to show "first-
-- hour velocity", "1-to-24h decay", etc.
--
-- Aggregation: ``post_metrics_rollup`` is a denormalised table the
-- learning loop populates with per-(workflow, source, strategy, slot)
-- engagement averages. Reads on the dashboard hit the rollup; writes
-- happen in the engagement.aggregate worker job.
--
-- Idempotent.

CREATE TABLE IF NOT EXISTS smms.post_metrics (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES smms.organizations(id) ON DELETE CASCADE,
    post_id         UUID NOT NULL REFERENCES smms.posts(id) ON DELETE CASCADE,
    snapshotted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    likes           INT,
    comments        INT,
    shares          INT,
    impressions     INT,
    reach           INT,
    clicks          INT,
    saves           INT,
    plays           INT,                  -- video / audio
    watch_time_s    INT,                  -- video
    -- Catch-all for platform-specific weirdness (LinkedIn celebrate,
    -- Reddit upvote ratio, …).
    extra           JSONB NOT NULL DEFAULT '{}'::jsonb,
    fetch_error     TEXT,                 -- non-null when fetch failed
    fetch_source    TEXT NOT NULL DEFAULT 'auto'  -- 'auto' | 'manual' | 'webhook'
);
CREATE INDEX IF NOT EXISTS idx_post_metrics_post  ON smms.post_metrics (post_id, snapshotted_at DESC);
CREATE INDEX IF NOT EXISTS idx_post_metrics_org_t ON smms.post_metrics (org_id, snapshotted_at DESC);


-- Aggregated learnings — ranked by avg engagement so the selection
-- layer + adaptive scheduler can read it. Keys reflect the dimensions
-- we attribute against: workflow + source + strategy + (utc-)hour.
CREATE TABLE IF NOT EXISTS smms.post_metrics_rollup (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id             UUID NOT NULL REFERENCES smms.organizations(id) ON DELETE CASCADE,
    workflow_id        UUID,
    source_id          UUID,
    strategy           TEXT,
    platform_kind      TEXT,
    utc_hour           SMALLINT,         -- 0–23
    weekday            SMALLINT,         -- 0=Mon..6=Sun
    sample_size        INT NOT NULL,
    avg_engagement     DOUBLE PRECISION NOT NULL,
    p50_engagement     DOUBLE PRECISION,
    p90_engagement     DOUBLE PRECISION,
    last_recomputed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rollup_org_wf ON smms.post_metrics_rollup
    (org_id, workflow_id, last_recomputed_at DESC);
CREATE INDEX IF NOT EXISTS idx_rollup_org_src ON smms.post_metrics_rollup
    (org_id, source_id, last_recomputed_at DESC);
CREATE INDEX IF NOT EXISTS idx_rollup_org_strategy ON smms.post_metrics_rollup
    (org_id, strategy, last_recomputed_at DESC);
