"""Strongly-typed ID value objects.

Using NewType keeps signatures honest (you can't pass a UserId where a PostId
is expected) without runtime overhead.
"""
from __future__ import annotations

from typing import NewType
from uuid import UUID, uuid4

OrgId = NewType("OrgId", UUID)
UserId = NewType("UserId", UUID)
PlatformId = NewType("PlatformId", UUID)
SourceId = NewType("SourceId", UUID)
WorkflowId = NewType("WorkflowId", UUID)
PostId = NewType("PostId", UUID)
RunId = NewType("RunId", UUID)
TriggerId = NewType("TriggerId", UUID)
ReviewId = NewType("ReviewId", UUID)
CampaignId = NewType("CampaignId", UUID)
CampaignStepId = NewType("CampaignStepId", UUID)
ExperimentId = NewType("ExperimentId", UUID)
VariantId = NewType("VariantId", UUID)
ApprovalPolicyId = NewType("ApprovalPolicyId", UUID)
ApprovalStepId = NewType("ApprovalStepId", UUID)
ApprovalRequestId = NewType("ApprovalRequestId", UUID)
LocaleVariantId = NewType("LocaleVariantId", UUID)
RecyclePolicyId = NewType("RecyclePolicyId", UUID)
DataExportJobId = NewType("DataExportJobId", UUID)


def new_id() -> UUID:
    """Factory used by entities to generate fresh IDs."""
    return uuid4()
