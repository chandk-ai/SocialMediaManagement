-- 004_llm_usage.sql
-- Per-completion LLM usage ledger.
--
-- Goal: enforce `organizations.monthly_llm_budget_usd` by summing this
-- table month-to-date before each LLM call, and surface MTD spend in the
-- Settings page. The table is append-only — no updates, no deletes — so
-- a simple sum() is correct under any concurrency.
--
-- Indexed for the two access patterns we care about:
--   1. SUM(cost_usd) WHERE org_id=? AND billing_month=current  → budget check
--   2. WHERE org_id=? ORDER BY occurred_at DESC LIMIT 50        → audit / debug
--
-- Idempotent: safe to re-run.
CREATE TABLE IF NOT EXISTS smms.llm_usage (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES smms.organizations(id) ON DELETE CASCADE,
    -- Provider plugin name (anthropic, openai, gemini, ...)
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cost_usd        NUMERIC(12, 6) NOT NULL DEFAULT 0,
    -- First day of the month — fast equality lookups for the budget guard.
    -- e.g. 2026-05-01 for any call in May 2026.
    billing_month   DATE NOT NULL,
    -- Free-form context: workflow_run_id, agent_role, etc. Useful for
    -- attribution dashboards without forcing a schema change every time.
    context         JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS llm_usage_org_month_idx
    ON smms.llm_usage (org_id, billing_month);

CREATE INDEX IF NOT EXISTS llm_usage_org_occurred_idx
    ON smms.llm_usage (org_id, occurred_at DESC);

-- Row-level security — organizations only see their own rows.
ALTER TABLE smms.llm_usage ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'llm_usage_tenant_isolation'
    ) THEN
        CREATE POLICY llm_usage_tenant_isolation ON smms.llm_usage
            USING (org_id::text = current_setting('app.current_org_id', true));
    END IF;
END $$;
