# Supabase provisioning report — `SocialMediaManagement`

Run on 2026-05-06 against project `ukaulrxhegrqyfrirnkb` (`us-east-1`, Postgres 17).

## ✅ Migrations applied

| Name | Effect |
|---|---|
| `smms_001_init` | 9 tables in `smms` schema, `current_org_id()` + `set_updated_at()` helpers, RLS enabled on all tables, base policies, realtime publication on `posts` + `workflow_runs` |
| `smms_002_tenancy_triggers_reviews` | Tenancy fields on `organizations`, `tags` on `platforms`, directive/initiator/trigger_id on `workflow_runs`, `triggers` + `review_sessions` tables with RLS, realtime on `review_sessions` |
| `smms_003_advisor_remediation` | platform_credentials lockdown, function `search_path` pinned, 4 missing FK indexes added, redundant SELECT policies removed |

## ✅ Storage

| Bucket | Visibility | Max upload |
|---|---|---|
| `smms-media` | private | 50 MB |

## Realtime publication

Live updates broadcast on: `posts`, `workflow_runs`, `review_sessions`. Drives the dashboard's live trace and the in-app review queue.

## Advisor results after remediation

**Security:** 0 errors. The 3 originally-flagged items (`rls_enabled_no_policy` on `platform_credentials`, mutable search_path on the two helpers) are all resolved.

**Performance:** Only "unused index" entries remain — those are expected on a fresh DB and resolve themselves once the tables get real traffic. No indexes will be dropped.

## What's deliberately *not* done

- **No bootstrap Organization or User row.** Provisioning the first user via Supabase Auth's Admin API is the right way (so the user gets a real `auth.users` row + can sign in). Do this after deploying the frontend — see `docs/ONBOARDING.md`.
- **No `auth.users` rows created.** That's a Supabase-managed table; create via the Admin API or the dashboard's "Invite user" button.
- **TypeScript types** — the auto-generator only sees `public` schema. To expose `smms` to PostgREST: Supabase Dashboard → Project Settings → API → "Exposed schemas" → add `smms`. Then `supabase gen types` will emit the full typing.

## Connection string for the backend

When you deploy FastAPI (Render / Fly / Railway), use this for `SUPABASE_POSTGRES_CONNECTION_STRING`:

```
postgresql+asyncpg://postgres.ukaulrxhegrqyfrirnkb:<DB-PASSWORD>@aws-0-us-east-1.pooler.supabase.com:6543/postgres
```

Get `<DB-PASSWORD>` from **Supabase Dashboard → Project Settings → Database → Connection string** (or reset it under "Database password").
