"""Planner — turns sources + intent into a per-platform ContentPlan."""
from __future__ import annotations

import json
from dataclasses import asdict

from app.adapters.llm.base import LLMRequest
from app.domain.value_objects.content import (
    ContentPlan, Hashtag, MediaAsset, MediaKind, PostBlueprint,
)

from .base import Agent, AgentState


# ── Per-platform media-routing table ────────────────────────────────────
# What kind of media each platform actually accepts, plus how many items
# its publisher will use. Order in ``prefer`` matters: ``video`` first
# means "pick a video if one's available, otherwise fall back to image".
# ``max_items`` is the cap our publisher honors today — most adapters
# only use ``media[0]`` so this is 1 nearly everywhere. When we ship
# real carousel / album support, bump these and the executor will
# automatically thread more items through.
#
# Plugins NOT listed here get the default: ``prefer=("image", "video"),
# max_items=1``. That's a safe fallback for any new text-+-one-attachment
# platform.
_PLATFORM_MEDIA_RULES: dict[str, dict] = {
    # Reels-style platforms — video-first, must have media.
    "instagram":  {"prefer": ("video", "image"), "max_items": 1, "requires": True},
    "tiktok":     {"prefer": ("video",),         "max_items": 1, "requires": True},
    "youtube":    {"prefer": ("video",),         "max_items": 1, "requires": True},
    # Image-first platforms.
    "pinterest":  {"prefer": ("image", "video"), "max_items": 1, "requires": True},
    # Text-with-optional-media platforms.
    "linkedin":   {"prefer": ("image", "video"), "max_items": 1, "requires": False},
    "facebook":   {"prefer": ("image", "video"), "max_items": 1, "requires": False},
    "twitter":    {"prefer": ("image", "video"), "max_items": 1, "requires": False},
    "threads":    {"prefer": ("image", "video"), "max_items": 1, "requires": False},
    "bluesky":    {"prefer": ("image", "video"), "max_items": 1, "requires": False},
    "mastodon":   {"prefer": ("image", "video"), "max_items": 1, "requires": False},
    "reddit":     {"prefer": ("image", "video"), "max_items": 1, "requires": False},
    "tumblr":     {"prefer": ("image", "video"), "max_items": 1, "requires": False},
    # Text-only (or rich-document) platforms — no media even if available.
    "telegram":   {"prefer": ("image", "video"), "max_items": 1, "requires": False},
    "discord":    {"prefer": ("image", "video"), "max_items": 1, "requires": False},
    "slack":      {"prefer": ("image", "video"), "max_items": 1, "requires": False},
    "medium":     {"prefer": ("image",),         "max_items": 1, "requires": False},
}
_DEFAULT_MEDIA_RULE = {"prefer": ("image", "video"), "max_items": 1, "requires": False}

PLANNER_SYSTEM = """You are the Planner agent in a social-media content pipeline.
You receive (a) reference source material and (b) a list of target platforms.
Produce a JSON object with this shape:
{
  "rationale": "<why these angles>",
  "source_summary": "<2-3 sentence summary of the source material>",
  "blueprints": [
    {
      "platform_name": "<linkedin|twitter|facebook|instagram|youtube>",
      "angle": "<what angle this post takes>",
      "hook": "<first 1-2 sentences>",
      "key_messages": ["...", "..."],
      "cta": "<call to action or null>",
      "hashtags": ["#tag1", "#tag2"],
      "notes": "<reasoning>"
    }
  ]
}
Tailor each blueprint to the platform: LinkedIn = professional & long, Twitter = punchy & short,
Instagram = visual & lifestyle, Facebook = community & conversational, YouTube = video pitch + description.
Respect the requested tone and audience.
"""


