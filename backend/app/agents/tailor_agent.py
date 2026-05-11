"""TailorAgent — refines a ContentPlan's PostBlueprints per platform.

Pillar 5's deeper integration. Sits between Planner and Executor:

    Planner    → ContentPlan(blueprints=[BP-linkedin, BP-x, BP-instagram])
    TailorAgent → ContentPlan with refined Blueprints
    Executor   → DraftPost per Blueprint

For each Blueprint, the TailorAgent:

  * Rewrites ``angle`` + ``hook`` to match the platform's idiom
    (LinkedIn long-form, X punchy, Instagram caption-friendly, etc.)
  * Replaces ``hashtags`` with the highest-historical-engagement
    hashtags pulled from HashtagIntelligence (when wired)
  * Caps prompt-side guidance to the platform's character budget
  * Suppresses Blueprints whose platform is denylisted by the
    workflow's compliance profile

Failure mode: any LLM/KB failure falls back to passing the original
Blueprint through unchanged. The pipeline still runs; just without
the per-platform polish.
"""
from __future__ import annotations

from typing import Any

from app.adapters.llm.base import LLMRequest
from app.core.logging import get_logger
from app.domain.value_objects.content import (
    ContentPlan, Hashtag, PostBlueprint,
)

log = get_logger(__name__)


