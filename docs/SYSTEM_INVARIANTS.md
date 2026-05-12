# SMMS — System Invariants

A living list of the **contracts that must hold** for the system to
behave correctly. Each line is a single statement that, if violated,
will break something somewhere. Read this before touching anything in
the relevant area.

The format is deliberately terse — one bullet per invariant, with
**"why this matters / who depends on it"** as a sub-bullet. Long
prose belongs in module docstrings; this file is a checklist.

---

## Scheduler

* **Beat fires `tick_scheduler` every minute on the `default` queue.**
  Set in `app/workers/scheduler.py:32` (`celery_app.conf.beat_schedule`).
  * If you rename the queue, `smms-worker`'s `-Q` flag in `render.yaml`
    must list the new name. Missing it = silent failure: Beat enqueues,
    nothing consumes, scheduled workflows never fire. (Incident: May 12
    2026.)

* **`workflows.last_fired_at` is the canonical "this workflow last
  fired at" timestamp.** Set by `_record_fired` in
  `app/workers/scheduler.py` after every fire, for every schedule kind.
  * `_is_due` and `OptimalScheduler.next_slot_for` MUST read this (with
    `wf.updated_at` as the fallback when it's NULL — first-ever fire).
  * Migration `014_workflows_last_fired_at.sql` introduced the column.
  * Don't infer "last fired" from `workflow_runs` or `updated_at` —
    `updated_at` advances on edits too, which would let a save during
    a quiet period re-arm scheduling incorrectly.

* **`Schedule(kind=ONCE)` requires a non-null `run_at`.**
  `Schedule.__post_init__` raises `ValueError` otherwise.
  * To "disarm" a ONCE schedule after it fires, downgrade `kind=MANUAL`
    AND null `run_at` AND null `cron`/`interval_minutes` in the same
    `Schedule(...)` construction. `_record_fired` already does this.

* **`tick_scheduler` calls `workflow_repo.list_active_all_orgs()`.**
  Every repo implementation (Supabase + memory) must define this.
  * Don't filter on `org_id` here — Beat runs cross-tenant, RLS isn't
    in scope. Filter on `status = 'active'` at the DB layer.

## Celery / Worker topology

* **Three distinct services run on Render:**
  * `smms-api` (FastAPI web)
  * `smms-worker` (Celery consumer of `default,workflows,publish,publish_dlq`)
  * `smms-jobs-worker` (drains `smms.jobs` Postgres-backed durable queue)
  * `smms-beat` (Celery Beat scheduler — beat-only, no worker role)
  * Pause any one of them and a specific feature breaks silently.

* **Every `smms-worker` queue listed in `app/infrastructure/queue/
  celery_app.py:task_routes` must appear in the Render service's
  `-Q` flag.** Otherwise the producer (`*.delay()`) succeeds, the
  message lands in Redis, and no consumer ever picks it up.

* **The `default` queue handles:** `tick_scheduler`,
  `collect_all_metrics`. Adding new periodic tasks defaults them to
  `default` unless `task_routes` says otherwise.

* **`fromService` vs `sync: false` in `render.yaml`** — they're not
  interchangeable. Render REFUSES Blueprint sync when a `fromService`
  reference points at a parent env var that doesn't yet exist (and
  `sync: false` parent vars don't exist until the user manually
  fills them in). Use `fromService` only for vars that are REQUIRED
  on the parent (Supabase URL, Redis URL — the parent definitely
  has a value). Use per-service `sync: false` for OPTIONAL vars
  (Sentry, YouTube cookies, optional LLM keys) so the Blueprint
  sync succeeds even when those values aren't filled in on smms-api.

## OAuth / credentials

* **`platforms.status='connected'` MUST imply a row in
  `platform_credentials`** for the platform_id, with non-zero
  `ciphertext`. The OAuth callback writes both atomically via
  `platform_repo.update(p)` after `p.mark_connected(creds)`.
  * The Supabase repo's `_platform_to_orm` / `_platform_to_domain` MUST
    bridge `Platform.credentials` ↔ `PlatformCredentialsORM`. Forgetting
    this was the May 12 2026 incident: status flipped to connected,
    credentials silently dropped.
  * `/posts/{id}/publish_now` pre-flights this check and returns a 422
    when credentials are missing — don't bypass it.

* **`SecretStr` fields (`service_role_key`, API keys) must be
  unwrapped via `.get_secret_value()` at the wire boundary** (httpx
  headers, request bodies). Never store the unwrapped form on
  long-lived objects.

* **Pre-OAuth-callback enrichment for Meta-family plugins (FB / IG /
  Threads)** runs `/me/accounts` to find Pages → for IG, also reads
  `instagram_business_account`. The callback exposes the outcome via
  the response's `setup_status` enum: `ready`, `no_pages`, `no_ig_link`,
  `needs_page_picker`, `enrichment_failed`. Don't fall back to using
  a Page ID as the IG user id — that produces "Object 1074... does
  not exist" downstream.

## Media import