class PlannerAgent(Agent):
    name = "planner"

    async def run(self, state: AgentState) -> AgentState:
        # A directive (from a WhatsApp / Instagram / webhook trigger) lets
        # the user run without sources, or steer how sources are interpreted.
        if not state.source_items and not state.directive:
            state.log(self.name, "no_sources_no_directive")
            return state.merge(plan=ContentPlan(blueprints=[], rationale="no input", source_summary=""))

        source_text = "\n\n---\n\n".join(
            f"# {it.title}\n{it.body[:1500]}" for it in state.source_items[:5]
        ) or "(no sources — directive-only run)"

        directive_block = (
            f"User directive (from trigger): {state.directive}\n\n"
            if state.directive else ""
        )
        prompt = (
            f"Tone: {state.workflow_config.tone}\n"
            f"Audience: {state.workflow_config.audience}\n"
            f"Target platforms: {', '.join(state.target_platforms)}\n\n"
            f"{directive_block}"
            f"Reference material:\n{source_text}\n"
        )
        # Compose the system prompt: hardcoded base + optional per-workflow
        # custom block. Custom comes AFTER the base so the JSON-output
        # contract above takes precedence — a custom prompt that says
        # "ignore everything else" still has to fight the structural
        # requirements that come before it.
        system = _augment_system(PLANNER_SYSTEM, state.workflow_config.custom_system_prompt)
        rsp = await self.llm.complete(LLMRequest(
            prompt=prompt, system=system,
            response_format="json", temperature=0.5, max_tokens=1500,
        ))
        plan = _parse_plan(rsp.text, fallback_platforms=state.target_platforms)
        # If the source items already carry media (Notion image block,
        # Drive image file, RSS enclosure, …), copy it onto every
        # blueprint as ``attached_media``. The Executor will see those
        # and skip the media-generation plugin entirely — using the
        # real source asset rather than synthesizing one.
        plan = _attach_source_media(plan, state.source_items)
        # Observability for "why does my post have an AI-generated image
        # instead of my source image?" — emit pool + per-blueprint
        # counts into the run trail so it's visible in the run-detail
        # UI without needing to grep worker logs.
        pool_size = len(_build_media_pool(state.source_items or []))
        attached_per_bp = [
            len(getattr(bp, "attached_media", None) or [])
            for bp in plan.blueprints
        ]
        state.log(
            self.name, "plan_built",
            blueprints=len(plan.blueprints),
            tokens=asdict(rsp.usage),
            source_media_pool_size=pool_size,
            attached_media_per_blueprint=attached_per_bp,
        )
        return state.merge(plan=plan)


def _attach_source_media(plan: ContentPlan, source_items) -> ContentPlan:
    """Pick the right media for each platform out of the full pool of
    media across all selected source items.

    The old behavior was "first source item's media wins, copied to
    every blueprint" — which silently discarded other items' media and
    didn't care whether the chosen asset was the right shape for the
    platform (square image to a Reels-only platform, etc.).

    The new behavior:

      1. **Pool across sources, preserve order.** Walk every source
         item in selection-rank order; collect each item's media into
         a single flat list. De-dupe by URL so the same Notion image
         attached to two pages doesn't appear twice. Earlier-ranked
         items contribute first, so a hero image from the top-ranked
         item still beats a thumbnail from item #3.

      2. **Per-platform selection.** For each blueprint, consult
         ``_PLATFORM_MEDIA_RULES[platform_name]`` to learn (a) what
         kinds this platform prefers and (b) how many items it can
         consume. Pick from the pool accordingly. A platform that
         requires video (TikTok) gets only videos; a platform that
         accepts either (LinkedIn) gets whatever ranks first.

      3. **Fail-soft.** If the pool is empty OR no item matches the
         platform's required kinds, ``attached_media`` is left empty
         and the Executor falls back to media-generation (if
         configured) or text-only.

    This is the right contract for workflows with multiple sources
    feeding different content types — a YouTube source contributing
    videos and a Notion source contributing images can publish a
    Reels post (gets the video) AND a LinkedIn post (gets the image)
    from the same run.
    """
    if not source_items:
        return plan
    pool = _build_media_pool(source_items)
    if not pool:
        return plan

    new_bps = []
    for bp in plan.blueprints:
        chosen = _select_media_for_platform(pool, bp.platform_name)
        new_bps.append(PostBlueprint(
            platform_name=bp.platform_name,
            angle=bp.angle, hook=bp.hook,
            key_messages=list(bp.key_messages),
            cta=bp.cta,
            hashtags=list(bp.hashtags),
            suggested_media=bp.suggested_media,
            media_prompt=bp.media_prompt,
            media_kind=bp.media_kind,
            notes=bp.notes,
            attached_media=chosen,
        ))
    return ContentPlan(
        blueprints=new_bps,
        rationale=plan.rationale,
        source_summary=plan.source_summary,
    )


