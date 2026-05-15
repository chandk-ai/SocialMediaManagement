"""In-memory repositories — used in tests and as the bootstrap default,
so the system runs without a Postgres deployment. Swap for SQLAlchemy
implementations in `sqlalchemy.py` once a DB is wired up."""
from __future__ import annotations

from collections import defaultdict
from typing import TypeVar

from app.domain.entities import (
    Platform,
    Post,
    Source,
    User,
    Workflow,
    WorkflowRun,
)
from app.domain.value_objects.ids import (
    OrgId,
    PlatformId,
    PostId,
    RunId,
    SourceId,
    UserId,
    WorkflowId,
)

T = TypeVar("T")


class _ScopedStore(dict[OrgId, dict]):
    def for_org(self, org_id: OrgId) -> dict:
        return self.setdefault(org_id, {})


class InMemoryPlatformRepository:
    def __init__(self) -> None:
        self._s = _ScopedStore()

    async def add(self, p: Platform) -> Platform:
        self._s.for_org(p.org_id)[p.id] = p
        return p

    async def get(self, org_id: OrgId, platform_id: PlatformId) -> Platform | None:
        return self._s.for_org(org_id).get(platform_id)

    async def list(self, org_id: OrgId) -> list[Platform]:
        return list(self._s.for_org(org_id).values())

    async def update(self, p: Platform) -> Platform:
        self._s.for_org(p.org_id)[p.id] = p
        return p

    async def delete(self, org_id: OrgId, platform_id: PlatformId) -> None:
        self._s.for_org(org_id).pop(platform_id, None)


class InMemorySourceRepository:
    def __init__(self) -> None:
        self._s = _ScopedStore()

    async def add(self, s: Source) -> Source:
        self._s.for_org(s.org_id)[s.id] = s
        return s

    async def get(self, org_id: OrgId, sid: SourceId) -> Source | None:
        return self._s.for_org(org_id).get(sid)

    async def list(self, org_id: OrgId) -> list[Source]:
        return list(self._s.for_org(org_id).values())

    async def update(self, s: Source) -> Source:
        self._s.for_org(s.org_id)[s.id] = s
        return s

    async def delete(self, org_id: OrgId, sid: SourceId) -> None:
        self._s.for_org(org_id).pop(sid, None)


class InMemoryWorkflowRepository:
    def __init__(self) -> None:
        self._s = _ScopedStore()

    async def add(self, w: Workflow) -> Workflow:
        self._s.for_org(w.org_id)[w.id] = w
        return w

    async def get(self, org_id: OrgId, wid: WorkflowId) -> Workflow | None:
        return self._s.for_org(org_id).get(wid)

    async def list(self, org_id: OrgId) -> list[Workflow]:
        return list(self._s.for_org(org_id).values())

    async def update(self, w: Workflow) -> Workflow:
        self._s.for_org(w.org_id)[w.id] = w
        return w

    async def delete(self, org_id: OrgId, wid: WorkflowId) -> None:
        self._s.for_org(org_id).pop(wid, None)

    async def list_active_all_orgs(self) -> list[tuple[OrgId, Workflow]]:
        """Cross-tenant scan used by the Celery Beat scheduler."""
        from app.domain.entities.workflow import WorkflowStatus
        out: list[tuple[OrgId, Workflow]] = []
        for org_id, by_id in self._s.items():
            for wf in by_id.values():
                if wf.status is WorkflowStatus.ACTIVE:
                    out.append((org_id, wf))
        out.sort(key=lambda pair: str(pair[0]))
        return out


class InMemoryWorkflowRunRepository:
    def __init__(self) -> None:
        self._s = _ScopedStore()
        self._by_workflow: dict[OrgId, dict[WorkflowId, list[RunId]]] = defaultdict(
            lambda: defaultdict(list)
        )

    async def add(self, r: WorkflowRun) -> WorkflowRun:
        self._s.for_org(r.org_id)[r.id] = r
        self._by_workflow[r.org_id][r.workflow_id].append(r.id)
        return r

    async def get(self, org_id: OrgId, run_id: RunId) -> WorkflowRun | None:
        return self._s.for_org(org_id).get(run_id)

    async def list_for_workflow(self, org_id: OrgId, workflow_id: WorkflowId) -> list[WorkflowRun]:
        ids = self._by_workflow[org_id].get(workflow_id, [])
        store = self._s.for_org(org_id)
        return [store[i] for i in ids if i in store]

    async def update(self, r: WorkflowRun) -> WorkflowRun:
        self._s.for_org(r.org_id)[r.id] = r
        return r


