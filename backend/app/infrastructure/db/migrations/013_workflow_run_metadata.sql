-- 013_workflow_run_metadata.sql
-- Adds the missing ``metadata`` JSONB column that the durable run
-- engine (Pillar 1) relies on for cross-phase state-passing.
--
-- Why this exists
-- ─────────────────────
-- The durable runner decomposes a workflow run into five jobs
-- (select → plan → tailor → execute → critique → publish), each
-- claimed independently by a worker. State that needs to flow from
-- one phase to the next — chosen items, planner blueprints, drafts,
-- critique decisions, published post ids — is keyed under
-- ``run.metadata`` so a re-claim resumes cleanly.
--
-- Pillar 1 added ``metadata: dict`` to the WorkflowRun domain entity
-- but the database table + ORM mapping were never updated. Each
-- phase's writes vanished silently at the ORM layer, so every
-- downstream phase saw ``{}`` and produced empty output. The
-- symptom was workflows "running successfully" through all five
-- jobs but ending with ``error="no posts produced"``.
--
-- Idempotent.

ALTER TABLE smms.workflow_runs
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Cheap index on the publish phase's output for the dashboard's
-- "runs that actually produced posts" query. Not strictly required
-- but the rollup view will want it.
CREATE INDEX IF NOT EXISTS idx_workflow_runs_metadata_post_ids
    ON smms.workflow_runs USING gin (metadata jsonb_path_ops);
