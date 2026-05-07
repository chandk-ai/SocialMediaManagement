# Architecture

## Goals

1. **Plug-and-play** — adding a new social platform, content source, or LLM provider must require *zero changes* to the core domain or agents.
2. **Configurable end-to-end** — every workflow (which sources feed which platforms, on what schedule, with what tone) is declarative configuration, not code.
3. **Reliability** — agentic workflows are stateful, idempotent, and resumable; failed posts retry with exponential backoff.
4. **Observable** — every agent decision, every external call, every domain event is traced and logged.
5. **Secure-by-default** — OIDC SSO (Okta), encrypted token vault, RBAC, full audit trail.

---

## Layered architecture (Hexagonal / Ports & Adapters)

```
┌────────────────────────────────────────────────────────────────────┐
│                         FRONTEND  (Next.js)                        │
└──────────────────────────────┬─────────────────────────────────────┘
                               │  REST / WebSocket
┌──────────────────────────────▼─────────────────────────────────────┐
│                       API LAYER  (FastAPI)                         │
│   Routers · Auth Middleware · Rate-limit · Request Validation      │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────────┐
│                    APPLICATION SERVICES                            │
│   Use-cases · Orchestration · Transactions · Domain-event dispatch │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────────┐
│                          DOMAIN                                    │
│   Entities · Value Objects · Domain Events · Business Invariants   │
│   (pure Python, zero framework imports)                            │
└──────┬───────────────┬────────────────┬──────────────────┬─────────┘
       │ports          │ports           │ports             │ports
┌──────▼─────┐  ┌──────▼──────┐  ┌──────▼───────┐  ┌───────▼────────┐
│ ADAPTERS:  │  │ ADAPTERS:   │  │ ADAPTERS:    │  │ ADAPTERS:      │
│ Platforms  │  │ Sources     │  │ LLMs         │  │ Persistence    │
│ (LinkedIn, │  │ (RSS, Web,  │  │ (Anthropic,  │  │ (Postgres,     │
│  X, FB,    │  │  Notion,    │  │  OpenAI,     │  │  Redis, S3)    │
│  IG, YT)   │  │  Drive, DB) │  │  Azure)      │  │                │
└────────────┘  └─────────────┘  └──────────────┘  └────────────────┘
```

### Why hexagonal?

The **domain** never imports from `adapters/`. Adapters depend on **ports** (Python `Protocol`s / ABCs) declared by the domain. This means:

- Swap PostgreSQL for DynamoDB → touch one file in `adapters/`.
- Replace Anthropic with Bedrock → drop in a new `LLMProvider` adapter.
- Add Pinterest → write `PinterestPlatform(SocialPlatform)` and register it.

---

## Design patterns in use

| Pattern | Where | Why |
|---|---|---|
| Hexagonal / Ports & Adapters | Project-wide | Decouple domain from infra |
| Repository | `repositories/` | Persistence-agnostic data access |
| Unit of Work | `infrastructure/db/uow.py` | Transactional consistency |
| Strategy | `adapters/platforms`, `adapters/sources`, `adapters/llm` | Interchangeable behavior |
| Plugin Registry | `plugins/registry.py` | Runtime adapter discovery |
| Factory | `agents/factory.py`, `adapters/factory.py` | Instantiation by config |
| Dependency Injection | `core/container.py` (using `dependency-injector`) | Wire components without globals |
| Domain Events + Outbox | `domain/events`, `infrastructure/outbox.py` | Reliable cross-aggregate communication |
| CQRS-lite | `services/` (commands) vs `repositories/` reads | Read/write separation |
| State Machine | `agents/orchestrator.py` (LangGraph) | Stateful multi-agent flow |
| Circuit Breaker + Retry | `core/resilience.py` | Resilient external calls |
| Specification | `domain/specifications.py` | Composable business rules |

---

## Data model (high level)

| Entity | Purpose |
|---|---|
| `User` | Authenticated principal (via Okta) |
| `Organization` | Tenant boundary; owns workspaces, platforms, sources |
| `Platform` | A connected social account (e.g., "Acme LinkedIn Page") with OAuth tokens in vault |
| `Source` | A configured content source (e.g., "Engineering Blog RSS") |
| `Workflow` | Declarative pipeline: Source(s) → Agents → Platform(s) + schedule + constraints |
| `WorkflowRun` | One execution; holds full agent trace, decisions, generated content |
| `Post` | A piece of generated content; lifecycle: `draft → review → approved → scheduled → published → failed` |
| `AuditLog` | Append-only log of every state transition and human action |

---

## Request / processing flow

```
┌───────┐  POST /workflows/{id}/run   ┌────────────────┐
│ User  │────────────────────────────▶│  API Router    │
└───────┘                             └────────┬───────┘
                                               │ enqueue Celery task
                                               ▼
                                      ┌────────────────┐
                                      │  Worker        │
                                      └────────┬───────┘
                                               ▼
                                      ┌────────────────┐
                                      │  Orchestrator  │  (LangGraph state machine)
                                      └────────┬───────┘
        ┌──────────────────┬──────────────────┼──────────────────┬──────────────────┐
        ▼                  ▼                  ▼                  ▼                  ▼
   ┌─────────┐        ┌─────────┐        ┌─────────┐        ┌─────────┐        ┌─────────┐
   │ Sources │──fetch▶│ PLANNER │──draft▶│EXECUTOR │──score▶│EVALUATOR│──fix──▶│CRITIQUE │
   └─────────┘        └─────────┘        └────┬────┘        └─────────┘        └────┬────┘
                                              │                                     │
                                              └──────────loop until quality ≥ threshold─┘
                                                                                    │
                                                                            ┌───────▼────────┐
                                                                            │ Post repository│
                                                                            └───────┬────────┘
                                                                                    ▼
                                                                            ┌───────────────┐
                                                                            │ Platform Adptr│──▶ social network
                                                                            └───────────────┘
```

See [`AGENTS.md`](AGENTS.md) for the full agent contract.

---

## Scalability

- **Horizontal API** — FastAPI is stateless; scale behind a load balancer.
- **Workers** — Celery + RabbitMQ; partition queues by platform (LinkedIn rate limits ≠ Twitter rate limits).
- **DB** — PostgreSQL with read replicas; partition `workflow_runs` by month.
- **Cache** — Redis for source content cache + LLM response cache.
- **LLM cost control** — `LLMProvider` adapter computes token counts and respects per-org budgets.

## Reusability

Three reusability axes:
1. **Adapter axis** — anyone can drop a new adapter into `adapters/<type>/<name>.py` and `@register`-decorate it.
2. **Agent axis** — agents are composable nodes; new agent types (e.g., `Translator`, `BrandSafety`) plug into the LangGraph.
3. **UI axis** — frontend talks to a stable OpenAPI spec; the `PlatformCard`, `WorkflowEditor`, `PostPreview` components render generically based on plugin metadata exposed by `GET /plugins`.