def _parse_plan(raw: str, fallback_platforms: list[str]) -> ContentPlan:
    try:
        data = json.loads(_extract_json(raw))
        blueprints = [
            PostBlueprint(
                platform_name=b["platform_name"],
                angle=b.get("angle", ""),
                hook=b.get("hook", ""),
                key_messages=list(b.get("key_messages", [])),
                cta=b.get("cta"),
                hashtags=[Hashtag(h) for h in b.get("hashtags", [])],
                notes=b.get("notes"),
            )
            for b in data.get("blueprints", [])
            if b.get("platform_name") in fallback_platforms
        ]
        if not blueprints:
            blueprints = _fallback_blueprints(fallback_platforms, raw)
        return ContentPlan(
            blueprints=blueprints,
            rationale=data.get("rationale", ""),
            source_summary=data.get("source_summary", ""),
        )
    except (ValueError, KeyError):
        return ContentPlan(
            blueprints=_fallback_blueprints(fallback_platforms, raw),
            rationale="LLM returned non-JSON; used fallback",
            source_summary="",
        )


def _build_media_pool(source_items) -> list[MediaAsset]:
    """Flatten every selected source item's ``media`` tuple into one
    ordered list, de-duping by URL. Earlier-ranked items contribute
    first so that highest-relevance media wins ties downstream."""
    pool: list[MediaAsset] = []
    seen_urls: set[str] = set()
    for item in source_items:
        for m in getattr(item, "media", ()) or ():
            if not m or not getattr(m, "url", None):
                continue
            if m.url in seen_urls:
                continue
            seen_urls.add(m.url)
            pool.append(m)
    return pool


def _select_media_for_platform(
    pool: list[MediaAsset], platform_name: str,
) -> list[MediaAsset]:
    """Pick the best subset of media from the pool for a given platform.

    Walks the platform's preferred kinds in order (e.g. video-first
    for Reels-style platforms), takes up to ``max_items`` matching
    assets in pool order. Returns ``[]`` if nothing matches — callers
    must be prepared for that and decide whether to fall back to
    media-generation, post text-only, or skip the platform entirely.
    """
    rule = _PLATFORM_MEDIA_RULES.get(platform_name, _DEFAULT_MEDIA_RULE)
    prefer_kinds = rule["prefer"]
    max_items = int(rule["max_items"])

    out: list[MediaAsset] = []
    for kind_name in prefer_kinds:
        target_kind = MediaKind(kind_name)
        for m in pool:
            if m in out:
                continue
            if getattr(m, "kind", None) is target_kind:
                out.append(m)
                if len(out) >= max_items:
                    return out
        if out:
            # We collected at least one of the preferred kind — don't
            # mix kinds in the same post. Stop here rather than
            # padding with a less-preferred kind that the platform
            # adapter may not handle gracefully.
            return out
    return out


def _fallback_blueprints(platforms: list[str], raw: str) -> list[PostBlueprint]:
    return [
        PostBlueprint(
            platform_name=p,
            angle="generic",
            hook=raw[:140],
            key_messages=[raw[:200]],
            cta=None,
            hashtags=[],
        )
        for p in platforms
    ]


def _extract_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    return text[start : end + 1] if start != -1 and end != -1 else "{}"


def _augment_system(base: str, custom: str | None) -> str:
    """Append the workflow's custom system prompt to a hardcoded base.

    Custom comes after the base so structural requirements (output JSON
    shape, agent role, etc.) take precedence over user policy. Empty /
    whitespace-only custom is a no-op.
    """
    if not custom or not custom.strip():
        return base
    return (
        base
        + "\n\n--- Custom instructions (per-workflow override) ---\n"
        + custom.strip()
    )
