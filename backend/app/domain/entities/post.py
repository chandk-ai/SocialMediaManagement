from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..value_objects.content import DraftPost, EvaluationReport, Hashtag, MediaAsset
from ..value_objects.ids import OrgId, PlatformId, PostId, RunId, SourceId, WorkflowId, new_id


class PostStatus(str, Enum):
    DRAFT = "draft"
    REVIEW = "review"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    FAILED = "failed"


@dataclass(slots=True)
class Post:
    id: PostId
    org_id: OrgId
    workflow_id: WorkflowId
    run_id: RunId
    platform_id: PlatformId
    text: str
    hashtags: list[Hashtag]
    media: list[MediaAsset]
    status: PostStatus = PostStatus.DRAFT
    evaluation: EvaluationReport | None = None
    scheduled_for: datetime | None = None
    published_at: datetime | None = None
    external_post_id: str | None = None      # ID in the social network
    error: str | None = None
    metrics: dict | None = None              # latest snapshot from fetch_metrics()
    metrics_updated_at: datetime | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    # Niche #3 — when this Post was generated from a CMS-mode Source row
    # (Notion, Airtable, ...), these fields let the publish path call back
    # into the source plugin to mark the row as Published / Failed and
    # write the live URL.
    source_id: SourceId | None = None
    source_external_id: str | None = None

    def approve(self) -> None:
        self.status = PostStatus.APPROVED

    def mark_published(self, external_id: str) -> None:
        self.status = PostStatus.PUBLISHED
        self.external_post_id = external_id
        self.published_at = datetime.utcnow()

    def mark_failed(self, message: str) -> None:
        self.status = PostStatus.FAILED
        self.error = message

    @classmethod
    def from_draft(
        cls, *, org_id: OrgId, workflow_id: WorkflowId, run_id: RunId,
        platform_id: PlatformId, draft: DraftPost,
        evaluation: EvaluationReport | None = None,
        source_id: SourceId | None = None,
        source_external_id: str | None = None,
    ) -> "Post":
        return cls(
            id=PostId(new_id()),
            org_id=org_id,
            workflow_id=workflow_id,
            run_id=run_id,
            platform_id=platform_id,
            text=draft.text,
            hashtags=list(draft.hashtags),
            media=list(draft.media),
            evaluation=evaluation,
            source_id=source_id,
            source_external_id=source_external_id,
        )
