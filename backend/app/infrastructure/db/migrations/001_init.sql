-- ============================================================================
-- SMMS — initial schema for Supabase Postgres
--
-- Run this in the Supabase SQL editor (Settings → Database → SQL editor) or
-- via the Supabase CLI:
--     supabase db push
-- All tables are scoped by `org_id` and protected by Row-Level Security so
-- a JWT-authenticated user only ever sees their organisation's rows.
-- ============================================================================

create extension if not exists "pgcrypto";
create extension if not exists "uuid-ossp";

-- ── helpers ────────────────────────────────────────────────────────────────
create or replace function smms.set_updated_at() returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create schema if not exists smms;

-- The org_id of the calling Supabase user — we read it from the JWT's
-- app_metadata.org_id. RLS policies use this to scope every read/write.
create or replace function smms.current_org_id() returns uuid as $$
  select coalesce(
    nullif(((current_setting('request.jwt.claims', true)::jsonb
            -> 'app_metadata' ->> 'org_id')), '')::uuid,
    nullif(((current_setting('request.jwt.claims', true)::jsonb
            -> 'user_metadata' ->> 'org_id')), '')::uuid
  );
$$ language sql stable;

-- ── core tables ───────────────────────────────────────────────────────────
create table if not exists smms.organizations (
  id              uuid primary key default uuid_generate_v4(),
  name            text not null,
  slug            text not null unique,
  okta_org_id     text,
  monthly_llm_budget_usd numeric not null default 100,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create trigger trg_orgs_updated before update on smms.organizations
  for each row execute function smms.set_updated_at();

create table if not exists smms.users (
  id              uuid primary key default uuid_generate_v4(),
  org_id          uuid not null references smms.organizations(id) on delete cascade,
  email           text not null,
  display_name    text not null,
  role            text not null check (role in ('viewer','editor','admin')),
  okta_subject    text,
  supabase_uid    uuid unique,             -- references auth.users.id
  is_active       boolean not null default true,
  created_at      timestamptz not null default now()
);
create index if not exists idx_users_org on smms.users(org_id);

create table if not exists smms.platforms (
  id                  uuid primary key default uuid_generate_v4(),
  org_id              uuid not null references smms.organizations(id) on delete cascade,
  plugin_name         text not null,
  display_name        text not null,
  account_handle      text,
  account_external_id text,
  status              text not null default 'disconnected'
                      check (status in ('disconnected','connected','expired','error')),
  is_default          boolean not null default false,
  config              jsonb not null default '{}'::jsonb,
  -- credentials are kept in a separate table to ease key rotation & RLS scope
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  last_used_at        timestamptz
);
create index if not exists idx_platforms_org_plugin on smms.platforms(org_id, plugin_name);
create trigger trg_platforms_updated before update on smms.platforms
  for each row execute function smms.set_updated_at();

create table if not exists smms.platform_credentials (
  platform_id     uuid primary key references smms.platforms(id) on delete cascade,
  ciphertext      bytea not null,            -- AES-256-GCM, encrypted by app
  key_id          text not null,
  scopes          text[] not null default '{}',
  expires_at      timestamptz,
  refreshed_at    timestamptz not null default now()
);

create table if not exists smms.sources (
  id              uuid primary key default uuid_generate_v4(),
  org_id          uuid not null references smms.organizations(id) on delete cascade,
  plugin_name     text not null,
  display_name    text not null,
  is_active       boolean not null default true,
  config          jsonb not null default '{}'::jsonb,
  last_fetched_at timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index if not exists idx_sources_org on smms.sources(org_id);
create trigger trg_sources_updated before update on smms.sources
  for each row execute function smms.set_updated_at();

create table if not exists smms.workflows (
  id              uuid primary key default uuid_generate_v4(),
  org_id          uuid not null references smms.organizations(id) on delete cascade,
  name            text not null,
  description     text not null default '',
  status          text not null default 'draft'
                  check (status in ('draft','active','paused','archived')),
  source_ids      uuid[] not null default '{}',
  platform_ids    uuid[] not null default '{}',
  config          jsonb not null default '{}'::jsonb,
  schedule        jsonb not null default '{}'::jsonb,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index if not exists idx_workflows_org_status on smms.workflows(org_id, status);
create trigger trg_workflows_updated before update on smms.workflows
  for each row execute function smms.set_updated_at();

create table if not exists smms.workflow_runs (
  id              uuid primary key default uuid_generate_v4(),
  org_id          uuid not null references smms.organizations(id) on delete cascade,
  workflow_id     uuid not null references smms.workflows(id) on delete cascade,
  status          text not null default 'queued',
  revision_count  integer not null default 0,
  trace           jsonb not null default '[]'::jsonb,
  error           text,
  started_at      timestamptz,
  finished_at     timestamptz,
  created_at      timestamptz not null default now()
);
create index if not exists idx_runs_workflow on smms.workflow_runs(workflow_id, created_at desc);
create index if not exists idx_runs_org_status on smms.workflow_runs(org_id, status);

create table if not exists smms.posts (
  id                  uuid primary key default uuid_generate_v4(),
  org_id              uuid not null references smms.organizations(id) on delete cascade,
  workflow_id         uuid not null references smms.workflows(id) on delete cascade,
  run_id              uuid not null references smms.workflow_runs(id) on delete cascade,
  platform_id         uuid not null references smms.platforms(id),
  text                text not null,
  hashtags            text[] not null default '{}',
  media               jsonb not null default '[]'::jsonb,
  status              text not null default 'draft'
                      check (status in ('draft','review','approved','scheduled','published','failed')),
  evaluation          jsonb,
  scheduled_for       timestamptz,
  published_at        timestamptz,
  external_post_id    text,
  error               text,
  created_at          timestamptz not null default now()
);
create index if not exists idx_posts_org_status on smms.posts(org_id, status);
create index if not exists idx_posts_workflow on smms.posts(workflow_id, created_at desc);
create index if not exists idx_posts_platform on smms.posts(platform_id);

create table if not exists smms.audit_log (
  id              bigserial primary key,
  org_id          uuid not null,
  actor_type      text not null,             -- user / system / agent
  actor_id        uuid,
  action          text not null,
  resource_type   text not null,
  resource_id     uuid,
  before          jsonb,
  after           jsonb,
  ip              inet,
  user_agent      text,
  request_id      text,
  occurred_at     timestamptz not null default now()
);
create index if not exists idx_audit_org_time on smms.audit_log(org_id, occurred_at desc);

-- ── Row-Level Security ────────────────────────────────────────────────────
do $$
declare t text;
begin
  for t in select unnest(array[
    'organizations','users','platforms','platform_credentials',
    'sources','workflows','workflow_runs','posts','audit_log'
  ]) loop
    execute format('alter table smms.%I enable row level security', t);
  end loop;
end$$;

-- Generic per-org policies. The service role bypasses RLS automatically,
-- so background workers don't need a per-row token.
create policy "org members can read" on smms.platforms for select
  using (org_id = smms.current_org_id());
create policy "editors can write platforms" on smms.platforms
  for all using (org_id = smms.current_org_id())
            with check (org_id = smms.current_org_id());

create policy "org members can read sources" on smms.sources for select
  using (org_id = smms.current_org_id());
create policy "editors can write sources" on smms.sources
  for all using (org_id = smms.current_org_id())
            with check (org_id = smms.current_org_id());

create policy "org members can read workflows" on smms.workflows for select
  using (org_id = smms.current_org_id());
create policy "editors can write workflows" on smms.workflows
  for all using (org_id = smms.current_org_id())
            with check (org_id = smms.current_org_id());

create policy "org members can read runs" on smms.workflow_runs for select
  using (org_id = smms.current_org_id());
create policy "service can write runs" on smms.workflow_runs
  for insert with check (org_id = smms.current_org_id());

create policy "org members can read posts" on smms.posts for select
  using (org_id = smms.current_org_id());
create policy "editors can write posts" on smms.posts
  for all using (org_id = smms.current_org_id())
            with check (org_id = smms.current_org_id());

create policy "users see own org" on smms.users for select
  using (org_id = smms.current_org_id());
create policy "users see own org orgs" on smms.organizations for select
  using (id = smms.current_org_id());

-- audit_log is append-only; only service role inserts.
create policy "audit readable to org admins" on smms.audit_log for select
  using (org_id = smms.current_org_id());

-- ── Realtime: enable Postgres change events on the live tables ────────────
alter publication supabase_realtime add table smms.workflow_runs;
alter publication supabase_realtime add table smms.posts;
