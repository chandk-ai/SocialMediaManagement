"""In-app review channel — drops the review into the dashboard queue.

Decisions arrive via `POST /api/v1/reviews/{id}/decision` from the UI.
Implementation is just a record-keeper; the actual queue lives in the
`ReviewSessionRepository`.
"""
from __future__ import annotations

from app.plugins.registry import register_plugin

from .base import ReviewChannel, ReviewMessage


@register_plugin("review_channel", "in_app", api_version="1.0", category="builtin")
class InAppReviewChannel(ReviewChannel):
    display_name = "In-app dashboard"
    description = "Default — review queue inside the SMMS portal."

    async def send_for_review(self, recipient: str, message: ReviewMessage) -> str:
        # Nothing to deliver — the ReviewSession itself is the queue entry.
        return f"in-app:{recipient}"
