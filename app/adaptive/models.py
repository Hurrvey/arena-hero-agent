"""Immutable values used by the SQLite adaptive coordinator."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WindowScore:
    start_tick: int
    end_tick: int
    sample_count: int
    raw_score: float
    score_per_tick: float

    def __post_init__(self) -> None:
        if self.end_tick <= self.start_tick or self.sample_count < 0:
            raise ValueError("invalid window score bounds")
