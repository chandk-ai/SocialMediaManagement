# SMMS — Operational runbook

Tactical doc for whoever is on-call. Optimised for "I have a problem, what
do I do" rather than "explain the architecture."

For architecture, read `docs/ARCHITECTURE.md`. For feature-by-feature
walk-throughs, read `docs/NICHE_FEATURES.md` or the in-app `/help` page.

---

## Deployment topology

```
   ┌─────────────────────┐         ┌────────────────────┐
   │    Vercel           │         │    Render          │
   │    (frontend)       │         │                    │
   │    Next.js 14       │ ◀──────▶│  smms-api  (web)   │
   │    Tailwind         │  HTTPS  │  smms-worker       │
   │                     │  proxy  │  smms-beat         │
   └─────────┬───────────┘         │  smms-redis        │
             │                     └────────┬───────────┘
             │ Supabase Auth (browser)      │
             ▼                              ▼
   ┌─────────────────────┐         ┌────────────────────┐
   │    Supabase         │         │  External APIs     │
   │    Postgres + RLS   │         │  Anthropic / OpenAI│
   │    Auth + Storage   │         │  LinkedIn / X / IG │
   │                     │         │  Telegram bots…    │
   └─────────────────────┘         └────────────────────┘
```

Components:
- **Vercel project** `frontend-mu-hazel-23` — Next.js, App Router, no server state.
- **Render blueprint** — `render.yaml` provisions:
  - `smms-api` (web) — FastAPI, the public REST surface
  - `smms-worker` (worker) — Celery, drains `workflows`, `publish`, `publish_dlq` queues
  - `smms-beat` (worker) — Celery Beat, drives scheduled workflows + nightly metrics
  - `smms-redis` (redis) — Celery broker + result backend + rate-limit token bucket
- **Supabase project** `ukaulrxhegrqyfrirnkb` (`SocialMediaManagement`) — Postgres + Auth + Storage. Schema lives under `smms.*`, RLS on every table.

---

## Standard procedures

### Deploy a new release

1. Push to `main`. Vercel + Render auto-deploy from the git push.
2. Wait for both deploys to go green:
   - Vercel: dashboard → Deployments → latest is "Ready"
   - Render: dashboard → each service → latest deploy is "Live"
3. Verify health: `curl https://<api-host>/api/v1/ready` returns `{"status":"ready"}` with both `postgres` and `redis` checks `ok: true`.
4. Run the smoke test: `python scripts/smoke_test.py` (read-only) or `--write` for the full round-trip.
5. If anything fails: see "Rollback" below.

### Apply a database migration

Migrations live in `backend/app/infrastructure/db/migrations/NNN_*.sql`. They are NOT auto-applied on deploy — you apply them deliberately.

**Production**: use the Supabase MCP tool from your dev machine:

```python
# (run from a Claude session that has the Supabase MCP connected)
mcp__74d58e67-cb78-4314-b03a-c8fbfe2df383__apply_migration(
    project_id="ukaulrxhegrqyfrirnkb",
    name="smms_NNN_descriptive_name",
    query=open("backend/app/infrastructure/db/migrations/NNN_xxx.sql").read(),
)
```

**Local** (Supabase CLI):

```bash
supabase db push --file backend/app/infrastructure/db/migrations/NNN_xxx.sql
```

Every migration in this repo is **idempotent** (uses `IF NOT EXISTS` / `IF EXISTS`) so re-running is safe.

To list applied migrations:

```sql
SELECT version, name FROM supabase_migrations.schema_migrations ORDER BY version;
```

### Rollback a release

**Frontend (Vercel)**: dashboard → Deployments → find the last green deploy → "Promote to Production". Takes ~30 seconds.

**Backend (Render)**: dashboard → smms-api → Deploys → find the last green → "Rollback to this deploy". Same for smms-worker and smms-beat — roll all three together so they stay in sync.

**Database**: migrations are forward-only. If you need to undo, write a new compensating migration (e.g. `010_undo_009.sql`) that reverses the change, then apply it via the same MCP/CLI path.

