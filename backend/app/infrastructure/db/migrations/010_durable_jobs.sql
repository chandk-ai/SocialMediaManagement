-- 010_durable_jobs.sql
-- Durable job queue for the orchestrator.
--
-- Why this table exists
-- ─────────────────────
-- Pre-010, every workflow run executed inline inside the API process. If the
-- pod restarted between Plan and Execute, that run was stranded — no retry,
-- no state, just a half-published post and a confused customer.
--
-- This table is a poor-man's Arq: rows ARE jobs. The worker loop claims one
-- with FOR UPDATE SKIP LOCKED, processes it, and updates status. If the
-- worker crashes mid-job, the row's started_at goes stale and a recovery
-- sweep re-queues it. If the worker hits a transient error, it bumps
-- ``attempt`` and reschedules with exponential backoff.
--
-- Idempotency: ``idempotency_key`` is UNIQUE per org so the same enqueue call
-- (e.g. retry from the API on a flaky 502) doesn't create duplicate jobs.
--
-- Phase decomposition: a single workflow run becomes 5+ rows here:
--   run_select  → run_plan → run_tailor → run_execute → run_critique → run_publish
-- Each phase is its own job so a failure in Publish doesn't lose the
-- already-generated drafts. The phase reads its predecessor's result from
-- the WorkflowRun row, so jobs stay small (just IDs + tiny payloads).
--
-- Idempotent.

CREATE TABLE IF NOT EXISTS smms.jobs (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id           UUID NOT NULL REFERENCES smms.organizations(id) ON DELETE CASCADE,
    kind             TEXT NOT NULL,                  -- e.g. 'run.select', 'run.publish', 'engagement.fetch'
    payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
    run_id           UUID,                            -- nullable; set when job is part of a workflow run
    status           TEXT NOT NULL DEFAULT 'queued', -- queued|running|succeeded|failed|cancelled|dead
    attempt          INT  NOT NULL DEFAULT 0,
    max_attempts     INT  NOT NULL DEFAULT 5,
    priority         INT  NOT NULL DEFAULT 100,      -- lower = sooner; admin can rush a job by lowering
    scheduled_for    TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_by       TEXT,                            -- worker hostname for debugging
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    error            TEXT,
    result           JSONB,
    idempotency_key  TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT jobs_status_chk CHECK (status IN
        ('queued','running','succeeded','failed','cancelled','dead'))
);

-- Hot path: the worker's claim query. Filter by status='queued' and
-- scheduled_for <= now(), order by (priority, scheduled_for, id).
CREATE INDEX IF NOT EXISTS idx_jobs_claim
    ON smms.jobs (status, scheduled_for, priority)
    WHERE status = 'queued';

CREATE INDEX IF NOT EXISTS idx_jobs_org_status   ON smms.jobs (org_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_run          ON smms.jobs (run_id) WHERE run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_kind_status  ON smms.jobs (kind, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_idem
    ON smms.jobs (org_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

-- Recovery sweep target: jobs that have been 'running' for too long are
-- almost certainly orphaned (worker crashed, network split). The recovery
-- task flips them back to 'queued' with attempt+=1.
CREATE INDEX IF NOT EXISTS idx_jobs_running_stale
    ON smms.jobs (started_at) WHERE status = 'running';

-- Auto-update updated_at.
CREATE OR REPLACE FUNCTION smms.tg_jobs_touch_updated() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS jobs_touch_updated ON smms.jobs;
CREATE TRIGGER jobs_touch_updated BEFORE UPDATE ON smms.jobs
    FOR EACH ROW EXECUTE FUNCTION smms.tg_jobs_touch_updated();

-- Circuit breaker state — per (org, target_kind, target_id). Tracks
-- consecutive failures, last failure time, and which state we're in
-- (closed / open / half-open). Read by adapters before any outbound call.
CREATE TABLE IF NOT EXISTS smms.circuit_state (
    org_id           UUID NOT NULL REFERENCES smms.organizations(id) ON DELETE CASCADE,
    target_kind      TEXT NOT NULL,        -- 'platform' | 'source' | 'llm'
    target_id        TEXT NOT NULL,        -- platform_id, source_id, provider name
    state            TEXT NOT NULL DEFAULT 'closed', -- closed|open|half_open
    consecutive_fail INT  NOT NULL DEFAULT 0,
    consecutive_ok   INT  NOT NULL DEFAULT 0,
    last_failure_at  TIMESTAMPTZ,
    last_success_at  TIMESTAMPTZ,
    opened_at        TIMESTAMPTZ,
    next_probe_at    TIMESTAMPTZ,
    failure_threshold INT NOT NULL DEFAULT 5,
    cooldown_seconds  INT NOT NULL DEFAULT 60,
    PRIMARY KEY (org_id, target_kind, target_id),
    CONSTRAINT circuit_state_chk CHECK (state IN ('closed','open','half_open'))
);
CREATE INDEX IF NOT EXISTS idx_circuit_open
    ON smms.circuit_state (state, next_probe_at) WHERE state IN ('open','half_open');

-- Tenant-scoped rate-limit budgets — consumed by a token-bucket service.
-- Augments the existing per-(plugin,account) rate-limit governor: this is
-- whole-org budget so a single noisy customer can't hog the queue.
CREATE TABLE IF NOT EXISTS smms.tenant_rate_state (
    org_id           UUID PRIMARY KEY REFERENCES smms.organizations(id) ON DELETE CASCADE,
    bucket_tokens    DOUBLE PRECISION NOT NULL DEFAULT 60.0,
    bucket_capacity  DOUBLE PRECISION NOT NULL DEFAULT 60.0,
    refill_per_sec   DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    last_refill_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
