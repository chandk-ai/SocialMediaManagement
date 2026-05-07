from __future__ import annotations

from app.domain.entities.source import Source
from app.domain.value_objects.ids import OrgId
from app.repositories.ports import SourceRepository


class SourceService:
    def __init__(self, repo: SourceRepository) -> None:
        self.repo = repo

    async def create(self, *, org_id: OrgId, plugin_name: str,
                     display_name: str, config: dict | None = None) -> Source:
        s = Source.create(org_id=org_id, plugin_name=plugin_name,
                          display_name=display_name, config=config)
        return await self.repo.add(s)

    async def list(self, org_id: OrgId) -> list[Source]:
        return await self.repo.list(org_id)
