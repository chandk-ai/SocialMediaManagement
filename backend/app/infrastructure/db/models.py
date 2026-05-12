"""SQLAlchemy ORM models that mirror the Supabase schema in 001_init.sql.

These are *infrastructure* — the domain layer never sees them. Repositories
translate between ORM rows and domain entities at the boundary.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SCHEMA = "smms"


class Base(DeclarativeBase):
    metadata_schema = SCHEMA


class OrganizationORM(Base):
    __tablename__ = "organizations"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    okta_org_id: Mapped[str | None] = mapped_column(String)
    monthly_llm_budget_usd: Mapped[float] = mapped_column(Numeric, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserORM(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True),
                                          ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    okta_subject: Mapped[str | None] = mapped_column(String)
    supabase_uid: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlatformORM(Base):
    __tablename__ = "platforms"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True),
                                          ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"))
    plugin_name: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    account_handle: Mapped[str | None] = mapped_column(String)
    account_external_id: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="disconnected")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    credentials: Mapped["PlatformCredentialsORM | None"] = relationship(
        "PlatformCredentialsORM", uselist=False, lazy="joined", cascade="all, delete-orphan",
    )


class PlatformCredentialsORM(Base):
    __tablename__ = "platform_credentials"
    __table_args__ = {"schema": SCHEMA}

    platform_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.platforms.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ciphertext: Mapped[bytes] = mapped_column(nullable=False)
    key_id: Mapped[str] = mapped_column(String, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceORM(Base):
    __tablename__ = "sources"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True),
                                          ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"))
    plugin_name: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkflowORM(Base):
    __tablename__ = "workflows"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True),
                                          ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String, default="draft")
    source_ids: Mapped[list[UUID]] = mapped_column(ARRAY(PG_UUID(as_uuid=True)), default=list)
    platform_ids: Mapped[list[UUID]] = mapped_column(ARRAY(PG_UUID(as_uuid=True)), default=list)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    schedule: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Stamped by the scheduler tick after each fire. Used by _is_due()
    # as the base for CRON / INTERVAL / OPTIMAL "has enough time passed?"
    # checks. NULL means "never fired" — _is_due() falls back to
    # updated_at to anchor the first eligibility check, then stamps
    # this on the first actual fire.
    last_fired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


class WorkflowRunORM(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True),
                                          ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"))
    workflow_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True),
                                               ForeignKey(f"{SCHEMA}.workflows.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String, default="queued")
    revision_count: Mapped[int] = mapped_column(Integer, default=0)
    trace: Mapped[list] = mapped_column(JSONB, default=list)
    # Phase state for the durable runner (selected_items, plan,
    # drafts, critique decisions, post_ids). Added in migration 013
    # — without it, every cross-phase write vanishes silently and
    # the runner ends with "no posts produced" despite all 5 phase
    # jobs succeeding.
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TriggerORM(Base):
    __tablename__ = "triggers"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True),
                                          ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"))
    workflow_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True),
                                               ForeignKey(f"{SCHEMA}.workflows.id", ondelete="CASCADE"))
    plugin_name: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    allowed_senders: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    review_channel: Mapped[str | None] = mapped_column(String)
    review_recipient: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewSessionORM(Base):
    __tablename__ = "review_sessions"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True),
                                          ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"))
    workflow_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True),
                                               ForeignKey(f"{SCHEMA}.workflows.id", ondelete="CASCADE"))
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True),
                                          ForeignKey(f"{SCHEMA}.workflow_runs.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String, nullable=False)
    recipient: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending")
    drafts_snapshot: Mapped[list] = mapped_column(JSONB, default=list)
    sent_message_ref: Mapped[str | None] = mapped_column(String)
    decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    feedback: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Group-quorum support — see migrations/006_review_quorum.sql.
    quorum_required: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    quorum_votes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)


class PostORM(Base):
    __tablename__ = "posts"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True),
                                          ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"))
    workflow_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True),
                                               ForeignKey(f"{SCHEMA}.workflows.id", ondelete="CASCADE"))
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True),
                                          ForeignKey(f"{SCHEMA}.workflow_runs.id", ondelete="CASCADE"))
    platform_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True),
                                               ForeignKey(f"{SCHEMA}.platforms.id"))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    hashtags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    media: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String, default="draft")
    evaluation: Mapped[dict | None] = mapped_column(JSONB)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_post_id: Mapped[str | None] = mapped_column(String)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # CMS-mode (Niche #3) — link back to the source row that authored this post.
    source_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
