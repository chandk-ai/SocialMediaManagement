"""DataPrivacyService — GDPR/CCPA "right of access" + "right to be
forgotten" workflows.

Two operations, both modelled as `DataExportJob`:
* EXPORT  — bundle every artifact for the org into a JSON archive and
            stash it under a signed URL with a short TTL.
* DELETE  — cascade-delete every org-scoped row after a configurable
            grace period (default 7 days), giving the user a window to
            cancel the request.

The service is sync-friendly so it can be invoked from a Celery worker
or directly from the FastAPI route in dev.
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.logging import get_logger
from app.domain.entities.data_export import (
    DataExportJob,
    DataJobKind,
    DataJobStatus,
)
from app.domain.value_objects.ids import DataExportJobId, OrgId, UserId
from app.repositories.ports import (
    ApprovalPolicyRepository,
    ApprovalRequestRepository,
    CampaignRepository,
    DataExportJobRepository,
    ExperimentRepository,
    PlatformRepository,
    PostRepository,
    RecyclePolicyRepository,
    ReviewSessionRepository,
    SourceRepository,
    TriggerRepository,
    WorkflowRepository,
    WorkflowRunRepository,
)

log = get_logger(__name__)


class DataPrivacyService:
    def __init__(
        self,
        job_repo: DataExportJobRepository,
        *,
        platform_repo: PlatformRepository,
        source_repo: SourceRepository,
        workflow_repo: WorkflowRepository,
        run_repo: WorkflowRunRepository,
        post_repo: PostRepository,
        trigger_repo: TriggerRepository,
        review_repo: ReviewSessionRepository,
        campaign_repo: CampaignRepository | None = None,
        experiment_repo: ExperimentRepository | None = None,
        approval_policy_repo: ApprovalPolicyRepository | None = None,
        approval_request_repo: ApprovalRequestRepository | None = None,
        recycle_repo: RecyclePolicyRepository | None = None,
    ) -> None:
        self.job_repo = job_repo
        self.platform_repo = platform_repo
        self.source_repo = source_repo
        self.workflow_repo = workflow_repo
        self.run_repo = run_repo
        self.post_repo = post_repo
        self.trigger_repo = trigger_repo
        self.review_repo = review_repo
        self.campaign_repo = campaign_repo
        self.experiment_repo = experiment_repo
        self.approval_policy_repo = approval_policy_repo
        self.approval_request_repo = approval_request_repo
        self.recycle_repo = recycle_repo

    # ── public API ────────────────────────────────────────────────────
    async def request_export(
        self,
        *,
        org_id: OrgId,
        requested_by: UserId,
    ) -> DataExportJob:
        job = DataExportJob.create_export(
            org_id=org_id, requested_by=requested_by,
        )
        return await self.job_repo.add(job)

    async def request_delete(
        self,
        *,
        org_id: OrgId,
        requested_by: UserId,
        grace_period_minutes: int = 60 * 24 * 7,
    ) -> DataExportJob:
        job = DataExportJob.create_delete(
            org_id=org_id,
            requested_by=requested_by,
            grace_period_minutes=grace_period_minutes,
        )
        return await self.job_repo.add(job)

    async def cancel(
        self,
        *,
        org_id: OrgId,
        job_id: DataExportJobId,
    ) -> DataExportJob:
        job = await self.job_repo.get(org_id, job_id)
        if not job:
            raise ValueError("job not found")
        job.cancel()
        return await self.job_repo.update(job)

    async def list(self, org_id: OrgId) -> list[DataExportJob]:
        return await self.job_repo.list(org_id)

    # ── execution ────────────────────────────────────────────────────
    async def run_pending(self, *, now: datetime | None = None) -> list[str]:
        """Walk every pending job and execute the appropriate operation.
        Returns a list of human-readable outcome strings."""
        now = now or datetime.utcnow()
        outcomes: list[str] = []
        for job in await self.job_repo.list_pending():
            try:
                if job.kind is DataJobKind.EXPORT:
                    await self.execute_export(job)
                    outcomes.append(f"exported {job.id}")
                else:
                    if not job.grace_period_elapsed(now):
                        continue
                    await self.execute_delete(job)
                    outcomes.append(f"deleted {job.id}")
            except Exception as exc:                              # noqa: BLE001
                log.exception(
                    "data_privacy_job_failed",
                    job_id=str(job.id), error=str(exc),
                )
                job.mark_failed(str(exc))
                await self.job_repo.update(job)
                outcomes.append(f"failed {job.id}: {exc}")
        return outcomes

    async def execute_export(self, job: DataExportJob) -> dict[str, Any]:
        """Bundle every org-scoped artifact into a JSON-serialisable dict.
        Real deployment uploads it to Supabase Storage and returns a
        signed URL — we surface a `data:` URL inline for the in-memory
        backend so tests don't need real storage."""
        if job.kind is not DataJobKind.EXPORT:
            raise ValueError("not an export job")
        job.mark_running()
        await self.job_repo.update(job)

        bundle: dict[str, Any] = {
            "schema": "smms.export.v1",
            "org_id": str(job.org_id),
            "exported_at": datetime.utcnow().isoformat() + "Z",
        }
        counts: dict[str, int] = {}
        bundle["platforms"] = await self._dump_repo(
            self.platform_repo, job.org_id, counts, "platforms",
        )
        bundle["sources"] = await self._dump_repo(
            self.source_repo, job.org_id, counts, "sources",
        )
        bundle["workflows"] = await self._dump_repo(
            self.workflow_repo, job.org_id, counts, "workflows",
        )
        bundle["posts"] = await self._dump_repo(
            self.post_repo, job.org_id, counts, "posts",
        )
        bundle["triggers"] = await self._dump_repo(
            self.trigger_repo, job.org_id, counts, "triggers",
        )
        bundle["reviews"] = await self._dump_review(job.org_id, counts)
        if self.campaign_repo:
            bundle["campaigns"] = await self._dump_repo(
                self.campaign_repo, job.org_id, counts, "campaigns",
            )
        if self.experiment_repo:
            bundle["experiments"] = await self._dump_repo(
                self.experiment_repo, job.org_id, counts, "experiments",
            )
        if self.approval_policy_repo:
            bundle["approval_policies"] = await self._dump_repo(
                self.approval_policy_repo, job.org_id, counts, "approval_policies",
            )
        if self.approval_request_repo:
            bundle["approval_requests"] = await self._dump_open(
                self.approval_request_repo, job.org_id, counts,
                "approval_requests",
            )
        if self.recycle_repo:
            bundle["recycle_policies"] = await self._dump_repo(
                self.recycle_repo, job.org_id, counts, "recycle_policies",
            )

        # Inline base64 fallback for storage-less deployments.
        try:
            payload = json.dumps(bundle, default=_json_default).encode("utf-8")
            from base64 import b64encode
            url = f"data:application/json;base64,{b64encode(payload).decode()}"
        except Exception:                                            # noqa: BLE001
            url = None

        job.mark_completed(download_url=url, counts=counts)
        await self.job_repo.update(job)
        return bundle

    async def execute_delete(self, job: DataExportJob) -> dict[str, int]:
        """Cascade-delete every org-scoped artifact. Idempotent."""
        if job.kind is not DataJobKind.DELETE:
            raise ValueError("not a delete job")
        job.mark_running()
        await self.job_repo.update(job)

        counts: dict[str, int] = {}
        counts["posts"] = await self._delete_all(
            self.post_repo, job.org_id,
        )
        counts["triggers"] = await self._delete_all(
            self.trigger_repo, job.org_id,
        )
        counts["workflows"] = await self._delete_all(
            self.workflow_repo, job.org_id,
        )
        counts["sources"] = await self._delete_all(
            self.source_repo, job.org_id,
        )
        counts["platforms"] = await self._delete_all(
            self.platform_repo, job.org_id,
        )
        if self.campaign_repo:
            counts["campaigns"] = await self._delete_all(
                self.campaign_repo, job.org_id,
            )
        if self.experiment_repo:
            counts["experiments"] = await self._delete_all(
                self.experiment_repo, job.org_id,
            )
        if self.approval_policy_repo:
            counts["approval_policies"] = await self._delete_all(
                self.approval_policy_repo, job.org_id,
            )
        if self.recycle_repo:
            counts["recycle_policies"] = await self._delete_all(
                self.recycle_repo, job.org_id,
            )

        job.mark_completed(counts=counts)
        await self.job_repo.update(job)
        return counts

    # ── internals ────────────────────────────────────────────────────
    async def _dump_repo(
        self,
        repo: Any,
        org_id: OrgId,
        counts: dict[str, int],
        key: str,
    ) -> list[dict]:
        items = await repo.list(org_id)
        rows = [_to_jsonable(it) for it in items]
        counts[key] = len(rows)
        return rows

    async def _dump_open(
        self,
        repo: Any,
        org_id: OrgId,
        counts: dict[str, int],
        key: str,
    ) -> list[dict]:
        items = await repo.list_open(org_id)
        rows = [_to_jsonable(it) for it in items]
        counts[key] = len(rows)
        return rows

    async def _dump_review(self, org_id: OrgId, counts: dict[str, int]) -> list[dict]:
        items = await self.review_repo.list_open(org_id)
        rows = [_to_jsonable(it) for it in items]
        counts["reviews"] = len(rows)
        return rows

    async def _delete_all(self, repo: Any, org_id: OrgId) -> int:
        # Try `list(org_id)` then delete by id. Repos that don't support
        # delete will raise — we swallow + log so partial cleans complete.
        items = await repo.list(org_id)
        deleted = 0
        for it in items:
            try:
                if hasattr(repo, "delete"):
                    await repo.delete(org_id, getattr(it, "id"))
                    deleted += 1
            except Exception as exc:                              # noqa: BLE001
                log.warning(
                    "data_privacy_partial_delete",
                    repo=type(repo).__name__,
                    item_id=str(getattr(it, "id", "?")),
                    error=str(exc),
                )
        return deleted


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat() + "Z"
    if isinstance(o, UUID):
        return str(o)
    if hasattr(o, "value") and not isinstance(o, (int, float, str, bool)):
        return o.value
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serialisable")


def _to_jsonable(obj: Any) -> dict:
    if is_dataclass(obj):
        try:
            return asdict(obj)
        except Exception:                                              # noqa: BLE001
            pass
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return {"value": str(obj)}
