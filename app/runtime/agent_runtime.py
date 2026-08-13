"""Single-threaded authoritative Arena Hero agent lifecycle."""

from __future__ import annotations

import logging
from collections import deque
from inspect import signature
from threading import Event, RLock, Thread, current_thread
from uuid import uuid4

from balanced_tactic import TacticMemory

from .account_lock import AccountLock
from .client import GameClientFactory
from .event_queue import RuntimeEventQueue
from .models import RuntimeBatch, RuntimeConflict, RuntimeSnapshot, RuntimeStatus
from .serialization import is_receipt, is_turn, receipt_batch

logger = logging.getLogger(__name__)


class AgentRuntime:
    def __init__(
        self,
        *,
        api_key: str,
        client_factory: GameClientFactory,
        planner,
        profile_provider,
        persistence,
        adaptive_observer,
        lock_directory,
        event_queue: RuntimeEventQueue | None = None,
    ) -> None:
        self.runtime_id = uuid4().hex
        self._api_key = api_key
        self._client_factory = client_factory
        self._planner = planner
        self._profile_provider = profile_provider
        self._persistence = persistence
        self._adaptive_observer = adaptive_observer
        self._lock = AccountLock.from_api_key(api_key, lock_directory, runtime_id=self.runtime_id)
        self._queue = event_queue or RuntimeEventQueue()
        self._memory = TacticMemory()
        self._status = RuntimeStatus.STOPPED
        self._status_lock = RLock()
        self._pause_requested = Event()
        self._stop_requested = Event()
        self._thread: Thread | None = None
        self._client = None
        self._recent_ticks: set[int] = set()
        self._recent_tick_order: deque[int] = deque()
        self._submitted_count = 0
        self._highest_submitted_tick = -1
        self._paused_tick: int | None = None
        self._last_tick: int | None = None
        self._error_code: str | None = None
        self._error_message: str | None = None

    def snapshot(self) -> RuntimeSnapshot:
        with self._status_lock:
            return RuntimeSnapshot(
                self.runtime_id,
                self._status,
                self._last_tick,
                self._error_code,
                self._error_message,
                self._submitted_count,
            )

    @property
    def dedupe_window_size(self) -> int:
        return len(self._recent_ticks)

    def _remember_tick(self, tick: int) -> None:
        if tick in self._recent_ticks:
            return
        self._recent_ticks.add(tick)
        self._recent_tick_order.append(tick)
        while len(self._recent_tick_order) > 2048:
            self._recent_ticks.discard(self._recent_tick_order.popleft())

    def _set_status(self, status: RuntimeStatus) -> RuntimeSnapshot:
        with self._status_lock:
            self._status = status
        return self.snapshot()

    def start(self) -> RuntimeSnapshot:
        with self._status_lock:
            if self._status in {
                RuntimeStatus.STARTING,
                RuntimeStatus.RUNNING,
                RuntimeStatus.PAUSED,
            }:
                return self.snapshot()
            if self._status is RuntimeStatus.STOPPING:
                raise RuntimeConflict("runtime is stopping")
            self._lock.acquire()
            self._stop_requested.clear()
            self._pause_requested.clear()
            self._error_code = None
            self._error_message = None
            self._status = RuntimeStatus.STARTING
            self._thread = Thread(
                target=self._run,
                name=f"arena-runtime-{self.runtime_id[:8]}",
                daemon=True,
            )
            self._thread.start()
            return self.snapshot()

    def pause(self) -> RuntimeSnapshot:
        with self._status_lock:
            if self._status is RuntimeStatus.PAUSED:
                return self.snapshot()
            if self._status not in {RuntimeStatus.STARTING, RuntimeStatus.RUNNING}:
                raise RuntimeConflict("runtime is not running")
            self._pause_requested.set()
            self._status = RuntimeStatus.PAUSED
            return self.snapshot()

    def resume(self) -> RuntimeSnapshot:
        with self._status_lock:
            if self._status is RuntimeStatus.RUNNING:
                return self.snapshot()
            if self._status is not RuntimeStatus.PAUSED:
                raise RuntimeConflict("runtime is not paused")
            self._pause_requested.clear()
            self._status = RuntimeStatus.RUNNING
            return self.snapshot()

    def stop(self) -> RuntimeSnapshot:
        with self._status_lock:
            if self._status is RuntimeStatus.STOPPED:
                return self.snapshot()
            if self._status is not RuntimeStatus.ERROR:
                self._status = RuntimeStatus.STOPPING
            self._stop_requested.set()
            client = self._client
            thread = self._thread
        if client is not None:
            client.close()
        if thread is not None and thread is not current_thread():
            thread.join(timeout=5)
        return self._finish_stop()

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout)

    def _finish_stop(self) -> RuntimeSnapshot:
        with self._status_lock:
            if self._status is not RuntimeStatus.ERROR:
                self._status = RuntimeStatus.STOPPED
            self._lock.release()
            return self.snapshot()

    def _run(self) -> None:
        try:
            self._client = self._client_factory(self._api_key)
            if not self._pause_requested.is_set():
                self._set_status(RuntimeStatus.RUNNING)
            for event in self._client.events():
                if self._stop_requested.is_set():
                    break
                self.handle_event(event)
            if self._status not in {RuntimeStatus.ERROR, RuntimeStatus.STOPPING}:
                self._set_status(RuntimeStatus.STOPPED)
        # This is the outer thread boundary: upstream SDK, injected planner,
        # and persistence implementations can raise unrelated exception types.
        # Convert all of them to a redacted terminal runtime state.
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__.upper()
            with self._status_lock:
                self._error_code = name.replace("AUTHENTICATIONERROR", "AUTHENTICATION_ERROR")
                self._error_message = (
                    "Arena Hero authentication failed"
                    if "AUTH" in name
                    else "Arena Hero runtime failed"
                )
                self._status = RuntimeStatus.ERROR
        finally:
            if self._client is not None:
                self._client.close()
            closer = getattr(self._adaptive_observer, "close", None)
            if not callable(closer):
                closer = getattr(
                    getattr(self._adaptive_observer, "__self__", None),
                    "close_adaptive",
                    None,
                )
            if callable(closer):
                closer()
            self._lock.release()

    def handle_event(self, event: object) -> None:
        if is_turn(event):
            self._handle_turn(event)
        elif is_receipt(event):
            self._persistence(receipt_batch(event))

    def _handle_turn(self, turn: object) -> None:
        tick = int(turn.tick)
        self._last_tick = tick
        if tick in self._recent_ticks or tick <= self._highest_submitted_tick:
            return
        if self._pause_requested.is_set() or self._status is RuntimeStatus.PAUSED:
            self._paused_tick = tick
            self._remember_tick(tick)
            self._persistence(RuntimeBatch("SNAPSHOT_ONLY", tick, turn=turn))
            return
        if tick == self._paused_tick:
            return
        provider = self._profile_provider
        if signature(provider).parameters:
            profile = provider(tick)
        else:
            profile = provider()
        result = self._planner(turn, self._memory, profile)
        receipt = turn.submit()
        self._remember_tick(tick)
        self._submitted_count += 1
        self._highest_submitted_tick = max(self._highest_submitted_tick, tick)
        batch = RuntimeBatch("TURN_SUBMITTED", tick, turn, result, receipt, "AGENT")
        self._queue.put_critical(batch)
        self._persistence(batch)
        try:
            self._adaptive_observer(turn, receipt, result)
        except Exception:  # noqa: BLE001 - post-submit adaptive work is fail-open
            logger.warning("adaptive observation failed after accepted plan")
