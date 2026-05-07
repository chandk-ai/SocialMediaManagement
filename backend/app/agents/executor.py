"""Executor — turns each PostBlueprint into a polished DraftPost."""
from __future__ import annotations

import asyncio
from dataclasses import asdict

from app.adapters.llm.base import LLMRequest
from app.domain.value_objects.content import DraftPost, Hashtag

from .base import Agent, AgentState

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
            rsp = await self.llm.complete(LLMRequest(
                prompt=prompt, system=EXECUTOR_SYSTEM + voice_block,
                temperature=0.7, max_tokens=600,
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


# ── media generation helper ───────────────────────────────────────────────
PLATFORMS_REQUIRING_MEDIA = {"instagram", "tiktok", "pinterest", "youtube"}


async def _maybe_generate_media(bp, state) -> list:
    """If the platform requires media and the Planner provided a media_prompt
    (or the platform implicitly demands it), invoke the configured media
    generator. Falls back to the mock generator so dev / tests still pass."""
    from app.adapters.media.base import MediaBrief, MediaGenerator
    from app.domain.value_objects.content import MediaKind
    from app.plugins.registry import PluginKind, get_global_registry

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
