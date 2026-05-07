# Social Media Management System (SMMS)

A highly configurable, plug-and-play Social Media Management platform powered by an AI agent orchestrator (Planner → Executor → Evaluator → Critique). Connect any **Source** of reference content, enrich it with **GenAI**, and publish to any **Social Platform** (LinkedIn, X/Twitter, Facebook, Instagram, YouTube, ...).

---

## Highlights

- **Hexagonal / Ports-and-Adapters architecture** — domain logic is fully decoupled from frameworks, social APIs, and LLMs.
- **Plug-and-play plugin system** — add a new social platform, source connector, or LLM provider by implementing a single interface and registering with the plugin registry. No core code changes required.
- **Four-agent orchestrator** built on **LangGraph** — `Planner`, `Executor`, `Evaluator`, `Critique` cooperate in a stateful loop with self-correction and human-in-the-loop checkpoints.
- **Modern Python backend** — FastAPI, async SQLAlchemy 2.x, Pydantic v2, Celery workers, Redis, PostgreSQL.
- **Modern frontend** — Next.js 14 (App Router) + React 18 + Tailwind + shadcn/ui. Clean, low-noise dashboard.
- **Enterprise security** — OAuth2 / OIDC via **Okta** (or any OIDC provider), per-platform OAuth token vault, RBAC, audit log, secrets management.
- **Observability built in** — OpenTelemetry tracing, Prometheus metrics, structured JSON logs.
- **Cloud-native** — Docker Compose for local, Helm/K8s manifests for production.

---

## Repository layout

```
SocialMediaManagmentSystem/
├── backend/              # Python FastAPI service + agents + adapters
│   └── app/
│       ├── api/          # HTTP layer (FastAPI routers)
│       ├── core/         # Config, security, DI container, logging
│       ├── domain/       # Entities, value objects, domain events (DDD)
│       ├── agents/       # Planner, Executor, Evaluator, Critique + Orchestrator
│       ├── adapters/     # Plug-and-play adapters
│       │   ├── platforms/  # LinkedIn, X, Facebook, Instagram, YouTube
│       │   ├── sources/    # RSS, web, Notion, Drive, DB, file
│       │   └── llm/        # Anthropic, OpenAI, Azure
│       ├── services/     # Application services (use-case orchestration)
│       ├── repositories/ # Data-access (Repository pattern)
│       ├── plugins/      # Plugin manager + registry
│       ├── infrastructure/  # DB, queue, cache, observability
│       ├── schemas/      # Pydantic DTOs
│       └── workers/      # Celery task workers
├── frontend/             # Next.js 14 portal
├── infra/                # Docker, K8s, Terraform
├── docs/                 # Architecture, agents, plugins, security
└── docker-compose.yml
```

---

## Quick start

```bash
# Copy env template
cp .env.example .env

# Bring up the full stack (Postgres, Redis, RabbitMQ, MinIO, backend, worker, frontend)
docker compose up --build

# UI       → http://localhost:3000
# API      → http://localhost:8000/docs (Swagger)
# Flower   → http://localhost:5555 (Celery monitor)
```

For development without Docker, see `backend/README.md` and `frontend/README.md`.

---

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system design, layers, patterns
- [`docs/AGENTS.md`](docs/AGENTS.md) — the 4-agent orchestrator
- [`docs/PLUGINS.md`](docs/PLUGINS.md) — building new platform / source / LLM adapters
- [`docs/SECURITY.md`](docs/SECURITY.md) — Okta/OIDC, token vault, RBAC, audit
- [`docs/SUPABASE.md`](docs/SUPABASE.md) — using Supabase as persistence + auth + realtime
- [`docs/TRIGGERS.md`](docs/TRIGGERS.md) — trigger system + WhatsApp / Instagram / Telegram review-and-approve flow
- [`docs/TARGETING.md`](docs/TARGETING.md) — conditional posting / fan-out via natural-language directives
- [`docs/TENANCY.md`](docs/TENANCY.md) — multi-tenancy strategy: shared pool vs. dedicated-DB vs. dedicated-stack
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — coverage audit and prioritized roadmap of remaining features

---

## What's bundled

**16 social platforms** — LinkedIn · X / Twitter · Facebook · Instagram · YouTube · TikTok · Threads · Pinterest · Reddit · Mastodon · Bluesky · Medium · Discord · Slack · Telegram · Tumblr.
Connect *multiple accounts per platform* (e.g. five Instagram pages) and target any subset from any workflow.

**12 source connectors** — RSS · web page · file folder · multi-level recursive web crawler · Google Drive · SQL database (any SQLAlchemy URL) · MongoDB · vector DB (Pinecone / Chroma / Qdrant / Weaviate) · YouTube channel + transcripts · Notion · GitHub · S3 / MinIO.

**9 LLM providers** — Anthropic Claude · OpenAI · Azure OpenAI · Ollama (self-hosted) · OpenAI-compatible (covers vLLM, Together, Groq, LM Studio, LocalAI, Qwen / DashScope, DeepSeek, Mistral, Perplexity, Fireworks) · Hugging Face · Google Gemini · AWS Bedrock · Mock (deterministic).

**Persistence: Supabase** — managed Postgres with **Row-Level Security** for multi-tenancy, **Supabase Auth** (JWT) as an alternative to Okta, **Supabase Storage** for media, and **Supabase Realtime** to stream agent run progress to the UI. Schema + RLS policies are versioned at `backend/app/infrastructure/db/migrations/`. Set `PERSISTENCE_BACKEND=memory` to fall back to in-process repos for tests.

**Triggers + human-in-the-loop review** — workflows can be initiated from any of: the dashboard "Run now" button, a cron schedule, a generic webhook, an inbound **WhatsApp** message, an **Instagram** DM/mention, or a **Telegram** bot message. When a run produces drafts that need approval, SMMS sends them back via the same channel (with Approve / Revise / Reject buttons or inline-keyboard) and resumes only after the reviewer replies — so you can drive a full publish cycle from your phone without ever opening the dashboard. See [`docs/TRIGGERS.md`](docs/TRIGGERS.md).

## Status

This repository is the **architectural foundation** with all reusable components in place. Each adapter ships with a working interface contract and a reference implementation (mocked external calls where credentials are required). Replace the mock layer with real API calls by adding credentials in `.env`.
