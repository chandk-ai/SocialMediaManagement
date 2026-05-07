"""GitHub source — list issues / READMEs / release notes from a repository."""
from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator

from app.domain.entities.source import SourceItem
from app.plugins.registry import register_plugin

from .base import ContentSource


@register_plugin("source", "github", api_version="1.0")
class GitHubSource(ContentSource):
    display_name = "GitHub repository"
    description = "Issues, releases, and READMEs from a public/private repo."
    config_schema = {
        "type": "object",
        "required": ["repo"],
        "properties": {
            "repo":    {"type": "string", "title": "owner/name"},
            "kind":    {"enum": ["issues", "releases", "readme"], "default": "releases"},
            "token":   {"type": "string", "title": "PAT (optional for public repos)"},
            "max_items": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
        },
    }

    async def connect(self) -> None:
        return None

    async def fetch(self, since: datetime | None = None) -> AsyncIterator[SourceItem]:
        try:
            import httpx
        except ImportError:
            return
        repo = self.config["repo"]
        kind = self.config.get("kind", "releases")
        headers = {"Accept": "application/vnd.github+json"}
        if self.config.get("token"):
            headers["Authorization"] = f"Bearer {self.config['token']}"

        async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
            if kind == "readme":
                r = await client.get(f"https://api.github.com/repos/{repo}/readme")
                r.raise_for_status()
                data = r.json()
                import base64
                yield SourceItem(
                    external_id=f"{repo}#readme",
                    title=f"{repo} README",
                    body=base64.b64decode(data["content"]).decode("utf-8", "replace"),
                    url=data.get("html_url"),
                    published_at=datetime.utcnow(),
                )
                return

            url = f"https://api.github.com/repos/{repo}/{kind}"
            r = await client.get(url, params={"per_page": int(self.config.get("max_items", 10))})
            r.raise_for_status()
            for item in r.json():
                created = datetime.fromisoformat(
                    (item.get("published_at") or item.get("updated_at") or item["created_at"])
                    .replace("Z", "+00:00")
                )
                if since and created <= since:
                    continue
                yield SourceItem(
                    external_id=str(item["id"]),
                    title=item.get("name") or item.get("title") or "",
                    body=item.get("body") or item.get("description") or "",
                    url=item.get("html_url"),
                    published_at=created,
                    metadata={"kind": kind, "tag": item.get("tag_name")},
                )
