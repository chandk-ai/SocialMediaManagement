"""FastAPI dependencies — wire services to request scope.

The system supports two persistence backends:
* `memory`   — in-process repos, used for tests and the zero-config dev mode
* `supabase` — async SQLAlchemy + asyncpg pointed at Supabase Postgres
"""
from __future__ import annotations

from functools import lru_cache

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.security import Principal, get_current_user
from app.plugins.manager import PluginManager
from app.plugins.registry import PluginRegistry
from app.repositories import (
    InMemoryPlatformRepository,
    InMemoryPostRepository,
    InMemoryReviewSessionRepository,
    InMemorySourceRepository,
    InMemoryTriggerRepository,
    InMemoryUserRepository,
    InMemoryWorkflowRepository,
    InMemoryWorkflowRunRepository,
)
from app.services import (
    PlatformService,
    PluginService,
    PostService,
    ReviewService,
    SourceService,
    TriggerService,
    WorkflowService,
)

log = get_logger(__name__)


@lru_cache
def get_registry() -> PluginRegistry:
    return PluginManager().load_all()


# ── repository singletons ──────────────────────────────────────────────────
@lru_cache
def _build_repos(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    backend = settings.resolved_persistence_backend()
    log.info("persistence_backend_selected", backend=backend)

    if backend == "supabase":
        try:
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
            from app.repositories.supabase_repo import (
                SupabasePlatformRepository,
                SupabasePostRepository,
                SupabaseReviewSessionRepository,
                SupabaseSourceRepository,
                SupabaseTriggerRepository,
                SupabaseUserRepository,
                SupabaseWorkflowRepository,
                SupabaseWorkflowRunRepository,
            )
        except ImportError:
            log.warning("sqlalchemy_missing_falling_back_to_memory")
            return _memory_repos()

        engine = create_async_engine(
            settings.db_url(),
            pool_pre_ping=True,
            pool_size=settings.db.pool_size,
            max_overflow=settings.db.max_overflow,
            echo=settings.db.echo,
            connect_args=settings.db_connect_args(),
        )
        sm = async_sessionmaker(engine, expire_on_commit=False)
        return {
            "platform": SupabasePlatformRepository(sm),
            "source":   SupabaseSourceRepository(sm),
            "workflow": SupabaseWorkflowRepository(sm),
            "run":      SupabaseWorkflowRunRepository(sm),
            "post":     SupabasePostRepository(sm),
            "user":     SupabaseUserRepository(sm),
            "trigger":  SupabaseTriggerRepository(sm),
            "review":   SupabaseReviewSessionRepository(sm),
        }
    return _memory_repos()


def _memory_repos() -> dict:
    return {
        "platform": InMemoryPlatformRepository(),
        "source":   InMemorySourceRepository(),
        "workflow": InMemoryWorkflowRepository(),
        "run":      InMemoryWorkflowRunRepository(),
        "post":     InMemoryPostRepository(),
        "user":     InMemoryUserRepository(),
        "trigger":  InMemoryTriggerRepository(),
        "review":   InMemoryReviewSessionRepository(),
    }


# ── service-level providers ────────────────────────────────────────────────
def get_platform_service() -> PlatformService:
    return PlatformService(_build_repos()["platform"])


def get_source_service() -> SourceService:
    return SourceService(_build_repos()["source"])


def get_post_service() -> PostService:
    return PostService(_build_repos()["post"])


def get_plugin_service() -> PluginService:
    return PluginService(get_registry())


def get_trigger_service() -> TriggerService:
    return TriggerService(_build_repos()["trigger"], get_registry())


def get_review_service() -> ReviewService:
    return ReviewService(_build_repos()["review"], get_registry())


def get_workflow_service() -> WorkflowService:
    repos = _build_repos()
    return WorkflowService(
        repo=repos["workflow"],
        run_repo=repos["run"],
        source_repo=repos["source"],
        platform_repo=repos["platform"],
        post_repo=repos["post"],
        registry=get_registry(),
        review_repo=repos["review"],
        llm_credentials=_get_llm_credentials_service(),
    )


@lru_cache
def _get_llm_credentials_service():
    """Lazy-init the LLM credentials service backed by the same DB engine
    as the rest of the repos. Returns None on the in-memory backend."""
    settings = get_settings()
    if settings.resolved_persistence_backend() != "supabase":
        return None
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from app.services.llm_credentials import LlmCredentialsService
        engine = create_async_engine(
            settings.db_url(),
            pool_pre_ping=True,
            connect_args=settings.db_connect_args(),
        )
        sm = async_sessionmaker(engine, expire_on_commit=False)
        return LlmCredentialsService(sm)
    except Exception:                                            # noqa: BLE001
        return None


def get_llm_credentials_service():
    return _get_llm_credentials_service()


async def build_dev_workflow_service() -> WorkflowService:
    return get_workflow_service()


def current_user(user: Principal = Depends(get_current_user)) -> Principal:
    return user
