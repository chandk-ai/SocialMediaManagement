"""MongoDB source — reads documents from a collection."""
from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator

from app.domain.entities.source import SourceItem
from app.plugins.registry import register_plugin

from .base import ContentSource, SourceConnectionError


@register_plugin("source", "mongodb", api_version="1.0")
class MongoDBSource(ContentSource):
    display_name = "MongoDB"
    description = "Pulls documents from a MongoDB collection."
    config_schema = {
        "type": "object",
        "required": ["uri", "database", "collection"],
        "properties": {
            "uri":         {"type": "string", "title": "Mongo URI"},
            "database":    {"type": "string", "title": "Database"},
            "collection":  {"type": "string", "title": "Collection"},
            "filter":      {"type": "object", "default": {}, "title": "Find filter"},
            "title_field": {"type": "string", "default": "title"},
            "body_field":  {"type": "string", "default": "body"},
            "id_field":    {"type": "string", "default": "_id"},
            "date_field":  {"type": "string", "default": "updatedAt"},
            "limit":       {"type": "integer", "minimum": 1, "maximum": 10000, "default": 200},
        },
    }

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._client: Any = None

    async def connect(self) -> None:
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
        except ImportError:
            return
        try:
            self._client = AsyncIOMotorClient(self.config["uri"], serverSelectionTimeoutMS=5000)
            await self._client.server_info()
        except Exception as exc:                          # noqa: BLE001
            raise SourceConnectionError(str(exc)) from exc

    async def fetch(self, since: datetime | None = None) -> AsyncIterator[SourceItem]:
        if self._client is None:
            yield SourceItem(
                external_id="stub-1", title="(MongoDB stub)",
                body="Install motor to query a real MongoDB.",
                url=None, published_at=datetime.utcnow(),
            )
            return
        coll = self._client[self.config["database"]][self.config["collection"]]
        flt = dict(self.config.get("filter", {}))
        date_field = self.config.get("date_field", "updatedAt")
        if since:
            flt[date_field] = {"$gt": since}
        title_f = self.config.get("title_field", "title")
        body_f = self.config.get("body_field", "body")
        id_f = self.config.get("id_field", "_id")
        async for doc in coll.find(flt).limit(int(self.config.get("limit", 200))):
            yield SourceItem(
                external_id=str(doc.get(id_f, "")),
                title=str(doc.get(title_f, "")),
                body=str(doc.get(body_f, "")),
                url=None,
                published_at=doc.get(date_field) if isinstance(doc.get(date_field), datetime) else None,
                metadata={k: v for k, v in doc.items()
                          if k not in {title_f, body_f, id_f, date_field}},
            )

    async def disconnect(self) -> None:
        if self._client is not None:
            self._client.close()
