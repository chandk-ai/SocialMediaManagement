# Retrospective — SMMS build through May 2026

A living document. Read this before extending any subsystem. The point is
to encode what was already learned the hard way so future work doesn't
repeat the same mistakes.

Last updated after **task #123 (UX-6 modern design tokens)**.

---

## 1. What got built

The system grew across five rough chapters. Listed here so the surface
area is concrete when reasoning about side effects.

### Chapter 1 — Foundation (tasks 1–30)
Domain entities (Source / Platform / Workflow / Post / Trigger /
ReviewSession), core services (config, security, DI, resilience),
plugin registry, four-agent orchestrator (Planner / Executor /
Evaluator / Critique), repository ports + in-memory + Supabase
implementations, FastAPI surface, Next.js portal with Sidebar + TopBar
shell, Docker / docker-compose / Render scaffolding, multi-account
platform support, open-source LLM adapters, advanced source connectors
(RSS, Notion, Drive, web, CMS), triggers and review-channel adapters,
TargetSelector for conditional fan-out.

### Chapter 2 — Production hardening (tasks 31–70)
Tenancy isolation levels, real OAuth for LinkedIn / X / Meta / YouTube,
Celery + Beat scheduler, per-(plugin, account) rate-limit governor,
publish DLQ + retry, frontend Analytics + Calendar + Audit pages,
brand-voice RAG fingerprint (the precursor to Pillar 4), media-
generation plugin kind, performance feedback learner, AI-driven
scheduling, engagement triage agent. Then schema-driven Source form,
real fetch() implementations across 15 platforms (Facebook /
Instagram / LinkedIn / X / YouTube / TikTok / Reddit / Pinterest /
Discord / Slack / Telegram / Bluesky / Mastodon / Medium / Tumblr),
brand-correct PlatformIcon component, big-bang build verification.

