"""Application service container shared by REST and WebSocket routes."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.runtime.models import RuntimeSnapshot, RuntimeStatus
from app.storage import (
    AdaptiveRepository,
    Database,
    MetricsRepository,
    RuntimeStore,
    StrategyRepository,
)


class StoppedManager:
    def status(self) -> RuntimeSnapshot:
        return RuntimeSnapshot("", RuntimeStatus.STOPPED)

    def start(self) -> RuntimeSnapshot:
        return self.status()

    def pause(self) -> RuntimeSnapshot:
        return self.status()

    def resume(self) -> RuntimeSnapshot:
        return self.status()

    def stop(self) -> RuntimeSnapshot:
        return self.status()


@dataclass(slots=True)
class Services:
    settings: Settings
    database: Database
    runtime_store: RuntimeStore
    strategies: StrategyRepository
    metrics: MetricsRepository
    adaptive: AdaptiveRepository
    runtime_manager: object
    broadcaster: object
    session_id: str | None = None
    runtime_factory: object | None = None
