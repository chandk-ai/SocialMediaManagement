"""TailorAgent — full-fidelity per-platform variant generator.

Pillar 5 deepens the lightweight ``tailor_for_platforms()`` helper into
a real agent with:

  * **Idiom rules per platform** (length caps, hashtag norms, link
    behavior) encoded as structured config so prompts stay short.
  * **Hashtag intelligence integration** — pulls historically-strong
    hashtags from ``HashtagIntelligenceService`` and asks the LLM to
    pick the best 3–5 for each platform.
  * **KB-aware rewriting** — when Pillar 4 returns brand-voice examples,
    they're injected into the rewrite prompt for tone-matching.
  * **Compliance-aware suppression** — if the workflow's
    ``compliance_profile`` flags a platform as out-of-bounds (e.g. no
    medical claims on consumer Facebook), the agent skips that platform.
  * **Per-platform critique hooks** — each variant carries a structured
    rationale the Critique agent reads to evaluate platform-fit.

Output: same shape as the helper — ``{platform: {plan, hint, ...}}`` —
plus per-platform extras: hashtags, char_estimate, compliance_skip.

Failure handling:
  * LLM refusal/error → fall back to the helper's plan-as-is.
  * Missing helper deps → still functional in degraded mode.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.tailor import PLATFORM_HINTS
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class TailorVariant:
    platform: str
    plan: dict[str, Any]
    hint: str
    hashtags: list[str]
    char_estimate: int
    rewritten: bool
    compliance_skip: bool = False
    rationale: str = ""


PLATFORM_LENGTH_BUDGET: dict[str, int] = {
    "twitter": 240, "x": 240,
    "bluesky": 300, "mastodon": 500,
    "telegram": 600, "threads": 480,
    "linkedin": 1300, "facebook": 1500,
    "instagram": 1100, "tiktok": 200,
    "reddit": 5000, "medium": 5000,
    "youtube": 2500,
    "discord": 1500, "slack": 1500,
    "pinterest": 500, "tumblr": 4000,
}


COMPLIANCE_PLATFORM_DENYLIST = {
    "medical_claims": {"facebook", "instagram", "tiktok"},
    # Add more profiles as the compliance feature grows.
}


class TailorAgent:
    def __init__(
        self, *, llm=None,
        hashtag_service=None, knowledge_store=None,
    ) -> None:
        self.llm = llm
        self.hashtag_service = hashtag_service
        self.knowledge_store = knowledge_store

    async def tailor(
        self, *, plan: dict[str, Any], platforms: list[str],
        workflow_config, org_id: str,
        directive: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        base_summary = (plan or {}).get("summary") or ""
        out: dict[str, dict[str, Any]] = {}

        # KB context — pulled once, applied to every variant rewrite.
        kb_block = ""
        if self.knowledge_store is not None and self.llm is not None:
            try:
                from app.services.knowledge.retriever import retrieve_for_prompt
                kb_block = await retrieve_for_prompt(
                    store=self.knowledge_store, llm=self.llm,
                    query=" ".join(filter(None, [directive or "", base_summary])),
                    org_id=str(org_id), top_k=5, max_chars=1200,
                )
            except Exception as exc:                                 # noqa: BLE001
                log.info("tailor_kb_skipped", error=str(exc))

        compliance_profile = (
            getattr(workflow_config, "compliance_profile", None) or ""
        ).strip().lower()
        denylist: set[str] = (
            COMPLIANCE_PLATFORM_DENYLIST.get(compliance_profile) or set()
        )

        for plat in platforms:
            key = plat.lower()
            if key in denylist:
                out[plat] = {
                    "plan": plan, "hint": "", "hashtags": [],
                    "char_estimate": 0, "rewritten": False,
                    "compliance_skip": True,
                    "rationale": (
                        f"compliance profile '{compliance_profile}' "
                        f"forbids {plat}"),
                }
                continue

            hint = PLATFORM_HINTS.get(key, "")
            budget = PLATFORM_LENGTH_BUDGET.get(key, 1000)

            # Hashtag selection (best-effort).
            hashtags: list[str] = []
            if self.hashtag_service is not None:
                try:
                    hashtags = await self._suggest_hashtags(
                        org_id=org_id, plat=plat, summary=base_summary,
                    )
                except Exception as exc:                             # noqa: BLE001
                    log.info("tailor_hashtag_skipped", error=str(exc))

            # Rewrite the plan summary if we have an LLM; otherwise
            # pass through unchanged.
            rewritten = False
            new_summary = base_summary
            if self.llm is not None and base_summary.strip():
                try:
                    new_summary = await self._rewrite(
                        plat=plat, hint=hint, budget=budget,
                        kb_block=kb_block, base_summary=base_summary,
                        workflow_config=workflow_config,
                    )
                    rewritten = bool(new_summary.strip()) and \
                                new_summary.strip() != base_summary.strip()
                except Exception as exc:                             # noqa: BLE001
                    log.info("tailor_rewrite_failed", platform=plat,
                             error=str(exc))
                    new_summary = base_summary

            new_plan = dict(plan or {})
            new_plan["summary"] = new_summary
            if hashtags:
                new_plan["hashtags"] = hashtags
            new_plan["platform_hint"] = hint
            new_plan["budget"] = budget
            char_est = len(new_summary) + sum(len(h) + 2 for h in hashtags)

            out[plat] = {
                "plan": new_plan, "hint": hint,
                "hashtags": hashtags,
                "char_estimate": char_est,
                "rewritten": rewritten,
                "compliance_skip": False,
                "rationale": (
                    f"rewritten for {plat} "
                    f"({char_est} chars, budget {budget})"
                    if rewritten else
                    f"plan reused for {plat} "
                    f"({char_est} chars, budget {budget})"
                ),
            }

        return out

    async def _suggest_hashtags(
        self, *, org_id, plat, summary,
    ) -> list[str]:
        """Use the existing hashtag intelligence service if present —
        it ranks by historical engagement on this platform. Falls
        back to no hashtags."""
        svc = self.hashtag_service
        if svc is None:
            return []
        try:
            ranked = await svc.suggest(
                org_id=str(org_id),
                platform=plat,
                content=summary,
                limit=8,
            )
            return [h["tag"] if isinstance(h, dict) else h for h in ranked][:5]
        except AttributeError:
            return []
        except Exception:                                            # noqa: BLE001
            return []

    async def _rewrite(
        self, *, plat, hint, budget, kb_block, base_summary,
        workflow_config,
    ) -> str:
        sys = (
            "You are a senior social-media editor. Rewrite the plan "
            "summary for the target platform, preserving facts and the "
            "core message but adopting the platform's idiom. Stay under "
            "the character budget. Output ONLY the rewritten summary — "
            "no preamble, no markdown, no quotes."
        )
        kb = f"\nBrand-voice examples:\n{kb_block}\n" if kb_block else ""
        user = (
            f"Target platform: {plat}\n"
            f"Idiom hint: {hint}\n"
            f"Character budget: {budget}\n"
            f"Brand voice: {getattr(workflow_config, 'tone', '') or 'neutral'}\n"
            f"Audience: {getattr(workflow_config, 'audience', '') or 'general'}\n"
            f"{kb}"
            f"\nOriginal plan summary:\n---\n{base_summary}\n---\n\n"
            f"Rewritten summary:"
        )
        text = await self.llm.complete(
            prompt=user, system=sys,
            max_tokens=min(800, max(200, budget // 3)),
            temperature=0.6,
        )
        return (text or "").strip()