* **Every external media URL that needs to outlive a single request
  goes through `MediaImportService`** in
  `app/services/media_import.py`. Source plugins, the `/media/upload`
  endpoint, and the `/media/import` (yt-dlp) endpoint all call into it.
  * Source plugins (Notion, Drive, RSS, web_*, youtube) populate
    `SourceItem.media` via this service.

* **The Planner pools media across ALL selected source items, then
  picks per-platform** via `_PLATFORM_MEDIA_RULES`.
  * Pool: every selected source item's media is flattened into one
    ordered list, de-duped by URL. Earlier-ranked items contribute
    first so the highest-relevance media wins.
  * Per-platform selection respects each platform's `prefer` tuple
    (video-first for Reels/TikTok, image-first for Pinterest, either
    for LinkedIn/X/FB) and `max_items` cap.
  * If no media in the pool matches a platform's preferred kinds,
    `attached_media` is left empty and the Executor falls back to
    media-generation OR text-only — never crashes the run.
  * Adding a new platform plugin: add an entry to
    `_PLATFORM_MEDIA_RULES` in `planner.py`. Missing entries get
    `_DEFAULT_MEDIA_RULE` (image-first, max 1).

* **The Executor uses `attached_media` ahead of any media-generation
  plugin** — source media wins. AI-generated media is the last
  fallback, not the default.

* **The `smms-media` Supabase bucket is `public=true`** with
  `file_size_limit = 524288000` (500 MB) and a closed
  `allowed_mime_types` allow-list. Platforms (Meta, Pinterest, …) fetch
  these URLs server-side, sometimes hours later for scheduled posts.
  Signed URLs would expire mid-cycle — DO NOT use them here.
  * `SupabaseStorage.public_url(path)` returns the permanent URL.
  * `SupabaseStorage.upload` calls the Storage REST endpoint directly
    via httpx, NOT the `supabase-py` SDK — the SDK's `file_options`
    key for content-type varies by version and silently dropping it
    causes Supabase to default to `text/plain` and reject.

* **Frontend file uploads use the supabase-js signed-token flow.**
  Backend `POST /media/signed-upload` returns `{bucket, storage_path,
  token, public_url, content_type}`. Frontend calls
  ``supabase.storage.from(bucket).uploadToSignedUrl(storage_path,
  token, file, { contentType, upsert: false })``.
  * **Do NOT hand-roll a `fetch('PUT', ...)` against the signed URL.**
    Supabase Storage's signed-URL PUT endpoint doesn't accept CORS
    preflight from a browser. (May 12 2026 incident — we tried twice.)
    Only the supabase-js client's `uploadToSignedUrl` method goes
    through the CORS-allowed code path; it also auto-falls-back to
    TUS resumable upload for files >6 MB.
  * The bytes never touch Vercel's proxy or our Render backend.
  * The buffered `POST /media/upload` route still exists for tiny
    images and server-side callers (source plugins, yt-dlp imports
    through MediaImportService) but **must not** be used from the
    frontend file picker.

* **`NEXT_PUBLIC_*` env vars are inlined at build time, not
  runtime.** Setting them in Vercel doesn't take effect until the
  next deploy. If you add one, trigger Deployments → ⋯ → Redeploy
  manually — Vercel doesn't auto-rebuild on env-var edits.

## Audit log

* **Rows with `row_hash IS NULL` are pre-feature legacy rows.** The
  hash chain shipped in migration 008; rows inserted before that
  have no hash. `verify_chain` MUST skip them rather than flag the
  chain as broken — they're known-trustworthy, not tampered.

* **Every state-changing user / system action emits an audit row.**
  Including: workflow CRUD, run lifecycle (`workflow.run.enqueued`
  on user click, `workflow.run.succeeded` / `.failed` /
  `.awaiting_review` from the durable runner), post lifecycle
  (`post.publish` queued, `post.published` / `post.publish.failed`),
  platform connect, LLM-key changes.
  * The durable runner emits its terminal audit events via
    `_emit_terminal_audit` in `workflow_durable_runner.py`. Don't add
    audit calls inside individual phase methods — central emission
    keeps it consistent and idempotent.

* **Date-range filters travel as ISO-8601 with explicit UTC offset.**
  The frontend converts `<input type="datetime-local">` local time to
  UTC before sending. Sending naked `YYYY-MM-DDTHH:MM` against
  `timestamptz` Postgres comparison shifts the window by the user's
  TZ offset.

## Runs / durable queue

* **Every phase of a workflow run is a row in `smms.jobs`** with
  `kind='run.<phase>'`. `smms-jobs-worker` claims via `FOR UPDATE SKIP
  LOCKED`. Run state lives in `smms.workflow_runs` (`metadata` JSONB
  carries plan / drafts / evaluations / rerun_count etc.).
  * Each phase reads prior outputs from `run.metadata`, writes its
    own output back. The runner's `_advance` helper enqueues the next
    phase (`run.<next>`) when the previous phase returned
    `result.next_phase`; otherwise the run terminates.

* **Idempotency keys for phase enqueue are `<phase>:<run_id>` plus a
  `:r<rerun_count>` suffix when in a revision cycle.** Without the
  rerun-count suffix, the 2nd critique→execute loop would collide
  with the 1st and the chain stalls silently.