### Replay a failed publish

Publishes that fail 5x get routed to the `publish_dlq` Celery queue. The Post is marked `FAILED` with the reason in `error`.

To retry:
```bash
curl -X POST https://<api>/api/v1/posts/<post_id>/republish \
  -H "Authorization: Bearer <admin-token>"
```

This re-enqueues the post on the `publish` queue with a fresh retry budget. The reply is `{"queued": true, "post_id": "..."}`.

### Rotate the TokenVault master key

The TokenVault encrypts platform OAuth tokens, source secrets, and LLM API keys at rest. The master key is `SEC_TOKEN_VAULT_MASTER_KEY` in Render env.

1. Generate a new key: `openssl rand -base64 32`.
2. **Don't replace yet** — set a second env var `SEC_TOKEN_VAULT_MASTER_KEY_NEXT=<new_key>` and deploy. Reads still use the old key.
3. Run a one-time re-encrypt script (TODO: build `scripts/rotate_vault_key.py` — currently this is a manual SQL job):
   - For each `platforms.credentials` row: decrypt with old key, encrypt with new, write back.
   - Same for `sources.secrets` and `organizations.llm_credentials`.
4. Once re-encrypt completes: set `SEC_TOKEN_VAULT_MASTER_KEY=<new_key>` (replace the original), unset `_NEXT`, deploy.

### Backup Supabase

Supabase Pro plan auto-backs-up daily and keeps 7 days. To export ad-hoc:

```bash
# From Supabase dashboard → Database → Backups → Download
# Or via CLI:
supabase db dump --project-ref ukaulrxhegrqyfrirnkb -f backup-$(date +%F).sql
```

Restore: create a new project from the dump in the dashboard.

---

## Sentry triage

Backend Sentry DSN goes in `SENTRY_DSN` (Render env). Frontend in `NEXT_PUBLIC_SENTRY_DSN` (Vercel env). Both are no-ops when blank.

### Common error patterns and what they mean

| Sentry message | Likely cause | Action |
|---|---|---|
| `IntegrityError ... org_invitations_invited_by_fkey` | `Principal.subject` (Supabase UID) used where `smms.users.id` was expected. We fixed three known sites; if you see this again, audit the new code path that's writing the FK. | Use `TeamService.resolve_local_user_id(org_id, identity)` to translate. |
| `LLMBudgetExceededError` | Org hit `monthly_llm_budget_usd`. Comes back to the user as a 402 with `code: llm_budget_exceeded`. | Confirm with the org admin → raise the cap in Settings → LLM budget. |
| `RateLimited` (publish path) | Per-(plugin, account) governor backed off. Auto-retried with the platform's stated cool-down. | Check `app/core/rate_limit.py` if a plugin's defaults are too tight. |
| `MaxRetriesExceededError` (publish path) | A post failed 5x — routed to DLQ. The post is `FAILED` with the error. | Use `/posts/<id>/republish` to retry, or fix the underlying issue first. |
| `team_resolve_failed_fallback_placeholder` (warn, not error) | Auth resolution hit a transient DB error. Auth path bails to AuthError → 401 instead of pooling users into the placeholder org. | Check Supabase connectivity. |
| `chain.smoke_test` rows in audit log | Someone ran the audit-chain trigger smoke test directly against the DB. **It breaks the chain on purpose.** | Delete the row(s) and audit_log_verified will report a chain break for everything after. Genuinely tampered rows look the same. |

### Where to start when an alert fires

1. Check `/api/v1/ready` first. If readiness is failing, every other alert is downstream noise.
2. Check the Render logs for the affected service. Structured JSON output — every line tagged with `request_id`, `org_id`, `user_id` (where available).
3. Cross-reference Sentry's `org_id` tag with the audit log: `GET /audit-logs?action_prefix=...&days=1` — usually the user's last action is informative.

---

## Celery queues

