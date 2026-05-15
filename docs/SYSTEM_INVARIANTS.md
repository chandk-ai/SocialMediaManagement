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

* **Celery tasks call `asyncio.run()` per tick. Each tick is a NEW
  event loop, then closed.** Any asyncpg connection that gets cached
  in the SQLAlchemy pool from a previous tick is bound to a dead
  loop — the next tick crashes with `Future attached to a different
  loop`. Mitigation:
  * `app/api/deps.py:_is_celery_context()` detects the worker via
    `CELERY_WORKER_RUNNING=1` env var (set by `worker_process_init` /
    `beat_init` signals in `celery_app.py`), with argv inspection as
    fallback.
  * `_make_async_engine(settings)` is the SINGLE chokepoint for
    `create_async_engine` everywhere in `deps.py`. It flips to
    `NullPool` in Celery context — connections are opened fresh per
    session and disposed on close, so nothing outlives a single
    `asyncio.run`. FastAPI keeps QueuePool (one long-lived loop per
    gunicorn worker).
  * **Do NOT call `create_async_engine` directly anywhere else in
    `deps.py`.** Every new service factory must go through
    `_make_async_engine`, otherwise it will silently regress to
    QueuePool and crash inside Celery.

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

## Review session lifecycle — adaptive channel resolution + dispatch

* **Single source of truth for review channel + recipient:** the
  Trigger row. Both trigger-fired runs AND manual "Run Now" from the
  web UI inherit the channel/recipient from the bound trigger. Set it
  once on the trigger, applies everywhere.

* **Inheritance order** in ``WorkflowService._execute``:
  1. ``review_channel`` explicitly passed to ``_execute`` (e.g. by
     ``run_from_trigger`` or a campaign override) — highest precedence
  2. ``_inherit_review_channel_from_trigger`` — finds the most
     recently created active Trigger bound to this workflow that has
     ``review_channel`` set, adopts its channel + recipient
  3. Workflow-level fallback: if ``require_human_approval=true`` and
     none of the above resolved a channel, default to ``in_app`` with
     empty recipient (the Reviews page surfaces it)

* **`_open_review_session` does TWO things:** persist the
  ``ReviewSession`` row AND dispatch the outbound message via the
  channel adapter. Before this fix the method only persisted —
  ``ReviewService.request_review`` (the dispatch fn) was defined but
  never called anywhere. Result: sessions existed in DB but no
  Telegram / WhatsApp / Slack messages were ever sent. Symptom:
  ``smms.review_sessions`` row exists with empty ``sent_message_ref``;
  user sees draft on Posts page but never receives a notification.

* **Recipient gating** — outbound channels (telegram, whatsapp,
  instagram, slack, email) require a recipient; an empty one logs a
  warning and skips the session. The ``in_app`` channel allows empty
  recipient (the Reviews page is the surface). This lets web-UI "Run
  Now" succeed without forcing the user to configure a trigger first.

* **Channel adapter config (bot tokens, etc.) is pulled from the
  matching Trigger's config at dispatch time.** Never hard-code or
  pass channel secrets through API calls. The adapter falls back to
  its env-var resolver (``TELEGRAM_BOT_TOKEN``, etc.) when no trigger
  matches — useful for single-bot self-hosted deployments.

* **Dispatch failure is logged but never re-raised.** A briefly down
  Telegram bot must not fail the workflow run — the session is
  already in DB and the user can still decide via the web Reviews
  page. ``sent_message_ref`` stays empty in that case; a retry path
  can be added later if needed.

* **Trace event** ``review.session_created`` is appended to the run
  trace on every session open, with channel + recipient +
  ``sent_message_ref``. Use this in the run-detail UI to triage
  "why didn't I get a notification?" — empty ref means dispatch
  failed; missing event entirely means session creation was skipped
  (check the log line ``review_skipped_*``).

## Adaptive consumption — the source_items contract

