"""Tests for multi-account workflow: many Platform rows for one plugin."""
import pytest

from app.domain.entities.organization import Organization
from app.domain.entities.platform import Platform
from app.repositories.memory import InMemoryPlatformRepository
from app.services.platform_service import PlatformService


@pytest.mark.asyncio
async def test_multiple_accounts_per_plugin_are_grouped():
    org = Organization.create(name="Acme", slug="acme")
    repo = InMemoryPlatformRepository()
    svc = PlatformService(repo)

    # Three Instagram accounts + one LinkedIn
    await svc.create(org_id=org.id, plugin_name="instagram",
                     display_name="Acme · Brand IG", account_handle="@acme",
                     is_default=True)
    await svc.create(org_id=org.id, plugin_name="instagram",
                     display_name="Acme · Marketing IG", account_handle="@acme-mkt")
    await svc.create(org_id=org.id, plugin_name="instagram",
                     display_name="Acme · Careers IG", account_handle="@acme-careers")
    await svc.create(org_id=org.id, plugin_name="linkedin",
                     display_name="Acme Page LI", account_handle="acme")

    grouped = await svc.grouped(org.id)
    assert set(grouped.keys()) == {"instagram", "linkedin"}
    assert len(grouped["instagram"]) == 3
    assert len(grouped["linkedin"]) == 1
    handles = sorted(p.account_handle for p in grouped["instagram"])
    assert handles == ["@acme", "@acme-careers", "@acme-mkt"]
    assert sum(p.is_default for p in grouped["instagram"]) == 1
