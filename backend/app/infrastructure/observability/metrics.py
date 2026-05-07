"""Prometheus metrics exposed via /metrics."""
from __future__ import annotations

from prometheus_client import Counter, Histogram

posts_published = Counter(
    "smms_posts_published_total", "Posts successfully published", ["platform"]
)
posts_failed = Counter(
    "smms_posts_failed_total", "Posts that failed to publish", ["platform", "reason"]
)
agent_run_duration = Histogram(
    "smms_agent_run_seconds", "Agent runtime in seconds", ["agent"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60),
)
llm_tokens = Counter(
    "smms_llm_tokens_total", "Tokens consumed", ["provider", "kind"]   # kind=in|out
)
