"""Engagement source plugin contract.

Each adapter polls a single platform for new comments / DMs / mentions
since `since` and yields a stream of `EngagementItem`s. The TriageAgent
consumes these and produces draft replies routed through the same review
channel as content posts.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import AsyncIterator, ClassVar


class EngagementKind(str, Enum):
    COMMENT = "comment"
    DM = "dm"
    MENTION = "mention"
    REVIEW = "review"


class EngagementSentiment(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    QUESTION = "question"
    SPAM = "spam"


@dataclass(frozen=True, slots=True)
class EngagementItem:
    external_id: str               # platform-side id (for dedup)
    plugin_name: str               # linkedin / twitter / instagram / ...
    account_external_id: str       # which connected account it belongs to
    kind: EngagementKind
    body: str
    author_handle: str | None = None
    in_reply_to_post_id: str | None = None
    occurred_at: datetime | None = None
    raw: dict = field(default_factory=dict)


class EngagementSource(ABC):
    plugin_name: ClassVar[str] = ""
    api_version: ClassVar[str] = "1.0"
    display_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    supported_kinds: ClassVar[tuple[EngagementKind, ...]] = (EngagementKind.COMMENT,)

    def __init__(self, credentials=None, config: dict | None = None) -> None:
        self.credentials = credentials
        self.config = config or {}

    @abstractmethod
    async def poll(self, since: datetime | None = None) -> AsyncIterator[EngagementItem]:
        if False:                          # pragma: no cover  (shape hint)
            yield                          # type: ignore[misc]
