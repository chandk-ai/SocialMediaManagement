"""Smoke tests for the pure-Python domain layer."""
from datetime import datetime

import pytest

from app.domain.entities.organization import Organization
from app.domain.entities.platform import Platform, PlatformStatus
from app.domain.entities.post import Post, PostStatus
from app.domain.entities.source import Source
from app.domain.entities.workflow import Workflow, WorkflowConfig, WorkflowStatus
from app.domain.value_objects.content import (
    DraftPost,
    EvaluationReport,
    Hashtag,
)
from app.domain.value_objects.schedule import Schedule, ScheduleKind


def _org():
    return Organization.create(name="Acme", slug="acme")


def test_hashtag_normalises_leading_hash():
    assert Hashtag("foo").value == "#foo"
    assert Hashtag("#bar").value == "#bar"
    with pytest.raises(ValueError):
        Hashtag("two words")


def test_workflow_activation_requires_platforms():
    """Sources are optional (trigger-driven runs use a directive),
    but at least one publish target is mandatory."""
    org = _org()
    wf = Workflow.create(
        org_id=org.id, name="x", description="",
        source_ids=[], platform_ids=[],
        config=WorkflowConfig(),
        schedule=Schedule(kind=ScheduleKind.MANUAL),
    )
    assert wf.status is WorkflowStatus.DRAFT
    with pytest.raises(ValueError):
        wf.activate()


def test_post_lifecycle():
    org = _org()
    plat = Platform.create(org_id=org.id, plugin_name="linkedin", display_name="LI")
    wf = Workflow.create(
        org_id=org.id, name="w", description="",
        source_ids=[], platform_ids=[plat.id],
        config=WorkflowConfig(), schedule=Schedule(kind=ScheduleKind.MANUAL),
    )
    draft = DraftPost(platform_name="linkedin", text="hello", hashtags=[Hashtag("ai")])
    p = Post.from_draft(
        org_id=org.id, workflow_id=wf.id, run_id=wf.id,  # any UUID for test
        platform_id=plat.id, draft=draft,
    )
    assert p.status is PostStatus.DRAFT
    p.approve()
    assert p.status is PostStatus.APPROVED
    p.mark_published("ext-1")
    assert p.status is PostStatus.PUBLISHED
    assert p.external_post_id == "ext-1"
    assert isinstance(p.published_at, datetime)


def test_evaluation_report_weights_overall():
    rep = EvaluationReport.from_scores({
        "clarity": 1.0, "brand_voice": 1.0, "compliance": 1.0,
        "platform_fit": 1.0, "predicted_engagement": 1.0,
    })
    assert pytest.approx(rep.overall, 0.001) == 1.0
    assert rep.passes(0.5)


def test_platform_lifecycle():
    org = _org()
    plat = Platform.create(org_id=org.id, plugin_name="twitter", display_name="X")
    assert plat.status is PlatformStatus.DISCONNECTED
