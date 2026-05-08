from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ScheduleKind(str, Enum):
    ONCE = "once"
    CRON = "cron"
    INTERVAL = "interval"
    MANUAL = "manual"
    OPTIMAL = "optimal"      # AI-driven best-time, consulted on every tick
    ADAPTIVE = "adaptive"    # Engagement-driven self-scheduling (Niche #10):
                              # cadence speeds up when recent engagement is
                              # rising, slows down when it's falling. Uses
                              # the same tick path as INTERVAL but the
                              # interval is recomputed from post performance.


@dataclass(frozen=True, slots=True)
class Schedule:
    kind: ScheduleKind
    cron: str | None = None        # required if kind == CRON
    interval_minutes: int | None = None  # required if kind == INTERVAL
    run_at: datetime | None = None  # required if kind == ONCE
    timezone: str = "UTC"
    # OPTIMAL hints — minimum gap between AI-scheduled runs and a per-run
    # latest-firing tolerance (so we don't post at an "optimal" time that has
    # already passed by hours).
    min_gap_minutes: int = 6 * 60
    tolerance_minutes: int = 30

    def __post_init__(self) -> None:
        if self.kind is ScheduleKind.CRON and not self.cron:
            raise ValueError("CRON schedule requires `cron` expression")
        if self.kind is ScheduleKind.INTERVAL and not self.interval_minutes:
            raise ValueError("INTERVAL schedule requires `interval_minutes`")
        if self.kind is ScheduleKind.ONCE and not self.run_at:
            raise ValueError("ONCE schedule requires `run_at`")