* **Core invariant (cross-source, cross-platform):** A source item is
  permanently consumed *if and only if* a non-failed Post derived from
  it exists. Otherwise the item is re-pickable on future runs. This
  rule is uniform — applies to every source plugin (Notion, RSS,
  Drive, YouTube, web scraper, …) and every platform plugin (LinkedIn,
  IG, X, …) because it operates on the run/post layer below the
  plugin boundary.

* **The release SQL** lives in
  ``SourceItemsService.release_unclaimed_for_run`` and joins
  ``source_items`` to ``posts`` so a source_item is released whenever
  its linked post is in ``status='failed'`` OR the link is NULL.
  Releases set ``status='new'`` + null out all consumed_* columns so
  the next run's Selector re-picks the item from scratch.

* **Where the release is called from** (must stay in sync with all
  terminal-failure transitions):
  - ``WorkflowService.resume_after_review`` — REJECTED / EXPIRED /
    CANCELLED branch
  - ``WorkflowService._execute`` — outer ``except`` handler on
    mid-run errors (FAILED transition)
  - Future: durable_runner critique-rejected path. Note the durable
    runner currently doesn't call ``mark_consumed`` either, so it has
    no orphans to release — but if/when it gains eager-claim
    semantics, it must call release too.

* **The single chokepoint** is
  ``WorkflowService._release_unclaimed_source_items(org_id, run,
  reason)``. It writes an ``orchestrator.source_items_released``
  AgentTraceEvent into the run trace with ``count`` and ``reason`` so
  the operator can see in the run-detail UI exactly which items were
  released and why. Release failure is logged but never propagates —
  it must not block the run's terminal transition.

