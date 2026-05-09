"""Tailor agent — turns one plan into per-platform variants.

Pillar 5 owns this agent. The skeleton lives here so the durable
runner can call ``tailor_for_platforms()`` regardless of whether
the deeper LLM-backed implementation is wired yet.

Default behaviour (no LLM call): return the plan as-is for each
platform with light per-platform shaping (length caps, hashtag
hints). When ``llm`` is provided, the function asks the model to
rewrite the same plan in each platform's idiom.

Per-platform idioms encoded here (extend as needed):
    linkedin    → long-form professional, no hashtag spam (max 3)
    twitter     → punchy, thread-friendly, 240 chars per post
    instagram   → caption + hashtag block at the end (~10 hashtags)
    facebook    → conversational, 1–3 paragraphs
    threads     → casual, conversational, 480 chars
    tiktok      → caption + 3–5 hashtags
    youtube     → title + description + tags
    reddit      → title + body, no marketing speak
    bluesky     → 300-char-per-post thread style
    mastodon    → 500-char, conversational
    medium      → headline + intro + body
    pinterest   → caption + clear CTA
    discord     → casual, hyperlinked
    slack       → professional, threaded
    telegram    → short, channel-style
    tumblr      → free-form blog
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)


PLATFORM_HINTS: dict[str, str] = {
    "linkedin":   "Professional long-form (~150–250 words). Max 3 hashtags. End with a question.",
    "twitter":    "Punchy. 240 chars max. If it must be longer, format as a numbered thread (1/, 2/, …).",
    "x":          "Punchy. 240 chars max. If it must be longer, format as a numbered thread (1/, 2/, …).",
    "instagram":  "Hook in first sentence. 80–150 word caption. Hashtag block (~8–12) on its own line at the end.",
    "facebook":   "Conversational, 1–3 short paragraphs. Direct CTA.",
    "threads":    "Casual conversational, 480 chars max. Mostly plain text, no hashtag overload.",
    "tiktok":     "Caption-style hook. 3–5 hashtags. Reference what the viewer will see.",
    "youtube":    "Provide a punchy title (~60 chars) and a description (200–500 words) with chapters / timestamps where applicable.",
    "reddit":     "Title that asks a question or promises payoff. Body is plain prose, no marketing speak.",
    "bluesky":    "300 chars per post; thread if needed. Conversational.",
    "mastodon":   "500 chars max. Conversational, no over-promotion.",
    "medium":     "Headline + dek + body. Professional editorial tone.",
    "pinterest":  "Vivid caption with a clear CTA and one link.",
    "discord":    "Casual, embed-friendly. Use markdown.",
    "slack":      "Professional, structured. Use Slack mrkdwn.",
    "telegram":   "Short channel-post style. 1–3 sentences max.",
    "tumblr":     "Free-form, blog-style.",
}


async def tailor_for_platforms(
    *, llm, plan: dict[str, Any], platforms: list[str],
    workflow_config,
) -> dict[str, dict[str, Any]]:
    """Returns a dict keyed by platform-id with the per-platform
    variant payload:

        {
          "linkedin": {"plan": {...}, "hint": "..."},
          "twitter":  {"plan": {...}, "hint": "..."},
        }

    The ``plan`` field is what the Executor will hand to the LLM. The
    LLM-backed mode rewrites the plan; the no-LLM fallback just
    annotates the same plan with the platform hint.
    """
    out: dict[str, dict[str, Any]] = {}
    base_summary = (plan or {}).get("summary") or ""

    for plat in platforms:
        key = plat.lower()
        hint = PLATFORM_HINTS.get(key, "")
        if llm is None or not base_summary:
            out[plat] = {"plan": plan, "hint": hint, "rewritten": False}
            continue

        try:
            sys = (
                "You are a social-media editor. Rewrite the plan summary "
                "for the target platform, preserving the core message but "
                "adopting the platform's idiom. Output ONLY the rewritten "
                "summary — no preamble, no markdown headings, no quotes."
            )
            user = (
                f"Target platform: {plat}\n"
                f"Style hint: {hint}\n"
                f"Brand voice: {getattr(workflow_config, 'tone', '') or 'neutral'}\n"
                f"Audience: {getattr(workflow_config, 'audience', '') or 'general'}\n\n"
                f"Original plan summary:\n---\n{base_summary}\n---\n\n"
                f"Rewritten summary:"
            )
            text = await llm.complete(prompt=user, system=sys,
                                       max_tokens=600, temperature=0.6)
            new_plan = dict(plan or {})
            new_plan["summary"] = (text or "").strip() or base_summary
            out[plat] = {"plan": new_plan, "hint": hint, "rewritten": True}
        except Exception as exc:                                     # noqa: BLE001
            log.warning("tailor_rewrite_failed", platform=plat,
                        error=str(exc))
            out[plat] = {"plan": plan, "hint": hint, "rewritten": False}

    return out
