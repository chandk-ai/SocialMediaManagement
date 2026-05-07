"""Planner — turns sources + intent into a per-platform ContentPlan."""
from __future__ import annotations

import json
from dataclasses import asdict

from app.adapters.llm.base import LLMRequest
from app.domain.value_objects.content import ContentPlan, Hashtag, PostBlueprint

from .base import Agent, AgentState

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
        rsp = await self.llm.complete(LLMRequest(
            prompt=prompt, system=PLANNER_SYSTEM,
            response_format="json", temperature=0.5, max_tokens=1500,
        ))
        plan = _parse_plan(rsp.text, fallback_platforms=state.target_platforms)
        state.log(self.name, "plan_built", blueprints=len(plan.blueprints), tokens=asdict(rsp.usage))
        return state.merge(plan=plan)


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
