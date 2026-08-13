"""Long-run boundedness checks without connecting a real Arena Hero account."""

from __future__ import annotations

from types import SimpleNamespace

from app.runtime.agent_runtime import AgentRuntime
from app.runtime.event_queue import RuntimeEventQueue


class FakeTurn:
    def __init__(self, tick: int) -> None:
        self.tick = tick
        self.submit_calls = 0

    def submit(self):
        self.submit_calls += 1
        return SimpleNamespace(accepted=True, tick=self.tick)


class NoopLock:
    def acquire(self) -> None: ...
    def release(self) -> None: ...


def test_ten_thousand_fake_turns_keep_one_submit_per_tick_and_bounded_runtime_memory(
    tmp_path,
    monkeypatch,
) -> None:
    queue = RuntimeEventQueue(maxsize=256)
    persisted: list[int] = []
    runtime = AgentRuntime(
        api_key="fake",
        client_factory=lambda _key: None,
        planner=lambda turn, _memory, _profile: SimpleNamespace(tick=turn.tick),
        profile_provider=lambda _tick: object(),
        persistence=lambda batch: persisted.append(batch.tick),
        adaptive_observer=lambda *_args: None,
        lock_directory=tmp_path,
        event_queue=queue,
    )
    monkeypatch.setattr(runtime, "_lock", NoopLock())
    turns = [FakeTurn(tick) for tick in range(1, 10_001)]

    for turn in turns:
        runtime.handle_event(turn)
        runtime.handle_event(turn)
    runtime.handle_event(turns[0])

    assert all(turn.submit_calls == 1 for turn in turns)
    assert runtime.snapshot().submitted_ticks == 10_000
    assert runtime.dedupe_window_size <= 2048
    assert queue.qsize() <= queue.maxsize
    assert len(persisted) == 10_000
