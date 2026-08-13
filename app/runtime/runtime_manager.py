"""Thread-safe owner of the optional local AgentRuntime instance."""

from __future__ import annotations

from threading import RLock

from .agent_runtime import AgentRuntime
from .models import RuntimeConflict, RuntimeSnapshot, RuntimeStatus


class RuntimeManager:
    def __init__(self, factory) -> None:
        self._factory = factory
        self._runtime: AgentRuntime | None = None
        self._lock = RLock()

    def status(self) -> RuntimeSnapshot:
        with self._lock:
            if self._runtime is None:
                return RuntimeSnapshot("", RuntimeStatus.STOPPED)
            return self._runtime.snapshot()

    def start(self) -> RuntimeSnapshot:
        with self._lock:
            if self._runtime is None or self._runtime.snapshot().status in {
                RuntimeStatus.STOPPED,
                RuntimeStatus.ERROR,
            }:
                self._runtime = self._factory()
            return self._runtime.start()

    def pause(self) -> RuntimeSnapshot:
        with self._lock:
            if self._runtime is None:
                raise RuntimeConflict("runtime has not started")
            return self._runtime.pause()

    def resume(self) -> RuntimeSnapshot:
        with self._lock:
            if self._runtime is None:
                raise RuntimeConflict("runtime has not started")
            return self._runtime.resume()

    def stop(self) -> RuntimeSnapshot:
        with self._lock:
            if self._runtime is None:
                return RuntimeSnapshot("", RuntimeStatus.STOPPED)
            return self._runtime.stop()
