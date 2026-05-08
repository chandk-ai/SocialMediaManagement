-- 006_review_quorum.sql
-- Adds quorum support to ReviewSession.
--
-- A workflow's trigger can specify ``quorum_required > 1`` (e.g. "3 of 5 admins
-- must approve before publishing"). Each tap on the Telegram inline keyboard
-- inserts a vote into the JSONB ``quorum_votes`` array; once N approve votes
-- accumulate, the session promotes to APPROVED and the orchestrator resumes.
--
-- A single REJECT vote is a hard veto — no quorum needed to kill a draft.
-- This matches how editorial boards actually work.
--
-- ``quorum_votes`` shape: list of {actor_id, kind, at}.
-- Idempotent.

ALTER TABLE smms.review_sessions
    ADD COLUMN IF NOT EXISTS quorum_required INTEGER NOT NULL DEFAULT 1
        CHECK (quorum_required >= 1),
    ADD COLUMN IF NOT EXISTS quorum_votes    JSONB   NOT NULL DEFAULT '[]'::jsonb;

-- Cheap predicate index for the open-sessions query that includes quorum_required.
CREATE INDEX IF NOT EXISTS review_sessions_quorum_idx
    ON smms.review_sessions (org_id, status)
    WHERE status = 'pending';