| Queue | Drains what | Worker command |
|---|---|---|
| `workflows` | `app.workers.workflow_runner.run_workflow` | smms-worker |
| `publish` | `app.workers.publish.publish_post` (autoretry x5) | smms-worker |
| `publish_dlq` | `app.workers.publish.publish_post_dlq` | smms-worker |
| `default` | `tick_scheduler` (every minute), `collect_all_metrics` (every 3h) | smms-beat |

Inspect: `redis-cli -u $REDIS_URL` then `LLEN celery@smms-worker.celery` etc.

To purge a queue (don't do this in prod without thinking):

```bash
celery -A app.infrastructure.queue.celery_app.celery_app purge -Q publish_dlq
```

---

## Known gotchas (real ones we've hit)

### "Magic link goes to localhost:8000"

Supabase's project Site URL is misconfigured. Dashboard → Authentication → URL Configuration:
- **Site URL**: `https://frontend-mu-hazel-23.vercel.app` (your Vercel URL)
- **Redirect URLs** (allowlist): add both `https://frontend-mu-hazel-23.vercel.app/**` and `http://localhost:3000/**`.

Without these, Supabase silently falls back to the Site URL, so `emailRedirectTo: window.location.origin + '/login'` is ignored.

### "Inviting a user fails with FK violation on `org_invitations_invited_by_fkey`"

`Principal.subject` is the Supabase auth UID; the FK on `org_invitations.invited_by` points to `smms.users.id`. They are different columns on the same row (`supabase_uid` vs `id`).

Fix exists in `TeamService.resolve_local_user_id(org_id, identity)`. Three known call sites have been fixed (team invites, team self-checks, llm_keys audit). If you add a new write that takes a Principal-derived UUID into a column with a FK to `smms.users.id`, translate first.

### "First-time sign-in puts everyone in the same org"

Pre-#86 behaviour. The placeholder default org `00000000-0000-0000-0000-000000000001` was used as a fallback in `SupabaseAuth.map_claims`. Removed in #86 — `map_claims` now returns `org_id=None` and `core/security.py::_maybe_resolve_via_team()` either:
1. Resolves via `smms.users.supabase_uid`
2. Resolves via `smms.users.email` (and backfills supabase_uid)
3. Claims a pending `smms.org_invitations` row
4. Bootstraps as the first admin if `smms.organizations` is empty
5. Otherwise raises `NoMembershipError → 403 (code: no_membership)`

If a user reports "I'm signed in but I see a /no-access page" — that's #5. The fix is for an admin to invite them via Settings → Team.

### "Cron / interval / once schedules never fire"

Pre-fix Celery Beat bug. The `tick_scheduler` was probing `workflow_repo._s` (the in-memory repo's private dict). On Supabase, that attribute didn't exist, so `enqueued` stayed at 0 forever. Fixed by adding `WorkflowRepository.list_active_all_orgs()` to the repo protocol with both in-memory and Supabase implementations.

If schedules stop firing again: check that `smms-beat` is actually running (Render → smms-beat → logs should show `scheduler_tick_done` every minute), and that `list_active_all_orgs` returns rows: `SELECT count(*) FROM smms.workflows WHERE status='active'`.

### "Workflow uses Anthropic even though I picked OpenAI in Settings"

Wizard wasn't sending `llm_model`. The schema default `claude-sonnet-4-6` would override the user's preference because `WorkflowConfig` always had the Claude model as a class default.

Fix: `_to_config()` now derives the model from the LLM plugin's `default_model` class var when not explicitly supplied. The wizard also seeds `llmModel` from `llmKeys.preferred_model` and sends it in the create payload.

### "DLQ failures don't show up in the audit log"

Pre-fix #80 follow-up. The DLQ writer didn't audit the terminal failure. Fixed by `_terminal_failure` calling `audit.record(action="post.publish.dlq", ...)`. If you see `FAILED` posts on the Posts page that aren't in the audit log, the audit-log service was unavailable when the failure was recorded — re-publish would fail again and re-record.

---

## YouTube cookies

When `/media/import` (or the YouTube source plugin) returns:

```
yt-dlp failed: ERROR: [youtube] <id>: Sign in to confirm you're not a bot.
```

YouTube has flagged Render's datacenter IP and is requiring an
authenticated session. Fix by giving yt-dlp a logged-in cookies file:

1. **Open YouTube in Chrome** while signed in as the account that owns
   the videos you want to download. Browse to any video to make sure
   the session is active.
2. **Install the "Get cookies.txt LOCALLY" extension** (Chrome Web
   Store — open-source, MIT-licensed, no telemetry). The blessed one
   is by Rahul Shaw — verify the publisher before installing. Firefox
   users: the equivalent is called "cookies.txt".
3. With the YouTube tab focused, click the extension icon → **Export
   → Netscape format → Save**. You'll get a `cookies.txt` file with a
   handful of `.youtube.com` rows.
4. **Base64-encode it** so it travels cleanly through Render's env-var
   plumbing:

   ```bash
   base64 -i ~/Downloads/cookies.txt | pbcopy        # macOS
   # or
   base64 -w0 cookies.txt | xclip -selection c       # Linux
   ```

5. **Paste into Render**: Dashboard → `smms-api` → Environment →
   `YTDLP_COOKIES_B64` → Save. The Blueprint inherits this to
   `smms-worker` and `smms-jobs-worker` automatically (`fromService`
   wiring).
6. **Trigger a redeploy** so the workers pick up the new env. Test
   with a `POST /media/import` of any YouTube URL.

The cookies are written to a per-request temp file (`/tmp/smms-import-*/cookies.txt`)
and the temp dir is wiped after each call — they never persist on
disk. Cookies last ~6 months before YouTube rotates the session; when
that happens, repeat steps 1–6.

If you don't want to use cookies, the service falls back to iOS and
Android player clients (which sometimes bypass the bot gate without
auth) before giving up. It's less reliable but zero-config.

## Useful one-liners

```bash
# Watch readiness during a deploy
watch -n 5 'curl -s https://<api>/api/v1/ready | jq'

# Tail backend logs (Render)
render logs --service smms-api -f

# Tail Celery worker
render logs --service smms-worker -f

# Verify the audit chain for an org
curl -s https://<api>/api/v1/audit-logs/verify -H "Authorization: Bearer <admin>" | jq

# What's the last thing this user did?
curl -s "https://<api>/api/v1/audit-logs?days=7&limit=10" -H "Authorization: Bearer <admin>" | jq '.events[].action'

# Force settle every running A/B experiment whose window has elapsed
# (smms-beat does this automatically every tick — only run manually for debugging)
celery -A app.infrastructure.queue.celery_app.celery_app call \
  app.workers.metrics_collector.collect_all_metrics

# Sign in via Supabase from the terminal to grab a bearer token
curl -s -X POST "$SUPABASE_URL/auth/v1/token?grant_type=password" \
  -H "apikey: $SUPABASE_ANON_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@acme.com","password":"..."}' | jq -r .access_token
```

---

## Things to monitor

Weekly (or on-call rotation):
- Sentry inbox — triage / silence / fix
- DLQ depth: `redis-cli -u $REDIS_URL LLEN celery@publish_dlq`
- LLM usage burn rate per org: `SELECT org_id, sum(cost_usd) FROM smms.llm_usage WHERE billing_month=current_date::date - extract(day from current_date)::int + 1 GROUP BY org_id ORDER BY 2 DESC`
- Audit chain integrity for any high-value tenant: `GET /audit-logs/verify`

Monthly:
- Audit who has admin role in each org (Settings → Team)
- Confirm daily Supabase backups are running
- Review the `experimental` flag on any platform plugins — promote stable ones to `experimental: false`

---

## Escalation

When something is on fire and the runbook doesn't cover it:
1. Capture: a screenshot, the affected user's email + org_id, and the request_id from Sentry.
2. Roll back the most recent deploy if the issue started right after one (see "Rollback").
3. Open an incident channel; record actions with timestamps.
4. After stabilising, write a post-mortem and add a section to this runbook so the next person doesn't have to reverse-engineer the fix.
