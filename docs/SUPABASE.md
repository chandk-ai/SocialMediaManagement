# Supabase as the persistence layer

Supabase is the recommended primary store for SMMS. It gives us managed Postgres with Row-Level Security, OAuth-ready Auth, S3-style Storage, and a Realtime channel for streaming agent progress to the UI — all of which the system maps onto cleanly.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                       Frontend (Next.js)                         │
│   @supabase/ssr (browser auth)  ──────────►  /api/proxy/…        │
│   useRunStream() ←── Realtime ◄── Supabase Realtime websocket    │
└──────────────────────┬─────────────────────┬─────────────────────┘
                       │ Bearer (Supabase JWT)
┌──────────────────────▼─────────────────────▼─────────────────────┐
│                       Backend (FastAPI)                          │
│  SupabaseAuth (verify HS256 JWT)  ──►  Principal {org_id, role}  │
│                                                                  │
│  SupabaseRepository  ◄── async SQLAlchemy + asyncpg ──────┐      │
│                                                            │      │
│  SupabaseStorage (signed URLs)  ──► supabase-py ──────────┤      │
│  SupabaseRealtime (broadcast) ─────► realtime/v1/broadcast│      │
└────────────────────────────────────────────────────────────┼─────┘
                                                              │
                                  ┌───────────────────────────▼────┐
                                  │         Supabase project        │
                                  │  Postgres · Auth · Storage ·    │
                                  │  Realtime (postgres_changes &   │
                                  │  broadcast)                     │
                                  └─────────────────────────────────┘
```

Two traffic paths:
- **High-throughput SQL** uses **asyncpg/SQLAlchemy** straight against the Supabase Postgres pooler URL. RLS still applies whenever the connection sets the `request.jwt.claims` GUC; the service role key bypasses it for trusted backend tasks.
- **Auth, Storage, Realtime** use the official **`supabase-py`** SDK on the backend and **`@supabase/supabase-js`** on the frontend — what each library is best at.

## Setup

```bash
# 1. Create / pick your Supabase project
# 2. Run the schema
supabase db push       # uses backend/app/infrastructure/db/migrations/001_init.sql
# (or paste the SQL into Supabase SQL editor)

# 3. Create the storage bucket
supabase storage create smms-media

# 4. Fill .env (see .env.example)
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...
SUPABASE_JWT_SECRET=...                 # Settings → API → JWT settings
SUPABASE_POSTGRES_CONNECTION_STRING=postgresql+asyncpg://postgres:<pwd>@aws-0-us-east-1.pooler.supabase.com:6543/postgres
PERSISTENCE_BACKEND=auto                # auto picks supabase when SUPABASE_URL is set
AUTH_BACKEND=auto                       # auto picks supabase when SUPABASE_URL is set
```

That's it — restart the backend and it's now backed by Supabase.

## What lives where

| Concern | Component | Notes |
|---|---|---|
| Schema | `backend/app/infrastructure/db/migrations/001_init.sql` | Tables + RLS policies + realtime publication |
| ORM models | `backend/app/infrastructure/db/models.py` | Mirrors the schema |
| Repos | `backend/app/repositories/supabase_repo.py` | Async SQLAlchemy implementations of the repo Ports |
| Auth | `backend/app/infrastructure/supabase/auth.py` | HS256 JWT verification, claims → `Principal` |
| Storage | `backend/app/infrastructure/supabase/storage.py` | Upload/sign/delete in the configured bucket |
| Realtime | `backend/app/infrastructure/supabase/realtime.py` | Broadcast events from worker; `workflow_runs`/`posts` tables auto-stream |
| Frontend auth | `frontend/lib/auth/supabase.ts` | Browser client + access-token helper |
| Frontend live trace | `frontend/lib/hooks/useRunStream.ts` | Subscribes to Postgres-changes for a run |

## Multi-tenancy & RLS

Every table is `enable row level security`. Policies use `smms.current_org_id()` which reads `app_metadata.org_id` from the JWT. To grant a new user access to an org, set the claim:

```bash
curl -X PUT "$SUPABASE_URL/auth/v1/admin/users/<user_id>" \
  -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
  -d '{"app_metadata":{"org_id":"<org_uuid>","role":"editor"}}'
```

The backend's service-role key bypasses RLS so workers and webhooks can write across orgs as needed.

## Realtime UX

The orchestrator persists each agent step to `workflow_runs.trace`. The frontend's `useRunStream(runId)` hook subscribes to Postgres-changes for that row and re-renders as soon as the agents progress — no polling, no extra plumbing.

## Migrating between backends

Set `PERSISTENCE_BACKEND=memory` for tests and `PERSISTENCE_BACKEND=supabase` for production. The Repository contract is identical, so application code, services, and the agent orchestrator do not change.
