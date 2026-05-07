"""Notion source — pulls pages from a Notion database."""
from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator

from app.domain.entities.source import SourceItem
from app.plugins.registry import register_plugin

from .base import ContentSource, SourceConnectionError


@register_plugin("source", "notion", api_version="1.0")
class NotionSource(ContentSource):
    display_name = "Notion database"
    description = "Pulls pages from a Notion database via the Notion API."
    config_schema = {
        "type": "object",
        "required": ["database_id", "api_token"],
        "properties": {
            "api_token":  {"type": "string", "title": "Notion integration token"},
            "database_id":{"type": "string", "title": "Database ID"},
            "title_property": {"type": "string", "default": "Name"},
            "page_size":  {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
        },
    }

    async def connect(self) -> None:
        if not self.config.get("api_token"):
            raise SourceConnectionError("api_token required")

    async def fetch(self, since: datetime | None = None) -> AsyncIterator[SourceItem]:
        try:
            import httpx
        except ImportError:
            return
        headers = {
            "Authorization": f"Bearer {self.config['api_token']}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {"page_size": int(self.config.get("page_size", 50))}
        if since:
            body["filter"] = {
                "timestamp": "last_edited_time",
                "last_edited_time": {"after": since.isoformat()},
            }
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                f"https://api.notion.com/v1/databases/{self.config['database_id']}/query",
                json=body, headers=headers,
            )
            r.raise_for_status()
            data = r.json()
        title_prop = self.config.get("title_property", "Name")
        for page in data.get("results", []):
            title = _extract_title(page.get("properties", {}), title_prop)
            yield SourceItem(
                external_id=page["id"],
                title=title,
                body=_render_page_text(page),
                url=page.get("url"),
                published_at=datetime.fromisoformat(
                    page["last_edited_time"].replace("Z", "+00:00")
                ),
                metadata={k: _flatten(v) for k, v in page.get("properties", {}).items()},
            )


def _extract_title(props: dict, key: str) -> str:
    item = props.get(key) or next(iter(props.values()), {})
    if not isinstance(item, dict):
        return ""
    title_arr = item.get("title") or []
    return "".join(t.get("plain_text", "") for t in title_arr)


def _flatten(prop: Any) -> Any:
    if isinstance(prop, dict):
        for k in ("rich_text", "title"):
            if k in prop and isinstance(prop[k], list):
                return "".join(t.get("plain_text", "") for t in prop[k])
        if "select" in prop and prop["select"]:
            return prop["select"].get("name")
    return prop


def _render_page_text(page: dict) -> str:
    parts: list[str] = []
    for k, v in (page.get("properties") or {}).items():
        f = _flatten(v)
        if isinstance(f, str) and f.strip():
            parts.append(f"{k}: {f}")
    return "\n".join(parts)
