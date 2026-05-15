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

import re
from datetime import datetime
from typing import Any, AsyncIterator

from app.core.logging import get_logger
from app.domain.entities.source import SourceItem
from app.domain.value_objects.content import MediaAsset, MediaKind
from app.plugins.registry import register_plugin
from app.services.media_import import MediaImportError, MediaImportService

from .base import ContentSource, SourceConnectionError

log = get_logger(__name__)

# A Notion database ID is a 32-char hex blob, optionally hyphenated as a
# UUID. Users paste either:
#   * the raw ID       — "c5e8d1f01234567890abcdef0123456789012345"
#   * the hyphenated   — "c5e8d1f0-1234-5678-9abc-def012345678"
#   * a Notion URL     — "https://www.notion.so/ws/Posts-c5e8d1f0...xyz?v=..."
# In every case we extract / normalize to the hyphenated form before
# hitting the API. Anything else (e.g. "CK", a column name, a workspace
# slug) gets rejected up front with a helpful error.
_HEX32_RE = re.compile(r"[0-9a-fA-F]{32}")


def _resolve_database_id(raw: str | None) -> str:
    """Normalize whatever the user typed into a hyphenated Notion database
    UUID. Raises ``SourceConnectionError`` with a user-readable message when
    the input is not parseable — that's surfaced at source-test time so the
    user sees the problem before the first workflow run.

    Accepts:
      * raw hex blob (with or without hyphens)
      * full Notion URL — extracts the 32-char hex from the path
    """
    s = (raw or "").strip()
    if not s:
        raise SourceConnectionError(
            "database_id is required — paste either the database ID or the "
            "Notion page URL."
        )
    # Strip URL noise: take the path component, drop anything after a `?` /
    # `#`, then look for a 32-char hex blob anywhere in what remains. The
    # Notion URL format puts the ID at the tail of the path: ``/Posts-<id>``
    # or ``/<id>``.
    candidate = s
    if "://" in s:
        # crude path extraction without pulling urllib — last segment after `/`
        candidate = s.split("?", 1)[0].split("#", 1)[0]
        candidate = candidate.rstrip("/").rsplit("/", 1)[-1]
    # Drop hyphens for matching — the hex is the same with or without them.
    flat = candidate.replace("-", "")
    m = _HEX32_RE.search(flat)
    if not m:
        raise SourceConnectionError(
            f"database_id {raw!r} doesn't look like a Notion database ID. "
            "Paste either the 32-character ID or the full Notion URL "
            "(e.g. https://www.notion.so/.../Posts-c5e8d1f0...)."
        )
    hex32 = m.group(0).lower()
    # Re-hyphenate to the canonical 8-4-4-4-12 form Notion's API accepts.
    return f"{hex32[0:8]}-{hex32[8:12]}-{hex32[12:16]}-{hex32[16:20]}-{hex32[20:32]}"

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
            "database_id":       {"type": "string", "title": "Database ID or URL",
                                  "description": "Paste either the 32-character "
                                  "database ID or the full Notion database URL — "
                                  "we'll extract the ID automatically."},
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
        # Validate + normalize the database ID up front so the user sees a
        # clear error at source-test time, not 6 hours later in a worker
        # log. ``_resolve_database_id`` raises SourceConnectionError on
        # garbage input — we re-stamp the canonical form into the config
        # so every subsequent fetch() / patch() call uses the hyphenated
        # UUID Notion's API expects.
        self.config["database_id"] = _resolve_database_id(
            self.config.get("database_id")
        )

    async def fetch(self, since: datetime | None = None) -> AsyncIterator[SourceItem]:
        try:
            import httpx
        except ImportError:
            return
        # Defensive normalize on every fetch — covers sources persisted
        # before the validator was added. ``connect()`` does this too, but
        # the worker path can bypass connect() when the source object is
        # reconstituted from the repo, so we re-resolve here.
        database_id = _resolve_database_id(self.config.get("database_id"))
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
                f"https://api.notion.com/v1/databases/{database_id}/query",
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

            # Pull page cover + child blocks → extract image / video /
            # file references → rehost in Supabase Storage so the URLs
            # survive the ~1 hour Notion signed-URL expiry. Best-effort:
            # import failures are logged but don't break the source
            # iteration. We pass the WHOLE page dict (not just the id)
            # so the extractor can access ``page.cover`` — that's where
            # most Notion authors put the hero image, NOT in block
            # children.
            media: tuple[MediaAsset, ...] = ()
            try:
                media = await self._extract_page_media(page)
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
    async def _extract_page_media(self, page: dict) -> tuple[MediaAsset, ...]:
        """Walk a Notion page's hero image + block tree and return its
        image / video / file attachments as MediaAsset objects whose URLs
        point at our Supabase bucket (not the short-lived Notion URLs).

        Sources we harvest, in priority order — earlier entries win
        the "hero image" slot downstream:

          1. **Page cover** (``page.cover``) — by far the most common
             place Notion authors put a hero image. Lives on the page
             object, NOT in block children, so the old block-only walk
             completely missed it. This was the #1 reason "AI generated
             a random image instead of using my Notion picture".
          2. **Top-level image / video / file blocks** in document
             order.
          3. **One level deep** inside common container blocks —
             column_list / column / toggle / callout / quote /
             synced_block. Notion's editor frequently nests media in
             these when authors use layouts.

        Block payload shapes we accept (same form for ``image`` /
        ``video`` / ``file``)::

            { type: "image",
              image: { type: "file",
                       file: { url: "https://prod-files-secure.s3...",
                               expiry_time: "..." } } }

            { type: "image",
              image: { type: "external",
                       external: { url: "https://example.com/x.png" } } }

        Returns up to ``_MAX_MEDIA_PER_PAGE`` MediaAssets — the first
        successful import is media[0], which the per-platform
        selection in the Planner treats as the hero asset.
        """
        try:
            import httpx
        except ImportError:                                           # pragma: no cover
            return ()

        page_id = page.get("id", "")

        # ── 1. Page cover ─────────────────────────────────────────────
        candidates: list[tuple[str, MediaKind, str]] = []
        cover_src = _extract_file_url(page.get("cover"))
        if cover_src:
            candidates.append((cover_src, MediaKind.IMAGE, "cover"))

        # ── 2 & 3. Block tree ─────────────────────────────────────────
        # First page of children is usually enough — Notion pages with
        # >100 top-level blocks are rare. If we need more we'd paginate
        # via ``next_cursor``; not worth the latency budget here.
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"https://api.notion.com/v1/blocks/{page_id}/children",
                params={"page_size": 100},
                headers=self._headers(),
            )
            if r.status_code >= 400:
                # Promoted INFO→WARNING — silent INFO logs were burying
                # this when users wondered why their Notion images weren't
                # showing up in posts.
                log.warning("notion_blocks_fetch_failed",
                            page_id=page_id, status=r.status_code,
                            body=r.text[:200])
                blocks: list[dict] = []
            else:
                blocks = (r.json() or {}).get("results") or []

            # Walk top-level blocks; recurse one level into common
            # container blocks so a column-list layout doesn't hide
            # everything.
            await self._harvest_blocks(
                client, blocks, candidates,
                _MAX_MEDIA_PER_PAGE, allow_recursion=True,
            )

        if not candidates:
            log.info("notion_no_media_found", page_id=page_id,
                     has_cover=bool(page.get("cover")),
                     blocks_fetched=len(blocks))
            return ()

        importer = MediaImportService()
        # OrgId comes through self.config["__org_id__"] when the
        # plugin host wires it; fall back to the synthetic "shared"
        # bucket key if the host didn't set it (only happens in legacy
        # CLI smoke tests). The storage path is org-scoped either way.
        org_id = str(self.config.get("__org_id__") or "shared")

        assets: list[MediaAsset] = []
        skipped = 0
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
                # log, keep going. Promoted INFO→WARNING so users see
                # this when investigating "why no source image?".
                skipped += 1
                log.warning("notion_media_import_failed",
                            page_id=page_id, src=src[:120],
                            error=str(exc))

        # Summary log so a single line tells the operator what happened
        # to this page's media. Shows up in the worker log right next
        # to the per-asset failures above.
        log.info("notion_media_summary", page_id=page_id,
                 candidates=len(candidates),
                 imported=len(assets), skipped=skipped)
        return tuple(assets)

    async def _harvest_blocks(
        self,
        client,
        blocks: list[dict],
        candidates: list[tuple[str, MediaKind, str]],
        max_items: int,
        *,
        allow_recursion: bool,
    ) -> None:
        """Append (src, kind, alt) tuples for every image/video/file
        block found in ``blocks``. When ``allow_recursion`` is True,
        descends ONE level into container blocks (column lists,
        toggles, callouts, etc.) — that's the common Notion pattern
        where authors put a hero image inside a two-column layout.
        We cap at one level because deeper recursion would explode
        latency on a 50-row database fetch."""
        # Container block types whose children commonly hold media in
        # real-world Notion layouts. Each of these has its own
        # ``has_children=true`` flag and its own /blocks/{id}/children
        # endpoint.
        CONTAINER_TYPES = (
            "column_list", "column", "toggle", "callout",
            "quote", "synced_block",
        )

        for b in blocks:
            if len(candidates) >= max_items:
                return
            btype = b.get("type")

            if btype in ("image", "video", "file"):
                payload = b.get(btype) or {}
                src = _extract_file_url(payload)
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
                continue

            if (
                allow_recursion
                and btype in CONTAINER_TYPES
                and b.get("has_children")
            ):
                try:
                    rr = await client.get(
                        f"https://api.notion.com/v1/blocks/{b['id']}/children",
                        params={"page_size": 50},
                        headers=self._headers(),
                    )
                    if rr.status_code >= 400:
                        continue
                    nested = (rr.json() or {}).get("results") or []
                except Exception as exc:                                # noqa: BLE001
                    log.info("notion_nested_fetch_failed",
                             block_id=b.get("id"), error=str(exc))
                    continue
                # ``allow_recursion=False`` on the nested walk caps
                # depth at one — children of children aren't visited.
                await self._harvest_blocks(
                    client, nested, candidates, max_items,
                    allow_recursion=False,
                )

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


def _extract_file_url(payload: Any) -> str | None:
    """Pull the URL out of a Notion file-bearing payload — works for
    page covers, image / video / file block payloads, and icon payloads.

    Notion serves two flavors:
      * ``{"type": "file",     "file":     {"url": "..."}}`` — uploaded
      * ``{"type": "external", "external": {"url": "..."}}`` — pasted

    Returns None for unsupported payload types (e.g. emoji icons,
    which are ``{"type": "emoji", "emoji": "🚀"}`` and shouldn't be
    treated as image URLs)."""
    if not isinstance(payload, dict):
        return None
    ptype = payload.get("type")
    if ptype == "file":
        return (payload.get("file") or {}).get("url") or None
    if ptype == "external":
        return (payload.get("external") or {}).get("url") or None
    return None


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
