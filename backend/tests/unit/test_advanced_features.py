"""Smoke tests for the advanced feature aggregates added in v0.2.

Exercises the pure-Python state machines for Campaigns, Experiments,
ApprovalRequests, RecyclePolicies and DataExportJobs without touching
any external service. These tests should run under bare pytest with no
extra dependencies beyond the project itself.
"""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.entities.approval_policy import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalRequestStatus,
    ApprovalStep,
)
from app.domain.entities.campaign import (
    Campaign,
    CampaignKind,
    CampaignStatus,
    CampaignStep,
    CampaignStepStatus,
)
from app.domain.entities.data_export import (
    DataExportJob,
    DataJobKind,
    DataJobStatus,
)
from app.domain.entities.experiment import (
    AllocationKind,
    Experiment,
    ExperimentStatus,
    Variant,
    VariantStatus,
)
from app.domain.entities.recycle_policy import RecyclePolicy, RecycleStrategy
from app.domain.value_objects.ids import (
    ApprovalStepId,
    OrgId,
    PlatformId,
    PostId,
    UserId,
    WorkflowId,
)


# ── Campaign ──────────────────────────────────────────────────────────────
def _campaign(org: OrgId, *, days: int = 7) -> Campaign:
    return Campaign.create(
        org_id=org,
        name="launch",
        description="",
        kind=CampaignKind.PRODUCT_LAUNCH,
        starts_at=datetime.utcnow(),
        ends_at=datetime.utcnow() + timedelta(days=days),
    )


def test_campaign_activate_requires_steps():
    org = OrgId(uuid4())
    c = _campaign(org)
    with pytest.raises(ValueError):
        c.activate()


def test_campaign_due_steps_respect_dependencies():
    org = OrgId(uuid4())
    c = _campaign(org)
    s1 = CampaignStep.create(
        workflow_id=WorkflowId(uuid4()),
        title="teaser", directive="",
        scheduled_for=datetime.utcnow() - timedelta(hours=1),
    )
    s2 = CampaignStep.create(
        workflow_id=WorkflowId(uuid4()),
        title="launch", directive="",
        scheduled_for=datetime.utcnow() - timedelta(minutes=30),
        depends_on=[s1.id],
    )
    c.steps.extend([s1, s2])
    c.activate()
    due = c.due_steps(datetime.utcnow())
    assert [s.id for s in due] == [s1.id]
    s1.mark_completed()
    due = c.due_steps(datetime.utcnow())
    assert [s.id for s in due] == [s2.id]


def test_campaign_evaluate_completion():
    org = OrgId(uuid4())
    c = _campaign(org)
    s = CampaignStep.create(
        workflow_id=WorkflowId(uuid4()),
        title="x", directive="",
        scheduled_for=datetime.utcnow(),
    )
    c.steps.append(s)
    c.activate()
    s.mark_completed()
    c.evaluate_completion()
    assert c.status is CampaignStatus.COMPLETED


# ── Experiment ────────────────────────────────────────────────────────────
def _two_variants() -> list[Variant]:
    return [
        Variant.create(label="A", text="a"),
        Variant.create(label="B", text="b"),
    ]


def test_experiment_settle_picks_top_metric():
    org = OrgId(uuid4())
    e = Experiment.create(
        org_id=org,
        workflow_id=WorkflowId(uuid4()),
        platform_id=PlatformId(uuid4()),
        hypothesis="shorter copy wins",
        variants=_two_variants(),
    )
    e.start()
    a, b = e.variants
    a.status = VariantStatus.PUBLISHED
    b.status = VariantStatus.PUBLISHED
    a.record_metric(0.04)
    b.record_metric(0.07)
    winner = e.settle()
    assert winner is not None and winner.label == "B"
    assert e.status is ExperimentStatus.SETTLED
    assert b.status is VariantStatus.WINNER
    assert a.status is VariantStatus.LOSER


