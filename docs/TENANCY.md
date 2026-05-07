# Multi-tenancy strategy

> **TL;DR.** SMMS uses the **pool model** by default: one Supabase Postgres, one schema, every table scoped by `org_id` with Row-Level Security. That's the right default for SMB and even most mid-market customers. The architecture lets you promote individual tenants to **dedicated infra** (silo at the data layer or full silo across DB + queue + workers) by changing a few fields on their `Organization` row — no code changes.

---

## The three isolation tiers

| Tier | What's shared | What's per-tenant | When to use |
|---|---|---|---|
| **`SHARED`** *(default)* | Postgres, queue, workers, storage bucket, KMS key | Logical row-scope via RLS, storage prefix, encryption-key alias | SMB / SaaS — the vast majority of customers |
| **`DEDICATED_DB`** | Compute (FastAPI, Celery, queue) | Postgres instance + backups + KMS key | Compliance (HIPAA / FedRAMP), data residency, customer-supplied KMS, "we want our backups" |
| **`DEDICATED_STACK`** | Nothing | Postgres + queue + workers + storage bucket + observability namespace | Strict regulatory regimes, contractual SLA isolation, very large enterprise |

Each tier is a property of `Organization.isolation_level`. The system reads it on every request through `TenantContext.from_org()` and routes accordingly.

---

## Why pool is the right default

**Operationally** one schema is one set of migrations, one backup policy, one connection pool, one upgrade window. Per-tenant DBs multiply every operational task by N.

**Economically** Postgres uses ~50–200 MB of resident memory per instance just to exist. Every dedicated DB you spin up is a fixed cost — fine for a $5k/mo enterprise customer, ruinous for a $99/mo startup.

**Functionally** the RLS we ship is exhaustive: every `smms.*` table has `enable row level security`, and `smms.current_org_id()` reads the org from the JWT. The service-role key bypasses RLS only inside trusted backend code (workers, webhook handlers). A misconfigured query *cannot* return another tenant's row — Postgres rejects it at the database level.

**For most regulated industries** what auditors *actually* want is:
1. Strong logical separation (RLS) — ✓ already
2. Encrypted-at-rest (AES-256) — ✓ Supabase default
3. Encrypted-in-transit (TLS) — ✓ Supabase default
4. Audit log of access — ✓ Supabase logs + our `audit_log` table
5. Data residency — see below

If you've checked those four, pool is sufficient.

---

## When to upgrade a tenant

Promote to `DEDICATED_DB` when **any one** of these is true:

- **Customer signs a BAA / DPA that explicitly forbids shared databases** (rare but happens — e.g. some healthcare and government contracts).
- **Customer requires their own KMS key** that they hold custody of (BYOK).
- **Customer is in a region** (EU, UAE, India, China) and your shared pool is in another region. Spin up a per-region or per-customer Postgres in the right geography.
- **Customer's workload is so large** they're consuming more than ~25 % of the shared DB's CPU / IOPS. Move them to their own to protect everyone else's p99.
- **Per-tenant point-in-time recovery is required** (you need to restore one customer to 14:32 yesterday without affecting others).

Promote to `DEDICATED_STACK` when:

- The above + **strict workload isolation** is needed (a SOC-2-Type-2 with system-and-organisation controls partition).
- The customer wants a dedicated worker pool for SLA reasons.

---

## How the upgrade works

```python
org.isolation_level = IsolationLevel.DEDICATED_DB
org.dedicated_db_url = "postgresql+asyncpg://..."   # new Supabase project's pooler URL
org.encryption_key_id = "kms://aws/eu/key-abc"       # customer's KMS
org.region = "eu-west-1"
```

Then run the migration files (`001_init.sql` + `002_tenancy.sql`) against the new database, copy this org's rows over, and flip `isolation_level`. From the next request onward, `TenantAwareSessionFactory` builds a per-tenant engine and routes them to the new DB. Everything else (auth, agents, webhooks) is unchanged.

`DEDICATED_STACK` adds two extra moves: spin up a per-tenant Celery queue (separate RabbitMQ vhost or a separate broker URL via `Organization.queue_broker_url` if you add the field) and a per-tenant storage bucket. The plugin layer already supports per-tenant config via `Organization.storage_prefix` + `encryption_key_id`.

---

## Tenant-scoped resources beyond the DB

Pool isolation isn't only about Postgres. Each of these layers is *also* multi-tenant aware:

| Layer | How it's scoped today |
|---|---|
| **Storage** | One bucket, one folder per org (`storage_prefix = orgs/<org_id>`). Switch to a dedicated bucket per `DEDICATED_STACK` tenant by changing the `storage_bucket` setting through `TenantContext`. |
| **Encryption** | One KMS data key by default; `Organization.encryption_key_id` lets a tenant supply their own. The `TokenVault` reads the per-tenant key id at runtime. |
| **Queue** | One Celery namespace; queue partitioned by platform (`workflows`, `publish`). Per-tenant routing via `task_routes` is one line of config away. |
| **Rate limits** | Global default + per-org override (`Organization.rate_limit_per_minute`). |
| **Observability** | All structlog records carry `org_id`; Prometheus metric labels include `org_id`; OpenTelemetry spans tag `tenant.id`. |
| **LLM budgets** | Per-org `monthly_llm_budget_usd`; the `LLMProvider` adapter respects it. |

---

## What this *doesn't* mean

- **You don't need a separate Supabase project per customer to start.** That's a self-imposed operational tax for a problem you might never have.
- **You don't need to rewrite if a customer demands isolation later.** The `TenantContext` + `TenantAwareSessionFactory` layer handles it transparently.
- **You don't need to operate dedicated infra yourself for every tier.** A reasonable model is: pool tier on shared Supabase, dedicated tier on per-customer Supabase projects (still managed for you), full silo on customer-owned Supabase / RDS for the largest enterprise deals.

---

## Suggested billing alignment

| Tier | Suggested plan |
|---|---|
| `SHARED` | Starter / Growth (~$30–500/mo per org) |
| `DEDICATED_DB` | Enterprise (~$2k–10k/mo) |
| `DEDICATED_STACK` | Enterprise+ / Custom (≥ $10k/mo with annual commit) |

The price band needs to comfortably cover the additional fixed cost of a dedicated Supabase project (~$25/mo minimum + traffic) plus the operational overhead.
