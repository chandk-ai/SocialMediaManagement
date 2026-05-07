"""HTTP routes for localization / translation."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import current_user, get_localization_service
from app.core.security import Principal
from app.services.localization_service import (
    LocalizationRequest,
    LocalizationService,
    supported_locales,
)

router = APIRouter()


class TranslateIn(BaseModel):
    text: str
    locales: list[str]
    platform_name: str | None = None
    preserve_terms: list[str] = Field(default_factory=list)
    tone_override: str | None = None
    max_chars: int | None = None


class LocalizedVariantOut(BaseModel):
    locale: str
    text: str
    rationale: str = ""
    platform_name: Optional[str] = None


class LocalesOut(BaseModel):
    locales: list[dict]


@router.get("/locales", response_model=LocalesOut)
async def list_locales(
    _user: Principal = Depends(current_user),
) -> LocalesOut:
    return LocalesOut(
        locales=[
            {"code": code, "name": name}
            for code, name in supported_locales()
        ],
    )


@router.post("/translate", response_model=list[LocalizedVariantOut])
async def translate(
    body: TranslateIn,
    _user: Principal = Depends(current_user),
    svc: LocalizationService | None = Depends(get_localization_service),
) -> list[LocalizedVariantOut]:
    if svc is None:
        raise HTTPException(503, "No LLM provider configured")
    if not body.text.strip():
        raise HTTPException(400, "text is required")
    if not body.locales:
        raise HTTPException(400, "at least one locale is required")
    out = await svc.translate(LocalizationRequest(
        text=body.text,
        locales=body.locales,
        platform_name=body.platform_name,
        preserve_terms=body.preserve_terms,
        tone_override=body.tone_override,
        max_chars=body.max_chars,
    ))
    return [
        LocalizedVariantOut(
            locale=v.locale,
            text=v.text,
            rationale=v.rationale,
            platform_name=v.platform_name,
        )
        for v in out
    ]
