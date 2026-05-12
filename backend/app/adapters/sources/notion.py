"""Notion source — pulls pages from a Notion database.

Two modes:

* **Reference mode** (default) — every row in the database is yielded as a
  ``SourceItem`` for the planner to draw inspiration from. This is the
  "ideas board" use case: a kanban of links/notes/transcripts the planner
  uses to generate posts.

* **CMS mode** (``cms_mode=true``) — every row is itself a publishable post.
  The system reads a status field, only fetches rows the user has marked
  ready, and writes back the status + published URL after publish. This
  is the Niche #3 wedge for agencies that already manage their content
  calendar in Notion.

CMS contract — the user's Notion DB needs these fields (names configurable):

  * Title-property               — used as the post identifier (default "Name")
  * Content rich-text property   — the actual post text (config: ``content_property``)
  * Status select property       — values: Draft / Ready / Published / Failed
                                   (config: ``status_property``, default "Status")
  * Platforms multi-select       — names matching plugin_name (config: ``platforms_property``)
  * Scheduled-at date property   — optional, deferred-publish time
  * Published URL url property   — written by us after publish
  * Error rich-text property     — written by us on failure
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator

from app.core.logging import get_logger
from app.domain.entities.source import SourceItem
from app.domain.value_objects.content import MediaAsset, MediaKind
from app.plugins.registry import register_plugin
from app.services.media_import import MediaImportError, MediaImportService

from .base import ContentSource, SourceConnectionError

log = get_logger(__name__)

# Hard cap on how many media items we'll import per Notion page to keep
# any one fetch() call bounded. The first few attachments are usually
# the hero image / video and the most relevant for a post; later ones
# are typically decorative.
_MAX_MEDIA_PER_PAGE = 4


# Default property names — overridable per-source via config.
DEFAULT_STATUS_PROPERTY = "Status"
DEFAULT_CONTENT_PROPERTY = "Content"
DEFAULT_PLATFORMS_PROPERTY = "Platforms"
DEFAULT_SCHEDULED_PROPERTY = "Scheduled at"
DEFAULT_PUBLISHED_URL_PROPERTY = "Published URL"
DEFAULT_ERROR_PROPERTY = "Error"

# Status values we care about. The user can rename them on their side; if
# they do, they can override `status_ready_value` etc. in the source config.
STATUS_READY = "Ready"
STATUS_PUBLISHED = "Published"
STATUS_FAILED = "Failed"


@register_plugin("source", "notion", api_version="1.1")
class NotionSource(ContentSource):
    display_name = "Notion database"
    description = (
        "Pulls pages from a Notion database. Toggle CMS mode to turn each row "
        "into a publishable post — the system reads a status field, only "
        "publishes rows you've marked Ready, and writes back the status + "
        "URL afterwards."
    )
    config_schema = {
        "type": "object",
        "required": ["database_id", "api_token"],
        "properties": {
            "api_token":         {"type": "string", "title": "Notion integration token",
                                  "x-secret": True},
            "database_id":       {"type": "string", "title": "Database ID"},
            "title_property":    {"type": "string", "default": "Name",
                                  "title": "Title property name"},
            "page_size":         {"type": "integer", "minimum": 1, "maximum": 100,
                                  "default": 50},
            # ── CMS mode ──
            "cms_mode":          {"type": "boolean", "default": False,
                                  "title": "Treat each row as a publishable post"},
            "content_property":  {"type": "string", "default": DEFAULT_CONTENT_PROPERTY,
                                  "title": "Content rich-text property (CMS only)"},
            "status_property":   {"type": "string", "default": DEFAULT_STATUS_PROPERTY,
                                  "title": "Status select property (CMS only)"},
            "platforms_property":{"type": "string", "default": DEFAULT_PLATFORMS_PROPERTY,
                                  "title": "Platforms multi-select property (CMS only)"},
            "scheduled_property":{"type": "string", "default": DEFAULT_SCHEDULED_PROPERTY,
                                  "title": "Scheduled-at date property (CMS, optional)"},
            "published_url_property": {"type": "string",
                                       "default": DEFAULT_PUBLISHED_URL_PROPERTY,
                                       "title": "Published-URL url property (CMS only)"},
            "error_property":    {"type": "string", "default": DEFAULT_ERROR_PROPERTY,
                                  "title": "Error rich-text property (CMS, optional)"},
            "status_ready_value":     {"type": "string", "default": STATUS_READY},
            "status_published_value": {"type": "string", "default": STATUS_PUBLISHED},
            "status_failed_value":    {"type": "string", "default": STATUS_FAILED},
        },
    }

    @property
    def is_cms(self) -> bool:
        return bool(self.config.get("cms_mode"))

    async def connect(self) -> None:
        if not self.config.get("api_token"):
            raise SourceConnectionError("api_token required")

    async def fetch(self, since: datetime | None = None) -> AsyncIterator[SourceItem]:
        try:
            import httpx
        except ImportError:
            return
        headers = self._headers()
        body: dict[str, Any] = {"page_size": int(self.config.get("page_size", 50))}

        if self.is_cms:
            # CMS mode — only ready-to-publish rows. The status filter narrows
            # the pull dramatically so we don't iterate the whole calendar.
            body["filter"] = {
                "property": self.config.get("status_property", DEFAULT_STATUS_PROPERTY),
                "select": {"equals": self.config.get("status_ready_value", STATUS_READY)},
            }
        elif since:
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
            props = page.get("properties", {})
            title = _extract_title(props, title_prop)
            url = page.get("url")
            metadata = {k: _flatten(v) for k, v in props.items()}
            if self.is_cms:
                metadata.update(self._cms_metadata(props))

            # Pull child blocks → extract image / video / file references →
            # rehost in Supabase Storage so the URLs survive the ~1 hour
            # Notion signed-URL expiry. Best-effort: import failures are
            # logged but don't break the source iteration.
            media: tuple[MediaAsset, ...] = ()
            try:
                media = await self._extract_page_media(page["id"])
            except Exception as exc:                                    # noqa: BLE001
                log.warning("notion_media_extract_failed",
                            page_id=page["id"], error=str(exc))

            yield SourceItem(
                external_id=page["id"],
                title=title,
                body=self._cms_body(props) if self.is_cms else _render_page_text(page),
                url=url,
                published_at=datetime.fromisoformat(
                    page["last_edited_time"].replace("Z", "+00:00")
                ),
                media=media,
                metadata=metadata,
            )

    # ── media extraction ──────────────────────────────────────────────────
    async def _extract_page_media(self, page_id: str) -> tuple[MediaAsset, ...]:
        """Walk a Notion page's block children and return its
        image/video/file attachments as MediaAsset objects whose URLs
        point at our Supabase bucket (not the short-lived Notion URLs).

        Notion block shapes we care about:

            { type: "image",
              image: {
                type: "file",
                file: { url: "https://prod-files-secure.s3...", expiry_time: "..." }
              } }

            { type: "image",
              image: {
                type: "external",
                external: { url: "https://example.com/x.png" }
              } }

        Same shape for ``video`` and ``file``. We deliberately don't
        recurse into nested block trees here — only top-level children —
        because deep recursion would make a 50-row database fetch
        prohibitively slow on the worker. Pages with media nested deep
        in toggles can be flattened by the user.
        """
        try:
            import httpx
        except ImportError:                                           # pragma: no cover
            return ()

        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"https://api.notion.com/v1/blocks/{page_id}/children",
                params={"page_size": 100},
                headers=self._headers(),
            )
            if r.status_code >= 400:
                log.info("notion_blocks_fetch_failed",
                         page_id=page_id, status=r.status_code,
                         body=r.text[:200])
                return ()
            blocks = (r.json() or {}).get("results") or []

        # Collect (source_url, kind) pairs in document order so the
        # first image found becomes media[0] — natural mapping to "hero
        # image" for downstream platform adapters.
        candidates: list[tuple[str, MediaKind, str]] = []
        for b in blocks:
            btype = b.get("type")
            if btype not in ("image", "video", "file"):
                continue
            payload = b.get(btype) or {}
            if payload.get("type") == "file":
                src = (payload.get("file") or {}).get("url")
            elif payload.get("type") == "external":
                src = (payload.get("external") or {}).get("url")
            else:
                src = None
            if not src:
                continue
            kind = (
                MediaKind.VIDEO if btype == "video"
                else MediaKind.IMAGE if btype == "image"
                # ``file`` blocks could be anything — infer from URL
                else (MediaKind.VIDEO if _looks_like_video(src) else MediaKind.IMAGE)
            )
            alt = _extract_block_caption(payload.get("caption"))
            candidates.append((src, kind, alt))
            if len(candidates) >= _MAX_MEDIA_PER_PAGE:
                break

        if not candidates:
            return ()

        importer = MediaImportService()
        # OrgId comes through self.config["__org_id__"] when the
        # plugin host wires it; fall back to the synthetic "shared"
        # bucket key if the host didn't set it (only happens in legacy
        # CLI smoke tests). The storage path is org-scoped either way.
        org_id = str(self.config.get("__org_id__") or "shared")

        assets: list[MediaAsset] = []
        for src, kind, alt in candidates:
            try:
                result = await importer.import_url(
                    org_id=org_id, url=src,
                    filename_hint=f"notion-{kind.value}",
                )
                assets.append(MediaAsset(
                    url=result.url, kind=kind, alt_text=alt or None,
                ))
            except MediaImportError as exc:
                # One bad asset shouldn't sink the page — drop it,
                # log, keep going.
                log.info("notion_media_skipped",
                         page_id=page_id, src=src, error=str(exc))
        return tuple(assets)

    # ── CMS writeback ────────────────────────────────────────────────────
    async def mark_published(
        self,
        external_id: str,
        *,
        url: str | None = None,
        platform: str | None = None,
        published_at: datetime | None = None,
    ) -> None:
        if not self.is_cms:
            return
        props: dict[str, Any] = {
            self.config.get("status_property", DEFAULT_STATUS_PROPERTY):
                {"select": {"name": self.config.get("status_published_value",
                                                    STATUS_PUBLISHED)}},
        }
        url_prop = self.config.get("published_url_property", DEFAULT_PUBLISHED_URL_PROPERTY)
        if url and url_prop:
            props[url_prop] = {"url": url}
        await self._patch_page(external_id, props)

    async def mark_failed(
        self,
        external_id: str,
        *,
        error: str,
        platform: str | None = None,
    ) -> None:
        if not self.is_cms:
            return
        props: dict[str, Any] = {
            self.config.get("status_property", DEFAULT_STATUS_PROPERTY):
                {"select": {"name": self.config.get("status_failed_value",
                                                    STATUS_FAILED)}},
        }
        err_prop = self.config.get("error_property", DEFAULT_ERROR_PROPERTY)
        if err_prop:
            props[err_prop] = {
                "rich_text": [{"type": "text", "text": {"content": (error or "")[:1900]}}],
            }
        await self._patch_page(external_id, props)

    # ── helpers ──────────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config['api_token']}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

    async def _patch_page(self, page_id: str, properties: dict[str, Any]) -> None:
        try:
            import httpx
        except ImportError:
            log.warning("notion_writeback_no_httpx")
            return
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.patch(
                    f"https://api.notion.com/v1/pages/{page_id}",
                    json={"properties": properties},
                    headers=self._headers(),
                )
                r.raise_for_status()
        except Exception as exc:                                        # noqa: BLE001
            # Writeback failure must never break publish — log and move on.
            # The post is already live; the row will look stale until the
            # next manual sync, but no double-post can happen.
            log.warning("notion_writeback_failed", page_id=page_id, error=str(exc))

    def _cms_metadata(self, props: dict[str, Any]) -> dict[str, Any]:
        """Extract the CMS-relevant fields into the SourceItem metadata so
        the workflow service can route + schedule + writeback without
        re-querying Notion."""
        return {
            "cms": True,
            "platforms": _flatten_multi_select(
                props.get(self.config.get("platforms_property",
                                          DEFAULT_PLATFORMS_PROPERTY))
            ),
            "scheduled_at": _flatten_date(
                props.get(self.config.get("scheduled_property",
                                          DEFAULT_SCHEDULED_PROPERTY))
            ),
        }

    def _cms_body(self, props: dict[str, Any]) -> str:
        """For CMS rows, the post body is the user-authored Content
        property — not a flattened render of every prop."""
        content_prop = self.config.get("content_property", DEFAULT_CONTENT_PROPERTY)
        prop = props.get(content_prop) or {}
        rich = prop.get("rich_text") or prop.get("title") or []
        return "".join(t.get("plain_text", "") for t in rich).strip()


# ── property extractors ────────────────────────────────────────────────────
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
        if "multi_select" in prop and isinstance(prop["multi_select"], list):
            return [s.get("name") for s in prop["multi_select"] if isinstance(s, dict)]
        if "url" in prop:
            return prop.get("url")
        if "date" in prop and isinstance(prop["date"], dict):
            return prop["date"].get("start")
    return prop


def _flatten_multi_select(prop: Any) -> list[str]:
    if not isinstance(prop, dict):
        return []
    items = prop.get("multi_select") or []
    return [s.get("name") for s in items if isinstance(s, dict) and s.get("name")]


def _flatten_date(prop: Any) -> str | None:
    if isinstance(prop, dict):
        date = prop.get("date") or {}
        return date.get("start") if isinstance(date, dict) else None
    return None


def _render_page_text(page: dict) -> str:
    parts: list[str] = []
    for k, v in (page.get("properties") or {}).items():
        f = _flatten(v)
        if isinstance(f, str) and f.strip():
            parts.append(f"{k}: {f}")
    return "\n".join(parts)


# Filename-based heuristic for ambiguous ``file`` blocks. Real video
# inspection would require sniffing the bytes (out of scope here).
_VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")


def _looks_like_video(url: str) -> bool:
    u = url.split("?", 1)[0].lower()
    return any(u.endswith(ext) for ext in _VIDEO_EXTS)


def _extract_block_caption(caption: Any) -> str:
    """Notion block captions live in a rich-text array — flatten it
    so we can pass it through as ``alt_text``."""
    if not isinstance(caption, list):
        return ""
    return "".join(
        t.get("plain_text", "") for t in caption if isinstance(t, dict)
    )