class InMemoryPostRepository:
    def __init__(self) -> None:
        self._s = _ScopedStore()

    async def add(self, p: Post) -> Post:
        self._s.for_org(p.org_id)[p.id] = p
        return p

    async def get(self, org_id: OrgId, post_id: PostId) -> Post | None:
        return self._s.for_org(org_id).get(post_id)

    async def list(self, org_id: OrgId, *, status: str | None = None) -> list[Post]:
        items = list(self._s.for_org(org_id).values())
        if status:
            items = [i for i in items if i.status.value == status]
        return items

    async def update(self, p: Post) -> Post:
        self._s.for_org(p.org_id)[p.id] = p
        return p

    async def delete(self, org_id: OrgId, post_id: PostId) -> None:
        self._s.for_org(org_id).pop(post_id, None)


class InMemoryUserRepository:
    def __init__(self) -> None:
        self._by_id: dict[UserId, User] = {}
        self._by_subject: dict[str, User] = {}

    async def get(self, user_id: UserId) -> User | None:
        return self._by_id.get(user_id)

    async def get_by_subject(self, subject: str) -> User | None:
        return self._by_subject.get(subject)

    async def upsert(self, user: User) -> User:
        self._by_id[user.id] = user
        self._by_subject[user.okta_subject] = user
        return user


# ── Triggers + ReviewSessions ──────────────────────────────────────────────
from app.domain.entities.trigger import Trigger
from app.domain.entities.review_session import ReviewSession, ReviewStatus
from app.domain.value_objects.ids import ReviewId, TriggerId


class InMemoryTriggerRepository:
    def __init__(self) -> None:
        self._s = _ScopedStore()
        self._global: dict[TriggerId, Trigger] = {}

    async def add(self, t: Trigger) -> Trigger:
        self._s.for_org(t.org_id)[t.id] = t
        self._global[t.id] = t
        return t

    async def get(self, org_id: OrgId, trigger_id: TriggerId) -> Trigger | None:
        return self._s.for_org(org_id).get(trigger_id)

    async def get_any(self, trigger_id: TriggerId) -> Trigger | None:
        """Used by webhook routes — caller doesn't yet know the org."""
        return self._global.get(trigger_id)

    async def list(self, org_id: OrgId, *, plugin_name: str | None = None) -> list[Trigger]:
        items = list(self._s.for_org(org_id).values())
        if plugin_name:
            items = [t for t in items if t.plugin_name == plugin_name]
        return items

    async def update(self, t: Trigger) -> Trigger:
        self._s.for_org(t.org_id)[t.id] = t
        self._global[t.id] = t
        return t

    async def delete(self, org_id: OrgId, trigger_id: TriggerId) -> None:
        self._s.for_org(org_id).pop(trigger_id, None)
        self._global.pop(trigger_id, None)


