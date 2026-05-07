"""Multi-agent orchestrator.

Uses LangGraph if available, falls back to a built-in mini state machine
otherwise. Either way, the public surface is the same:

    result = await Orchestrator(...).run(state)

This double implementation means the system runs even in environments where
the LangGraph wheel isn't installed (e.g. on minimal CI), without losing the
benefits when it is.
"""
from __future__ import annotations

from typing import Any

from app.adapters.llm.base import LLMProvider
from app.core.logging import get_logger

from .base import Agent, AgentDecision, AgentState
from .critique import CritiqueAgent
from .evaluator import EvaluatorAgent
from .executor import ExecutorAgent
from .planner import PlannerAgent

log = get_logger(__name__)


class Orchestrator:
    def __init__(
        self,
        llm: LLMProvider,
        *,
        planner: Agent | None = None,
        executor: Agent | None = None,
        evaluator: Agent | None = None,
        critique: Agent | None = None,
    ) -> None:
        self.planner = planner or PlannerAgent(llm)
        self.executor = executor or ExecutorAgent(llm)
        self.evaluator = evaluator or EvaluatorAgent(llm)
        self.critique = critique or CritiqueAgent(llm)
        self._graph = self._build_graph()

    # ── public API ────────────────────────────────────────────────────
    async def run(self, state: AgentState) -> AgentState:
        if self._graph is not None:
            # LangGraph implementation
            result: dict[str, Any] = await self._graph.ainvoke({"state": state})
            return result["state"]
        # Built-in fallback
        return await self._run_fallback(state)

    # ── LangGraph builder ─────────────────────────────────────────────
    def _build_graph(self):
        try:
            from langgraph.graph import StateGraph, END
        except ImportError:
            log.info("langgraph_unavailable_using_fallback")
            return None

        async def _planner_node(payload):
            return {"state": await self.planner.run(payload["state"])}

        async def _executor_node(payload):
            return {"state": await self.executor.run(payload["state"])}

        async def _evaluator_node(payload):
            return {"state": await self.evaluator.run(payload["state"])}

        async def _critique_node(payload):
            return {"state": await self.critique.run(payload["state"])}

        def _route_after_critique(payload) -> str:
            decision = payload["state"].decision
            if decision is AgentDecision.REVISE:
                return "executor"
            return "end"

        g = StateGraph(dict)
        g.add_node("planner", _planner_node)
        g.add_node("executor", _executor_node)
        g.add_node("evaluator", _evaluator_node)
        g.add_node("critique", _critique_node)

        g.set_entry_point("planner")
        g.add_edge("planner", "executor")
        g.add_edge("executor", "evaluator")
        g.add_edge("evaluator", "critique")
        g.add_conditional_edges("critique", _route_after_critique, {
            "executor": "executor",
            "end": END,
        })
        return g.compile()

    # ── fallback (no LangGraph) ───────────────────────────────────────
    async def _run_fallback(self, state: AgentState) -> AgentState:
        state = await self.planner.run(state)
        while True:
            state = await self.executor.run(state)
            state = await self.evaluator.run(state)
            state = await self.critique.run(state)
            if state.decision is not AgentDecision.REVISE:
                return state