def test_experiment_holdout_weights():
    """In HOLDOUT mode the first variant should be the small "control"
    cohort; the rest split the remaining 90%."""
    variants = [
        Variant.create(label="control", text="c"),
        Variant.create(label="A", text="a"),
        Variant.create(label="B", text="b"),
    ]
    # Import lazily — the services package pulls in optional deps that
    # aren't relevant to this pure-domain assertion.
    import importlib
    es = importlib.import_module("app.services.experiment_service")
    out = es._normalize_weights(variants, AllocationKind.HOLDOUT)
    assert out[0].weight == pytest.approx(0.1)
    assert out[1].weight == pytest.approx(0.45)
    assert out[2].weight == pytest.approx(0.45)


# ── ApprovalRequest ───────────────────────────────────────────────────────
def test_approval_chain_with_optional_step():
    org = OrgId(uuid4())
    a, b, c = UserId(uuid4()), UserId(uuid4()), UserId(uuid4())
    s1 = ApprovalStep(id=ApprovalStepId(uuid4()), name="legal",
                      approver_user_ids=(a,))
    s2 = ApprovalStep(id=ApprovalStepId(uuid4()), name="marketing",
                      approver_user_ids=(b,))
    s3 = ApprovalStep(id=ApprovalStepId(uuid4()), name="exec",
                      approver_user_ids=(c,), optional=True)
    pol = ApprovalPolicy.create(
        org_id=org, name="regulated", description="",
        steps=[s1, s2, s3],
    )
    req = ApprovalRequest.create(
        org_id=org, policy_id=pol.id, post_id=PostId(uuid4()),
    )
    req.record_decision(step=s1, approver_user_id=a, approved=True)
    req.advance(pol)
    assert req.current_step_index == 1
    req.record_decision(step=s2, approver_user_id=b, approved=True)
    req.advance(pol)
    # s3 is optional → request completes.
    assert req.status is ApprovalRequestStatus.APPROVED


def test_approval_rejection_short_circuits():
    org = OrgId(uuid4())
    a = UserId(uuid4())
    s = ApprovalStep(id=ApprovalStepId(uuid4()), name="legal",
                    approver_user_ids=(a,))
    pol = ApprovalPolicy.create(org_id=org, name="x", description="", steps=[s])
    req = ApprovalRequest.create(
        org_id=org, policy_id=pol.id, post_id=PostId(uuid4()),
    )
    req.record_decision(step=s, approver_user_id=a, approved=False,
                        comment="bad copy")
    req.advance(pol)
    assert req.status is ApprovalRequestStatus.REJECTED


# ── RecyclePolicy ─────────────────────────────────────────────────────────
def test_recycle_policy_is_due_after_cadence():
    pol = RecyclePolicy.create(org_id=OrgId(uuid4()), name="weekly",
                               cadence_days=7)
    # Cold start always due.
    assert pol.is_due(datetime.utcnow())
    pol.mark_ran()
    assert not pol.is_due(datetime.utcnow())
    pol.last_run_at = datetime.utcnow() - timedelta(days=8)
    assert pol.is_due(datetime.utcnow())


def test_recycle_policy_disabled_never_due():
    pol = RecyclePolicy.create(org_id=OrgId(uuid4()), name="x")
    pol.enabled = False
    assert not pol.is_due(datetime.utcnow())


# ── DataExportJob ─────────────────────────────────────────────────────────
def test_data_export_lifecycle():
    job = DataExportJob.create_export(
        org_id=OrgId(uuid4()), requested_by=UserId(uuid4()),
    )
    assert job.status is DataJobStatus.PENDING
    job.mark_running()
    assert job.status is DataJobStatus.RUNNING
    job.mark_completed(download_url="data:application/json;base64,eyJ9",
                       counts={"posts": 42})
    assert job.status is DataJobStatus.COMPLETED
    assert job.counts["posts"] == 42


def test_data_delete_grace_period():
    job = DataExportJob.create_delete(
        org_id=OrgId(uuid4()),
        requested_by=UserId(uuid4()),
        grace_period_minutes=10,
    )
    now = datetime.utcnow()
    assert not job.grace_period_elapsed(now)
    assert job.grace_period_elapsed(now + timedelta(minutes=11))
