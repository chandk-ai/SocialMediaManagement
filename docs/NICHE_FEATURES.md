# Niche features — what makes SMMS different

Most social-media-management tools (Buffer, Hootsuite, Later, Sprout) are
fast-followers of each other: scheduled posting, multi-platform publishing,
basic analytics. SMMS adds a layer of niche features specifically for
contexts those general tools don't handle well — agencies that approve
from chat, regulated industries, content teams that live in Notion.

This file is the canonical reference for those features. Each section
covers: what it is, when to use it, how it's wired, and where to extend.

The in-app `/help` page covers the user-facing version of the same content;
this doc is the developer / operator perspective.

---

## #1 — Approve-from-Telegram

**What**: agencies can approve drafts at midnight from their phone. Bot DMs
the draft with ✅/✏️/❌ inline keyboard buttons. One tap publishes (or
opens revision, or kills the run).

**When**: any team that already uses chat over email for fast iteration.
The hero demo because viewers can replicate it themselves in 3 minutes
(WhatsApp's equivalent takes days because of Meta's verification wall).

**Wiring**:
- `app/adapters/triggers/telegram.py` — webhook + callback_query handler
- `app/adapters/review_channels/telegram.py` — outbound message + inline keyboard
- `frontend/components/triggers/TelegramSetup.tsx` — two-step setup wizard
  with auto-generated `secret_token` and copy-pasteable curl command
- Bot token stored per-trigger in encrypted config; `TELEGRAM_BOT_TOKEN`
  env var still works as a single-bot fallback for self-hosted deployments

---

## #3 — Notion / Airtable as social CMS

**What**: each row in a Notion database is a publishable post. The system
reads a `Status` field, only publishes rows you've marked `Ready`, and writes
back `Published` + the live URL when it's done. No separate calendar to
maintain.

**When**: content teams that already plan in Notion. Most agency-side teams
do. The pitch is "stop maintaining a separate calendar in our tool."

**Wiring**:
- `app/adapters/sources/notion.py` — `cms_mode` toggle changes fetch + adds
  `mark_published` / `mark_failed` writeback hooks
- `app/services/workflow_service.py::_execute_cms()` — bypasses the agent
  loop (the user already wrote the post; no Planner / Evaluator / Critique
  needed) and skips straight to Post creation + publish
- Migration `007_post_source_link.sql` — `posts.source_id` + `source_external_id`
  columns so writeback survives the review checkpoint

**Notion DB contract** (field names configurable per source):
- `Name` — title
- `Content` — rich text, the post body
- `Status` — select with values `Draft / Ready / Published / Failed`
- `Platforms` — multi-select; values match plugin names (`linkedin`, `twitter`, …)
- `Scheduled at` — date (optional)
- `Published URL` — url, written by us after publish
- `Error` — rich text, written on failure

---

## #4 — Compliance scanning

**What**: per-industry rule packs (FINRA, HIPAA, FDA, crypto, GDPR) scan
every Executor draft before publish. Violations populate the
`EvaluationReport.flags`, which the Critique agent already escalates to
human review with the specific rule that fired.

**When**: the org operates in a regulated space and a non-compliant post
could trigger enforcement (FINRA fine, FDA warning letter, FTC action).

**Wiring**:
- `app/services/compliance.py` — profile registry + scanner. Each profile
  is `forbidden` regex patterns + `required_any` (must include at least
  one match — used for disclaimer enforcement)
- `app/agents/evaluator.py` — calls `scan_drafts()` after LLM scoring and
  merges violations into each draft's flags
- `app/api/v1/compliance.py` — `GET /compliance/profiles` for the UI picker
- `WorkflowConfig.compliance_profile` — None disables, otherwise picks the
  named profile

**Adding a profile**: edit `_PROFILES` in `app/services/compliance.py`. Pure
data, no migration. Each profile is name + label + description + tuple of
`CompliancePattern`s.

**Honest limits**: this catches the obvious mistakes (junior team members
typing "guaranteed returns"). It is not a substitute for legal review and
won't catch context-dependent violations.

---

## #7 — A/B with auto-winner promotion

**What**: experiments already exist (`/experiments`); v0.3 adds the
"self-improving" loop. When an experiment settles with a clear winner, the
winning variant's text is folded into the brand-voice corpus
(`BrandVoiceService.ingest_post`), so future Executor calls bias toward that
style.

**When**: any account where "what tone works best" isn't obvious yet. Over
weeks, the corpus grows smarter without human intervention.

**Wiring**:
- `app/services/experiment_service.py::_promote_winner()` — runs after
  `e.settle()` returns a winner
- Brand-voice service is an optional dep; `_promote_winner` is a no-op when
  it's None (memory backend / no LLM provider)

