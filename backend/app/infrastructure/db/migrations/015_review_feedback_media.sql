-- 015_review_feedback_media.sql
-- Adds ``feedback_media`` to review_sessions so reviewer-supplied media
-- (e.g. an image they attached to a Telegram reply with revision
-- feedback) can flow through to the regenerated post.
--
-- Why this exists
-- ─────────────────────
-- When a reviewer says "use this flyer for the image" while requesting
-- a revision, the attached photo is meaningful: the user wants THAT
-- image attached to the published post. Before this column there was
-- nowhere to persist the media URLs between the inbound webhook and
-- the agent re-run. The Revise loop would receive the text feedback
-- only and ignore the image.
--
-- Each entry is a {url, kind, alt_text} dict matching the existing
-- MediaAsset wire shape. ``_rerun_with_feedback`` reads this column
-- and forces those media onto the regenerated DraftPosts'
-- ``attached_media`` — bypassing the planner's source-media
-- selection so the reviewer's explicit choice always wins.
--
-- Idempotent.

ALTER TABLE smms.review_sessions
    ADD COLUMN IF NOT EXISTS feedback_media JSONB NOT NULL DEFAULT '[]'::jsonb;
