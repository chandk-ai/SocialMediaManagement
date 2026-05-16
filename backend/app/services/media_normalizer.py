"""Media normalizer — fit an image to a platform's allowed aspect ratio
before publishing, so we don't get rejected at the platform-API layer
with cryptic "invalid aspect ratio" errors.

Why this exists
───────────────
Each social platform has different aspect-ratio constraints:

* **Instagram feed (image)**: 4:5 portrait (0.80) to 1.91:1 landscape (1.91)
* **Instagram Reels (video)**: 9:16 portrait (0.5625) — strict
* **Facebook feed**: lenient, ~1.91:1 recommended; we don't enforce
* **X / Twitter**: 1:1 or 16:9 typically; loose enforcement
* **LinkedIn**: 1.91:1 recommended, loose
* **Pinterest**: 2:3 ideal (0.6667)

When a user attaches a flyer that's an unusual ratio (1080×1920 = 0.5625
portrait too tall for IG feed; 1920×1080 = 1.78 borderline), IG rejects
with error_subcode 2207009. Same content, different aspect-ratio
requirements — solve once at the publish boundary.

Strategy
────────
*Pad* (letterbox) rather than crop. Cropping silently throws away
parts of the user's image they may have intended to keep. Padding with
a neutral background (white by default, optionally black) preserves
the entire original image and just adds bars on the side(s) so the
ratio falls inside the allowed range. The published post still
contains the full intended content.

Re-host the padded image in Supabase Storage so the platform fetches
a stable URL (not a one-shot bytes stream). The original URL is
preserved on the source so future runs can re-normalize for different
platforms.

Caching
───────
For idempotency: the normalized image's storage key derives from a
hash of (source_url, target_min_ar, target_max_ar, bg_color). Re-runs
for the same platform return the same URL — no duplicate uploads.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from typing import Optional

import httpx

from app.core.logging import get_logger
from app.services.media_import import MediaImportService

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AspectRatioRule:
    """Allowed aspect-ratio range for a platform + media kind.

    Aspect ratio = width / height.
      * 1.0   = square
      * <1.0  = portrait (taller than wide)
      * >1.0  = landscape (wider than tall)

    ``min_ar`` and ``max_ar`` are inclusive. If an image's ratio falls
    inside [min_ar, max_ar] we pass it through unchanged.
    """
    min_ar: float
    max_ar: float
    # Background color used for letterboxing — RGB tuple. White is
    # safer than black for most platforms (less likely to look like
    # accidental dark mode). Operators can override per-workflow if
    # the brand prefers a specific color.
    bg_color: tuple[int, int, int] = (255, 255, 255)
    # Output format. JPEG smaller, fine for photos. PNG preserves
    # transparency but the padded canvas is opaque anyway, so JPEG
    # is the default.
    output_format: str = "JPEG"


# Per-(platform_plugin_name, kind) rules. Only platforms that actually
# enforce ratios live here; everything else just publishes the original
# URL untouched.
PLATFORM_RULES: dict[tuple[str, str], AspectRatioRule] = {
    # Instagram feed — Meta's documented limits:
    # https://developers.facebook.com/docs/instagram-api/reference/ig-user/media
    ("instagram", "image"): AspectRatioRule(min_ar=0.80, max_ar=1.91),
    # IG Reels — vertical 9:16. We only enforce for IG image posts in
    # this round; video transformation needs ffmpeg and is out of
    # scope here. The video path skips normalization.

    # Facebook feed — leniency varies by post type. Use loose bounds
    # so we only catch truly extreme aspect ratios. We don't currently
    # see failures here.
    ("facebook", "image"): AspectRatioRule(min_ar=0.5, max_ar=2.5),

    # Pinterest prefers 2:3 (0.67) but accepts a wider range. Don't
    # normalize aggressively — Pinterest's algorithm favors tall
    # images, so padding a tall image to be wider would hurt
    # discoverability. Loose bounds only.
    ("pinterest", "image"): AspectRatioRule(min_ar=0.5, max_ar=2.5),
}


class MediaNormalizer:
    """Reach: takes a media URL + target platform, returns a URL whose
    aspect ratio is guaranteed to satisfy the platform's constraints.
    Either the input URL (if already valid) or a freshly-padded copy
    re-hosted in Supabase Storage.

    Stateless — safe to construct per call. Uses MediaImportService
    for the re-host step which handles content-type, size cap, and
    the bucket policy.
    """

    def __init__(self, importer: MediaImportService | None = None) -> None:
        self.importer = importer or MediaImportService()

    async def normalize_image_for_platform(
        self, *,
        image_url: str,
        platform_plugin_name: str,
        org_id: str,
    ) -> str:
        """Return a URL whose image fits the platform's allowed aspect
        ratios. Returns ``image_url`` unchanged when:
          * no rule is defined for this platform (no-op pass-through), OR
          * the image already fits the rule, OR
          * Pillow / fetch / upload fails (fail-soft — caller publishes
            the original URL and the platform will reject it cleanly).

        The "fail-soft" semantics are deliberate: a normalizer bug
        must not silently block publishes. If transformation fails, we
        log + return the original URL and let the platform adapter's
        own error path handle the rejection.
        """
        rule = PLATFORM_RULES.get((platform_plugin_name, "image"))
        if rule is None:
            return image_url

        try:
            # 1. Fetch the image bytes.
            raw = await self._fetch_image(image_url)
            if not raw:
                return image_url

            # 2. Detect current aspect ratio. Pillow is the right tool.
            try:
                from PIL import Image
            except ImportError:                                    # pragma: no cover
                log.warning("media_normalizer_pillow_missing")
                return image_url

            with Image.open(io.BytesIO(raw)) as im:
                w, h = im.size
                if w == 0 or h == 0:
                    return image_url
                ar = w / h
                if rule.min_ar <= ar <= rule.max_ar:
                    log.info(
                        "media_normalizer_passthrough",
                        platform=platform_plugin_name,
                        width=w, height=h, ar=round(ar, 3),
                    )
                    return image_url

                # 3. Compute target ratio (nearest allowed boundary).
                if ar < rule.min_ar:
                    target_ar = rule.min_ar         # too tall → make wider
                else:
                    target_ar = rule.max_ar         # too wide → make taller

                # 4. Pad. New canvas keeps the original dimensions on
                # whichever axis is already large enough; we add bars
                # on the other axis until the ratio matches.
                #   ar (current) < target_ar (boundary) → image is too
                #     TALL → need to widen the canvas (add bars left/right)
                #   ar > target_ar → image is too WIDE → need to make
                #     canvas TALLER (add bars top/bottom)
                if ar < target_ar:
                    new_w = int(round(h * target_ar))
                    new_h = h
                    paste_x = (new_w - w) // 2
                    paste_y = 0
                else:
                    new_w = w
                    new_h = int(round(w / target_ar))
                    paste_x = 0
                    paste_y = (new_h - h) // 2

                # Compose onto a flat background.
                # Convert source to RGB to avoid alpha-channel issues
                # when saving as JPEG. PNG-with-alpha gets composited
                # over the bg_color.
                if im.mode in ("RGBA", "LA", "P"):
                    rgba = im.convert("RGBA")
                    bg = Image.new("RGB", (new_w, new_h), rule.bg_color)
                    bg.paste(rgba, (paste_x, paste_y), rgba.split()[-1])
                    out_im = bg
                else:
                    rgb = im.convert("RGB")
                    bg = Image.new("RGB", (new_w, new_h), rule.bg_color)
                    bg.paste(rgb, (paste_x, paste_y))
                    out_im = bg

                buf = io.BytesIO()
                out_im.save(buf, format=rule.output_format, quality=92)
                padded_bytes = buf.getvalue()

            # 5. Re-host. Filename includes the original hash for
            # idempotency — same input + same rule → same output URL.
            key = hashlib.sha256(
                f"{image_url}|{rule.min_ar}|{rule.max_ar}|{rule.bg_color}".encode()
            ).hexdigest()[:16]
            fname = f"normalized-{platform_plugin_name}-{key}.jpg"
            result = await self.importer.import_bytes(
                org_id=org_id,
                data=padded_bytes,
                content_type="image/jpeg",
                filename=fname,
                source_url=image_url,
            )
            log.info(
                "media_normalizer_padded",
                platform=platform_plugin_name,
                from_size=(w, h),
                to_size=(new_w, new_h),
                from_ar=round(ar, 3),
                target_ar=round(target_ar, 3),
                new_url=result.url,
            )
            return result.url
        except Exception as exc:                                   # noqa: BLE001
            log.warning(
                "media_normalizer_failed",
                platform=platform_plugin_name,
                image_url=image_url,
                error=str(exc)[:200],
            )
            return image_url

    async def _fetch_image(self, url: str) -> bytes:
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                r = await client.get(url)
                r.raise_for_status()
                return r.content
        except Exception as exc:                                   # noqa: BLE001
            log.warning("media_normalizer_fetch_failed",
                        url=url, error=str(exc)[:200])
            return b""
