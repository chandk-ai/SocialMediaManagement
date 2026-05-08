"""Factory for instantiating an Orchestrator from a Workflow + plugin registry."""
from __future__ import annotations

import inspect
from typing import Any

from app.adapters.llm.base import LLMProvider
from app.domain.entities.workflow import Workflow
from app.domain.value_objects.ids import OrgId
from app.plugins.registry import PluginKind, PluginRegistry

from .orchestrator import Orchestrator


def build_orchestrator(
    workflow: Workflow,
    registry: PluginRegistry,
    *,
    api_key: str | None = None,
    org_id: OrgId | None = None,
    usage_service: Any | None = None,        # LlmUsageService; loosely typed to avoid cycles
    usage_context: dict[str, Any] | None = None,
) -> Orchestrator:
    """Instantiate an Orchestrator with the LLM the workflow requested.

    If ``api_key`` is supplied, we pass it to the provider class (only when
    its ``__init__`` accepts it — most providers do). This is how the
    workflow service injects the org's stored API key at runtime.

    When both ``org_id`` and ``usage_service`` are provided, the LLM is
    wrapped in :class:`BudgetGuardedLLM` which:
      • blocks calls when the org's MTD spend ≥ ``monthly_llm_budget_usd``,
      • records every completion to ``smms.llm_usage`` for the dashboard.
    """
    llm_entry = registry.get(PluginKind.LLM, workflow.config.llm_provider)
    cls = llm_entry.cls
    sig = inspect.signature(cls.__init__)
    kwargs: dict[str, str] = {}
    if api_key and "api_key" in sig.parameters:
        kwargs["api_key"] = api_key
    llm: LLMProvider = cls(**kwargs)

    # Wrap with budget guard if the workflow service handed us the deps.
    if org_id is not None and usage_service is not None:
        from app.adapters.llm.budget_guard import BudgetGuardedLLM
        llm = BudgetGuardedLLM(
            llm,
            org_id=org_id,
            provider_name=workflow.config.llm_provider,
            usage_service=usage_service,
            context=usage_context or {"workflow_id": str(workflow.id)},
        )

    return Orchestrator(llm=llm)
