-- 005_org_invitations.sql
-- Pending invitations for the email-claim auto-join flow.
--
-- An admin pre-creates a row here ("alice@acme.com → editor"). When Alice
-- signs in (via OAuth or magic-link, with that email), the auth path
-- resolves her Principal by matching this row, creates her smms.users row,
-- and marks the invitation claimed. No invite tokens, no email sent — the
-- email itself is the claim key. Designed for internal admin tools where
-- the admin shares the URL out-of-band (Slack, in person, whatever).
--
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS smms.org_invitations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES smms.organizations(id) ON DELETE CASCADE,
    email           TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('viewer','editor','admin')),
    invited_by      UUID REFERENCES smms.users(id) ON DELETE SET NULL,
    invited_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Set once the invited user signs in and we promote them to a member.
    claimed_at      TIMESTAMPTZ,
    claimed_by      UUID REFERENCES smms.users(id) ON DELETE SET NULL,
    revoked_at      TIMESTAMPTZ
);

-- Email comparison is case-insensitive; we lowercase on write but enforce
-- uniqueness on lower() so a stray-cased dup can't sneak through.
CREATE UNIQUE INDEX IF NOT EXISTS org_invitations_unique_pending
    ON smms.org_invitations (org_id, lower(email))
    WHERE claimed_at IS NULL AND revoked_at IS NULL;

-- Lookups during the auto-claim path: "is there a pending invite for this email?"
CREATE INDEX IF NOT EXISTS org_invitations_email_pending_idx
    ON smms.org_invitations (lower(email))
    WHERE claimed_at IS NULL AND revoked_at IS NULL;

-- RLS: admins of the org can see / manage their invitations.
ALTER TABLE smms.org_invitations ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE policyname = 'org_invitations_tenant_isolation'
    ) THEN
        CREATE POLICY org_invitations_tenant_isolation ON smms.org_invitations
            USING (org_id::text = current_setting('app.current_org_id', true));
    END IF;
END $$;
