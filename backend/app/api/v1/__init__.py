from fastapi import APIRouter

from . import (
    auth, platforms, sources, workflows, posts, plugins, health,
    triggers, reviews, webhooks, llm_keys,
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
