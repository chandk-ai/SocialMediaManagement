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
    InMemoryApprovalPolicyRepository,
    InMemoryApprovalRequestRepository,
    InMemoryCampaignRepository,
    InMemoryDataExportJobRepository,
    InMemoryExperimentRepository,
    InMemoryPlatformRepository,
    InMemoryPostRepository,
    InMemoryRecyclePolicyRepository,
    InMemoryReviewSessionRepository,
    InMemorySourceRepository,
    InMemoryTriggerRepository,
    InMemoryUserRepository,
    InMemoryWorkflowRepository,
    InMemoryWorkflowRunRepository,
)
from app.services import (
    ApprovalPolicyService,
    CampaignService,
    ContentRecyclerService,
    DataPrivacyService,
    ExperimentService,
    HashtagIntelligenceService,
    LocalizationService,
    PerformanceLearner,
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
            # New aggregates fall back to in-memory until Supabase mirrors
            # are implemented; data persists for the lifetime of the
            # process which is sufficient for the current beta.
            "campaign":         InMemoryCampaignRepository(),
            "experiment":       InMemoryExperimentRepository(),
            "approval_policy":  InMemoryApprovalPolicyRepository(),
            "approval_request": InMemoryApprovalRequestRepository(),
            "recycle":          InMemoryRecyclePolicyRepository(),
            "data_export":      InMemoryDataExportJobRepository(),
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
        # New aggregates introduced for advanced features. These always
        # use the in-memory backend even on Supabase deployments because
        # their Supabase mirrors haven't shipped yet — the data is still
        # available via the API but resets on app restart.
        "campaign":         InMemoryCampaignRepository(),
        "experiment":       InMemoryExperimentRepository(),
        "approval_policy":  InMemoryApprovalPolicyRepository(),
        "approval_request": InMemoryApprovalRequestRepository(),
        "recycle":          InMemoryRecyclePolicyRepository(),
        "data_export":      InMemoryDataExportJobRepository(),
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
        llm_usage=_get_llm_usage_service(),
        source_items=_get_source_items_service(),
    )


@lru_cache
def _get_llm_usage_service():
    """LLM usage service backed by Postgres. None on the in-memory backend
    (no usage table exists in memory mode — budget guard is a no-op there)."""
    settings = get_settings()
    if settings.resolved_persistence_backend() != "supabase":
        return None
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from app.services.llm_usage import LlmUsageService
        engine = create_async_engine(
            settings.db_url(),
            pool_pre_ping=True,
            connect_args=settings.db_connect_args(),
        )
        sm = async_sessionmaker(engine, expire_on_commit=False)
        return LlmUsageService(sm)
    except Exception:                                            # noqa: BLE001
        return None


def get_llm_usage_service():
    return _get_llm_usage_service()


@lru_cache
def _get_team_service():
    """Team / invitation service. None on memory backend (the auto-claim
    flow + member management are Postgres-only)."""
    settings = get_settings()
    if settings.resolved_persistence_backend() != "supabase":
        return None
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from app.services.team import TeamService
        engine = create_async_engine(
            settings.db_url(),
            pool_pre_ping=True,
            connect_args=settings.db_connect_args(),
        )
        sm = async_sessionmaker(engine, expire_on_commit=False)
        return TeamService(sm)
    except Exception:                                            # noqa: BLE001
        return None


def get_team_service():
    return _get_team_service()


@lru_cache
def _get_source_items_service():
    """Postgres-backed source-items registry. None on memory backend —
    the workflow service detects this and skips the de-dup / claim path
    (the selection layer still runs, just without persistent memory)."""
    settings = get_settings()
    if settings.resolved_persistence_backend() != "supabase":
        return None
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from app.services.source_items import SourceItemsService
        engine = create_async_engine(
            settings.db_url(),
            pool_pre_ping=True,
            connect_args=settings.db_connect_args(),
        )
        sm = async_sessionmaker(engine, expire_on_commit=False)
        return SourceItemsService(sm)
    except Exception:                                            # noqa: BLE001
        return None


def get_source_items_service():
    return _get_source_items_service()


@lru_cache
def _get_audit_log_service():
    """Append-only audit log writer. None on memory backend (audit_log table
    is Postgres-only); callers should null-guard."""
    settings = get_settings()
    if settings.resolved_persistence_backend() != "supabase":
        return None
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from app.services.audit_log import AuditLogService
        engine = create_async_engine(
            settings.db_url(),
            pool_pre_ping=True,
            connect_args=settings.db_connect_args(),
        )
        sm = async_sessionmaker(engine, expire_on_commit=False)
        return AuditLogService(sm)
    except Exception:                                            # noqa: BLE001
        return None


def get_audit_log_service():
    return _get_audit_log_service()


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


# ── Advanced feature services ─────────────────────────────────────────────
def get_campaign_service() -> CampaignService:
    repos = _build_repos()
    return CampaignService(
        repo=repos["campaign"],
        wf_repo=repos["workflow"],
        wf_service=get_workflow_service(),
    )


def get_experiment_service() -> ExperimentService:
    repos = _build_repos()
    return ExperimentService(
        repo=repos["experiment"],
        post_repo=repos["post"],
        platform_repo=repos["platform"],
        registry=get_registry(),
    )


def get_approval_policy_service() -> ApprovalPolicyService:
    repos = _build_repos()
    return ApprovalPolicyService(
        policy_repo=repos["approval_policy"],
        request_repo=repos["approval_request"],
        post_repo=repos["post"],
    )


def get_content_recycler() -> ContentRecyclerService:
    repos = _build_repos()
    return ContentRecyclerService(
        repo=repos["recycle"],
        post_repo=repos["post"],
        platform_repo=repos["platform"],
        wf_repo=repos["workflow"],
        wf_service=get_workflow_service(),
        llm=None,
    )


def get_localization_service() -> LocalizationService | None:
    """Returns None when no LLM provider is configured — the route can
    return 503 in that case."""
    try:
        from app.adapters.llm.mock import MockLLM   # type: ignore[attr-defined]
        return LocalizationService(MockLLM())
    except Exception:                                                 # noqa: BLE001
        return None


def get_hashtag_intelligence_service() -> HashtagIntelligenceService:
    repos = _build_repos()
    return HashtagIntelligenceService(
        post_repo=repos["post"],
        platform_repo=repos["platform"],
    )


def get_performance_learner() -> PerformanceLearner:
    repos = _build_repos()
    return PerformanceLearner(
        post_repo=repos["post"],
        platform_repo=repos["platform"],
    )


def get_data_privacy_service() -> DataPrivacyService:
    repos = _build_repos()
    return DataPrivacyService(
        job_repo=repos["data_export"],
        platform_repo=repos["platform"],
        source_repo=repos["source"],
        workflow_repo=repos["workflow"],
        run_repo=repos["run"],
        post_repo=repos["post"],
        trigger_repo=repos["trigger"],
        review_repo=repos["review"],
        campaign_repo=repos.get("campaign"),
        experiment_repo=repos.get("experiment"),
        approval_policy_repo=repos.get("approval_policy"),
        approval_request_repo=repos.get("approval_request"),
        recycle_repo=repos.get("recycle"),
    )


def current_user(user: Principal = Depends(get_current_user)) -> Principal:
    return user
