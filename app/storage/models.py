"""Immutable records returned by the SQLite repositories."""

from __future__ import annotations

from dataclasses import dataclass

from strategy_policy import StrategyProfile


@dataclass(frozen=True, slots=True)
class RuntimeSession:
    session_id: str
    account_hash: str
    status: str
    started_at: str
    ended_at: str | None = None
    last_tick: int | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ServiceEvent:
    seq: int
    session_id: str
    tick: int | None
    event_type: str
    payload: dict[str, object]
    created_at: str


@dataclass(frozen=True, slots=True)
class EventPage:
    events: tuple[ServiceEvent, ...]
    last_seq: int


@dataclass(frozen=True, slots=True)
class StrategyRevision:
    revision: int
    source: str
    parent_revision: int | None
    profile: StrategyProfile
    reason: str
    activated_tick: int | None
    status: str
    created_at: str


@dataclass(frozen=True, slots=True)
class AdaptiveWindow:
    cycle_id: str
    start_tick: int
    end_tick: int
    sample_count: int
    base_revision: int
    candidate_revision: int | None
    skill_fingerprint: str
    raw_score: float
    normalized_score: float
    status: str