* **What does NOT get released:**
  - Items whose linked Post is in ``review`` / ``approved`` /
    ``scheduled`` / ``published`` (active live commitment)
  - Items consumed by a different run (only this run's claims)
  - Items where no Post exists yet AND consumed_by_run_id is NULL
    (never claimed)

* **Known orphan-session bug:** Today the inline-path ``_execute``
  flow can create Posts in ``status='review'`` without ever opening a
  ReviewSession when ``review_channel`` is unset. Those Posts sit on
  the Posts page forever waiting for a review that will never arrive.
  Workaround: reject them via the orphan-Post review flow on the
  ``/reviews`` page (already wired). Real fix: always open a
  ReviewSession (default to in_app) when a workflow has
  ``require_human_approval=true``.

## Telegram webhook — must point at backend, not at frontend proxy

* **The Telegram `setWebhook` URL MUST be the FastAPI backend directly**
  (`{NEXT_PUBLIC_API_URL}/api/v1/webhooks/telegram/{trigger_id}`), NOT the
  Vercel frontend's `/api/proxy/...` path. Telegram refuses to follow
  redirects on webhook URLs (security policy), and Next.js
  `rewrites()` in `next.config.mjs` rewrites `/api/proxy/*` to an
  external destination — which Vercel implements as a **307 Temporary
  Redirect** to the backend. Inbound updates silently pile up in
  Telegram's pending queue, never reaching FastAPI.

* **The proxy is a browser-auth helper, not a webhook target.** It
  exists to attach the Supabase JWT cookie to outbound requests from
  the React app. Telegram authenticates via the `secret_token` query
  parameter set on `setWebhook`, verified in
  `TelegramTriggerAdapter.verify_signature` — no JWT involved. So
  inbound webhooks have no reason to traverse the frontend.

* **`TelegramSetup.tsx` Step 2 reads `NEXT_PUBLIC_API_URL`** to build
  the curl. If that env var isn't set on Vercel, the wizard falls
  back to `window.location.origin` (dev convenience for localhost)
  but the resulting URL will fail in production. Ensure
  `NEXT_PUBLIC_API_URL` is set to your backend root on every Vercel
  environment that ships the wizard.

* **Symptom to recognize.** `getWebhookInfo` shows
  `"last_error_message": "Wrong response from the webhook: 307
  Temporary Redirect"` and `"pending_update_count"` > 0. Fix is to
  re-run `setWebhook` with the backend URL — Telegram auto-flushes
  the pending queue to the new endpoint within a minute or two.

## Triggers — edit / pause / delete

* **`PATCH /triggers/{id}` is the single edit surface.** Accepts
  ``display_name``, ``config``, ``allowed_senders``, ``review_channel``,
  ``review_recipient``, ``is_active`` — any field omitted is preserved.
  ``plugin_name`` and ``workflow_id`` are intentionally NOT editable
  here: changing the plugin invalidates the config schema, and
  rebinding to a different workflow is a separate workflow-editor
  concern. Delete + recreate is the supported migration for both.

* **`config` is replaced wholesale**, never merged. Plugin configs
  are plugin-specific JSON shapes with required fields — a merge can
  leave a half-populated config that crashes the adapter at fire-time.
  The frontend always sends the full intended config.

* **`is_active=false` honored at every entry point.**
  ``TriggerService.parse_payload`` returns `[]` immediately when the
  trigger is paused — the webhook still ACKs (so upstream providers
  don't retry) but nothing dispatches. Scheduler tick reads
  workflow-level schedules, not triggers, so it's unaffected. Adding
  a new trigger-driven dispatch path? It MUST check
  ``trigger.is_active`` first.

* **DELETE is hard-delete.** No soft-delete column. Deleting a webhook
  trigger means its URL stops working immediately — there's no grace
  period. UI guides users toward Pause for reversible disable. Admin
  role required (not just editor).

* **TriggerRepository.delete** added to the port + both
  InMemoryTriggerRepository and SupabaseTriggerRepository. The
  SQL delete is org-scoped, so cross-tenant attempts no-op.

* **Audit-log diff omits secret-bearing fields.** The
  ``trigger.update`` audit entry records ``config_replaced=true``
  rather than the full config diff, because configs commonly carry
  bot tokens, webhook secrets, etc. The UI's preview also masks any
  key matching ``(token|secret|password|api_key|apikey)``.

## Reviews & per-target control

* **`drafts_snapshot` is keyed per-Post, not per-draft.** Each entry
  carries ``post_id``, ``platform_id``, ``plugin_name``,
  ``display_name``, ``account_handle``, plus the draft body + media.
  The Reviews UI uses this to render a target chip per account; the
  decision endpoint uses ``post_id`` / ``platform_id`` to cancel
  specific siblings on exclusion. Built by ``_build_drafts_snapshot``
  in ``workflow_service.py``.

* **Exclusions live on the ReviewSession** as ``excluded_platform_ids``
  (JSONB column, see migration ``014_review_exclusions.sql``). The
  decision payload (``DecisionIn.excluded_platform_ids``) writes there
  BEFORE ``_apply`` runs. ``resume_after_review`` reads from the
  session — never from the request — so a Telegram-initiated decision
  and a web-UI decision behave identically. Persisting on the session
  also means a Revise round preserves the prior exclusion set across
  the re-review.

* **Approve gate.** Excluding every target on a session blocks the
  Approve button on the web UI (Reject is the correct action). The
  backend doesn't enforce this — it would publish to zero platforms
  silently — so the UI guardrail is the only check. If you add a
  second client (mobile app, CLI), enforce it server-side.

* **Cancellation trail.** When a reviewer excludes accounts, the
  corresponding sibling Posts transition to FAILED with
  ``error="excluded by reviewer at approval"`` plus a
  ``review.platform_excluded`` ``AgentTraceEvent`` on the run. Don't
  silently drop them — orphaned REVIEW-status Posts confuse the
  Posts dashboard.

* **Posts API serializer denormalizes target metadata.**
  ``platform_plugin_name``, ``platform_display_name``,
  ``account_handle`` are resolved server-side via a batched lookup
  (``_batch_platforms_for_posts``). Adding a new mutation route on
  posts MUST resolve the platform before returning ``_to_out(p,
  plat)`` — otherwise the response loses the target chip and the
  UI re-renders without it until the next list-refresh.

* **Posts API ``?run_id=`` filter** is applied at the API layer (not
  the service / repo) so the InMemory + Supabase repo contracts
  don't gain a new parameter. Fine for current volume; revisit if a
  single run produces >5k posts.

* **Telegram review message lists targets explicitly.** ``_build_body``
  emits one bullet per draft showing platform + ``account_handle``,
  plus a "*Publishing to N accounts. Use the web UI to skip specific
  ones.*" instruction. Per-target inline-button toggling on Telegram
  is intentionally not implemented yet — stateful callback handling
  requires changes to the bot webhook + a "selected set" stored on
  the session. Direct reviewers to the web UI for now.

## Source plugin inputs

* **Every source plugin whose config carries an identifier that the
  user copy-pastes (Notion database ID, Google Drive folder ID, RSS
  URL, etc.) MUST normalize + validate that identifier at
  `connect()` time** so the error surfaces in the Source Test UI,
  not 6 hours later in a worker log. The pattern is in
  `app/adapters/sources/notion.py`:
  * `_resolve_database_id(raw)` accepts a raw ID, a hyphenated UUID,
    or a full Notion URL — extracts/rebuilds the canonical form,
    raises `SourceConnectionError` with a user-readable message on
    garbage input.
  * `connect()` calls the resolver and re-stamps the canonical value
    back into `self.config` so every subsequent `fetch()` uses it.
  * `fetch()` also calls the resolver defensively, because the
    worker reconstitutes the source from the repo and may bypass
    `connect()` on the run path.
  * When adding a new source plugin, copy this shape — never inject
    `self.config['some_id']` into a URL without validation.

## Source media extraction (Notion specifically — pattern applies elsewhere)

* **Notion's page cover image lives on `page.cover`, NOT in
  `/blocks/{id}/children`.** Most Notion authors put their hero
  image there, not as an inline image block. A source media
  extractor that only walks block children silently misses ~80% of
  user images. Fix: `_extract_page_media` accepts the FULL page dict
  and pulls `page.cover` as candidate #1 before walking blocks.

* **Recurse one level into container blocks** (`column_list`,
  `column`, `toggle`, `callout`, `quote`, `synced_block`) — they
  commonly hold media in real-world Notion layouts. Cap recursion
  at one level: deeper trees explode latency on a 50-row fetch.

* **Per-page extraction must emit a `notion_media_summary` log
  line** with `candidates / imported / skipped`, and per-asset
  failures log at WARNING (not INFO). The previous INFO-level
  silent-skip is what hid "every image failed to import" for so
  long.

* **Planner emits `source_media_pool_size` +
  `attached_media_per_blueprint` into the run trail** via
  `state.log("plan_built", ...)`. When a user asks "why does my post
  have an AI-generated image instead of my Notion picture?" — look
  at this entry first. If `pool_size=0`, the source didn't yield
  media; if `pool_size>0` but `attached=0`, the per-platform rules
  rejected everything in the pool (kind mismatch).

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

* **Never compare ``media.kind`` directly to a string literal in a
  platform adapter.** ``MediaKind(str, Enum)`` *should* satisfy
  ``media.kind == "video"`` but a real-world representation drift
  (May 12 2026 incident — video Post got routed to
  ``image_url``) proves it doesn't in every code path. Use the
  ``_is_video_asset(media)`` helper pattern from
  ``app/adapters/platforms/instagram.py`` which checks:
    1. ``MediaKind`` enum's ``.value``
    2. ``str(kind)``  (representation drift)
    3. URL extension fallback (``.mp4 .mov .m4v .webm .mkv``)
  Mirror this helper into every adapter that branches on kind
  (Pinterest, TikTok, Facebook, X, etc.) — currently only IG has it.

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
