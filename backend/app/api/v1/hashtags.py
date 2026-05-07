"""HTTP routes for the hashtag-intelligence service."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import current_user, get_hashtag_intelligence_service
from app.core.security import Principal
from app.domain.value_objects.ids import OrgId
from app.services.hashtag_intelligence import HashtagIntelligenceService

router = APIRouter()


class HashtagInsightOut(BaseModel):
    tag: str
    plugin_name: str
    use_count: int
    avg_engagement: float
    score: float
    last_used_at: datetime | None = None
    sample_post_ids: list[str] = Field(default_factory=list)


class SuggestionOut(BaseModel):
    tag: str
    score: float
    rationale: str


@router.get("/insights", response_model=list[HashtagInsightOut])
async def insights(
    plugin_name: str | None = None,
    user: Principal = Depends(current_user),
    svc: HashtagIntelligenceService = Depends(get_hashtag_intelligence_service),
) -> list[HashtagInsightOut]:
    items = await svc.insights_for(
        OrgId(UUID(user.org_id)), plugin_name=plugin_name,
    )
    return [
        HashtagInsightOut(
            tag=i.tag,
            plugin_name=i.plugin_name,
            use_count=i.use_count,
            avg_engagement=i.avg_engagement,
            score=i.score,
            last_used_at=i.last_used_at,
            sample_post_ids=i.sample_post_ids,
        )
        for i in items
    ]


class SuggestIn(BaseModel):
    plugin_name: str
    seed_text: str = ""
    limit: int = 10
    exclude: list[str] = Field(default_factory=list)


@router.post("/suggest", response_model=list[SuggestionOut])
async def suggest(
    body: SuggestIn,
    user: Principal = Depends(current_user),
    svc: HashtagIntelligenceService = Depends(get_hashtag_intelligence_service),
) -> list[SuggestionOut]:
    items = await svc.suggest_for(
        OrgId(UUID(user.org_id)),
        plugin_name=body.plugin_name,
        seed_text=body.seed_text,
        limit=body.limit,
        exclude=tuple(body.exclude),
    )
    return [
        SuggestionOut(tag=s.tag, score=s.score, rationale=s.rationale)
        for s in items
    ]
