# Roadmap — coverage audit + missing features

This is an honest accounting of what's already in the repo vs. what's still needed for a *truly* 360° social-media-management portal — and which gaps matter most for a niche, defensible product.

---

## What's already covered ✓

### Architecture & platform
- **Hexagonal architecture** with strict domain isolation; pluggable adapters for everything that touches the outside world
- **Plugin registry** with five plugin kinds: platforms, sources, LLMs, triggers, review channels
- **Multi-tenant** with org-scoped repositories and Supabase Row-Level Security policies
- **Typed domain layer** in pure Python (no framework leakage)
- **Dependency injection** via `dependency-injector`; in-memory + Supabase repository implementations
- **OpenAPI-typed REST API**, Next.js portal proxy, Okta + Supabase auth backends

### Content pipeline
- **4-agent orchestrator** (Planner / Executor / Evaluator / Critique) on LangGraph with a fallback state machine
- **Per-run quality threshold + auto-revision** loop with `max_revisions` cap
- **Compliance heuristics** (banned-term scan + LLM rubric); escalates flagged drafts to humans
- **Audit-grade trace** of every agent decision, persisted with the run

### Connectivity (the long tail)
- **16 social platforms** — LinkedIn · X · Facebook · Instagram · YouTube · TikTok · Threads · Pinterest · Reddit · Mastodon · Bluesky · Medium · Discord · Slack · Telegram · Tumblr
- **Multiple accounts per platform** with per-account OAuth credentials, handles, default flag, and freeform tags
- **12 source connectors** including recursive multi-level web crawler, Google Drive, SQL/NoSQL, vector DBs (Pinecone/Chroma/Qdrant/Weaviate), YouTube transcripts, Notion, GitHub, S3
- **9 LLM providers** including self-hosted Ollama and a generic OpenAI-compatible adapter that covers vLLM, Together, Groq, LM Studio, LocalAI, Qwen/DashScope, DeepSeek, Mistral, Perplexity, Fireworks
- **6 trigger types** — manual, schedule, generic webhook, WhatsApp, Instagram, Telegram
- **6 review channels** — in-app dashboard, WhatsApp, Instagram, Telegram, Slack, email

### Targeting & routing
- **TargetSelector** value object with `explicit_platform_ids`, `all_of_platforms`, `by_handle`, `tagged`, plus exclusions
- **DirectiveRouter** parses natural language ("post to all my Instagram accounts", "@brand only", "every IG and FB account except slack", "tag:vip") into a selector
- **TargetResolver** unions workflow defaults with per-run directive selectors and falls back gracefully
- **Fan-out** preserved end-to-end through agent → review → publish

### Human-in-the-loop
- **ReviewSession** state machine — pending → approved/revision/rejected/expired/cancelled
- **Same-channel approval** — drafts go back via WhatsApp / IG / Telegram with Approve/Revise/Reject buttons; replies route back into the workflow
- Free-text revision feedback re-runs the Executor with the feedback as critique notes

### Operations
- **Persistence** — Supabase Postgres + RLS + Realtime (live trace), or in-memory for tests
- **Storage** — Supabase Storage for media, signed URL helper
- **Queue** — Celery + RabbitMQ + Redis with per-platform queue partitioning
- **Observability** — structured JSON logs, Prometheus metrics, OpenTelemetry tracing
- **Resilience** — async circuit breaker + tenacity retries per external dependency
- **Security** — OIDC (Okta), Supabase Auth (HS256), AES-256-GCM token vault, RBAC, request-id propagation, rate limiting
- **Deployment** — Docker Compose, Dockerfiles, GitHub Actions CI

### Advanced features (v0.2)
- **Campaign aggregate** — multi-step, cross-platform campaigns with internal causality (`teaser → launch → recap`); each step produces a directive-driven workflow run with optional `depends_on` ordering
- **A/B/n experiments** — `Experiment` aggregate with EQUAL/HOLDOUT/BANDIT allocation, automatic winner selection from real metrics, per-variant publish status
- **Performance feedback loop** — `PerformanceLearner` fits per-(org, plugin) Evaluator weights via closed-form OLS from past `(predicted_score, actual_engagement)` pairs; Evaluator can blend learned weights into `overall`
- **Hashtag intelligence** — historical-engagement-weighted hashtag scoring per plugin with seed-text relevance boost
- **Content recycling** — `RecyclePolicy` + `ContentRecyclerService` with VERBATIM / LIGHT_REWRITE / FULL_REGEN / THREAD_FROM_TOP strategies and per-policy cadence/cooldown
- **Localization & translation** — N-locale fan-out with platform-aware tone hints and term-preservation for hashtags / URLs / @-mentions
- **Approval policies (multi-step)** — `ApprovalPolicy` ladder with n-of-m sign-off, optional steps, delegate (vacation) backups, per-step review channel
- **GDPR data export / delete** — `DataExportJob` aggregate, signed-URL bundle export, configurable grace window for cascading deletes
- **Public REST surface** — `/campaigns`, `/experiments`, `/approvals`, `/recycling`, `/localization`, `/hashtags`, `/performance`, `/privacy` with full Pydantic schemas

---

## What's still missing — by priority

### P0 — Need before going live

