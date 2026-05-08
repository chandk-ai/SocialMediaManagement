-- 007_post_source_link.sql
-- Niche #3: Notion/Airtable as social CMS.
--
-- Each row in a Notion DB (CMS mode) becomes a Post; after publish we want
-- to call back into the source plugin to mark the row as Published with
-- the live URL. To do that across the review checkpoint (which can pause
-- the run for hours) we persist the link (source_id, external_id) on the
-- Post row itself.
--
-- ON DELETE SET NULL on source_id so deleting a Source doesn't cascade
-- away historical posts.
-- Idempotent: safe to re-run.

ALTER TABLE smms.posts
    ADD COLUMN IF NOT EXISTS source_id          UUID
        REFERENCES smms.sources(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS source_external_id TEXT;

CREATE INDEX IF NOT EXISTS posts_source_external_idx
    ON smms.posts (source_id, source_external_id)
    WHERE source_id IS NOT NULL;
