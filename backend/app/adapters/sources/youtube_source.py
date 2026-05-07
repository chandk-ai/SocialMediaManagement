"""YouTube source — list channel videos and extract their transcripts."""
from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator

from app.core.logging import get_logger
from app.domain.entities.source import SourceItem
from app.plugins.registry import register_plugin

from .base import ContentSource

log = get_logger(__name__)


@register_plugin("source", "youtube", api_version="1.0")
class YouTubeSource(ContentSource):
    display_name = "YouTube channel"
    description = "Pulls a channel's recent videos + transcript."
    config_schema = {
        "type": "object",
        "required": ["channel_id"],
        "properties": {
            "channel_id":   {"type": "string", "title": "Channel ID (UC…)"},
            "api_key":      {"type": "string", "title": "YouTube Data API v3 key"},
            "max_results":  {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            "languages":    {"type": "array", "items": {"type": "string"},
                             "default": ["en"], "title": "Transcript languages"},
        },
    }

    async def connect(self) -> None:
        return None

    async def fetch(self, since: datetime | None = None) -> AsyncIterator[SourceItem]:
        videos = await self._list_videos()
        for v in videos:
            transcript = self._fetch_transcript(v["id"])
            published = datetime.fromisoformat(v["publishedAt"].replace("Z", "+00:00"))
            if since and published <= since:
                continue
            yield SourceItem(
                external_id=v["id"],
                title=v["title"],
                body=transcript or v.get("description", ""),
                url=f"https://www.youtube.com/watch?v={v['id']}",
                published_at=published,
                metadata={"channel_id": self.config["channel_id"]},
            )

    async def _list_videos(self) -> list[dict[str, Any]]:
        try:
            import httpx
        except ImportError:
            return []
        api_key = self.config.get("api_key", "")
        if not api_key:
            return [{
                "id": "stub", "title": "(YouTube stub)",
                "description": "Set api_key to fetch real videos",
                "publishedAt": datetime.utcnow().isoformat() + "Z",
            }]
        max_results = int(self.config.get("max_results", 10))
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "key": api_key, "channelId": self.config["channel_id"],
                    "part": "snippet", "order": "date", "maxResults": max_results,
                    "type": "video",
                },
            )
            r.raise_for_status()
            data = r.json()
        return [
            {
                "id": item["id"]["videoId"],
                "title": item["snippet"]["title"],
                "description": item["snippet"]["description"],
                "publishedAt": item["snippet"]["publishedAt"],
            }
            for item in data.get("items", [])
            if item.get("id", {}).get("videoId")
        ]

    def _fetch_transcript(self, video_id: str) -> str:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
        except ImportError:
            return ""
        try:
            entries = YouTubeTranscriptApi.get_transcript(
                video_id, languages=self.config.get("languages", ["en"])
            )
            return " ".join(e["text"] for e in entries)
        except Exception:                                  # noqa: BLE001
            return ""
