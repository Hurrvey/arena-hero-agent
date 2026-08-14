import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace

from arena_hero import CommandPlan, CommandSource, Received

from app.runtime.agent_runtime import AgentRuntime
from app.runtime.event_queue import RuntimeEventQueue
from app.runtime.models import RuntimeStatus
from tests.fixtures.fake_game import FakeGameClient


class FakeTurn:
    def __init__(self, tick: int, order: list[str]) -> None:
        self.tick = tick
        self.order = order
        self.submissions = 0

    def submit(self):
        self.order.append("submit")
        self.submissions += 1
        return SimpleNamespace(tick=self.tick, source="AGENT", accepted=True)


def make_runtime(
    tmp_path,
    events,
    *,
    persistence=None,
    queue=None,
    planner=None,
    exploration=None,
):
    client = FakeGameClient(events)
    order: list[str] = []
    agent = AgentRuntime(
        api_key="key",
        client_factory=lambda _key: client,
        planner=planner
        or (
            lambda turn, memory, profile: (
                order.append("plan") or SimpleNamespace(plan={"tick": turn.tick})
            )
        ),
        profile_provider=lambda: SimpleNamespace(),
        persistence=persistence or (lambda batch: order.append("persist")),
        adaptive_observer=lambda *args: order.append("adaptive"),
        lock_directory=tmp_path,
        event_queue=queue,
        exploration=exploration,
    )
    return agent, client, order


def test_exploration_observes_before_plan_and_persists_after_submit(tmp_path) -> None:
    calls: list[str] = []
    observation = SimpleNamespace(
        current_cells=frozenset({(0, 0)}),
        delta=None,
        base_revision=0,
        loaded_history=True,
    )
    exploration = SimpleNamespace(
        observe_turn=lambda turn, memory: calls.append("observe") or observation,
        persist=lambda value: calls.append("persist-exploration") or 1,
    )

    def persistence(batch):
        calls.append("persist")
        assert batch.exploration is observation
        exploration.persist(batch.exploration)

    turn = FakeTurn(8, calls)
    agent, _client, _order = make_runtime(
        tmp_path,
        (),
        persistence=persistence,
        planner=lambda turn, memory, profile: (
            calls.append("plan") or SimpleNamespace(plan={"tick": turn.tick})
        ),
        exploration=exploration,
    )

    agent.handle_event(turn)

    assert calls == ["observe", "plan", "submit", "persist", "persist-exploration"]


def test_paused_runtime_observes_before_snapshot_persistence(tmp_path) -> None:
    calls: list[str] = []
    observation = SimpleNamespace(current_cells=frozenset({(0, 0)}))
    exploration = SimpleNamespace(
        observe_turn=lambda turn, memory: calls.append("observe") or observation,
    )
    agent, _client, _order = make_runtime(
        tmp_path,
        (),
        persistence=lambda batch: calls.append(f"persist-{batch.kind}"),
        exploration=exploration,
    )
    agent._set_status(RuntimeStatus.PAUSED)
    turn = FakeTurn(9, calls)

    agent.handle_event(turn)

    assert calls == ["observe", "persist-SNAPSHOT_ONLY"]


def test_duplicate_turn_submits_at_most_once(tmp_path) -> None:
    order: list[str] = []
    turn = FakeTurn(3, order)
    agent, _client, _ = make_runtime(tmp_path, (turn, turn))

    agent.start()
    agent.join(timeout=3)

    assert turn.submissions == 1


def test_agent_and_manual_received_are_both_persisted_with_source(tmp_path) -> None:
    persisted = []
    agent, _client, _ = make_runtime(
        tmp_path,
        (
            SimpleNamespace(tick=2, source="AGENT", accepted=True, kind="RECEIVED"),
            SimpleNamespace(tick=2, source="MANUAL", accepted=True, kind="RECEIVED"),
        ),
        persistence=persisted.append,
    )

    agent.start()
    agent.join(timeout=3)

    assert [batch.source for batch in persisted] == ["AGENT", "MANUAL"]


def test_official_sdk_received_event_is_classified_and_persisted(tmp_path) -> None:
    persisted = []
    receipt = Received(
        tick=2,
        source=CommandSource.AGENT,
        received_at=datetime.now(UTC),
        plan=CommandPlan(tick=2),
    )
    agent, _client, _ = make_runtime(tmp_path, (receipt,), persistence=persisted.append)

    agent.start()
    agent.join(timeout=3)

    assert len(persisted) == 1
    assert persisted[0].kind == "RECEIPT"
    assert persisted[0].source == "AGENT"


def test_submit_precedes_slow_persistence_and_adaptive_observation(tmp_path) -> None:
    order: list[str] = []
    turn = FakeTurn(4, order)

    def slow_persistence(batch):
        order.append("persist")
        time.sleep(0.02)

    agent, _client, runtime_order = make_runtime(tmp_path, (turn,), persistence=slow_persistence)
    order.extend(runtime_order)

    agent.start()
    agent.join(timeout=3)

    assert order.index("submit") < order.index("persist")
    assert order.index("submit") < runtime_order.index("adaptive")


def test_stop_waits_for_current_submit_then_closes_and_releases_lock(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingTurn(FakeTurn):
        def submit(self):
            started.set()
            release.wait(2)
            return super().submit()

    order: list[str] = []
    turn = BlockingTurn(5, order)
    agent, client, _ = make_runtime(tmp_path, (turn,))
    agent.start()
    assert started.wait(1)
    stopper = threading.Thread(target=agent.stop)
    stopper.start()
    time.sleep(0.02)
    assert stopper.is_alive()
    release.set()
    stopper.join(2)

    assert agent.snapshot().status is RuntimeStatus.STOPPED
    assert client.closed
    replacement, _client, _ = make_runtime(tmp_path, ())
    replacement.start()
    replacement.join(2)


def test_full_low_priority_queue_does_not_block_submit() -> None:
    queue = RuntimeEventQueue(maxsize=2)
    queue.put_low("one")
    queue.put_low("two")

    assert queue.put_critical("critical")
    assert queue.get(timeout=0.1) == "two"
    assert queue.get(timeout=0.1) == "critical"
