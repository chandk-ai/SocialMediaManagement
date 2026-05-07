-- ============================================================================
-- 002 — tenancy fields on `organizations`
--
-- Adds the columns that drive `Organization.isolation_level` and friends.
-- All defaults preserve the existing pool-model behaviour, so applying this
-- migration is a no-op for current data.
-- ============================================================================

alter table smms.organizations
  add column if not exists isolation_level text
    not null default 'shared'
    check (isolation_level in ('shared','dedicated_db','dedicated_stack')),
  add column if not exists region text not null default 'us-east-1',
  add column if not exists dedicated_db_url text,            -- nullable
  add column if not exists storage_prefix text,
  add column if not exists encryption_key_id text,
  add column if not exists max_concurrent_runs integer not null default 5,
  add column if not exists rate_limit_per_minute integer,    -- null = inherit
  add column if not exists plan text not null default 'starter';

create index if not exists idx_orgs_isolation
  on smms.organizations(isolation_level)
  where isolation_level <> 'shared';

create index if not exists idx_orgs_region on smms.organizations(region);

-- Tags column for Platform multi-account routing (added by the targeting
-- feature; kept here for completeness if you're starting from migration 002).
alter table smms.platforms
  add column if not exists tags text[] not null default '{}';

create index if not exists idx_platforms_tags
  on smms.platforms using gin (tags);

-- workflow_runs: directive + initiator + trigger correlation + new status
alter table smms.workflow_runs
  add column if not exists directive text not null default '',
  add column if not exists initiator text,
  add column if not exists trigger_id uuid;

-- The new `awaiting_review` status — drop the old check (if any) and re-create
-- the constraint so it's permitted.
do $$
begin
  if exists (
    select 1 from information_schema.table_constraints
     where table_schema = 'smms' and table_name = 'workflow_runs'
       and constraint_name = 'workflow_runs_status_check'
  ) then
    alter table smms.workflow_runs drop constraint workflow_runs_status_check;
  end if;
end$$;

-- (No explicit re-add — `status` is `text` without check; if you want strict
-- validation, add a check constraint listing the new RunStatus values.)

-- Optional: Triggers + ReviewSessions tables (only if you choose to persist
-- them via Supabase). Until then the in-memory implementation is used.
create table if not exists smms.triggers (
  id              uuid primary key default uuid_generate_v4(),
  org_id          uuid not null references smms.organizations(id) on delete cascade,
  workflow_id     uuid not null references smms.workflows(id) on delete cascade,
  plugin_name     text not null,
  display_name    text not null,
  kind            text not null,
  is_active       boolean not null default true,
  config          jsonb not null default '{}'::jsonb,
  allowed_senders text[] not null default '{}',
  review_channel  text,
  review_recipient text,
  created_at      timestamptz not null default now(),
  last_fired_at   timestamptz
);
create index if not exists idx_triggers_org on smms.triggers(org_id);
alter table smms.triggers enable row level security;
create policy "org members can read triggers" on smms.triggers for select
  using (org_id = smms.current_org_id());
create policy "editors can write triggers" on smms.triggers
  for all using (org_id = smms.current_org_id())
            with check (org_id = smms.current_org_id());

create table if not exists smms.review_sessions (
  id              uuid primary key default uuid_generate_v4(),
  org_id          uuid not null references smms.organizations(id) on delete cascade,
  workflow_id     uuid not null references smms.workflows(id) on delete cascade,
  run_id          uuid not null references smms.workflow_runs(id) on delete cascade,
  channel         text not null,
  recipient       text not null,
  status          text not null default 'pending',
  drafts_snapshot jsonb not null default '[]'::jsonb,
  sent_message_ref text,
  decision_at     timestamptz,
  feedback        text,
  expires_at      timestamptz,
  created_at      timestamptz not null default now()
);
create index if not exists idx_reviews_open on smms.review_sessions(org_id, status);
create index if not exists idx_reviews_msg_ref on smms.review_sessions(channel, sent_message_ref)
  where sent_message_ref is not null;
alter table smms.review_sessions enable row level security;
create policy "org members can read reviews" on smms.review_sessions for select
  using (org_id = smms.current_org_id());
create policy "editors can write reviews" on smms.review_sessions
  for all using (org_id = smms.current_org_id())
            with check (org_id = smms.current_org_id());

alter publication supabase_realtime add table smms.review_sessions;