* **Permanent errors (LLM credential missing, platform validation
  failed, payload too large) MUST be re-raised as `PermanentError`**
  so the worker DLQ's them instead of retrying 5x with exponential
  backoff against a config that can't possibly succeed.

## LLM providers

* **Hosted providers raise `MissingLLMCredentialError`** when no key
  is configured, instead of silently returning a `[mock <provider>]`
  string. The durable runner translates this into a clean FAILED run.
  * The opt-in escape hatch is `LLM_ALLOW_MOCK_FALLBACK=1` (legacy
    smoke tests). Off in prod.

* **API keys are passed as headers, never as URL params.** The Gemini
  provider was bitten by this — httpx exception messages put the
  full URL (including `?key=AIza…`) in the error string, which then
  ended up in audit logs and Sentry breadcrumbs.

* **`_scrub_secrets` runs against every error string before it's
  persisted to the run trace or job error column.** Adds a defensive
  layer even if a new provider regresses on the no-key-in-URL rule.

* **Gemini auto-falls-back across player clients on 404** (configured
  → iOS → Android → web). The default model is `gemini-flash-latest`
  rather than a versioned name so it tracks Google's rotations.

* **Per-run LLM concurrency is capped at `EXECUTOR_LLM_CONCURRENCY`
  (default 4).** The Executor used to fire all per-platform LLM calls
  with no concurrency limit, which trip per-second burst limits even
  on paid LLM tiers. The semaphore is scoped per run (independent
  buckets across orgs); the worker process's own concurrency
  (`SMMS_WORKERS_CONCURRENCY=4`) provides the cross-run ceiling.
  Adjust via env if you upgrade to a higher LLM tier with more RPS
  headroom.

* **The Gemini provider retries 429 up to 3× with exponential
  backoff** (base 2s, doubling), honoring Google's `retryDelay` hint
  when present. After retries exhaust, the error surfaces with
  actionable copy ("lower EXECUTOR_LLM_CONCURRENCY or upgrade tier").
  Other providers (Anthropic, OpenAI) get the same treatment when we
  hit the same problem there — keep this contract uniform.

## Posts / publish

* **Drafts are materialized as `Post(status=REVIEW)` whenever a run
  stops short of autopublish** (critique escalated, revisions
  exhausted, `require_human_approval=true`). Don't let work die in
  `run.metadata['drafts']` only — the user must see it on the Posts
  page.

* **`MediaAsset.url` on a `Post` must be permanent + HTTPS.** The
  Instagram adapter's `validate()` rejects HTTP and YouTube/Drive
  view URLs. Use `MediaImportService` to re-host non-direct URLs.

* **Celery's `publish_post` task does NOT autoretry permanent
  errors** (`PlatformValidationError`, `PlatformNotImplemented`).
  These DLQ on the first attempt with a clear audit
  `post.publish.failed` event.

## Data model

* **`workflows.config.last_run_at` does NOT exist.** "Last run X" on
  the workflow card comes from joining `workflow_runs` for the
  latest `started_at`. To track schedule firings specifically, use
  `workflows.last_fired_at` (May 2026).

* **`platforms.config['__pages_available__']` carries the Meta
  page list for the Page-picker UI** when the user has multiple FB
  Pages. Keys prefixed `__` are reserved for runtime context, not
  user-configurable fields.

* **Source plugins receive their owning `org_id` via reserved
  `config['__org_id__']`**, injected by `source_loader.load_items`.
  Don't surface this in the user-facing source config schema.

## Frontend

* **Tailwind only ships `grid-cols-1..12` by default.** `grid-cols-14`
  silently fails — use inline `style={{ gridTemplateColumns: …}}` for
  arbitrary column counts.

* **`useSearchParams()` in any page component requires that page to be
  wrapped in `<Suspense>`** in Next.js 14+ App Router. The audit page
  hit this; future filter-bearing pages will too.

## Recurring failure modes

These have bitten us more than once and represent durable lessons.

* **Speculative API usage** — read the actual class definition or repo
  signature before writing the consumer. Examples that bit us:
  `PluginRegistry.iter` (doesn't exist; real method is `list(kind)`),
  `principal.id` (real attr is `subject`), `repo.get(id)` (real
  signature is `(org_id, id)`), `from_dict` on Platform (real method
  is `from_draft`).

* **SDK key-name drift** — supabase-py's `file_options` for
  content-type was `contentType` (camelCase) then `content-type`
  (kebab-case) across versions. When in doubt, hit the REST endpoint
  directly with httpx.

* **Silent fallbacks hide real failures** — every layer that defaults
  to a mock / no-op / empty list when a dependency is missing should
  surface that fact in logs AND fail-closed on the production code
  path. Opt-in escape hatches (env vars) for tests are OK; defaults
  must be loud.

* **Catch-all `except Exception: ...` swallowed root causes** — the
  Meta OAuth callback's `except Exception: pages_available = []` hid
  Meta's actual error for weeks. Always log the exception when you
  catch it; expose a `setup_status` / `error_reason` on the response
  so the next layer can surface it.
