"""Executor — turns each PostBlueprint into a polished DraftPost."""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import asdict

from app.adapters.llm.base import LLMRequest
from app.domain.value_objects.content import DraftPost, Hashtag

from .base import Agent, AgentState


# Matches http(s):// URLs the user might have typed into the directive
# or that appear in source-item bodies. Conservative — stops at common
# trailing punctuation so we don't include the period at the end of a
# sentence. Doesn't try to validate the URL beyond shape.
_URL_RE = re.compile(r"https?://[^\s<>\"')]+[^\s<>\"')\.,;:!?]")


def _extract_urls(text: str) -> list[str]:
    """All http(s) URLs in ``text``, in order, deduplicated."""
    seen: list[str] = []
    for m in _URL_RE.finditer(text or ""):
        u = m.group(0)
        if u not in seen:
            seen.append(u)
    return seen


def _ensure_urls_present(text: str, state: AgentState, bp) -> str:
    """Guarantee that any URL the user mentioned in the directive (or
    that came from a source item — e.g. the user's Notion page included
    a registration link) appears in the final post text.

    The Executor's system prompt asks the LLM to preserve URLs, but
    LLMs ignore that ~10% of the time when generating short-form
    social copy where URLs feel unnatural. This post-processing step
    is the safety net: extract URLs from the directive + key_messages
    + source bodies, check which are missing from the LLM output,
    and append them on a clean "Register / Learn more:" line.

    Idempotent — if all URLs are already present, returns ``text``
    unchanged.
    """
    # Sources to scan for URLs the user "meant" to surface:
    #   * the directive (highest signal — user typed this just now)
    #   * the blueprint's key_messages (planner extracted these)
    #   * each selected source item's body (Notion page text etc.)
    candidates: list[str] = []
    candidates += _extract_urls(state.directive or "")
    for km in (getattr(bp, "key_messages", None) or []):
        candidates += _extract_urls(str(km))
    for it in (state.source_items or []):
        candidates += _extract_urls(getattr(it, "body", "") or "")

    if not candidates:
        return text

    # Dedupe preserving order; keep only URLs the LLM dropped.
    seen: set[str] = set()
    missing: list[str] = []
    for u in candidates:
        if u in seen:
            continue
        seen.add(u)
        if u not in text:
            missing.append(u)

    if not missing:
        return text

    # Insert before any hashtag block so the URL sits with the body,
    # not after the tags. Hashtags are typically the LAST chunk
    # separated from the body by a blank line — Executor's own
    # convention.
    suffix_label = "Register here:" if len(missing) == 1 else "Links:"
    appendage = "\n\n" + suffix_label + "\n" + "\n".join(missing)

    # Find the hashtag block (line starting with "#" near the end).
    lines = text.split("\n")
    tag_start = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            tag_start = i
        else:
            break

    if tag_start < len(lines):
        body = "\n".join(lines[:tag_start]).rstrip()
        tags = "\n".join(lines[tag_start:]).lstrip("\n")
        return f"{body}{appendage}\n\n{tags}".strip()
    return (text.rstrip() + appendage).strip()


# Concurrency cap on parallel per-blueprint LLM calls. The Executor
# used to call ``asyncio.gather(*tasks)`` with no limit; a workflow
# fanning out to 5 platforms therefore fired 5 simultaneous LLM
# requests, plus another 1-2 if Planner / Critique calls overlapped.
# Even on a paid LLM tier this trips the provider's PER-SECOND
# burst limit (1000 RPM ≈ 16 RPS; 5 concurrent + multimodal payloads
# regularly produces 429 Quota Exceeded).
#
# 4 is the floor that keeps a typical 5-platform run from saturating
# any single provider's burst window. Override via env for stress
# tests or if you upgrade to higher tiers.
_EXECUTOR_LLM_CONCURRENCY = int(os.getenv("EXECUTOR_LLM_CONCURRENCY", "4"))


EXECUTOR_SYSTEM = """You are the Executor agent. Given a single PostBlueprint
and any prior critique notes, produce the final post text for the target platform.
Rules:
- Match the requested tone exactly.
- Stay within the platform character limit (you'll be told).
- Open with the supplied hook.
- End with the CTA if present.
- Place hashtags after the body, separated from it by a blank line.
- **If the brief or directive mentions a URL, include the FULL URL verbatim
  in the post body (do NOT paraphrase, shorten, or drop it). The user
  explicitly wants readers to click that link — preserving it is mandatory,
  not optional. Place URLs naturally in the body or in a "Register here:"
  / "Learn more:" line, but never silently omit them.**
- Output ONLY the post text — no preamble, no explanation."""


