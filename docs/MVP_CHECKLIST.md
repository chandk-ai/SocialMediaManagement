# MVP checklist + tech-debt audit

Honest list of what still blocks shipping a paying-customer-ready v1, plus the tech debt that should get cleaned up before we accumulate more on top of it.

---

## 🔴 P0 — blocks first paying customer

### Auth, billing, identity
- [ ] **Working sign-up flow** — today the portal assumes a user already exists in Okta/Supabase. Need a self-serve flow that creates an Organization + first admin user.
- [ ] **Forgot password / magic link** — only OAuth right now. Add Supabase Auth email magic-link as a fallback.
- [ ] **Stripe billing** — plan selection, card capture, monthly metered LLM usage, dunning. There's no billing layer at all.
- [ ] **Per-tenant LLM budget enforcement** — `Organization.monthly_llm_budget_usd` exists but nothing reads it. The `LLMProvider` adapter needs a budget check before every `complete()`.
- [ ] **Per-tenant rate-limit enforcement** — `Organization.rate_limit_per_minute` exists but only `SecuritySettings.rate_limit_per_minute` is consulted by the middleware.

### Real OAuth — finish the long tail
- [x] LinkedIn / Twitter / Facebook / Instagram / YouTube / TikTok / Reddit / Pinterest (real OAuth wired)
- [ ] Threads / Bluesky / Mastodon / Medium / Discord / Slack / Telegram / Tumblr — still mocked. Most are simple bearer-token configs (no OAuth flow at all).
- [ ] **Token refresh worker** — when an `access_token` nears expiry, a Celery task should `refresh_credentials()` and re-encrypt. Currently we'd 401 silently.

### Persistence + migrations
- [ ] **Alembic migrations runner wired up** — we ship raw `*.sql` files but no `alembic upgrade head` automation. Add an Alembic env that runs the SQL files in order on container start.
- [ ] **Down-migrations** — `001_init.sql` and `002_tenancy.sql` have no rollback. Add them.
- [ ] **Supabase repos for `Organization` + `User`** — `Workflow`, `Source`, `Platform`, `Post`, `Run`, `Trigger`, `Review` all have Supabase repos. `Organization` and `User` still in-memory only.
- [ ] **Connection pool tuning** — defaults are fine for dev, terrible for production. Document recommended `pool_size` per Supabase plan.

