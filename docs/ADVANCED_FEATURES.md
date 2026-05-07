# Advanced features — v0.2

The v0.2 milestone adds eight first-class aggregates that move SMMS from
"orchestrator that fans out single posts" to "content operating system
for marketing teams". Everything below is wired into the in-memory
backend by default; Supabase mirrors land in the next sprint.

## Campaigns (`/campaigns`)

`Campaign` is a sequence of `CampaignStep`s scheduled across one or more
platforms over a multi-day window with internal causality (`depends_on`).
Each step references a `workflow_id` and a free-text `directive`; the
`CampaignService.execute_due_steps()` walker pulls every campaign whose
step schedule has elapsed and dispatches it through `WorkflowService.run_now`
with the campaign's goal injected into the prompt.

| Endpoint | Purpose |
|---|---|
| `POST /campaigns` | Create a campaign with optional steps |
| `POST /campaigns/{id}/steps` | Append a step |
| `POST /campaigns/{id}/activate` | Mark scheduled |
| `POST /campaigns/run-due` | Walk every campaign and dispatch due steps |
| `POST /campaigns/{id}/pause` / `cancel` | Lifecycle |

## Experiments (`/experiments`)

`Experiment` runs A/B/n variant tests with EQUAL, HOLDOUT (control + cohort)
or BANDIT allocation. Each variant becomes its own `Post` published via
the configured platform adapter. After the settling window
`Experiment.settle()` selects the winner by the configured metric (default
`engagement_rate`) and freezes the result.

## Approval policies (`/approvals`)

`ApprovalPolicy` is the multi-step gate (e.g. `legal → marketing → exec`)
that supersedes the single-reviewer `ReviewSession` flow for regulated
industries. Each step supports n-of-m sign-off, optional skips, delegate
"vacation" approvers and a per-step review channel.

`ApprovalRequest` walks the ladder; `record_decision` advances when a
step's quorum is met or short-circuits on the first rejection.

## Content recycling (`/recycling`)

`RecyclePolicy` declares "for posts that match these workflow / platform
filters and beat this engagement floor, republish on this cadence".
Strategies: `repost_verbatim`, `light_rewrite`, `full_regen`,
`thread_from_top`. Light/full strategies route through an LLM provider
(when configured) to refresh the copy.

## Localization (`/localization`)

`LocalizationService.translate(...)` produces `n` locale-aware variants
of a source post. Platform-specific tone hints + term-preservation keep
hashtags, URLs and @-mentions verbatim. 22 starter locales are pre-listed
via `/localization/locales`; any BCP-47 tag is accepted at runtime.

## Hashtag intelligence (`/hashtags`)

`HashtagIntelligenceService` mines the org's own historical posts for
per-(tag, plugin) `(use_count, avg_engagement, recency_weight)` triples
and ranks them. `/hashtags/suggest` boosts tags whose past posts share
lexical terms with the seed text the caller plans to publish.

## Performance learner (`/performance`)

`PerformanceLearner` fits a tiny closed-form OLS regression of evaluator
dimension scores → realised engagement, per (org, plugin). The Evaluator
multiplies its raw dimension scores by the learned weights to compute
`overall`, so the agent's notion of "good post" auto-tunes to the org's
audience over time. `MIN_SAMPLES = 12` before the model takes effect; any
plugin with fewer samples falls back to uniform default weights.

## GDPR data export / delete (`/privacy`)

Two `DataExportJob` kinds:
- `export` bundles every org-scoped artifact into a JSON archive (signed
  URL with a 7-day TTL by default; in-memory deployments inline the data
  as a `data:` URL so tests don't need a real bucket).
- `delete` cascade-removes everything after a configurable grace window
  (default 7 days), so the user can cancel before destruction.

`run_pending` walks every queued job; the typical deployment exposes it
as a Celery beat task on a 5-minute interval.
