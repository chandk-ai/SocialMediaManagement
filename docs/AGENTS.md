# The 4-Agent Orchestrator

The system uses a stateful, multi-agent loop built on **LangGraph**. Agents share an immutable `AgentState` and produce updates that are merged into the next state.

---

## Roles

### 1. Planner

**Goal:** turn high-level intent + source material into an actionable content plan.

**Inputs**
- `Workflow.config` (tone, audience, hashtags policy, cadence)
- Aggregated source content (raw text, URLs, structured data)
- Platform constraints (per-platform character limits, media types)

**Outputs**
- `ContentPlan`: list of `PostBlueprint` (one per target platform) including angle, hook, key messages, hashtags, CTA, media suggestion.

### 2. Executor

**Goal:** generate the actual post content from each blueprint.

**Inputs**
- `PostBlueprint`
- LLM provider + model selection
- Brand voice guide (RAG over brand guidelines, optional)

**Outputs**
- `DraftPost` per blueprint with text, suggested media alt-text, hashtag list.

### 3. Evaluator

**Goal:** score each draft against quality, brand, compliance, and platform-fit rubrics.

**Rubric dimensions**
- Clarity & hook strength
- Brand voice match
- Compliance flags (sensitive topics, banned words)
- Platform fit (length, hashtag count, media required)
- Predicted engagement (heuristic)

**Outputs**
- `EvaluationReport` with per-dimension score 0-1, overall score, and concrete suggestions.

### 4. Critique

**Goal:** decide whether to publish, revise, or escalate to a human.

**Decision logic**
- `score ≥ workflow.threshold` → mark `APPROVED`, schedule for publishing.
- `score ∈ [low_threshold, threshold)` → send back to **Executor** with critique notes (max `N` revisions).
- `score < low_threshold` OR `compliance_flag = true` → mark `NEEDS_HUMAN_REVIEW`.

---

## State machine (LangGraph)

```
            ┌─────────┐
            │  START  │
            └────┬────┘
                 ▼
         ┌──────────────┐
         │ load_sources │
         └──────┬───────┘
                ▼
         ┌──────────────┐
         │   PLANNER    │
         └──────┬───────┘
                ▼
         ┌──────────────┐         ┌────────────────────┐
         │   EXECUTOR   │◀────────│ revise (with notes)│
         └──────┬───────┘         └─────────▲──────────┘
                ▼                           │
         ┌──────────────┐                   │
         │  EVALUATOR   │                   │
         └──────┬───────┘                   │
                ▼                           │
         ┌──────────────┐                   │
         │   CRITIQUE   │───── revise ──────┘
         └──────┬───────┘
                ▼
        ┌──────────────────┐
        │ approve / human  │
        └──────┬───────────┘
               ▼
        ┌──────────────┐
        │   PUBLISH    │
        └──────┬───────┘
               ▼
            ┌─────┐
            │ END │
            └─────┘
```

State is persisted to Postgres via the LangGraph checkpointer, so a crashed worker resumes mid-workflow.

---

## Extending: adding a new agent

Implement `agents.base.Agent`:

```python
class TranslatorAgent(Agent):
    name = "translator"

    async def run(self, state: AgentState) -> AgentState:
        translated = await self.llm.complete(...)
        return state.update(drafts=translated)
```

Register the node in `agents/orchestrator.py` and wire the edges. No other layer changes.
