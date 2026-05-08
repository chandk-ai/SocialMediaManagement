"""SelectionStrategy — the plugin contract for item-selection logic.

Every strategy receives the same input bundle and returns a SelectionResult.
Strategies are pure-async + side-effect-free: they don't touch the
``smms.source_items`` table directly. The orchestrator handles persistence
based on the SelectionResult, so a strategy that takes the same input
twice MUST return the same output (modulo non-deterministic ranking like
LLM-similarity, which the trace records explicitly).

Why a plugin contract rather than a function:
* Each strategy can declare its own ``config_schema`` for the per-workflow
  picker, so customers see schema-driven config in the wizard (same UX
  as Sources).
* Customers / partners can write their own strategies (compliance-first
  filter, "only items where the title matches a regex", etc.) by adding
  one file and a @register_plugin decorator.
* The Critique / Evaluator agents can reason about *which* strategy ran,
  so the trace is informative.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from app.domain.entities.source import SourceItem
from app.domain.entities.workflow import WorkflowConfig
from app.domain.value_objects.ids import OrgId
from app.domain.value_objects.selection import SelectionResult


@dataclass(frozen=True, slots=True)
class SelectionContext:
    """Everything a strategy needs to decide what to use.

    candidates       — the items the source loader fetched this run, before
                       any de-dup. Strategies free to filter further.
    consumed_keys    — set of (source_id, external_id) tuples already in
                       smms.source_items with status='consumed' for this
                       org. Strategies should normally exclude these.
    workflow_config  — full config so strategies can read tone/audience/
                       compliance_profile etc. when ranking.
    target_platforms — list of plugin names. Useful for strategies that
                       want to make platform-aware choices.
    directive        — per-run free-text from a chat trigger. None for
                       schedule-driven runs. Strategies that do
                       relevance ranking key off this.
    org_id           — included so strategies that need DB lookups (e.g.
                       past-engagement scoring) know which tenant to scope
                       to.
    """
    candidates: list[SourceItem]
    consumed_keys: set[tuple[str, str]]   # (source_id_str, external_id)
    workflow_config: WorkflowConfig
    target_platforms: list[str]
    directive: str | None
    org_id: OrgId


class SelectionStrategy(ABC):
    """Implement once per pluggable strategy. Register with
    ``@register_plugin("selection", "<name>")``."""

    plugin_name: ClassVar[str] = ""
    api_version: ClassVar[str] = "1.0"
    display_name: ClassVar[str] = ""
    description: ClassVar[str] = ""

    # JSON Schema describing the per-workflow ``selection_config`` shape —
    # drives the wizard's strategy-config form (same pattern as Sources).
    config_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    @abstractmethod
    async def select(self, ctx: SelectionContext) -> SelectionResult: ...
