"""LocalizationService — produce N localised variants of a single source
post via the configured LLM, optionally tuning per-platform conventions
(e.g. `formal-you` for Japanese LinkedIn vs casual TikTok).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from app.adapters.llm.base import LLMProvider, LLMRequest
from app.core.logging import get_logger
from app.domain.value_objects.ids import LocaleVariantId, new_id

log = get_logger(__name__)


# A small set of well-supported locales — clients are free to pass any
# BCP-47 tag at runtime; this is just a friendly registry for the UI.
SUPPORTED_LOCALES: dict[str, str] = {
    "en-US": "English (US)",
    "en-GB": "English (UK)",
    "es-ES": "Spanish (Spain)",
    "es-MX": "Spanish (Mexico)",
    "fr-FR": "French (France)",
    "de-DE": "German",
    "it-IT": "Italian",
    "pt-BR": "Portuguese (Brazil)",
    "ja-JP": "Japanese",
    "ko-KR": "Korean",
    "zh-CN": "Chinese (Simplified)",
    "zh-TW": "Chinese (Traditional)",
    "ar-SA": "Arabic (Saudi Arabia)",
    "hi-IN": "Hindi",
    "ru-RU": "Russian",
    "tr-TR": "Turkish",
    "vi-VN": "Vietnamese",
    "th-TH": "Thai",
    "id-ID": "Indonesian",
    "nl-NL": "Dutch",
    "pl-PL": "Polish",
    "sv-SE": "Swedish",
}


_PLATFORM_HINTS: dict[str, str] = {
    "linkedin": "Maintain professional tone; use polite forms in languages "
                "that distinguish formality.",
    "twitter": "Keep below 280 characters; preserve hashtags verbatim.",
    "tiktok": "Casual, energetic tone; use trending platform-native phrases.",
    "instagram": "Lifestyle-friendly tone; keep emoji usage natural for the "
                 "target locale.",
    "facebook": "Conversational, slightly more verbose tone.",
    "youtube": "Treat as a video description; preserve URLs and timestamps.",
    "pinterest": "Action-oriented, keyword-rich phrasing.",
    "threads": "Casual, conversational; preserve thread-style breaks.",
    "reddit": "Match the subreddit's tone; avoid overt marketing language.",
    "mastodon": "Friendly, low-key tone; respect content warnings if present.",
    "bluesky": "Casual, conversational tone.",
    "medium": "Long-form, narrative; preserve markdown-style emphasis.",
    "discord": "Casual, in-the-moment tone.",
    "slack": "Workspace-appropriate, slightly informal tone.",
    "telegram": "Direct, succinct tone.",
    "tumblr": "Playful, occasionally irreverent tone.",
}


@dataclass(slots=True)
class LocalizedVariant:
    """One localized rendering of a source post."""
    id: LocaleVariantId
    source_text: str
    locale: str
    platform_name: str | None
    text: str
    rationale: str = ""
    extras: dict = field(default_factory=dict)


@dataclass(slots=True)
class LocalizationRequest:
    text: str
    locales: list[str]
    platform_name: str | None = None
    preserve_terms: list[str] = field(default_factory=list)   # don't translate
    tone_override: str | None = None
    max_chars: int | None = None


class LocalizationService:
    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    async def translate(
        self, req: LocalizationRequest,
    ) -> list[LocalizedVariant]:
        """Translate `req.text` into every locale in `req.locales`. Returns
        in the order requested. Failures degrade to the source text so the
        caller can still ship the post."""
        out: list[LocalizedVariant] = []
        for locale in req.locales:
            try:
                rendered = await self._translate_one(
                    text=req.text,
                    locale=locale,
                    platform_name=req.platform_name,
                    preserve_terms=req.preserve_terms,
                    tone_override=req.tone_override,
                    max_chars=req.max_chars,
                )
            except Exception as exc:                          # noqa: BLE001
                log.warning(
                    "localization_failed",
                    locale=locale, error=str(exc),
                )
                rendered = LocalizedVariant(
                    id=LocaleVariantId(new_id()),
                    source_text=req.text,
                    locale=locale,
                    platform_name=req.platform_name,
                    text=req.text,
                    rationale=f"fallback to source: {exc}",
                )
            out.append(rendered)
        return out

    async def _translate_one(
        self,
        *,
        text: str,
        locale: str,
        platform_name: str | None,
        preserve_terms: list[str],
        tone_override: str | None,
        max_chars: int | None,
    ) -> LocalizedVariant:
        platform_hint = _PLATFORM_HINTS.get((platform_name or "").lower(), "")
        preserve_block = (
            "\nDo NOT translate the following terms (keep verbatim): "
            + ", ".join(preserve_terms)
            if preserve_terms else ""
        )
        char_block = (
            f"\nLimit the result to {max_chars} characters."
            if max_chars else ""
        )
        tone_block = (
            f"\nOverride the source tone with: {tone_override}."
            if tone_override else ""
        )

        prompt = (
            f"Translate and localise the following social media post into "
            f"{locale} ({SUPPORTED_LOCALES.get(locale, 'BCP-47 locale')}).\n"
            f"{platform_hint}\n"
            f"Preserve all hashtags, URLs and @-mentions verbatim.\n"
            f"Adapt idioms so the result reads natively, not like a translation."
            f"{preserve_block}{char_block}{tone_block}\n\n"
            f"Source post:\n{text}\n\n"
            f"Localised post:"
        )

        req = LLMRequest(
            system="You are a senior multilingual social-media copy editor.",
            prompt=prompt,
            temperature=0.4,
            max_tokens=800,
        )
        res = await self.llm.complete(req)
        return LocalizedVariant(
            id=LocaleVariantId(new_id()),
            source_text=text,
            locale=locale,
            platform_name=platform_name,
            text=(res.text or text).strip(),
            rationale=f"model={res.model}, tokens={res.usage.output_tokens}",
        )


def supported_locales() -> Iterable[tuple[str, str]]:
    return tuple(SUPPORTED_LOCALES.items())
