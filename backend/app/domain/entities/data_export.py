"""DataExportJob — GDPR/CCPA "right of access" + "right to be forgotten".

A single record represents one of two operations:
* `EXPORT`  — bundle every artifact for the org into a downloadable archive.
* `DELETE`  — cascading delete of all org-scoped data after a grace period.

The actual heavy lifting happens in `app/services/data_privacy.py` and the
async workers in `app/workers/data_privacy.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from ..value_objects.ids import (
    DataExportJobId,
    OrgId,
    UserId,
    new_id,
)


class DataJobKind(str, Enum):
    EXPORT = "export"
    DELETE = "delete"


class DataJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class DataExportJob:
    id: DataExportJobId
    org_id: OrgId
    requested_by: UserId
    kind: DataJobKind
    status: DataJobStatus = DataJobStatus.PENDING
    grace_period_minutes: int = 60 * 24 * 7      # 7 days for DELETE
    download_url: str | None = None              # populated on EXPORT success
    expires_at: datetime | None = None
    error: str | None = None
    counts: dict[str, int] = field(default_factory=dict)  # entity → row count
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    extras: dict = field(default_factory=dict)

    # ── lifecycle ─────────────────────────────────────────────────────
    def mark_running(self) -> None:
        self.status = DataJobStatus.RUNNING
        self.updated_at = datetime.utcnow()

    def mark_completed(
        self,
        *,
        download_url: str | None = None,
        counts: dict[str, int] | None = None,
        ttl_hours: int = 24 * 7,
    ) -> None:
        self.status = DataJobStatus.COMPLETED
        self.completed_at = datetime.utcnow()
        self.updated_at = self.completed_at
        if download_url is not None:
            self.download_url = download_url
            self.expires_at = self.completed_at + timedelta(hours=ttl_hours)
        if counts is not None:
            self.counts = dict(counts)

    def mark_failed(self, message: str) -> None:
        self.status = DataJobStatus.FAILED
        self.error = message
        self.updated_at = datetime.utcnow()

    def cancel(self) -> None:
        if self.status not in (DataJobStatus.PENDING, DataJobStatus.RUNNING):
            raise ValueError(f"Cannot cancel job in status {self.status.value}")
        self.status = DataJobStatus.CANCELLED
        self.cancelled_at = datetime.utcnow()
        self.updated_at = self.cancelled_at

    def grace_period_elapsed(self, now: datetime) -> bool:
        """Used by the DELETE worker to gate destruction until the grace
        window expires — gives the user a chance to cancel."""
        if self.kind is not DataJobKind.DELETE:
            return True
        elapsed = (now - self.created_at).total_seconds() / 60.0
        return elapsed >= self.grace_period_minutes

    @classmethod
    def create_export(
        cls,
        *,
        org_id: OrgId,
        requested_by: UserId,
        extras: dict | None = None,
    ) -> "DataExportJob":
        return cls(
            id=DataExportJobId(new_id()),
            org_id=org_id,
            requested_by=requested_by,
            kind=DataJobKind.EXPORT,
            extras=dict(extras or {}),
        )

    @classmethod
    def create_delete(
        cls,
        *,
        org_id: OrgId,
        requested_by: UserId,
        grace_period_minutes: int = 60 * 24 * 7,
        extras: dict | None = None,
    ) -> "DataExportJob":
        return cls(
            id=DataExportJobId(new_id()),
            org_id=org_id,
            requested_by=requested_by,
            kind=DataJobKind.DELETE,
            grace_period_minutes=grace_period_minutes,
            extras=dict(extras or {}),
        )
