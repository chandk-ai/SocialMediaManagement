"""Executor — turns each PostBlueprint into a polished DraftPost."""
from __future__ import annotations

import asyncio
import os
from dataclasses import asdict

from app.adapters.llm.base import LLMRequest
from app.domain.value_objects.content import DraftPost, Hashtag

from .base import Agent, AgentState


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
