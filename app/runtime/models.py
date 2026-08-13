"""Agent runtime lifecycle and post-submit records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RuntimeStatus(StrEnum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


class RuntimeConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    runtime_id: str
    status: RuntimeStatus
    last_tick: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    submitted_ticks: int = 0


@dataclass(frozen=True, slots=True)
class RuntimeBatch:
    kind: str
    tick: int | None
    turn: object | None = None
    result: object | None = None
    receipt: object | None = None
    source: str | None = None
