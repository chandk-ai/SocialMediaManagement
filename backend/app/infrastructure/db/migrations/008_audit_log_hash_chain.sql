-- 008_audit_log_hash_chain.sql
-- Niche #9: tamper-evident audit log.
--
-- Adds two columns to smms.audit_log: ``prev_hash`` (the row_hash of the
-- previous audit row in this org's chain) and ``row_hash`` (sha256 of a
-- canonical representation of THIS row, mixed with prev_hash). A BEFORE
-- INSERT trigger populates both atomically so application code can't
-- forget to compute them.
--
-- Verifying the chain is then a simple scan: walk by occurred_at,id and
-- confirm row_hash[i] == sha256(canonical(row[i]) || prev_hash[i]). Any
-- deletion or modification breaks the chain at that row, and every row
-- after it. Useful for SOC2-adjacent compliance and any "who changed
-- what when" question that needs to be answered without trusting the DB
-- admins.
--
-- Per-org chains (rather than one global chain) so the per-tenant verify
-- query stays cheap and so a chain break in tenant A doesn't pollute the
-- audit story for tenant B.
--
-- Idempotent.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE smms.audit_log
    ADD COLUMN IF NOT EXISTS prev_hash TEXT,
    ADD COLUMN IF NOT EXISTS row_hash  TEXT;

CREATE INDEX IF NOT EXISTS audit_log_chain_walk_idx
    ON smms.audit_log (org_id, occurred_at, id);

CREATE OR REPLACE FUNCTION smms.audit_log_hash_chain()
RETURNS TRIGGER AS $$
DECLARE
    last_hash TEXT;
    canonical TEXT;
BEGIN
    -- Find the prior link in this org's chain. NULL on the first row of an
    -- org, in which case prev_hash is the empty string (still a stable
    -- input to the hash so the chain start is deterministic).
    SELECT row_hash INTO last_hash
    FROM smms.audit_log
    WHERE org_id = NEW.org_id
    ORDER BY occurred_at DESC, id DESC
    LIMIT 1;

    NEW.prev_hash := COALESCE(last_hash, '');

    -- Canonical input — every meaningful field, pipe-separated. JSONB cast
    -- to text is the same on every Postgres so the hash is reproducible
    -- by any verifier (Python, JS, another Postgres). occurred_at is
    -- formatted via cast-to-text to keep the timezone marker stable.
    canonical := concat_ws('|',
        NEW.org_id::text,
        COALESCE(NEW.actor_type, ''),
        COALESCE(NEW.actor_id::text, ''),
        NEW.action,
        NEW.resource_type,
        COALESCE(NEW.resource_id::text, ''),
        COALESCE(NEW.before::text, ''),
        COALESCE(NEW.after::text, ''),
        COALESCE(NEW.occurred_at::text, now()::text),
        NEW.prev_hash
    );

    NEW.row_hash := encode(digest(canonical::bytea, 'sha256'), 'hex');

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_log_hash_chain_trigger ON smms.audit_log;
CREATE TRIGGER audit_log_hash_chain_trigger
    BEFORE INSERT ON smms.audit_log
    FOR EACH ROW
    EXECUTE FUNCTION smms.audit_log_hash_chain();

-- Convenience view: pre-computed expected_row_hash so the verify query
-- can compare existing row_hash against what the chain *should* be.
-- Returns rows in walk order; any row where row_hash <> expected_row_hash
-- is a tamper indicator (or a chain-policy migration artefact).
CREATE OR REPLACE VIEW smms.audit_log_verified AS
SELECT
    a.id,
    a.org_id,
    a.occurred_at,
    a.action,
    a.row_hash,
    a.prev_hash,
    encode(
        digest(
            concat_ws('|',
                a.org_id::text,
                COALESCE(a.actor_type, ''),
                COALESCE(a.actor_id::text, ''),
                a.action,
                a.resource_type,
                COALESCE(a.resource_id::text, ''),
                COALESCE(a.before::text, ''),
                COALESCE(a.after::text, ''),
                COALESCE(a.occurred_at::text, ''),
                a.prev_hash
            )::bytea,
            'sha256'
        ),
        'hex'
    ) AS expected_row_hash
FROM smms.audit_log a;