**Difference vs. the competition**: Hootsuite has A/B but doesn't feed the
winner back into a generative pipeline. Here, the brand-voice corpus that
the Executor retrieves from grows smarter with every concluded experiment.

---

## #9 — Tamper-evident audit log (hash chain)

**What**: every audit row carries `prev_hash` (previous row's hash) and
`row_hash` (sha256 of canonical-fields-of-this-row || prev_hash). A BEFORE
INSERT trigger populates both atomically. Direct deletion or modification
breaks the chain at that row and every subsequent row.

**When**: SOC2 / HIPAA-adjacent buyers ask for tamper-evident audit by
name. Useful any time "who changed what when" needs to be answered without
trusting the DB admins.

**Wiring**:
- Migration `008_audit_log_hash_chain.sql` — adds columns, function,
  trigger, and a recompute view (`smms.audit_log_verified`)
- `app/services/audit_log.py::verify_chain()` — walks the chain and reports
  any mismatch
- `GET /audit-logs/verify` (admin only) — surfaces the verification
- Audit page renders a "✓ chain verified · N/N" or "⚠ chain break" badge

---

## #10 — Adaptive scheduling

**What**: workflow decides its own cadence from recent engagement. Posts
more often when engagement is rising, backs off when audience fatigue
shows up. Bounded between 1 post / 2h and 1 post / 7d.

**When**: new accounts where the right cadence isn't known yet, or any
team that wants the system to figure it out.

**Wiring**:
- `app/domain/value_objects/schedule.py` — new `ScheduleKind.ADAPTIVE`
- `app/services/adaptive_scheduler.py` — pure-function `compute_interval`
  (compare median engagement of latest 4 posts vs the prior 4) + `is_due`
- `app/workers/scheduler.py::_adaptive_due()` — Beat-tick consultation
- Workflow wizard's Schedule step → "Adaptive — engagement-driven cadence"

**Trace transparency**: every adaptive decision (interval + reason +
engagement values) is logged so customers see the reasoning rather than
a black box.

---

## #11 — Telegram group quorum approvals

**What**: the bot sits in a Telegram group of N approvers. When ANY one
approves, OR when K-of-N approve (configurable), the run resumes. A single
❌ vetoes immediately, a single ✏️ pulls the draft back for revision.

**When**: editorial-board approval flows where multiple voices need to
sign off, but you don't want to wait for everyone.

**Wiring**:
- Migration `006_review_quorum.sql` — adds `quorum_required` +
  `quorum_votes[]` to `smms.review_sessions`
- `ReviewSession.record_vote()` — per-actor idempotent (second tap by same
  user updates their vote in place)
- `ReviewService._apply_with_quorum()` — separate code path for quorum
  channels; routes to existing approve/revise/reject once threshold is hit
- `TriggerEvent.actor_id` — additive field so the per-user identity travels
  through the webhook layer (other channels just pass `None`)
- Each individual vote is audit-logged with the Telegram username + tally

---

## Production smoke test

`scripts/smoke_test.py` — Python stdlib only, drives the deployed system
through the read-only invariants (and an optional create-cleanup round
trip via `--write`). Use after every production deploy.

```bash
# Read-only against your prod URL:
BASE_URL=https://api.example.com \
SUPABASE_URL=https://xxx.supabase.co \
SUPABASE_ANON_KEY=ey... \
SMMS_EMAIL=admin@acme.com \
SMMS_PASSWORD=... \
python scripts/smoke_test.py

# Read + create-and-cleanup:
python scripts/smoke_test.py --write

# Or skip Supabase sign-in by passing a bearer token directly:
SMMS_BEARER=ey... BASE_URL=https://api.example.com python scripts/smoke_test.py
```

What it checks: liveness, readiness (Postgres + Redis pings), `/auth/me`
(verifies you're not in the placeholder org), audit hash-chain integrity,
LLM budget shape, compliance profile registry, plugin counts per kind,
team members. With `--write`: creates a Source / Platform / Workflow,
verifies they're listed, and deletes them in a `finally` block.

## Niches still pending (held)

- **#2 Voice-clone brand fingerprint** — scoped to your *connected*
  accounts (no public scraping due to platform ToS). One-time backfill
  from existing posts into the brand-voice corpus.
- **#5 Live-event blast mode** — large build, only useful at conferences /
  launches / sports moments. Held for a real customer ask.
- **#6 Voice-memo planning via Telegram** — record a 60-sec voice note,
  Whisper transcribes, Planner builds your week. Needs Whisper API
  integration + binary file download from Telegram.
- **#8 Podcast clip auto-publisher** — RSS → Whisper → 3 best 60s clips
  with captions. Large build with several open questions about clip
  selection heuristics.
