"""End-to-end agent loop test using the deterministic mock LLM."""
import pytest

from app.adapters.llm.mock import MockProvider
from app.agents.base import AgentDecision, AgentState
from app.agents.orchestrator import Orchestrator
from app.domain.entities.source import SourceItem
from app.domain.entities.workflow import WorkflowConfig


@pytest.mark.asyncio
async def test_orchestrator_runs_to_decision():
    state = AgentState(
        workflow_config=WorkflowConfig(
            quality_threshold=0.0,           # accept anything for the smoke test
            low_quality_threshold=-1.0,
        ),
        target_platforms=["linkedin", "twitter"],
        source_items=[
            SourceItem(external_id="1", title="Hi", body="Reference content", url=None),
        ],
    )
    orch = Orchestrator(llm=MockProvider())
    final = await orch.run(state)
    assert final.decision in {
        AgentDecision.APPROVE, AgentDecision.ESCALATE, AgentDecision.REVISE,
    }
    assert final.plan is not None
    assert len(final.drafts) >= 1
    assert all(d.platform_name in {"linkedin", "twitter"} for d in final.drafts)