### Critical workflow gaps
- [ ] **`PUT /workflows/{id}` endpoint** — only `POST /workflows` (create) and `POST /workflows/{id}/activate`. Editors can't edit a workflow once created.
- [ ] **`PUT /platforms/{id}`** — same gap (can't add tags, change handle, rename).
- [ ] **`DELETE` endpoints** — none exist. Soft-delete via status enums for everything except triggers (hard delete OK).
- [ ] **Source connection test** — UI lets you create a Source but doesn't probe it; users only find out it's broken on the first run.
- [ ] **Workflow dry-run** — let users preview agent output without publishing.

### Operations
- [ ] **Croniter as a hard dependency** — it's optional today, so cron schedules silently no-op when missing.
- [ ] **Health-check probes that include the DB + queue** — currently `/health` returns 200 unconditionally.
- [ ] **Error tracking integration** (Sentry / Honeycomb). Logs go to stdout but there's no aggregation by default.
- [ ] **Backups verified** — Supabase has PITR but we should add a nightly logical export to S3 just in case.

### Frontend essentials
- [ ] **Sign-out flow** wired into `next-auth` (button is in the new TopBar but not connected yet).
- [ ] **Protect routes** — every `/(app)` page assumes a session; add a middleware that redirects to `/login`.
- [ ] **Form validation** — most forms accept any input; add Zod schemas + inline errors.
- [ ] **Confirm-modal for destructive actions** — Reject / Reject-with-feedback / Delete should require a confirmation step.
- [ ] **Mobile responsiveness pass** — sidebar collapses to a drawer; cards stack; tables scroll horizontally.

---

## 🟡 P1 — needed soon, not blocking

- Audit log endpoint (`GET /audit`) so the Audit page stops aggregating client-side from 4 other endpoints.
- Workflow run trace UI — we have the data; needs a timeline component.
- Platform "Reconnect" button needs to actually start the OAuth flow (currently a no-op stub).
- DLQ inspection page — list `Post.status = failed` with `error` and a "Republish" action.
- Engagement triage UI — show pending proposed replies, with platform context.
- Public webhook URLs in the Triggers UI — the trigger detail page should display the exact `/api/v1/webhooks/<plugin>/<id>` URL with a copy button.
- Notification preferences — let users mute review pings outside business hours.
- Slack slash-command trigger — natural extension of the Telegram trigger.
- Search in TopBar wired up.

---

## 🟢 P2 — quality / polish

- Dark mode (Tailwind class-based; system preference detection).
- i18n scaffolding — the agent prompts are English-only and so is the UI.
- Pyright/mypy in CI (currently only ruff).
- Lighthouse CI for the frontend.
- Concurrency-controlled tests with pytest-asyncio + a Postgres-test container.
- Frontend Storybook for the design system.

---

## Tech debt to repay

| Where | Debt | Why it matters |
|---|---|---|
| `services/workflow_service.py` | Imports + helpers concentrate inside one file (~360 lines) | Hard to test in isolation; split into `RunOrchestrator` + `Publisher` collaborators |
| `services/workflow_service.py` `_rerun_with_feedback()` | Recomputes platforms via the original directive — but on revise rounds we sometimes only want the *originally chosen* platform set, not a fresh resolution | Add `Run.target_platform_ids` snapshot |
| `agents/triage.py` | Returns a `TriageResult` but no persistence layer for "open triage items" | Missing entity `EngagementItemRecord` + repository |
| `workers/metrics_collector.py` | Uses a process-local singleton for `BrandVoiceService` | Tied to single-worker; move into a real persistence layer (Redis sorted set or a vector DB collection) |
| `workers/engagement_collector.py` | Creates `ReviewSession` with `workflow_id=org_id, run_id=org_id` placeholders | These should reference an EngagementWorkflow / EngagementRun aggregate; fix when adding the persistence layer above |
| `repositories/memory.py` | Hand-rolled `_ScopedStore` | Replace the in-memory backend with a SQLite-via-aiosqlite implementation so tests exercise SQL paths |
| `core/config.py` | Settings re-instantiated per `get_settings()` call due to `lru_cache` keyed on no args, which is right — but config reload-from-env (e.g. for tests) needs a clear pattern | Add `reload_settings()` helper |
| `adapters/platforms/*.py` | Most adapters have copy-pasted `_compose(payload)` style helpers | Pull a shared `compose_text(payload, separator='\n\n')` helper into `_http.py` |
| `adapters/sources/*.py` | Some adapters call `httpx.AsyncClient` directly without retries / circuit breaker | Standardise on `core/resilience.py` everywhere external calls are made |
| `adapters/llm/*.py` | The "fall back to mock when SDK missing" pattern is repeated 6 times | Put it in `LLMProvider.__init_subclass__` so each adapter only declares `_complete_real()` |
| `api/deps.py` | `_build_repos()` cached with `lru_cache`; settings change won't propagate | Acceptable for now; document the caveat |
| `domain/entities/post.py` | `media: list[MediaAsset]` after Supabase round-trip becomes `list[dict]`; the publish worker has a runtime check | Add a proper deserialiser at the repo boundary |
| `agents/orchestrator.py` | LangGraph fallback never persists checkpoints (real LangGraph code does); a worker crash mid-revise loses state | Add `CheckpointSaver` even in fallback |
| Frontend `lib/api/client.ts` | All errors thrown as plain `Error`; no typed error envelope | Define `ApiError` with `status / detail / requestId` |
| Tests | Almost everything verified through one inline shell-script per change | Migrate to `pytest` + `pytest-asyncio` test files; add to CI |

---

## "Definition of done" for v1

A new customer can go from email → first published post in under 15 minutes:

1. Sign up → email verification → org created
2. Connect 1 social account via OAuth (no copy-pasting tokens)
3. Connect 1 source (paste an RSS URL)
4. Pick a workflow template (defaults pre-filled)
5. Press "Run now" → see drafts in the dashboard within 30 s
6. Approve → post goes live → external URL returned
7. Receive a Telegram / WhatsApp ping when next scheduled run completes

If any of those 7 steps fails or has friction, the MVP isn't done. Today, steps 2 (OAuth real for 8/16 platforms), 6 (works with mock LLM but real platform publish is per-adapter), and 7 (working but Beat scheduler needs `croniter` installed) all have caveats.
