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

| Feature | Why it's a wedge | What to build |
|---|---|---|
| **Brand-voice fingerprint** | Most tools generate generic, "AI-flavored" content | Index the org's last 12 months of posts in a vector DB; the Executor RAG-retrieves the closest 5 high-engagement posts and constrains generation to that style. Add `voice_examples` to `WorkflowConfig` |
| **Image + video generation pipeline** | Visual platforms (IG, TikTok, Pinterest) require media; the system should *make* it, not just consume it | Add a `MediaGenerator` plugin kind with adapters for DALL-E, Flux, Stable Diffusion, Midjourney via API, ElevenLabs (voice), HeyGen / Synthesia (video). The Planner emits `media_brief`s alongside `PostBlueprint`s; Executor renders both |
| **Performance feedback loop** | The Evaluator scores predicted engagement but never *learns* | After publish, fetch real metrics via `fetch_metrics()` (already on the platform contract). Store on `Post.metrics`. A nightly job builds a regression model from historical scores → real CTR/likes; Evaluator weighting auto-tunes |
| **AI-driven scheduling** | Posting at 09:00 UTC for everyone is suboptimal | Per-(account, content-type) "best-time" model from historical metrics; if `Schedule.kind = OPTIMAL`, the scheduler picks the slot |
| **Engagement / reply triage** | Comments + DMs roll in after publishing — the agent should help drain that queue | New `EngagementSource` plugin (per-platform polling) + a `TriageAgent` that classifies sentiment, drafts replies, and pushes them through the same Review channel |
| **Cross-platform campaign orchestration** | Today each post is an island | New `Campaign` aggregate: a sequence of posts across platforms over N days, with internal causality (e.g. teaser → launch → recap). Planner becomes campaign-aware |
| **A/B variant testing** | Marketing teams need to learn what works | Generate `n` variants per draft, publish to a holdout cohort or split audiences, compare metrics, persist a "winning template" |

### P2 — Operational polish

| Feature | Notes |
|---|---|
| **Analytics dashboard** | Aggregate impressions / engagement / cost per workflow; Recharts-driven, live via Supabase realtime |
| **Visual content calendar** | Drag-and-drop month view; rescheduling rewrites `Post.scheduled_for` |
| **Approval policies** | Multi-step approval (legal → marketing → exec); first-class `ApprovalPolicy` aggregate; vacation mode + delegation |
| **Audit log UI** | Read-only timeline of every state-changing action — already persisted in Supabase, just needs a page |
| **Cost dashboard** | Token + media-gen spend per org/workflow; budget alerts (we already track tokens + budget) |
| **Compliance guardrails** | Pluggable rules engine (banned terms, regulated industries, claim-substantiation); per-org policies |
| **Localization & translation** | One source post → N localised variants, optionally per platform |
| **Hashtag intelligence** | `HashtagSource` plugin for trending tags + per-niche scoring |
| **Content recycling** | Auto-republish evergreen posts on a long cadence with style refresh |
| **GDPR data export / delete** | One-click org export + cascading delete; scheduled via a Celery job |
| **Mobile app surface** | The WhatsApp/Telegram triggers already give you a "phone-first" UX; a proper PWA for Reviews would close the loop |

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

If we keep building on this foundation, the order with the best ROI is:

1. **Real OAuth** for the top-4 platforms (LinkedIn, X, FB/IG, YouTube) — unlocks production usage
2. **Celery Beat scheduler + DLQ retry** — the system becomes self-running
3. **Brand-voice fingerprint** (P1.1) — by far the biggest user-perceived quality jump and what most justify-the-spend pitches turn on
4. **Image generation plugin kind** (P1.2) — IG/Pinterest become *creative* targets, not just text dumps
5. **Performance feedback loop** (P1.3) — the Evaluator stops guessing and starts learning

After that, the Engagement Triage Agent + Campaign aggregate are the two features that turn this from "an SMB tool" into "an enterprise content operating system".
