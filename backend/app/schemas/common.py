from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True, extra="forbid")


class Page(APIModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    page_size: int = 20


class IdResponse(APIModel):
    id: UUID
    created_at: datetime | None = None