class InMemoryReviewSessionRepository:
    def __init__(self) -> None:
        self._s = _ScopedStore()
        self._by_msg_ref: dict[tuple[str, str], ReviewSession] = {}
        self._latest_pending: dict[tuple[str, str], ReviewSession] = {}

    async def add(self, r: ReviewSession) -> ReviewSession:
        self._s.for_org(r.org_id)[r.id] = r
        if r.sent_message_ref:
            self._by_msg_ref[(r.channel, r.sent_message_ref)] = r
        if r.status is ReviewStatus.PENDING:
            self._latest_pending[(r.channel, r.recipient)] = r
        return r

    async def get(self, org_id: OrgId, review_id: ReviewId) -> ReviewSession | None:
        return self._s.for_org(org_id).get(review_id)

    async def get_by_message_ref(self, channel: str, ref: str) -> ReviewSession | None:
        return self._by_msg_ref.get((channel, ref))

    async def latest_pending_for(self, channel: str, sender: str) -> ReviewSession | None:
        r = self._latest_pending.get((channel, sender))
        return r if r and r.status is ReviewStatus.PENDING else None

    async def list_open(self, org_id: OrgId) -> list[ReviewSession]:
        return [r for r in self._s.for_org(org_id).values() if r.status is ReviewStatus.PENDING]

    async def update(self, r: ReviewSession) -> ReviewSession:
        self._s.for_org(r.org_id)[r.id] = r
        if r.sent_message_ref:
            self._by_msg_ref[(r.channel, r.sent_message_ref)] = r
        if r.status is not ReviewStatus.PENDING:
            self._latest_pending.pop((r.channel, r.recipient), None)
        return r


# ── Campaigns / Experiments / Approval / Recycle / Privacy ────────────────
from app.domain.entities.campaign import Campaign, CampaignStatus
from app.domain.entities.experiment import Experiment, ExperimentStatus
from app.domain.entities.approval_policy import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalRequestStatus,
)
from app.domain.entities.recycle_policy import RecyclePolicy
from app.domain.entities.data_export import DataExportJob, DataJobStatus
from app.domain.value_objects.ids import (
    ApprovalPolicyId,
    ApprovalRequestId,
    CampaignId,
    DataExportJobId,
    ExperimentId,
    RecyclePolicyId,
)


class InMemoryCampaignRepository:
    def __init__(self) -> None:
        self._s = _ScopedStore()
        self._global: dict[CampaignId, Campaign] = {}

    async def add(self, c: Campaign) -> Campaign:
        self._s.for_org(c.org_id)[c.id] = c
        self._global[c.id] = c
        return c

    async def get(self, org_id: OrgId, campaign_id: CampaignId) -> Campaign | None:
        return self._s.for_org(org_id).get(campaign_id)

    async def list(self, org_id: OrgId, *, status: str | None = None) -> list[Campaign]:
        items = list(self._s.for_org(org_id).values())
        if status:
            items = [c for c in items if c.status.value == status]
        return items

    async def list_due(self, now) -> list[Campaign]:
        out: list[Campaign] = []
        active = {CampaignStatus.SCHEDULED, CampaignStatus.RUNNING}
        for c in self._global.values():
            if c.status not in active:
                continue
            if c.due_steps(now):
                out.append(c)
        return out

    async def update(self, c: Campaign) -> Campaign:
        self._s.for_org(c.org_id)[c.id] = c
        self._global[c.id] = c
        return c

    async def delete(self, org_id: OrgId, campaign_id: CampaignId) -> None:
        self._s.for_org(org_id).pop(campaign_id, None)
        self._global.pop(campaign_id, None)


class InMemoryExperimentRepository:
    def __init__(self) -> None:
        self._s = _ScopedStore()
        self._global: dict[ExperimentId, Experiment] = {}

    async def add(self, e: Experiment) -> Experiment:
        self._s.for_org(e.org_id)[e.id] = e
        self._global[e.id] = e
        return e

    async def get(self, org_id: OrgId, experiment_id: ExperimentId) -> Experiment | None:
        return self._s.for_org(org_id).get(experiment_id)

    async def list(
        self, org_id: OrgId, *, status: str | None = None,
    ) -> list[Experiment]:
        items = list(self._s.for_org(org_id).values())
        if status:
            items = [e for e in items if e.status.value == status]
        return items

    async def list_running(self) -> list[Experiment]:
        return [
            e for e in self._global.values()
            if e.status is ExperimentStatus.RUNNING
        ]

    async def update(self, e: Experiment) -> Experiment:
        self._s.for_org(e.org_id)[e.id] = e
        self._global[e.id] = e
        return e

    async def delete(self, org_id: OrgId, experiment_id: ExperimentId) -> None:
        self._s.for_org(org_id).pop(experiment_id, None)
        self._global.pop(experiment_id, None)


