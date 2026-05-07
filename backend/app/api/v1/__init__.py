from fastapi import APIRouter

from . import (
    auth, platforms, sources, workflows, posts, plugins, health,
    triggers, reviews, webhooks, llm_keys,
    campaigns, experiments, approvals, recycling,
    localization, hashtags, performance, privacy,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(platforms.router, prefix="/platforms", tags=["platforms"])
api_router.include_router(sources.router, prefix="/sources", tags=["sources"])
api_router.include_router(workflows.router, prefix="/workflows", tags=["workflows"])
api_router.include_router(posts.router, prefix="/posts", tags=["posts"])
api_router.include_router(plugins.router, prefix="/plugins", tags=["plugins"])
api_router.include_router(triggers.router, prefix="/triggers", tags=["triggers"])
api_router.include_router(reviews.router, prefix="/reviews", tags=["reviews"])
api_router.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
api_router.include_router(llm_keys.router, tags=["llm"])

# Advanced feature routers
api_router.include_router(campaigns.router, prefix="/campaigns", tags=["campaigns"])
api_router.include_router(experiments.router, prefix="/experiments", tags=["experiments"])
api_router.include_router(approvals.router, prefix="/approvals", tags=["approvals"])
api_router.include_router(recycling.router, prefix="/recycling", tags=["recycling"])
api_router.include_router(localization.router, prefix="/localization", tags=["localization"])
api_router.include_router(hashtags.router, prefix="/hashtags", tags=["hashtags"])
api_router.include_router(performance.router, prefix="/performance", tags=["performance"])
api_router.include_router(privacy.router, prefix="/privacy", tags=["privacy"])
