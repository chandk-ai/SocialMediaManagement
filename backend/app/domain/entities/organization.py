from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..value_objects.ids import OrgId, new_id


class IsolationLevel(str, Enum):
    """Tenant isolation strategy.

    SHARED         — pool model: shared Supabase Postgres, RLS-scoped.
                     Default for SMB. Cheapest + simplest to operate.
    DEDICATED_DB   — silo at the data layer: this org gets its own Postgres
                     instance (own connection string, own backups, own KMS key).
                     Compute (FastAPI + workers) is still shared.
    DEDICATED_STACK— full silo: dedicated DB + queue + workers + storage bucket.
                     Used for the most demanding regulatory / SLA needs.
    """
    SHARED = "shared"
    DEDICATED_DB = "dedicated_db"
    DEDICATED_STACK = "dedicated_stack"


@dataclass(slots=True)
class Organization:
    """Tenant boundary.

    Multiple `Organization`s share a single deployment by default; an org
    can be promoted to dedicated infra by changing `isolation_level` and
    pointing `dedicated_db_url` etc. at the new resources.
    """
    id: OrgId
    name: str
    slug: str
    okta_org_id: str | None = None
    monthly_llm_budget_usd: float = 100.0

    # ── tenancy ──────────────────────────────────────────────────────
    isolation_level: IsolationLevel = IsolationLevel.SHARED
    region: str = "us-east-1"        # pin data residency
    # When isolation_level >= DEDICATED_DB, these override the shared values.
    dedicated_db_url: str | None = None
    storage_prefix: str | None = None     # bucket folder or whole bucket
    encryption_key_id: str | None = None  # KMS key id (BYOK)
    # Per-tenant resource limits.
    max_concurrent_runs: int = 5
    rate_limit_per_minute: int | None = None    # None = inherit global
    plan: str = "starter"            # starter | growth | enterprise

    created_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def is_dedicated(self) -> bool:
        return self.isolation_level is not IsolationLevel.SHARED

    @classmethod
    def create(
        cls, *, name: str, slug: str, okta_org_id: str | None = None,
        isolation_level: IsolationLevel = IsolationLevel.SHARED,
        region: str = "us-east-1", plan: str = "starter",
    ) -> "Organization":
        return cls(
            id=OrgId(new_id()), name=name, slug=slug,
            okta_org_id=okta_org_id, isolation_level=isolation_level,
            region=region, plan=plan,
        )
