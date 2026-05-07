"""PostMetrics — engagement signals pulled back from each social platform."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PostMetrics:
    impressions: int = 0
    reach: int = 0
    clicks: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    video_views: int = 0
    raw: dict = field(default_factory=dict)
    fetched_at: datetime = field(default_factory=datetime.utcnow)

    def engagement(self) -> int:
        return self.likes + self.comments + self.shares + self.saves

    def engagement_rate(self) -> float:
        denom = self.impressions or self.reach or self.video_views or 1
        return self.engagement() / denom

    def normalised_score(self) -> float:
        """Map this post's engagement rate onto [0, 1] using a rough
        log-scale so a single viral post doesn't blow out the corpus."""
        import math
        rate = max(0.0, min(0.5, self.engagement_rate()))
        return math.log1p(rate * 1000) / math.log1p(500)   # ~ 0..1
