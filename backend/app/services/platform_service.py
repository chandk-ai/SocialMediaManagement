from __future__ import annotations

from collections import defaultdict

from app.domain.entities.platform import Platform
from app.domain.value_objects.ids import OrgId
from app.repositories.ports import PlatformRepository


class PlatformService:
    def __init__(self, repo: PlatformRepository) -> None:
        self.repo = repo

    async def create(
        self, *, org_id: OrgId, plugin_name: str, display_name: str,
        account_handle: str | None = None, config: dict | None = None,
        is_default: bool = False, tags: list[str] | None = None,
    ) -> Platform:
        platform = Platform.create(
            org_id=org_id, plugin_name=plugin_name,
            display_name=display_name, account_handle=account_handle,
            config=config, is_default=is_default, tags=tags,
        )
        return await self.repo.add(platform)

    async def list(self, org_id: OrgId, *, plugin_name: str | None = None) -> list[Platform]:
        items = await self.repo.list(org_id)
        if plugin_name:
            items = [p for p in items if p.plugin_name == plugin_name]
        return items

    async def grouped(self, org_id: OrgId) -> dict[str, list[Platform]]:
        """Group accounts by plugin name — drives the multi-account UI card."""
        out: dict[str, list[Platform]] = defaultdict(list)
        for p in await self.repo.list(org_id):
            out[p.plugin_name].append(p)
        return dict(out)