class ExecutorAgent(Agent):
    name = "executor"

    PLATFORM_LIMITS = {
        "linkedin": 3000,
        "twitter": 280,
        "facebook": 5000,
        "instagram": 2200,
        "youtube": 5000,
    }

    async def run(self, state: AgentState) -> AgentState:
        if state.plan is None or not state.plan.blueprints:
            state.log(self.name, "no_blueprints")
            return state.merge(drafts=[])

        critique_block = ""
        if state.critique_notes:
            critique_block = (
                "\nPrior critique to address:\n- "
                + "\n- ".join(state.critique_notes)
            )

        # One semaphore per executor invocation. Different runs get
        # independent buckets — we're throttling within a run, not
        # globally across the org. The worker process's own concurrency
        # (smms-jobs-worker = 4 by default) provides the cross-run cap.
        llm_gate = asyncio.Semaphore(_EXECUTOR_LLM_CONCURRENCY)

        async def _make_one(bp) -> DraftPost:
            limit = self.PLATFORM_LIMITS.get(bp.platform_name, 1000)
            voice_block = ("\n\n" + state.voice_block) if state.voice_block else ""
            prompt = (
                f"Platform: {bp.platform_name} (limit {limit} chars)\n"
                f"Tone: {state.workflow_config.tone}\n"
                f"Audience: {state.workflow_config.audience}\n"
                f"Angle: {bp.angle}\n"
                f"Hook: {bp.hook}\n"
                f"Key messages: {bp.key_messages}\n"
                f"CTA: {bp.cta or '(none)'}\n"
                f"Hashtags: {[h.value for h in bp.hashtags]}\n"
                f"{critique_block}"
            )
            # System prompt = hardcoded base + voice RAG (if any) + per-workflow
            # custom block (if any). The custom block lands LAST so the agent's
            # core rules (platform limits, hashtag placement, etc.) still
            # take precedence — see comment in app/agents/planner.py.
            system = EXECUTOR_SYSTEM + voice_block + _custom_block(
                state.workflow_config.custom_system_prompt,
            )
            # Multimodal context: when the blueprint already has
            # source-attached IMAGES, pass them to the LLM so the
            # generated caption can actually reference what's in the
            # picture — not hallucinate from text alone. Videos are
            # excluded (most vision models can't process them, and
            # frame extraction is out of scope here). Provider adapters
            # that don't support vision will quietly ignore this field.
            image_urls = tuple(
                m.url for m in getattr(bp, "attached_media", None) or []
                if m and m.url and getattr(m, "kind", None) is not None
                and m.kind.value == "image"
            )
            # Hold the gate around the LLM call (the multimodal-heavy
            # part). Media generation that follows is fast / non-LLM
            # so we let it run after release.
            async with llm_gate:
                rsp = await self.llm.complete(LLMRequest(
                    prompt=prompt, system=system,
                    temperature=0.7, max_tokens=600,
                    image_urls=image_urls,
                ))
            text = rsp.text.strip()
            # Belt-and-suspenders URL preservation: the system prompt
            # asks the LLM to include any URLs from the directive
            # verbatim, but LLMs ignore that ~10% of the time
            # (especially for Instagram-style copy where URLs feel
            # unnatural). Post-process to GUARANTEE: if the user's
            # directive or source items mention a URL and it's NOT
            # in the LLM output, append it. Belt-and-suspenders so
            # the user's explicit "include this link" intent is
            # never silently dropped.
            text = _ensure_urls_present(text, state, bp)
            media = await _maybe_generate_media(bp, state)
            return DraftPost(
                platform_name=bp.platform_name,
                text=text,
                hashtags=list(bp.hashtags),
                media=media,
                blueprint_ref=bp,
            )

        drafts = await asyncio.gather(*(_make_one(b) for b in state.plan.blueprints))
        state.log(self.name, "drafts_generated", count=len(drafts), revision=state.revision_count)
        return state.merge(drafts=list(drafts))


def _custom_block(custom: str | None) -> str:
    """Wrap the workflow's optional custom system-prompt with a header so
    the LLM treats it as supplementary policy, not a replacement of the
    main agent contract. Empty / whitespace-only is a no-op."""
    if not custom or not custom.strip():
        return ""
    return (
        "\n\n--- Custom instructions (per-workflow override) ---\n"
        + custom.strip()
    )


# ── media generation helper ───────────────────────────────────────────────
PLATFORMS_REQUIRING_MEDIA = {"instagram", "tiktok", "pinterest", "youtube"}


async def _maybe_generate_media(bp, state) -> list:
    """Decide what to attach to a draft's media list. Priority order:

      1. **attached_media** — real media the Planner copied off
         ``SourceItem.media`` (Notion image block, Drive file, RSS
         enclosure, etc.). When this is non-empty we use the actual
         source asset and skip generation entirely. This is the
         strictly correct behaviour for "publish what's in my Notion
         page", not "publish an AI-imagined image of what my Notion
         page describes".
      2. **suggested_media** — a one-off asset the Planner conjured
         (rarely used).
      3. **media_prompt** — invoke the configured media-generation
         plugin (DALL-E, etc.).
      4. **Implicit demand** — platform needs media but the Planner
         didn't ask for any; synthesize from the hook as a last resort.

    Returns an empty list when no media is needed AND none of the above
    applies — caller (executor) leaves ``DraftPost.media`` empty so
    text-only platforms publish text-only posts.
    """
    from app.adapters.media.base import MediaBrief, MediaGenerator
    from app.domain.value_objects.content import MediaKind
    from app.plugins.registry import PluginKind, get_global_registry

    attached = list(getattr(bp, "attached_media", None) or [])
    if attached:
        # Use the real asset(s) from the source. Don't call any
        # generation plugin — the user attached this on purpose.
        return attached

    suggested = bp.suggested_media
    has_prompt = bool(bp.media_prompt)
    needs_media = bp.platform_name in PLATFORMS_REQUIRING_MEDIA
    if not (suggested or has_prompt or needs_media):
        return []
    if suggested:
        return [suggested]
    prompt = bp.media_prompt or bp.hook or " ".join(bp.key_messages[:1])
    kind = bp.media_kind or MediaKind.IMAGE
    extras = (state.workflow_config.extra or {}) if state else {}
    plugin_name = extras.get("media_generator", "mock")
    try:
        entry = get_global_registry().get(PluginKind.MEDIA, plugin_name)
        gen: MediaGenerator = entry.cls()
        asset = await gen.generate(MediaBrief(prompt=prompt, kind=kind))
        return [asset]
    except Exception:                            # noqa: BLE001
        # Last-resort fallback so a missing API key never breaks publish.
        from app.adapters.media.mock import MockMediaGenerator
        gen = MockMediaGenerator()
        return [await gen.generate(MediaBrief(prompt=prompt, kind=kind))]
