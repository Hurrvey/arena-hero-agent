"""Long-run boundedness checks without connecting a real Arena Hero account."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from arena_hero import UnitType
from app.runtime.agent_runtime import AgentRuntime
from app.runtime.event_queue import RuntimeEventQueue
from balanced_tactic import TacticMemory, choose_actions
from test_balanced_tactic import FakeController, make_turn


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


def test_frontier_worker_progress_has_no_four_tick_two_cell_loop() -> None:
    memory = TacticMemory()
    current = (1, 0)
    positions = [current]
    reasons: list[str] = []
    core_id = UUID(int=100)
    worker_id = UUID(int=1)

    for tick in range(1, 21):
        core = FakeController(
            object_id=core_id,
            position=(0, 0),
            hp=5,
            shield=10,
        )
        worker = FakeController(
            object_id=worker_id,
            position=current,
            hp=2,
            unit_type=UnitType.WORKER,
        )
        turn = make_turn(
            core=core,
            units=(worker,),
            resources=0,
            beacon=SimpleNamespace(
                position=(100, 100),
                status="CARRIED",
                carrier_id=core_id,
            ),
        )
        turn.tick = tick
        choose_actions(turn, memory)
        if worker.actions:
            direction = worker.actions[-1][1]
            dx, dy = direction.delta
            current = (current[0] + dx, current[1] + dy)
            positions.append(current)
            reasons.append(memory.planned_reason_codes[worker_id])

    assert all(
        not (
            positions[index - 4] == positions[index - 2]
            and positions[index - 3] == positions[index - 1]
        )
        for index in range(4, len(positions))
    )
    assert set(reasons) <= {"SCOUT_FRONTIER", "SCOUT_REASSIGNED", "SCOUT_WAIT_NO_SAFE_FRONTIER"}
