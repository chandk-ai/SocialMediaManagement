# Conditional posting & fan-out

Every workflow has a default routing rule and every individual run can override it via natural-language directive (typed in the app, sent over WhatsApp / Telegram / IG).

## The selector

`TargetSelector` is a small declarative value object:

| Field | Meaning |
|---|---|
| `explicit_platform_ids` | Pin specific accounts by ID |
| `all_of_platforms` | Fan out to *every* connected account for these plugins (`["instagram","facebook"]`) |
| `by_handle` | Match accounts by their `@handle` regardless of platform |
| `tagged` | Match accounts with any of these tags (e.g. `marketing`, `vip`, `us-region`) |
| `exclude_platform_ids` | Subtract these |
| `exclude_plugins` | Subtract everything on these plugins |

Resolution is **additive** — explicit IDs ∪ all-of-plugin ∪ by-handle ∪ tagged, then minus exclusions. If the result is empty, we fall back to `Workflow.platform_ids`. The full computation (with rationale) is recorded in the workflow run trace.

## Where it gets configured

1. **On the workflow** — `Workflow.target_selector` is your default. Set this once when you create the workflow ("post to all my Instagram accounts on this workflow").
2. **Per run** — any directive that arrives via WhatsApp / Telegram / IG / the app is parsed by `DirectiveRouter` into a per-run selector. The two are merged additively.

## Directive grammar (natural language)

The router recognises these patterns case-insensitively. Plugin synonyms are mapped to canonical names (`ig` → `instagram`, `x` / `tweet` → `twitter`, `fb` / `meta` → `facebook`, `tg` → `telegram`, `bsky` → `bluesky`, …).

| You say… | Selector becomes |
|---|---|
| "post to all my **Instagram** accounts" | `all_of_platforms = ["instagram"]` |
| "drop on **every IG and FB account**" | `all_of_platforms = ["instagram","facebook"]` |
| "**LinkedIn only** — Q4 results" | `all_of_platforms = ["linkedin"]` |
| "push to **LinkedIn, Twitter and YouTube**" | `all_of_platforms = ["linkedin","twitter","youtube"]` |
| "post on **@brand and @careers**" | `by_handle = ["@brand","@careers"]` |
| "promote to **all my marketing accounts**" | `tagged = ["marketing"]` |
| "**tag:vip** — exclusive promo" | `tagged = ["vip"]` |
| "every IG account **except slack**" | `all_of_platforms = ["instagram"]`, `exclude_plugins = ["slack"]` |

When nothing matches, the selector is empty and the workflow defaults are used as-is. The system never silently drops a publish.

## End-to-end example via WhatsApp

```
You → WA bot:    "Drop on every IG and FB account: Q4 launch announcement"
                        │
                        ▼
WhatsAppTrigger.parse()           → directive = "Drop on every IG and FB account: …"
DirectiveRouter().parse(directive) → TargetSelector(all_of_platforms=("facebook","instagram"))
WorkflowService.run_from_trigger()
TargetResolver.resolve()           → 4 connected accounts (3 IG + 1 FB)
Planner generates 2 drafts (one per plugin)
ExecutorAgent personalises per platform
EvaluatorAgent scores each
CritiqueAgent decides
ReviewSession ────► back to you on WhatsApp with [Approve][Revise][Reject]
"Approve"
        ▼
WorkflowService.resume_after_review()
        ▼
Fan-out: 4 Posts published, one per connected account
```

## In code

```python
from app.domain.value_objects.targeting import TargetSelector

# Workflow default — every run hits all FB pages tagged "marketing"
selector = TargetSelector(
    all_of_platforms=("facebook",),
    tagged=("marketing",),
)

# Override at run-time (e.g. per directive)
override = TargetSelector(by_handle=("@brand-us",))
final = selector.merged_with(override)
```

## Tagging accounts

Tags live on the `Platform` entity. Use the Platforms page in the dashboard or via API:

```bash
curl -X POST $API/api/v1/platforms \
     -H 'Authorization: Bearer …' \
     -d '{
       "plugin_name":"instagram",
       "display_name":"Acme · Marketing IG",
       "account_handle":"@acme-marketing",
       "tags":["marketing","us-region"]
     }'
```

Once tagged, any directive like `"post to all my marketing accounts"` will resolve to that account regardless of which platform it lives on.
