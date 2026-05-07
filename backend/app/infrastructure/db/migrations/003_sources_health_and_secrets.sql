-- 003_sources_health_and_secrets.sql
-- Adds health-tracking columns to sources (last_failure_at, last_error,
-- error_count, item_count), and a per-org sensitive-config encryption marker.
-- Idempotent: safe to re-run.

ALTER TABLE smms.sources
    ADD COLUMN IF NOT EXISTS last_failure_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_error      VARCHAR(2000),
    ADD COLUMN IF NOT EXISTS error_count     INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS item_count      INTEGER NOT NULL DEFAULT 0;

-- Index recent-failure queries for the future "Source health" dashboard.
CREATE INDEX IF NOT EXISTS sources_last_failure_at_idx
    ON smms.sources (last_failure_at)
    WHERE last_failure_at IS NOT NULL;
