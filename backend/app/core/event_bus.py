"""In-process domain event bus.

For cross-service propagation, events are also written to the transactional
outbox (`infrastructure/outbox.py`) and shipped to the message broker.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Awaitable, Callable, TypeVar

from app.core.logging import get_logger
from app.domain.events import DomainEvent

E = TypeVar("E", bound=DomainEvent)
Handler = Callable[[E], Awaitable[None]]

log = get_logger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type[DomainEvent], list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: type[E], handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    async def publish(self, event: DomainEvent) -> None:
        handlers = self._handlers.get(type(event), [])
        if not handlers:
            return
        results = await asyncio.gather(
            *(self._safe_call(h, event) for h in handlers),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                log.warning("event_handler_failed", event=type(event).__name__, error=str(r))

    @staticmethod
    async def _safe_call(handler: Handler, event: DomainEvent) -> None:
        await handler(event)