### Chapter 3 — Niche + Tier-2 (tasks 71–99)
Real LLM integration with org-stored keys, in-app help docs,
LLM cost guards (hard-stop at budget), per-org Redis rate limiting,
audit-log writes for sensitive operations, email-claim team flow,
magic-link login, Playwright E2E. Then the niche features that became
selling points: Telegram-approve (#1), Notion/Airtable as social CMS
(#3), compliance agent (#4), cross-platform A/B with auto-winner (#7),
tamper-evident audit chain (#9), engagement-driven self-scheduling
(#10), Telegram quorum approvals (#11), workflow custom system prompt.

### Chapter 4 — Selection layer + six pillars (tasks 100–109)
Pluggable selection layer with persistent `smms.source_items` registry
and five strategies (freshness, per_item, roundrobin, relevance,
engagement_weighted). Then six robustness pillars: durable run engine
(Postgres-backed jobs + circuit breakers + tenant rate limiter),
observability spine (OTel + Prometheus + alerts), engagement feedback
loop (post_metrics + rollup + attribution), RAG knowledge base
(pgvector + retriever + KB UI), Tailor agent (per-platform Blueprint
refinement), plugin SDK + marketplace.

### Chapter 5 — Operationalization + UX polish (tasks 110–123)
Worker pool deployment (`smms-jobs-worker` Render service +
docker-compose), durable engine as the default for `/run` and Celery
schedule firings, full Tailor→Executor wiring via real `AgentState`,
`fetch_metrics()` implementations for LinkedIn / X / Instagram,
sidebar entries for the new admin pages, KB auto-ingest of top-decile
posts. Then UX: Breadcrumbs across all pages, AppShell wrapper, Jobs
board with detail drawer + bulk retry-all-dead, Plugins marketplace
with cross-kind search + usage badges, help docs refreshed with
Operations group + walkthroughs, ErrorBoundary + app/error.tsx +
no-access page improvements, dead search bar removal, real brand
logo + wordmark, modern design token refresh.

---

## 2. What worked well

These patterns paid off — keep using them.

### 2.1. The plugin-registry-as-first-class-citizen pattern
`@register_plugin("kind", "name")` on a class is the entire surface
needed to add a source, platform, selection strategy, trigger, review
channel, media generator, or LLM provider. Side effects of this:
* New platform adapters are one-file drops, not multi-file edits.
* The marketplace UI, workflow wizard, and orchestrator all read from
  the same registry — there's no "the wizard knows about LinkedIn but
  the orchestrator doesn't" drift.
* Customers can author their own with the `smms-plugin` CLI without
  touching core code.

**Apply forward**: when adding a new kind of pluggable behaviour,
prefer the registry pattern over a switch statement or a manual
import list.

### 2.2. Dual persistence backend (memory + Postgres)
Every service that touches data implements the contract twice:
`InMemoryXService` and `PostgresXService` (or `SupabaseXService` for
the older ones). The DI layer picks based on
`PERSISTENCE_BACKEND=memory|supabase`. Benefits:
* Tests run with no infrastructure.
* Local dev mode boots in one command.
* The contract is forced to be backend-agnostic, so it stays simple.

**Apply forward**: when adding a new service that holds state, write
the memory implementation first (it's faster) and have it expose the
exact same surface you'll bind to Postgres later. Don't add Postgres-
specific methods like `sweep_orphans` to the contract without an
in-memory equivalent — even if the memory one is a no-op.

### 2.3. Iterative TodoList with verification subagents
Breaking work into small, independently-completable tasks made
progress legible and recoverable. End-of-sprint verification subagents
(AST parsing every backend .py file, grep'ing for wiring contracts,
checking file existence + line counts) consistently caught real
issues — typos, missing imports, dangling references — before they
hit production.

**Apply forward**: after any sprint that touches >5 files, spawn a
verification agent with explicit grep checks for the wiring contracts
that were supposed to be established.

### 2.4. The durable runner phase decomposition (once fixed)
Decomposing a workflow run into `select → plan → tailor → execute →
critique → publish`, each its own job row with state persisted in
`run.metadata` between phases, is a genuinely good architecture.
Retries resume from where they left off; pod restarts don't lose
state; per-phase metrics fall out naturally; new phases (like Tailor)
slot in without disturbing the others.

**Apply forward**: when extending the pipeline, add a new phase by
introducing `_phase_<name>` + a `handlers_workflow.py` entry that
enqueues the next phase — don't try to cram logic into an existing
phase.

### 2.5. Layout shell as a shared component (AppShell)
Once `AppShell` existed, new pages went from "9 lines of
Sidebar+TopBar boilerplate plus a forget-to-wrap-it-and-the-nav-
disappears bug" to "one wrapper". The cost of the abstraction was
~50 lines; the savings recurred on every new page.

**Apply forward**: new top-level pages MUST wrap in `<AppShell>`. If
the page needs custom layout, prefer overriding via AppShell props
(crumbOverrides, title) rather than building parallel chrome.

---

## 3. Concrete mistakes + what to do instead

Each of these caused a real failure that the user surfaced. They share
a root cause: **assuming an API surface without reading it**.

### 3.1. Wrong dict key (`KeyError: 'workflow_run'`)
**What happened**: `_get_durable_runner` used `repos["workflow_run"]`.
The actual key was `"run"`. Worker pod crash-looped on every deploy.

**Why**: I introduced a new function consuming a dict shape I hadn't
re-read. Other consumers in the same file used the correct `"run"`
key; one grep would have caught it.

**Rule**: before consuming an existing data structure (dict, dataclass,
DB row), grep for at least one other consumer to see the actual
shape. Don't infer it from the variable name.

### 3.2. Made-up method names (`registry.iter`, `executor.execute`,
`planner.plan`, `critique.review`, `get_metrics`)
**What happened**: I wrote the marketplace catalog endpoint calling
`registry.iter(kind)` — but the real method is `.list(kind)` returning
`PluginEntry`, not bare classes. I wrote the durable runner calling
`orch.executor.execute(...)`, `orch.planner.plan(...)`,
`orch.critique.review(...)` — none of which existed; the real agent
surface is `agent.run(state) → state` with `AgentState`. The
engagement service called `adapter.get_metrics(...)` — the base class
defines `fetch_metrics(...)` with a different signature.

**Why**: I imagined an ergonomic API based on what I thought it
*should* look like rather than what existed. Then wrote a bunch of
call sites against the imagined API. None of it worked at runtime.

**Rule**: before calling a method on a class you didn't write in the
same change, open the class file and confirm:
  1. The method exists.
  2. The exact parameter shape.
  3. The exact return type.
This is two minutes of read; it saves rounds of failed deploys.

### 3.3. `registry.get(...)` treated as returning a bare class
**What happened**: `self.registry.get(PluginKind.SELECTION, name)`
returns a `PluginEntry` object with a `.cls` attribute, not the class
itself. I used the result directly as a constructor in three places.
Same `.cls` mistake repeated in `_publish_one`'s adapter lookup.

**Why**: same as 3.2 — assumed the return type without checking.

**Rule**: when an existing service is consumed in multiple places,
**grep the consumers** to see how they unpack the return value. If
the conventional pattern is `entry.cls(...)`, follow it.

### 3.4. Wrong frontend import paths (`@/lib/api`, `@/components/Toast`)
**What happened**: I wrote `import { fetchJSON } from '@/lib/api'`
and `import { showToast } from '@/components/Toast'`. Neither path
existed — the real paths were `@/lib/api/client` (exporting `api`)
and `@/components/ui/Toast` (exporting `toast`). The build failed.

**Why**: I made up import paths that felt idiomatic without checking
how other pages imported the same utilities.

**Rule**: for any utility (API client, toast, components), find one
existing usage via `grep "from '@/" components/something` and copy
its import line exactly.

### 3.5. Parallel route collision (`/no-access`)
**What happened**: I created `app/no-access/page.tsx`. A page at
`app/(auth)/no-access/page.tsx` already existed. Next.js refuses to
build with two pages resolving to the same path.

**Why**: I assumed the route didn't exist without checking. The
existing implementation was better (real Supabase signout, shows
user email) — my work was both broken AND inferior.

**Rule**: before creating a new page under `app/`, grep for the route
name. Route groups `(name)` are invisible in the URL but visible in
the filesystem — they don't protect against collisions.

### 3.6. New pages skipped the layout shell
**What happened**: `/admin/jobs`, `/admin/plugins`, `/knowledge`,
`/analytics/engagement` were created without Sidebar+TopBar wrapping.
They'd render content but with no navigation. Caught only after the
user landed on one and noticed the missing nav.

**Why**: the codebase convention is per-page Sidebar + TopBar wrapping
(no global app-router layout for the authenticated area). I didn't
look at how `/sources/page.tsx` or `/dashboard/page.tsx` started.

**Rule**: when creating a new top-level page, the first thing to do
is open an existing peer page and copy its outer JSX skeleton (or
wrap with `AppShell`, which now exists).

### 3.7. Migrations written but not applied
**What happened**: I wrote three SQL migrations (durable jobs,
post_metrics, knowledge_base) and didn't apply them. User had to ask
"did you run the migrations?".

**Why**: I treated writing the SQL file as the deliverable. In a
prod-deployed system the deliverable is the applied migration.

**Rule**: when writing a migration, immediately apply it (via the
Supabase MCP for this project) and verify the tables exist. Treat
"the SQL file exists" as half-done.

### 3.8. Adapter instantiation without credentials
**What happened**: `EngagementService._adapter_fetch` did
`adapter = adapter_cls()` — no OAuth credentials passed. Every
authenticated metrics fetch would have 401'd at runtime.

**Why**: I instantiated the adapter the way you'd instantiate any
class, without remembering that platform adapters need credentials.

**Rule**: when constructing an instance of a class from another
service, check its `__init__` signature. If it takes optional kwargs
that affect behaviour (`credentials`, `config`), find a working
example and copy the construction.

### 3.9. Carried-forward placeholder code (search bar, logo square)
**What happened**: The TopBar's "Search workflows, posts…" input
and the Sidebar's colored-square "logo" had been there since the
initial frontend scaffold. I never questioned them. The user had to
ask "is the search bar functional?" and "the logo has to make more
sense".

**Why**: when extending existing code, I assumed prior decisions were
intentional. Sometimes they're just scaffolding nobody got around to
finishing.

**Rule**: when working on a page, scan for UI elements that look
placeholder-ish (generic icons, lorem-ipsum text, colored squares,
unwired inputs). If you can't trace them to a real backend or a
deliberate design choice, raise the question.

### 3.10. Stub-write vs actual deletion
**What happened**: when asked to remove the legacy
`sentry.server.config.ts` and `sentry.edge.config.ts`, I overwrote
them with empty `export {}` stubs because I couldn't delete from
the sandbox. But the Sentry plugin checks for **file existence**, not
contents — so the warnings persisted. Same pattern repeated when I
needed to delete my duplicate `/no-access` page.

**Why**: I treated "rewrite the file to be inert" as equivalent to
"delete it". Sometimes it is; sometimes the consumer cares about
the path, not the bytes.

**Rule**: when the right answer is `rm`, tell the user clearly that
they need to run it, with the exact command. Don't pretend a stub is
equivalent.

---

## 4. Recurring root cause: speculative API usage

Almost every mistake above traces back to one habit: **writing
consumer code against an imagined API surface, then discovering at
runtime that the real surface is different.**

The remedy is mechanical, not creative:

> Before writing any non-trivial call site against an existing
> module, **open the module and read the surface you're about to
> consume**. Read the class definition. Read at least one other
> consumer if there is one. Then write.

This is two minutes of friction that saves a deploy round-trip every
time. Apply it to:
* Service constructors (`InMemoryFooService(...)`)
* Method names + signatures (`registry.get`, `agent.run`,
  `adapter.fetch_metrics`)
* Dict / dataclass keys (`repos["run"]`, `state.plan`)
* Import paths (`@/lib/api/client`, not `@/lib/api`)
* Existing routes (grep before creating)

---

## 5. Conventions that exist (don't violate)

These are decisions already locked into the codebase. Future work
must follow them unless there's an explicit reason not to.

### 5.1. Persistence backend dual-implementation
Any service that holds state has a memory + Postgres implementation
behind a common interface. The DI layer (`backend/app/api/deps.py`)
picks via `settings.resolved_persistence_backend()`.

### 5.2. Repo keys
`_build_repos()` exposes these keys: `workflow`, `run` (NOT
`workflow_run`), `source`, `platform`, `post`, `review`, plus the
advanced-feature repos (`campaign`, `experiment`, `approval_policy`,
`approval_request`, `recycle`, `data_export_job`, `user`, `trigger`).

### 5.3. Plugin registry surface
`PluginRegistry.list(kind=None) -> list[PluginEntry]` for iteration.
`PluginRegistry.get(kind, name) -> PluginEntry` (raises
`PluginNotRegisteredError`, never returns None). Always unpack via
`.cls`.

### 5.4. Agent contract
`Agent.run(state: AgentState) -> AgentState`. State carries
`plan`, `drafts`, `evaluations`, `decision`, `critique_notes`,
`voice_block`, `directive`, `revision_count`. No `execute()`,
`plan()`, `review()`, or `tailor()` methods at the top level — that's
all done through `state` mutation.

### 5.5. Platform adapter contract
`SocialPlatform.__init__(credentials, config)` → both optional.
`publish(payload: PostPayload) -> PublishResult`.
`fetch_metrics(external_post_id: str) -> dict[str, Any]`.
Always pass credentials + config when constructing.

### 5.6. Frontend imports
* `import { api } from '@/lib/api/client'` (also `ApiError`,
  `useApi`)
* `import { toast } from '@/components/ui/Toast'` (`toast.success`,
  `toast.error`, `toast.info`)
* `import { AppShell } from '@/components/layout/AppShell'` for new
  pages
* `import { Logo, Wordmark } from '@/components/brand/Logo'` for
  branding

### 5.7. Frontend route layout
Pages live at `app/<section>/page.tsx`. They are responsible for
their own layout chrome — wrap in `<AppShell>` (preferred) or
build the per-page Sidebar+TopBar pair manually. The `(app)/`
route group has a layout file but is not currently the convention
for new pages.

### 5.8. Durable workflow phases
The phase chain is `run.start → run.select → run.plan → (run.tailor
when multi-platform or compliance-profiled) → run.execute → run.
critique → run.publish`. Each phase reads/writes its outputs to
`run.metadata` so retries resume cleanly. Adding a new phase means
adding `_phase_<name>` + a `handlers_workflow.py` entry that calls
`ctx.queue.enqueue(next_kind, ...)`.

### 5.9. Job kinds
Current registered handler kinds (do not collide):
`run.start`, `run.select`, `run.plan`, `run.tailor`, `run.execute`,
`run.critique`, `run.publish`, `engagement.fetch`,
`engagement.aggregate`, `jobs.cleanup`, `knowledge.auto_ingest`.
New kinds register via `@register_handler("kind")` in
`backend/app/services/jobs/handlers_*.py`.

### 5.10. SQL migrations
File-numbered sequentially in
`backend/app/infrastructure/db/migrations/`. Current head is
`012_knowledge_base.sql`. Applied to Supabase project
`ukaulrxhegrqyfrirnkb` via the Supabase MCP. When writing a new
migration: write the file, apply it immediately, verify the table
exists.

### 5.11. Design tokens
Tailwind config exposes the `ink` (neutral) + `accent` (brand)
palettes with soft/strong/muted variants. Three-tier shadow:
`card-sm` (resting), `card` (hover), `card-lg` (drawers). Subtle
page gradient on `body`. The Logo uses the `brand.from/to` gradient
stops which mirror the SVG.

---

## 6. Process improvements for future work

Concrete habits to apply:

1. **Read before write**. Two-minute grep saves an hour of debug.
   Specifically: when consuming an existing service, open its file
   and read the surface.

2. **One sweep per concept**. Before declaring a feature done, grep
   for the symbol across the codebase and verify all usages are
   consistent. Example: when changing `registry.get` semantics, grep
   all 17 call sites.

3. **Apply migrations inline**. Writing a SQL file is half-done.
   Apply it via the available tooling (Supabase MCP for this
   project) and verify the tables exist.

4. **Wrap new pages in AppShell**. New top-level frontend pages get
   `<AppShell>` unless there's an explicit reason (auth pages,
   error pages) to be standalone.

5. **Verify after major edits**. After any sprint touching >5 files,
   spawn a verification agent with explicit grep checks for the
   wiring contracts. AST parse all backend .py files. Confirm new
   files exist at expected sizes.

6. **Question placeholders**. When extending an existing page or
   component, scan for UI elements that don't trace to a real
   backend. Surface them to the user rather than carrying them
   forward silently.

7. **Be precise about deletion**. When the right answer is `rm` and
   the sandbox can't do it, tell the user the exact command. Don't
   pretend stubs are equivalent.

8. **Honest scope**. When the user asks "do we need X?", don't
   default to yes. Sometimes the right answer is "the existing
   pattern already handles this" — and that's more valuable than
   building something redundant.

9. **Recognize cross-cutting work as cross-cutting**. A breadcrumb
   component touches every page. A logo touches the whole app.
   Plan for the breadth, not just the immediate file.

10. **Treat the user's tooling as part of the toolchain**. When the
    user has shell, deployment platform, or admin access, surface
    the exact commands they need. Don't end an interaction with a
    handoff that requires them to figure out the next step.

---

## 7. Outstanding tech debt to revisit

Honest list of things known to be incomplete or fragile. Should be
real tasks before they're called done.

* **Run trace UI**. `/runs/{id}` is linked from the Jobs board but the
  page itself may not render the full trace. Verify and build out
  if missing.
* **`get_metrics()` for the other 12 platforms**. Only LinkedIn / X /
  Instagram return normalised snapshots; the rest return empty
  dicts. Each platform with an API surface for metrics (YouTube,
  Facebook, TikTok, Pinterest, Reddit, Bluesky) deserves a real
  implementation.
* **Notifications system**. Bell icon was removed from TopBar
  because nothing was wired. When real notifications land, they
  need their own component, not a placeholder.
* **Global search**. Removed for being non-functional. A real
  implementation needs a `/search` backend endpoint over posts /
  workflows / sources / platforms, a debounced typeahead popover,
  and keyboard navigation. Half-day job, not retrofittable as a
  placeholder.
* **Workflow templates marketplace**. Plugin marketplace exists;
  workflow templates as a similar concept (one-click install of a
  pre-configured workflow) would compound the platform's value.
* **HNSW index for the KB**. Current `ivfflat` with `lists=100` is
  fine up to ~100k chunks per org. Larger customers will need
  HNSW; the migration is one ALTER TABLE.
* **Webhook engagement ingestion**. Currently polls at T+1h and
  T+24h. Platforms that support webhooks (Meta Graph, LinkedIn,
  Twitter Account Activity) should push back, reducing fetch jobs
  ~80% and getting near-real-time metrics.
* **Live-event blast mode (Niche #93)**. Pending in task list;
  good differentiator. The durable engine is the foundation it
  needs.

---

## 8. The big lesson

Most of the failures in this build came from **moving fast at the
edge of speculation**: writing a call site against what I thought
the API was, against what the dict key was, against what the route
path was. The cost of one grep before each write is 30 seconds. The
cost of a failed deploy is 5-10 minutes plus loss of user trust.

The math is obvious. Apply it.