PLATFORM_HINTS: dict[str, str] = {
    "linkedin":   "Professional long-form (~150–250 words). Max 3 hashtags. End with a question that invites discussion.",
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


PLATFORM_LENGTH_BUDGET: dict[str, int] = {
    "twitter": 240, "x": 240,
    "bluesky": 300, "mastodon": 500,
    "telegram": 600, "threads": 480,
    "linkedin": 1300, "facebook": 1500,
    "instagram": 1100, "tiktok": 200,
    "reddit": 5000, "medium": 5000,
    "youtube": 2500, "discord": 1500, "slack": 1500,
    "pinterest": 500, "tumblr": 4000,
}


COMPLIANCE_PLATFORM_DENYLIST: dict[str, set[str]] = {
    "medical_claims": {"facebook", "instagram", "tiktok"},
    # Extend as the compliance feature grows.
}


class TailorAgent:
    """Pure refining agent — does not touch state.drafts. Output is a
    new ContentPlan whose Blueprints are platform-tuned versions of
    the input plan's Blueprints."""

    name = "tailor"

    def __init__(
        self, *, llm=None,
        hashtag_service=None, knowledge_store=None,
    ) -> None:
        self.llm = llm
        self.hashtag_service = hashtag_service
        self.knowledge_store = knowledge_store

    async def refine(
        self, *, plan: ContentPlan, workflow_config,
        org_id: str, directive: str | None = None,
    ) -> tuple[ContentPlan, dict[str, Any]]:
        """Returns (refined_plan, telemetry). Telemetry includes:
            - rewritten_platforms: list of platforms whose Blueprints
              had their angle/hook rewritten by the LLM
            - skipped_platforms:  platforms removed due to compliance
            - hashtag_overrides:  count of Blueprints whose hashtags
              were replaced by HashtagIntelligence suggestions"""
        compliance = (
            getattr(workflow_config, "compliance_profile", None) or ""
        ).strip().lower()
        denylist: set[str] = COMPLIANCE_PLATFORM_DENYLIST.get(compliance, set())

        # KB context — pull once, reuse across Blueprints.
        kb_block = ""
        if self.knowledge_store is not None and self.llm is not None:
            try:
                from app.services.knowledge.retriever import retrieve_for_prompt
                kb_query = " ".join(filter(None, [
                    directive or "", plan.source_summary or "",
                ]))
                kb_block = await retrieve_for_prompt(
                    store=self.knowledge_store, llm=self.llm,
                    query=kb_query, org_id=str(org_id),
                    top_k=5, max_chars=1200,
                )
            except Exception as exc:                                 # noqa: BLE001
                log.info("tailor_kb_skipped", error=str(exc))

        rewritten_platforms: list[str] = []
        skipped_platforms: list[str] = []
        hashtag_overrides = 0
        refined: list[PostBlueprint] = []

        for bp in plan.blueprints:
            key = bp.platform_name.lower()

            if key in denylist:
                skipped_platforms.append(bp.platform_name)
                log.info("tailor_compliance_skip",
                         platform=bp.platform_name, profile=compliance)
                continue

            hint = PLATFORM_HINTS.get(key, "")
            budget = PLATFORM_LENGTH_BUDGET.get(key, 1000)

            # Hashtag override (best-effort).
            new_hashtags = list(bp.hashtags)
            if self.hashtag_service is not None:
                try:
                    suggestions = await self._suggest_hashtags(
                        org_id=org_id, plat=bp.platform_name,
                        seed=" ".join([bp.angle, bp.hook,
                                         *bp.key_messages]),
                    )
                    if suggestions:
                        new_hashtags = [Hashtag(value=s) for s in suggestions]
                        hashtag_overrides += 1
                except Exception as exc:                             # noqa: BLE001
                    log.info("tailor_hashtag_skipped",
                             platform=bp.platform_name, error=str(exc))

            # Per-platform angle/hook rewrite.
            new_angle, new_hook = bp.angle, bp.hook
            rewritten = False
            if self.llm is not None:
                try:
                    rew = await self._rewrite_angle_hook(
                        platform=bp.platform_name, hint=hint,
                        budget=budget, kb_block=kb_block,
                        bp=bp, workflow_config=workflow_config,
                        directive=directive,
                    )
                    if rew:
                        new_angle = rew.get("angle") or bp.angle
                        new_hook = rew.get("hook") or bp.hook
                        rewritten = (
                            new_angle.strip() != bp.angle.strip()
                            or new_hook.strip() != bp.hook.strip()
                        )
                except Exception as exc:                             # noqa: BLE001
                    log.info("tailor_rewrite_failed",
                             platform=bp.platform_name, error=str(exc))

            if rewritten:
                rewritten_platforms.append(bp.platform_name)

            # Build the refined Blueprint. notes carries the platform
            # hint so the Executor's prompt picks it up automatically.
            tailor_note = (
                f"[Tailor: {hint}; budget {budget} chars]"
                if hint else f"[Tailor: budget {budget} chars]"
            )
            new_notes = (
                (bp.notes + " " if bp.notes else "") + tailor_note
            ).strip()
            refined.append(PostBlueprint(
                platform_name=bp.platform_name,
                angle=new_angle, hook=new_hook,
                key_messages=list(bp.key_messages),
                cta=bp.cta, hashtags=new_hashtags,
                suggested_media=bp.suggested_media,
                media_prompt=bp.media_prompt,
                media_kind=bp.media_kind,
                notes=new_notes,
            ))

        new_plan = ContentPlan(
            blueprints=refined,
            rationale=plan.rationale + " | tailored",
            source_summary=plan.source_summary,
        )
        telemetry = {
            "rewritten_platforms": rewritten_platforms,
            "skipped_platforms": skipped_platforms,
            "hashtag_overrides": hashtag_overrides,
            "kb_chars": len(kb_block),
            "input_count": len(plan.blueprints),
            "output_count": len(refined),
        }
        return new_plan, telemetry

    # ── helpers ────────────────────────────────────────────────────
    async def _suggest_hashtags(self, *, org_id, plat, seed) -> list[str]:
        """Pull HashtagIntelligence-ranked hashtags for this platform.
        The service's method is ``suggest_for(org_id, *, plugin_name,
        seed_text, limit, exclude)`` returning ``list[HashtagSuggestion]``
        — each suggestion exposes ``.tag``."""
        svc = self.hashtag_service
        if svc is None:
            return []
        try:
            # org_id may arrive as a string or as an OrgId; the service
            # accepts either since it stringifies internally.
            ranked = await svc.suggest_for(
                org_id, plugin_name=plat, seed_text=seed, limit=8,
            )
            tags = [getattr(h, "tag", None) for h in ranked]
            return [t for t in tags if t][:5]
        except (AttributeError, NotImplementedError):
            return []
        except Exception as exc:                                     # noqa: BLE001
            log.info("tailor_hashtag_service_error", error=str(exc))
            return []

    async def _rewrite_angle_hook(
        self, *, platform, hint, budget, kb_block, bp,
        workflow_config, directive,
    ) -> dict[str, str] | None:
        """Single LLM call that produces both rewritten ``angle`` and
        ``hook`` for the platform. We expect a small JSON-ish output
        and parse leniently — anything that fails parsing falls back
        to the original Blueprint."""
        kb = f"\nBrand-voice examples:\n{kb_block}\n" if kb_block else ""
        directive_block = f"\nRun directive: {directive}" if directive else ""

        sys = (
            "You are a senior social-media editor. Rewrite a post's "
            "angle and hook for the target platform, preserving facts "
            "and the core message but adopting the platform's idiom. "
            "Stay under the budget.\n\n"
            "Output EXACTLY this format on two lines, no preamble:\n"
            "ANGLE: <new angle, one line>\n"
            "HOOK: <new hook, one short paragraph>"
        )
        user = (
            f"Target platform: {platform}\n"
            f"Idiom hint: {hint}\n"
            f"Character budget for the final post: {budget}\n"
            f"Brand voice: {getattr(workflow_config, 'tone', '') or 'neutral'}\n"
            f"Audience: {getattr(workflow_config, 'audience', '') or 'general'}\n"
            f"{directive_block}{kb}\n"
            f"Original angle: {bp.angle}\n"
            f"Original hook: {bp.hook}\n"
            f"Key messages: {bp.key_messages}\n\n"
            f"Rewrite:"
        )
        rsp = await self.llm.complete(LLMRequest(
            prompt=user, system=sys,
            max_tokens=min(800, max(240, budget // 3)),
            temperature=0.6,
        ))
        text = (rsp.text or "").strip()
        if not text:
            return None

        out: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if line.upper().startswith("ANGLE:"):
                out["angle"] = line.split(":", 1)[1].strip()
            elif line.upper().startswith("HOOK:"):
                out["hook"] = line.split(":", 1)[1].strip()
        return out or None
