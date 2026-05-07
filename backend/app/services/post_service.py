from __future__ import annotations

from app.domain.entities.post import Post
from app.domain.value_objects.ids import OrgId
from app.repositories.ports import PostRepository


class PostService:
    def __init__(self, repo: PostRepository) -> None:
        self.repo = repo

    async def list(self, org_id: OrgId, *, status: str | None = None) -> list[Post]:
        return await self.repo.list(org_id, status=status)
