"""Factory for instantiating an Orchestrator from a Workflow + plugin registry."""
from __future__ import annotations

from app.adapters.llm.base import LLMProvider
from app.domain.entities.workflow import Workflow
from app.plugins.registry import PluginKind, PluginRegistry

from .orchestrator import Orchestrator


def build_orchestrator(workflow: Workflow, registry: PluginRegistry) -> Orchestrator:
    llm_entry = registry.get(PluginKind.LLM, workflow.config.llm_provider)
    llm: LLMProvider = llm_entry.cls()
    return Orchestrator(llm=llm)
