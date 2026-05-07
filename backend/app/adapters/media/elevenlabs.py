"""ElevenLabs — text-to-speech voice generation for short-form video."""
from __future__ import annotations

import os

import httpx

from app.core.logging import get_logger
from app.domain.value_objects.content import MediaAsset, MediaKind
from app.plugins.registry import register_plugin

from .base import MediaBrief, MediaGenerator

log = get_logger(__name__)


@register_plugin("media", "elevenlabs", api_version="1.0", category="audio")
class ElevenLabsGenerator(MediaGenerator):
    display_name = "ElevenLabs (TTS)"
    description = "Text-to-speech via ElevenLabs API."
    supported_kinds = (MediaKind.VIDEO,)   # paired with HeyGen / lip-sync
    config_schema = {
        "type": "object",
        "properties": {
            "voice_id": {"type": "string", "default": "EXAVITQu4vr4xnSDxMaL"},
            "model_id": {"type": "string", "default": "eleven_multilingual_v2"},
        },
    }

    async def generate(self, brief: MediaBrief) -> MediaAsset:
        token = os.getenv("ELEVENLABS_API_KEY", "")
        voice = brief.voice or self.config.get("voice_id", "EXAVITQu4vr4xnSDxMaL")
        if not token:
            return MediaAsset(
                url=f"mock://elevenlabs/{voice}/{abs(hash(brief.prompt))}.mp3",
                kind=MediaKind.VIDEO, alt_text=brief.prompt[:120],
            )
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                    headers={"xi-api-key": token},
                    json={"text": brief.prompt,
                          "model_id": self.config.get("model_id", "eleven_multilingual_v2")},
                )
                r.raise_for_status()
            # In production we'd upload r.content to Supabase Storage and return
            # the signed URL. Stub returns a mock URL.
            return MediaAsset(
                url=f"mock://elevenlabs/{voice}/uploaded.mp3",
                kind=MediaKind.VIDEO, alt_text=brief.prompt[:120],
            )
        except httpx.HTTPError as exc:
            log.warning("elevenlabs_call_failed", error=str(exc))
            return MediaAsset(url="mock://elevenlabs/error.mp3",
                              kind=MediaKind.VIDEO, alt_text=brief.prompt[:120])
