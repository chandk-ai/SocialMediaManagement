"""SQLAlchemy 2.x async repository implementations.

Stub: showcases the mapping pattern. Production deployments should fill in
the ORM models in `infrastructure/db/models.py` and Alembic migrations,
then wire these classes through the DI container.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


class SQLAlchemyRepoBase:
    """Common machinery: org-scoping, eager-loading helpers, audit hooks."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _commit(self) -> None:
        await self.session.commit()

    @staticmethod
    def _scoped(query, org_id: Any):
        return query.where(query.column_descriptions[0]["entity"].org_id == org_id)


# Concrete classes (PlatformRepo, SourceRepo, ...) follow the same pattern
# as the in-memory versions above. See `infrastructure/db/models.py` for the
# ORM mapping declarations.
