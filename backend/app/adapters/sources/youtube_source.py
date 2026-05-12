"""YouTube source — list channel videos and extract their transcripts."""
from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator

import httpx

from app.core.logging import get_logger
from app.domain.entities.source import SourceItem
from app.domain.value_objects.content import MediaAsset, MediaKind
from app.plugins.registry import register_plugin
from app.services.media_import import MediaImportError, MediaImportService

from .base import ContentSource, SourceConnectionError

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
            # When true, each video's MP4 is downloaded via yt-dlp and
            # uploaded to Supabase Storage so it can be republished to
            # other platforms (Instagram Reels, TikTok, …). Off by
            # default — pulling videos is bandwidth-heavy and isn't
            # always wanted for transcript-only "topic ideas" use.
            "download_video": {"type": "boolean", "default": False,
                               "title": "Download video file (Reels-ready MP4)"},
            "max_video_height": {"type": "integer", "minimum": 360,
                                 "maximum": 1080, "default": 1080,
                                 "title": "Max video height for download (px)"},
        },
    }

    async def connect(self) -> None:
        api_key = self.config.get("api_key", "")
        channel_id = self.config.get("channel_id", "")
        if not api_key:
            raise SourceConnectionError(
                "YouTube Data API v3 key is required. "
                "Get one in Google Cloud Console → APIs & Services → Credentials.",
            )
        if not channel_id.startswith("UC") or len(channel_id) < 10:
            raise SourceConnectionError(
                "channel_id should start with 'UC'. You can find it on the channel's "
                "About page → Share channel → Copy channel ID.",
            )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    "https://www.googleapis.com/youtube/v3/channels",
                    params={"key": api_key, "id": channel_id, "part": "id"},
                )
                if r.status_code == 403:
                    raise SourceConnectionError(
                        "API key rejected (403). Check that YouTube Data API v3 "
                        "is enabled for this key and your daily quota isn't exhausted.",
                    )
                r.raise_for_status()
                if not r.json().get("items"):
                    raise SourceConnectionError(f"channel {channel_id!r} not found")
        except httpx.HTTPError as exc:
            raise SourceConnectionError(str(exc)) from exc

    async def fetch(self, since: datetime | None = None) -> AsyncIterator[SourceItem]:
        videos = await self._list_videos()
        org_id = str(self.config.get("__org_id__") or "shared")
        download_video = bool(self.config.get("download_video"))
        max_height = int(self.config.get("max_video_height", 1080))
        importer = MediaImportService()

        for v in videos:
            transcript = self._fetch_transcript(v["id"])
            published = datetime.fromisoformat(v["publishedAt"].replace("Z", "+00:00"))
            if since and published <= since:
                continue
            watch_url = f"https://www.youtube.com/watch?v={v['id']}"
            media = await self._import_video_assets(
                importer=importer, org_id=org_id,
                video_id=v["id"], title=v["title"],
                thumbnail_url=v.get("thumbnail_url"),
                download_video=download_video, max_height=max_height,
                watch_url=watch_url,
            )
            yield SourceItem(
                external_id=v["id"],
                title=v["title"],
                body=transcript or v.get("description", ""),
                url=watch_url,
                published_at=published,
                media=media,
                metadata={"channel_id": self.config["channel_id"]},
            )

    async def _import_video_assets(
        self, *, importer: MediaImportService, org_id: str,
        video_id: str, title: str,
        thumbnail_url: str | None,
        download_video: bool, max_height: int,
        watch_url: str,
    ) -> tuple[MediaAsset, ...]:
        """Yield up to two assets per video: the MP4 (if download_video
        is on) and the thumbnail. Failures are tolerated — a missing
        thumbnail shouldn't block the source iteration, and a failed
        yt-dlp download shouldn't block the transcript-only path."""
        assets: list[MediaAsset] = []

        if download_video:
            try:
                vid = await importer.import_via_ytdlp(
                    org_id=org_id, url=watch_url,
                    max_height=max_height, title_hint=title,
                )
                assets.append(MediaAsset(url=vid.url, kind=MediaKind.VIDEO))
            except MediaImportError as exc:
                log.info("youtube_video_import_skipped",
                         video_id=video_id, error=str(exc))

        if thumbnail_url:
            try:
                thumb = await importer.import_url(
                    org_id=org_id, url=thumbnail_url,
                    filename_hint=f"yt-{video_id}-thumb",
                )
                assets.append(MediaAsset(
                    url=thumb.url, kind=MediaKind.IMAGE,
                    alt_text=f"YouTube thumbnail: {title}",
                ))
            except MediaImportError as exc:
                log.info("youtube_thumbnail_import_skipped",
                         video_id=video_id, error=str(exc))

        return tuple(assets)

    async def _list_videos(self) -> list[dict[str, Any]]:
        api_key = self.config.get("api_key", "")
        if not api_key:
            return []
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
        out = []
        for item in data.get("items", []):
            vid = item.get("id", {}).get("videoId")
            if not vid:
                continue
            snip = item.get("snippet", {}) or {}
            thumbs = snip.get("thumbnails", {}) or {}
            # YouTube serves several thumbnail sizes — prefer ``high``
            # (480x360 in practice) for IG / Reels framing. Fall back
            # to medium then default if the channel doesn't expose
            # higher resolutions.
            thumbnail_url = (
                (thumbs.get("high") or thumbs.get("medium")
                 or thumbs.get("default") or {})
                .get("url")
            )
            out.append({
                "id": vid,
                "title": snip.get("title", ""),
                "description": snip.get("description", ""),
                "publishedAt": snip.get("publishedAt", ""),
                "thumbnail_url": thumbnail_url,
            })
        return out

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
