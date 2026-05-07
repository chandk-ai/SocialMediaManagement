"""Factory for instantiating an Orchestrator from a Workflow + plugin registry."""
from __future__ import annotations

import inspect

from app.adapters.llm.base import LLMProvider
from app.domain.entities.workflow import Workflow
from app.plugins.registry import PluginKind, PluginRegistry

from .orchestrator import Orchestrator


def build_orchestrator(
    workflow: Workflow,
    registry: PluginRegistry,
    *,
    api_key: str | None = None,
) -> Orchestrator:
    """Instantiate an Orchestrator with the LLM the workflow requested.

    If ``api_key`` is supplied, we pass it to the provider class (only when
    its ``__init__`` accepts it — most providers do). This is how the
    workflow service injects the org's stored API key at runtime.
    """
    llm_entry = registry.get(PluginKind.LLM, workflow.config.llm_provider)
    cls = llm_entry.cls
    sig = inspect.signature(cls.__init__)
    kwargs: dict[str, str] = {}
    if api_key and "api_key" in sig.parameters:
        kwargs["api_key"] = api_key
    llm: LLMProvider = cls(**kwargs)
    return Orchestrator(llm=llm)
