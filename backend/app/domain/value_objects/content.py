"""Value objects describing content as it flows through the agent pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class MediaKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    GIF = "gif"
    DOCUMENT = "document"


@dataclass(frozen=True, slots=True)
class MediaAsset:
    url: str
    kind: MediaKind
    alt_text: str | None = None
    duration_sec: float | None = None  # videos
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True, slots=True)
class Hashtag:
    """Wraps a tag and validates the leading '#'."""
    value: str

    def __post_init__(self) -> None:
        v = self.value.strip()
        if not v.startswith("#"):
            object.__setattr__(self, "value", "#" + v)
        if " " in self.value:
            raise ValueError(f"Hashtag must not contain spaces: {self.value!r}")


@dataclass(frozen=True, slots=True)
class PostBlueprint:
    """Planner output: a per-platform blueprint for a single post."""
    platform_name: str
    angle: str                   # e.g. "behind-the-scenes engineering story"
    hook: str                    # the first 1-2 sentences
    key_messages: list[str]
    cta: str | None = None
    hashtags: list[Hashtag] = field(default_factory=list)
    suggested_media: MediaAsset | None = None
    # When the platform requires media, the Planner emits a brief that the
    # Executor passes to the configured MediaGenerator.
    media_prompt: str | None = None
    media_kind: MediaKind | None = None
    notes: str | None = None     # planner reasoning, useful in audit


@dataclass(frozen=True, slots=True)
class ContentPlan:
    """Aggregate planner output for one workflow run."""
    blueprints: list[PostBlueprint]
    rationale: str
    source_summary: str


@dataclass(frozen=True, slots=True)
class DraftPost:
    """Executor output."""
    platform_name: str
    text: str
    hashtags: list[Hashtag]
    media: list[MediaAsset] = field(default_factory=list)
    blueprint_ref: PostBlueprint | None = None


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Evaluator output — per-dimension scores in [0, 1]."""
    overall: float
    clarity: float
    brand_voice: float
    compliance: float
    platform_fit: float
    predicted_engagement: float
    suggestions: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    evaluated_at: datetime = field(default_factory=datetime.utcnow)

    def passes(self, threshold: float) -> bool:
        return self.overall >= threshold and not self.flags

    @classmethod
    def from_scores(cls, scores: dict[str, float], **rest: Any) -> "EvaluationReport":
        weights = {
            "clarity": 0.25,
            "brand_voice": 0.20,
            "compliance": 0.20,
            "platform_fit": 0.20,
            "predicted_engagement": 0.15,
        }
        overall = sum(scores.get(k, 0.0) * w for k, w in weights.items())
        return cls(overall=overall, **scores, **rest)
