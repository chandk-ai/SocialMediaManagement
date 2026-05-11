"""Supabase-backed repository implementations using async SQLAlchemy.

These talk directly to Supabase Postgres — RLS is enforced server-side when
the connecting user-JWT is set on the session, and bypassed for the service
role we use in workers. Domain entities are mapped to ORM models at the
boundary so the rest of the app remains framework-agnostic.
"""
from __future__ import annotations

from typing import TypeVar

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.entities import (
    Organization,
    Platform,
    Post,
    Source,
    User,
    Workflow,
    WorkflowRun,
)
from app.domain.entities.platform import PlatformStatus
from app.domain.entities.post import PostStatus
from app.domain.entities.user import Role
from app.domain.entities.workflow import WorkflowConfig, WorkflowStatus
from app.domain.entities.workflow_run import AgentTraceEvent, RunStatus
from app.domain.value_objects.content import (
    EvaluationReport,
    Hashtag,
    MediaAsset,
    MediaKind,
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
from app.domain.value_objects.schedule import Schedule, ScheduleKind
from app.infrastructure.db.models import (
    OrganizationORM,
    PlatformORM,
    PostORM,
    SourceORM,
    UserORM,
    WorkflowORM,
    WorkflowRunORM,
)

T = TypeVar("T")


# ── mappers ────────────────────────────────────────────────────────────────
def _platform_to_domain(orm: PlatformORM) -> Platform:
    return Platform(
        id=PlatformId(orm.id), org_id=OrgId(orm.org_id),
        plugin_name=orm.plugin_name, display_name=orm.display_name,
        account_handle=orm.account_handle, account_external_id=orm.account_external_id,
        status=PlatformStatus(orm.status), config=dict(orm.config or {}),
        is_default=bool(orm.is_default),
        created_at=orm.created_at, last_used_at=orm.last_used_at,
    )


def _platform_to_orm(d: Platform) -> PlatformORM:
    return PlatformORM(
        id=d.id, org_id=d.org_id, plugin_name=d.plugin_name,
        display_name=d.display_name, account_handle=d.account_handle,
        account_external_id=d.account_external_id, status=d.status.value,
        is_default=d.is_default, config=d.config,
    )


def _source_to_domain(orm: SourceORM) -> Source:
    return Source(
        id=SourceId(orm.id), org_id=OrgId(orm.org_id),
        plugin_name=orm.plugin_name, display_name=orm.display_name,
        is_active=orm.is_active, config=dict(orm.config or {}),
        last_fetched_at=orm.last_fetched_at,
        last_failure_at=getattr(orm, "last_failure_at", None),
        last_error=getattr(orm, "last_error", None),
        error_count=getattr(orm, "error_count", 0) or 0,
        item_count=getattr(orm, "item_count", 0) or 0,
        created_at=orm.created_at,
    )


def _source_to_orm(d: Source) -> SourceORM:
    return SourceORM(
        id=d.id, org_id=d.org_id, plugin_name=d.plugin_name,
        display_name=d.display_name, is_active=d.is_active, config=d.config,
        last_fetched_at=d.last_fetched_at,
        last_failure_at=d.last_failure_at,
        last_error=d.last_error,
        error_count=d.error_count,
        item_count=d.item_count,
    )


def _workflow_to_domain(orm: WorkflowORM) -> Workflow:
    cfg = orm.config or {}
    sched = orm.schedule or {}
    return Workflow(
        id=WorkflowId(orm.id), org_id=OrgId(orm.org_id),
        name=orm.name, description=orm.description,
        source_ids=[SourceId(s) for s in (orm.source_ids or [])],
        platform_ids=[PlatformId(p) for p in (orm.platform_ids or [])],
        config=WorkflowConfig(
            tone=cfg.get("tone", "professional"),
            audience=cfg.get("audience", "general"),
            voice_guide=cfg.get("voice_guide"),
            max_revisions=int(cfg.get("max_revisions", 3)),
            quality_threshold=float(cfg.get("quality_threshold", 0.75)),
            low_quality_threshold=float(cfg.get("low_quality_threshold", 0.5)),
            require_human_approval=bool(cfg.get("require_human_approval", False)),
            llm_provider=cfg.get("llm_provider", "anthropic"),
            llm_model=cfg.get("llm_model", "claude-sonnet-4-6"),
            extra=dict(cfg.get("extra") or {}),
        ),
        schedule=Schedule(
            kind=ScheduleKind(sched.get("kind", "manual")),
            cron=sched.get("cron"), interval_minutes=sched.get("interval_minutes"),
            run_at=sched.get("run_at"), timezone=sched.get("timezone", "UTC"),
        ),
        status=WorkflowStatus(orm.status),
        created_at=orm.created_at, updated_at=orm.updated_at,
    )


def _workflow_to_orm(d: Workflow) -> WorkflowORM:
    return WorkflowORM(
        id=d.id, org_id=d.org_id, name=d.name, description=d.description,
        status=d.status.value,
        source_ids=list(d.source_ids), platform_ids=list(d.platform_ids),
        config={
            "tone": d.config.tone, "audience": d.config.audience,
            "voice_guide": d.config.voice_guide,
            "max_revisions": d.config.max_revisions,
            "quality_threshold": d.config.quality_threshold,
            "low_quality_threshold": d.config.low_quality_threshold,
            "require_human_approval": d.config.require_human_approval,
            "llm_provider": d.config.llm_provider,
            "llm_model": d.config.llm_model,
            "extra": d.config.extra,
        },
        schedule={
            "kind": d.schedule.kind.value, "cron": d.schedule.cron,
            "interval_minutes": d.schedule.interval_minutes,
            "run_at": d.schedule.run_at.isoformat() if d.schedule.run_at else None,
            "timezone": d.schedule.timezone,
        },
    )


def _run_to_domain(orm: WorkflowRunORM) -> WorkflowRun:
    return WorkflowRun(
        id=RunId(orm.id), org_id=OrgId(orm.org_id),
        workflow_id=WorkflowId(orm.workflow_id), status=RunStatus(orm.status),
        revision_count=orm.revision_count,
        trace=[AgentTraceEvent(agent=e["agent"], event=e["event"],
                                payload={k: v for k, v in e.items() if k not in ("agent","event","occurred_at")})
               for e in (orm.trace or [])],
        started_at=orm.started_at, finished_at=orm.finished_at,
        error=orm.error,
        # ``metadata_`` on the ORM maps to the ``metadata`` column —
        # we rename here back to the domain field. The durable runner
        # reads/writes this every phase; dropping it = pipeline broken.
        metadata=dict(orm.metadata_ or {}),
    )


def _run_to_orm(d: WorkflowRun) -> WorkflowRunORM:
    return WorkflowRunORM(
        id=d.id, org_id=d.org_id, workflow_id=d.workflow_id,
        status=d.status.value, revision_count=d.revision_count,
        trace=[{"agent": e.agent, "event": e.event,
                "occurred_at": e.occurred_at.isoformat(), **e.payload} for e in d.trace],
        error=d.error, started_at=d.started_at, finished_at=d.finished_at,
        metadata_=dict(d.metadata or {}),
    )


def _post_to_domain(orm: PostORM) -> Post:
    media = [MediaAsset(
        url=m.get("url", ""), kind=MediaKind(m.get("kind", "image")),
        alt_text=m.get("alt_text"),
    ) for m in (orm.media or [])]
    return Post(
        id=PostId(orm.id), org_id=OrgId(orm.org_id),
        workflow_id=WorkflowId(orm.workflow_id), run_id=RunId(orm.run_id),
        platform_id=PlatformId(orm.platform_id),
        text=orm.text, hashtags=[Hashtag(h) for h in (orm.hashtags or [])],
        media=media, status=PostStatus(orm.status),
        evaluation=_eval_from_dict(orm.evaluation),
        scheduled_for=orm.scheduled_for, published_at=orm.published_at,
        external_post_id=orm.external_post_id, error=orm.error,
        created_at=orm.created_at,
        source_id=SourceId(orm.source_id) if orm.source_id else None,
        source_external_id=orm.source_external_id,
    )


def _post_to_orm(d: Post) -> PostORM:
    return PostORM(
        id=d.id, org_id=d.org_id, workflow_id=d.workflow_id,
        run_id=d.run_id, platform_id=d.platform_id,
        text=d.text, hashtags=[h.value for h in d.hashtags],
        media=[{"url": m.url, "kind": m.kind.value, "alt_text": m.alt_text} for m in d.media],
        status=d.status.value,
        evaluation=_eval_to_dict(d.evaluation),
        scheduled_for=d.scheduled_for, published_at=d.published_at,
        external_post_id=d.external_post_id, error=d.error,
        source_id=d.source_id,
        source_external_id=d.source_external_id,
    )


def _eval_to_dict(e: EvaluationReport | None) -> dict | None:
    if e is None: return None
    return {
        "overall": e.overall, "clarity": e.clarity, "brand_voice": e.brand_voice,
        "compliance": e.compliance, "platform_fit": e.platform_fit,
        "predicted_engagement": e.predicted_engagement,
        "suggestions": list(e.suggestions), "flags": list(e.flags),
        "evaluated_at": e.evaluated_at.isoformat(),
    }


def _eval_from_dict(d: dict | None) -> EvaluationReport | None:
    if not d: return None
    from datetime import datetime
    return EvaluationReport(
        overall=d.get("overall", 0.0), clarity=d.get("clarity", 0.0),
        brand_voice=d.get("brand_voice", 0.0), compliance=d.get("compliance", 0.0),
        platform_fit=d.get("platform_fit", 0.0),
        predicted_engagement=d.get("predicted_engagement", 0.0),
        suggestions=list(d.get("suggestions", [])), flags=list(d.get("flags", [])),
        evaluated_at=datetime.fromisoformat(d.get("evaluated_at", datetime.utcnow().isoformat())),
    )


# ── repositories ───────────────────────────────────────────────────────────
class _Base:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker


class SupabasePlatformRepository(_Base):
    async def add(self, p: Platform) -> Platform:
        async with self._sm() as s:
            s.add(_platform_to_orm(p))
            await s.commit()
        return p

    async def get(self, org_id: OrgId, platform_id: PlatformId) -> Platform | None:
        async with self._sm() as s:
            row = (await s.execute(
                select(PlatformORM).where(
                    PlatformORM.id == platform_id, PlatformORM.org_id == org_id,
                ),
            )).scalar_one_or_none()
            return _platform_to_domain(row) if row else None

    async def list(self, org_id: OrgId) -> list[Platform]:
        async with self._sm() as s:
            rows = (await s.execute(
                select(PlatformORM).where(PlatformORM.org_id == org_id),
            )).scalars().all()
            return [_platform_to_domain(r) for r in rows]

    async def update(self, p: Platform) -> Platform:
        async with self._sm() as s:
            await s.merge(_platform_to_orm(p))
            await s.commit()
        return p

    async def delete(self, org_id: OrgId, platform_id: PlatformId) -> None:
        async with self._sm() as s:
            await s.execute(
                delete(PlatformORM).where(
                    PlatformORM.id == platform_id,
                    PlatformORM.org_id == org_id,
                ),
            )
            await s.commit()


class SupabaseSourceRepository(_Base):
    async def add(self, src: Source) -> Source:
        async with self._sm() as s:
            s.add(_source_to_orm(src))
            await s.commit()
        return src

    async def get(self, org_id: OrgId, source_id: SourceId) -> Source | None:
        async with self._sm() as s:
            row = (await s.execute(
                select(SourceORM).where(
                    SourceORM.id == source_id, SourceORM.org_id == org_id,
                ),
            )).scalar_one_or_none()
            return _source_to_domain(row) if row else None

    async def list(self, org_id: OrgId) -> list[Source]:
        async with self._sm() as s:
            rows = (await s.execute(
                select(SourceORM).where(SourceORM.org_id == org_id),
            )).scalars().all()
            return [_source_to_domain(r) for r in rows]

    async def update(self, src: Source) -> Source:
        async with self._sm() as s:
            await s.merge(_source_to_orm(src))
            await s.commit()
        return src

    async def delete(self, org_id: OrgId, source_id: SourceId) -> None:
        async with self._sm() as s:
            await s.execute(
                delete(SourceORM).where(
                    SourceORM.id == source_id,
                    SourceORM.org_id == org_id,
                ),
            )
            await s.commit()


class SupabaseWorkflowRepository(_Base):
    async def add(self, w: Workflow) -> Workflow:
        async with self._sm() as s:
            s.add(_workflow_to_orm(w))
            await s.commit()
        return w

    async def get(self, org_id: OrgId, wid: WorkflowId) -> Workflow | None:
        async with self._sm() as s:
            row = (await s.execute(
                select(WorkflowORM).where(
                    WorkflowORM.id == wid, WorkflowORM.org_id == org_id,
                ),
            )).scalar_one_or_none()
            return _workflow_to_domain(row) if row else None

    async def list(self, org_id: OrgId) -> list[Workflow]:
        async with self._sm() as s:
            rows = (await s.execute(
                select(WorkflowORM).where(WorkflowORM.org_id == org_id),
            )).scalars().all()
            return [_workflow_to_domain(r) for r in rows]

    async def update(self, w: Workflow) -> Workflow:
        async with self._sm() as s:
            await s.merge(_workflow_to_orm(w))
            await s.commit()
        return w

    async def delete(self, org_id: OrgId, workflow_id: WorkflowId) -> None:
        async with self._sm() as s:
            await s.execute(
                delete(WorkflowORM).where(
                    WorkflowORM.id == workflow_id,
                    WorkflowORM.org_id == org_id,
                ),
            )
            await s.commit()

    async def list_active_all_orgs(self) -> list[tuple[OrgId, Workflow]]:
        """Cross-tenant scan used by Celery Beat. Filters at the database
        level so we don't pull paused/archived rows over the wire — this
        stays cheap as the workflow count grows.

        Note: bypasses RLS by relying on the service-role connection the
        worker uses; that's fine here because the scheduler runs in the
        platform's privileged process, not on behalf of a user."""
        async with self._sm() as s:
            rows = (await s.execute(
                select(WorkflowORM)
                .where(WorkflowORM.status == "active")
                .order_by(WorkflowORM.org_id, WorkflowORM.id),
            )).scalars().all()
            return [(OrgId(r.org_id), _workflow_to_domain(r)) for r in rows]


class SupabaseWorkflowRunRepository(_Base):
    async def add(self, r: WorkflowRun) -> WorkflowRun:
        async with self._sm() as s:
            s.add(_run_to_orm(r))
            await s.commit()
        return r

    async def get(self, org_id: OrgId, run_id: RunId) -> WorkflowRun | None:
        async with self._sm() as s:
            row = (await s.execute(
                select(WorkflowRunORM).where(
                    WorkflowRunORM.id == run_id, WorkflowRunORM.org_id == org_id,
                ),
            )).scalar_one_or_none()
            return _run_to_domain(row) if row else None

    async def list_for_workflow(self, org_id: OrgId, workflow_id: WorkflowId) -> list[WorkflowRun]:
        async with self._sm() as s:
            rows = (await s.execute(
                select(WorkflowRunORM)
                .where(WorkflowRunORM.org_id == org_id, WorkflowRunORM.workflow_id == workflow_id)
                .order_by(WorkflowRunORM.created_at.desc()),
            )).scalars().all()
            return [_run_to_domain(r) for r in rows]

    async def update(self, r: WorkflowRun) -> WorkflowRun:
        async with self._sm() as s:
            await s.merge(_run_to_orm(r))
            await s.commit()
        return r


class SupabasePostRepository(_Base):
    async def add(self, p: Post) -> Post:
        async with self._sm() as s:
            s.add(_post_to_orm(p))
            await s.commit()
        return p

    async def get(self, org_id: OrgId, post_id: PostId) -> Post | None:
        async with self._sm() as s:
            row = (await s.execute(
                select(PostORM).where(
                    PostORM.id == post_id, PostORM.org_id == org_id,
                ),
            )).scalar_one_or_none()
            return _post_to_domain(row) if row else None

    async def list(self, org_id: OrgId, *, status: str | None = None) -> list[Post]:
        async with self._sm() as s:
            stmt = select(PostORM).where(PostORM.org_id == org_id)
            if status:
                stmt = stmt.where(PostORM.status == status)
            stmt = stmt.order_by(PostORM.created_at.desc())
            rows = (await s.execute(stmt)).scalars().all()
            return [_post_to_domain(r) for r in rows]

    async def update(self, p: Post) -> Post:
        async with self._sm() as s:
            await s.merge(_post_to_orm(p))
            await s.commit()
        return p

    async def delete(self, org_id: OrgId, post_id: PostId) -> None:
        async with self._sm() as s:
            await s.execute(
                delete(PostORM).where(
                    PostORM.id == post_id,
                    PostORM.org_id == org_id,
                ),
            )
            await s.commit()


class SupabaseUserRepository(_Base):
    async def get(self, user_id: UserId) -> User | None:
        async with self._sm() as s:
            row = (await s.execute(
                select(UserORM).where(UserORM.id == user_id),
            )).scalar_one_or_none()
            return _user_to_domain(row) if row else None

    async def get_by_subject(self, subject: str) -> User | None:
        async with self._sm() as s:
            row = (await s.execute(
                select(UserORM).where(
                    (UserORM.okta_subject == subject) | (UserORM.supabase_uid == subject),
                ),
            )).scalar_one_or_none()
            return _user_to_domain(row) if row else None

    async def upsert(self, user: User) -> User:
        async with self._sm() as s:
            orm = UserORM(
                id=user.id, org_id=user.org_id, email=user.email,
                display_name=user.display_name, role=user.role.value,
                okta_subject=user.okta_subject, is_active=user.is_active,
            )
            await s.merge(orm)
            await s.commit()
        return user


def _user_to_domain(orm: UserORM) -> User:
    return User(
        id=UserId(orm.id), org_id=OrgId(orm.org_id), email=orm.email,
        display_name=orm.display_name, role=Role(orm.role),
        okta_subject=orm.okta_subject or "", is_active=orm.is_active,
        created_at=orm.created_at,
    )


# ── Trigger / ReviewSession concretions ────────────────────────────────────
from app.domain.entities.review_session import ReviewSession, ReviewStatus
from app.domain.entities.trigger import Trigger, TriggerKind
from app.domain.value_objects.ids import ReviewId, TriggerId
from app.infrastructure.db.models import ReviewSessionORM, TriggerORM


def _trigger_to_domain(orm: TriggerORM) -> Trigger:
    return Trigger(
        id=TriggerId(orm.id), org_id=OrgId(orm.org_id),
        workflow_id=WorkflowId(orm.workflow_id),
        plugin_name=orm.plugin_name, display_name=orm.display_name,
        kind=TriggerKind(orm.kind), is_active=orm.is_active,
        config=dict(orm.config or {}),
        allowed_senders=list(orm.allowed_senders or []),
        review_channel=orm.review_channel,
        review_recipient=orm.review_recipient,
        created_at=orm.created_at, last_fired_at=orm.last_fired_at,
    )


def _trigger_to_orm(d: Trigger) -> TriggerORM:
    return TriggerORM(
        id=d.id, org_id=d.org_id, workflow_id=d.workflow_id,
        plugin_name=d.plugin_name, display_name=d.display_name,
        kind=d.kind.value, is_active=d.is_active, config=d.config,
        allowed_senders=list(d.allowed_senders),
        review_channel=d.review_channel, review_recipient=d.review_recipient,
        last_fired_at=d.last_fired_at,
    )


def _review_to_domain(orm: ReviewSessionORM) -> ReviewSession:
    from app.domain.entities.review_session import QuorumVote
    raw_votes = list(orm.quorum_votes or [])
    votes: list[QuorumVote] = []
    for v in raw_votes:
        if not isinstance(v, dict):
            continue
        votes.append(QuorumVote(
            actor_id=str(v.get("actor_id", "")),
            actor_handle=v.get("actor_handle"),
            kind=str(v.get("kind", "")),
            at=str(v.get("at", "")),
        ))
    return ReviewSession(
        id=ReviewId(orm.id), org_id=OrgId(orm.org_id),
        workflow_id=WorkflowId(orm.workflow_id), run_id=RunId(orm.run_id),
        channel=orm.channel, recipient=orm.recipient,
        status=ReviewStatus(orm.status),
        drafts_snapshot=list(orm.drafts_snapshot or []),
        sent_message_ref=orm.sent_message_ref,
        decision_at=orm.decision_at,
        feedback=orm.feedback, expires_at=orm.expires_at,
        created_at=orm.created_at,
        quorum_required=int(getattr(orm, "quorum_required", 1) or 1),
        quorum_votes=votes,
    )


def _review_to_orm(d: ReviewSession) -> ReviewSessionORM:
    return ReviewSessionORM(
        id=d.id, org_id=d.org_id, workflow_id=d.workflow_id,
        run_id=d.run_id, channel=d.channel, recipient=d.recipient,
        status=d.status.value, drafts_snapshot=list(d.drafts_snapshot),
        sent_message_ref=d.sent_message_ref,
        decision_at=d.decision_at, feedback=d.feedback,
        expires_at=d.expires_at,
        quorum_required=int(d.quorum_required or 1),
        quorum_votes=[
            {"actor_id": v.actor_id, "actor_handle": v.actor_handle,
             "kind": v.kind, "at": v.at}
            for v in d.quorum_votes
        ],
    )


class SupabaseTriggerRepository(_Base):
    async def add(self, t: Trigger) -> Trigger:
        async with self._sm() as s:
            s.add(_trigger_to_orm(t))
            await s.commit()
        return t

    async def get(self, org_id: OrgId, trigger_id: TriggerId) -> Trigger | None:
        async with self._sm() as s:
            row = (await s.execute(
                select(TriggerORM).where(
                    TriggerORM.id == trigger_id, TriggerORM.org_id == org_id,
                ),
            )).scalar_one_or_none()
            return _trigger_to_domain(row) if row else None

    async def get_any(self, trigger_id: TriggerId) -> Trigger | None:
        async with self._sm() as s:
            row = (await s.execute(
                select(TriggerORM).where(TriggerORM.id == trigger_id),
            )).scalar_one_or_none()
            return _trigger_to_domain(row) if row else None

    async def list(self, org_id: OrgId, *, plugin_name: str | None = None) -> list[Trigger]:
        async with self._sm() as s:
            stmt = select(TriggerORM).where(TriggerORM.org_id == org_id)
            if plugin_name:
                stmt = stmt.where(TriggerORM.plugin_name == plugin_name)
            rows = (await s.execute(stmt)).scalars().all()
            return [_trigger_to_domain(r) for r in rows]

    async def update(self, t: Trigger) -> Trigger:
        async with self._sm() as s:
            await s.merge(_trigger_to_orm(t))
            await s.commit()
        return t


class SupabaseReviewSessionRepository(_Base):
    async def add(self, r: ReviewSession) -> ReviewSession:
        async with self._sm() as s:
            s.add(_review_to_orm(r))
            await s.commit()
        return r

    async def get(self, org_id: OrgId, review_id: ReviewId) -> ReviewSession | None:
        async with self._sm() as s:
            row = (await s.execute(
                select(ReviewSessionORM).where(
                    ReviewSessionORM.id == review_id,
                    ReviewSessionORM.org_id == org_id,
                ),
            )).scalar_one_or_none()
            return _review_to_domain(row) if row else None

    async def get_by_message_ref(self, channel: str, ref: str) -> ReviewSession | None:
        async with self._sm() as s:
            row = (await s.execute(
                select(ReviewSessionORM).where(
                    ReviewSessionORM.channel == channel,
                    ReviewSessionORM.sent_message_ref == ref,
                ),
            )).scalar_one_or_none()
            return _review_to_domain(row) if row else None

    async def latest_pending_for(self, channel: str, sender: str) -> ReviewSession | None:
        async with self._sm() as s:
            row = (await s.execute(
                select(ReviewSessionORM)
                .where(
                    ReviewSessionORM.channel == channel,
                    ReviewSessionORM.recipient == sender,
                    ReviewSessionORM.status == "pending",
                )
                .order_by(ReviewSessionORM.created_at.desc())
                .limit(1),
            )).scalar_one_or_none()
            return _review_to_domain(row) if row else None

    async def list_open(self, org_id: OrgId) -> list[ReviewSession]:
        async with self._sm() as s:
            rows = (await s.execute(
                select(ReviewSessionORM).where(
                    ReviewSessionORM.org_id == org_id,
                    ReviewSessionORM.status == "pending",
                ).order_by(ReviewSessionORM.created_at.desc()),
            )).scalars().all()
            return [_review_to_domain(r) for r in rows]

    async def update(self, r: ReviewSession) -> ReviewSession:
        async with self._sm() as s:
            await s.merge(_review_to_orm(r))
            await s.commit()
        return r