class InMemoryApprovalPolicyRepository:
    def __init__(self) -> None:
        self._s = _ScopedStore()

    async def add(self, p: ApprovalPolicy) -> ApprovalPolicy:
        self._s.for_org(p.org_id)[p.id] = p
        return p

    async def get(
        self, org_id: OrgId, policy_id: ApprovalPolicyId,
    ) -> ApprovalPolicy | None:
        return self._s.for_org(org_id).get(policy_id)

    async def list(self, org_id: OrgId) -> list[ApprovalPolicy]:
        return list(self._s.for_org(org_id).values())

    async def update(self, p: ApprovalPolicy) -> ApprovalPolicy:
        self._s.for_org(p.org_id)[p.id] = p
        return p

    async def delete(self, org_id: OrgId, policy_id: ApprovalPolicyId) -> None:
        self._s.for_org(org_id).pop(policy_id, None)


class InMemoryApprovalRequestRepository:
    def __init__(self) -> None:
        self._s = _ScopedStore()

    async def add(self, r: ApprovalRequest) -> ApprovalRequest:
        self._s.for_org(r.org_id)[r.id] = r
        return r

    async def get(
        self, org_id: OrgId, request_id: ApprovalRequestId,
    ) -> ApprovalRequest | None:
        return self._s.for_org(org_id).get(request_id)

    async def list_open(self, org_id: OrgId) -> list[ApprovalRequest]:
        return [
            r for r in self._s.for_org(org_id).values()
            if r.status in (
                ApprovalRequestStatus.PENDING,
                ApprovalRequestStatus.IN_PROGRESS,
            )
        ]

    async def list_for_post(
        self, org_id: OrgId, post_id: PostId,
    ) -> list[ApprovalRequest]:
        return [
            r for r in self._s.for_org(org_id).values()
            if r.post_id == post_id
        ]

    async def update(self, r: ApprovalRequest) -> ApprovalRequest:
        self._s.for_org(r.org_id)[r.id] = r
        return r


class InMemoryRecyclePolicyRepository:
    def __init__(self) -> None:
        self._s = _ScopedStore()
        self._global: dict[RecyclePolicyId, RecyclePolicy] = {}

    async def add(self, p: RecyclePolicy) -> RecyclePolicy:
        self._s.for_org(p.org_id)[p.id] = p
        self._global[p.id] = p
        return p

    async def get(
        self, org_id: OrgId, policy_id: RecyclePolicyId,
    ) -> RecyclePolicy | None:
        return self._s.for_org(org_id).get(policy_id)

    async def list(self, org_id: OrgId) -> list[RecyclePolicy]:
        return list(self._s.for_org(org_id).values())

    async def list_due(self, now) -> list[RecyclePolicy]:
        return [p for p in self._global.values() if p.is_due(now)]

    async def update(self, p: RecyclePolicy) -> RecyclePolicy:
        self._s.for_org(p.org_id)[p.id] = p
        self._global[p.id] = p
        return p

    async def delete(self, org_id: OrgId, policy_id: RecyclePolicyId) -> None:
        self._s.for_org(org_id).pop(policy_id, None)
        self._global.pop(policy_id, None)


class InMemoryDataExportJobRepository:
    def __init__(self) -> None:
        self._s = _ScopedStore()
        self._global: dict[DataExportJobId, DataExportJob] = {}

    async def add(self, j: DataExportJob) -> DataExportJob:
        self._s.for_org(j.org_id)[j.id] = j
        self._global[j.id] = j
        return j

    async def get(
        self, org_id: OrgId, job_id: DataExportJobId,
    ) -> DataExportJob | None:
        return self._s.for_org(org_id).get(job_id)

    async def list(self, org_id: OrgId) -> list[DataExportJob]:
        return list(self._s.for_org(org_id).values())

    async def list_pending(self) -> list[DataExportJob]:
        return [
            j for j in self._global.values()
            if j.status in (DataJobStatus.PENDING, DataJobStatus.RUNNING)
        ]

    async def update(self, j: DataExportJob) -> DataExportJob:
        self._s.for_org(j.org_id)[j.id] = j
        self._global[j.id] = j
        return j
