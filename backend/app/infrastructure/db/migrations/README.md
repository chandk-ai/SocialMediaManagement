# Database migrations

The system uses **Supabase Postgres** as its primary store. Migrations live as plain `.sql` files so you can run them via:

- the Supabase **SQL editor** (paste the file contents)
- the Supabase **CLI**: `supabase db push`
- Alembic (a stub `alembic.ini` is included for teams that prefer a Python-driven migration flow)

## Files

| File | Purpose |
|---|---|
| `001_init.sql` | All core tables (`organizations`, `users`, `platforms`, `sources`, `workflows`, `workflow_runs`, `posts`, `audit_log`), indexes, triggers, and **Row-Level Security** policies |
| `001_init.sql` (continued) | Adds `workflow_runs` + `posts` to `supabase_realtime` so the frontend can subscribe live |

## RLS overview

- Every table is `enable row level security`.
- `smms.current_org_id()` reads `app_metadata.org_id` from the calling user's JWT.
- Per-org policies allow members to read and editors to write.
- The **service role key** bypasses RLS — only the backend uses it (e.g. Celery workers).

## Bootstrapping a new project

```bash
# 1. Create the schema
supabase db push                    # or paste 001_init.sql in the SQL editor

# 2. Create the storage bucket the app uses for media
supabase storage create smms-media

# 3. Provision your first user — set role/org via Admin API
curl -X POST "$SUPABASE_URL/auth/v1/admin/users" \
  -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
  -d '{
        "email": "you@example.com",
        "password": "********",
        "email_confirm": true,
        "app_metadata": {"org_id": "<your org uuid>", "role": "admin"}
      }'
```