| Gap | Why it matters | Sketch |
|---|---|---|
| **Real OAuth flows for each platform** | Every platform adapter has a mock `authenticate()` returning fake tokens | Wire `start/callback` endpoints to each platform's real OAuth URLs; encrypt tokens via the existing `TokenVault`; show a "Reconnect" UI when refresh fails |
| **Celery Beat scheduler** | `Workflow.schedule` is parsed but no scheduler fires it | Add a Beat config that reads active workflows hourly and creates per-workflow periodic tasks |
| **DLQ + retry for publish** | A failed publish currently marks the post `failed`; no retry loop | Use Celery's `autoretry_for` with exponential backoff (already wired for the worker; needs the post-publish path) |
| **Supabase repos for triggers + reviews** | These currently live in-memory even when Supabase is configured | Add `SupabaseTriggerRepository` + `SupabaseReviewSessionRepository` mirroring the existing pattern |
| **Per-platform rate-limit governor** | High-volume orgs will get throttled by LinkedIn/X/IG | A token-bucket per `(plugin_name, account_id)` in Redis; the publish step waits / queues |

### P1 — Sharp differentiators (the niche features)

These are what would make the product *win* against generic schedulers like Buffer / Hootsuite.

| Feature | Status | What to build / what shipped |
|---|---|---|
| **Brand-voice fingerprint** | ✅ shipped (`BrandVoiceService`) | Org's prior posts indexed (memory/Redis/vector); Executor RAG-retrieves top-K and adds them to the prompt. `WorkflowConfig.use_brand_voice` toggles the path |
| **Image + video generation pipeline** | 🔄 in progress (adapters skeleton in `adapters/media`) | Add `MediaGenerator` plugin kind for DALL-E, Flux, SD, Midjourney, ElevenLabs, HeyGen / Synthesia |
| **Performance feedback loop** | ✅ shipped (`PerformanceLearner`) | Closed-form OLS over `(predicted, actual)` pairs per (org, plugin); per-dimension weights and intercept persisted; surfaced via `/performance/weights/{plugin}` and `/performance/fit` |
| **AI-driven scheduling** | ✅ shipped (`OptimalScheduler`) | Per-platform peak-hour table + `Schedule.kind = OPTIMAL` route |
| **Engagement / reply triage** | 🔄 in progress (`engagement_collector`, `agents/triage.py`) | Per-platform polling source + TriageAgent draft replies → review |
| **Cross-platform campaign orchestration** | ✅ shipped (`Campaign` aggregate) | Multi-step campaigns with `depends_on`, kind-aware Planner directive; `/campaigns` REST surface and `execute_due_steps` walker |
| **A/B variant testing** | ✅ shipped (`Experiment` aggregate) | EQUAL/HOLDOUT/BANDIT allocation, automatic winner selection, `/experiments` REST surface |

### P2 — Operational polish

| Feature | Status | Notes |
|---|---|---|
| **Analytics dashboard** | 🔄 in progress | Aggregate impressions / engagement / cost per workflow; Recharts-driven, live via Supabase realtime |
| **Visual content calendar** | ✅ shipped (frontend `/calendar`) | Drag-and-drop month view; rescheduling rewrites `Post.scheduled_for` |
| **Approval policies** | ✅ shipped (`ApprovalPolicy` + `ApprovalRequest`) | n-of-m sign-off, optional steps, delegate backups; `/approvals` REST surface |
| **Audit log UI** | 🔄 in progress | Read-only timeline of every state-changing action — already persisted, needs page |
| **Cost dashboard** | 🔄 in progress | Token + media-gen spend per org/workflow; budget alerts (we already track tokens + budget) |
| **Compliance guardrails** | ✅ shipped (heuristics + LLM rubric in Critique) | Pluggable rules engine for banned terms / regulated industries; per-org policies |
| **Localization & translation** | ✅ shipped (`LocalizationService`) | N-locale fan-out with platform-aware tone hints; preserve hashtags / URLs / @-mentions; `/localization/translate` REST endpoint |
| **Hashtag intelligence** | ✅ shipped (`HashtagIntelligenceService`) | Historical-engagement-weighted scoring + seed-text relevance boost; `/hashtags/insights` and `/hashtags/suggest` |
| **Content recycling** | ✅ shipped (`ContentRecyclerService`) | VERBATIM / LIGHT_REWRITE / FULL_REGEN / THREAD_FROM_TOP strategies; per-policy cadence/cooldown; `/recycling/policies` REST surface |
| **GDPR data export / delete** | ✅ shipped (`DataPrivacyService`) | Signed-URL bundle export, configurable grace window for cascading deletes; `/privacy/jobs` REST surface |
| **Mobile app surface** | 🔄 in progress | WhatsApp/Telegram triggers already give a "phone-first" UX; PWA for Reviews would close the loop |

### P3 — Nice-to-have

- Plagiarism / originality check via web search
- Trademark / brand-guideline enforcement (typography, banned competitor mentions)
- Voice-cloning integration for short-form video
- Slack slash-command + Microsoft Teams bot triggers
- IFTTT-style condition builder in the UI for advanced workflows
- Multi-region + active-active deployment topology
- SOC 2 / ISO 27001 control-mapping doc

---

## Recommended next sprint (1 week)

The previous sprint shipped a large chunk of the P1/P2 backlog (Campaigns,
Experiments, Performance Learner, Hashtag Intel, Content Recycling,
Localization, Multi-step Approvals, GDPR Privacy). The next high-ROI bets
are the items still marked 🔄:

1. **Real OAuth** for the top-4 platforms (LinkedIn, X, FB/IG, YouTube) — unlocks production usage
2. **Celery Beat scheduler + DLQ retry** — the system becomes self-running
3. **Image generation plugin kind** — IG / Pinterest / TikTok become *creative* targets, not just text dumps
4. **Engagement Triage Agent** — drains the inbound comment / DM queue with the same review pipeline
5. **Supabase mirrors for the new aggregates** (Campaigns, Experiments, Approvals, Recycling, Privacy, Hashtag insights) so cross-restart durability matches the rest of the platform

After that, the Analytics Dashboard + Cost Dashboard turn the now-rich
backend telemetry into a story the buyer can pitch internally.
