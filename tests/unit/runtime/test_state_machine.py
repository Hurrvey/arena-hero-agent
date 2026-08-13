import threading
from types import SimpleNamespace

import pytest

from app.runtime.agent_runtime import AgentRuntime
from app.runtime.models import RuntimeConflict, RuntimeStatus


class EmptyClient:
    def __init__(self) -> None:
        self.closed = False
        self.release = threading.Event()

    def events(self):
        self.release.wait(1)
        return iter(())

    def close(self) -> None:
        self.closed = True
        self.release.set()


def runtime(tmp_path, client=None):
    client = client or EmptyClient()
    return AgentRuntime(
        api_key="key",
        client_factory=lambda _key: client,
        planner=lambda turn, memory, profile: SimpleNamespace(plan={"tick": turn.tick}),
        profile_provider=lambda: SimpleNamespace(),
        persistence=lambda batch: None,
        adaptive_observer=lambda *args: None,
        lock_directory=tmp_path,
    )


def test_start_pause_resume_stop_are_idempotent(tmp_path) -> None:
    agent = runtime(tmp_path)

    assert agent.start().status in {RuntimeStatus.STARTING, RuntimeStatus.RUNNING}
    assert agent.start().status in {RuntimeStatus.STARTING, RuntimeStatus.RUNNING}
    assert agent.pause().status is RuntimeStatus.PAUSED
    assert agent.pause().status is RuntimeStatus.PAUSED
    assert agent.resume().status is RuntimeStatus.RUNNING
    assert agent.resume().status is RuntimeStatus.RUNNING
    assert agent.stop().status is RuntimeStatus.STOPPED
    assert agent.stop().status is RuntimeStatus.STOPPED


def test_invalid_transition_returns_domain_conflict(tmp_path) -> None:
    agent = runtime(tmp_path)

    with pytest.raises(RuntimeConflict):
        agent.resume()


def test_pause_observes_turns_without_planning_or_submitting(tmp_path) -> None:
    planned: list[int] = []
    persisted: list[object] = []
    agent = runtime(tmp_path)
    agent._planner = lambda turn, memory, profile: planned.append(turn.tick)
    agent._persistence = persisted.append
    agent._set_status(RuntimeStatus.PAUSED)
    turn = SimpleNamespace(tick=4, submit=lambda: pytest.fail("submit while paused"))

    agent.handle_event(turn)

    assert planned == []
    assert persisted and persisted[0].kind == "SNAPSHOT_ONLY"


def test_resume_waits_for_a_new_authoritative_turn(tmp_path) -> None:
    submitted: list[int] = []
    agent = runtime(tmp_path)
    agent._set_status(RuntimeStatus.PAUSED)
    old = SimpleNamespace(tick=4, submit=lambda: submitted.append(4))
    agent.handle_event(old)

    agent.resume()
    agent.handle_event(old)
    new = SimpleNamespace(tick=5, submit=lambda: submitted.append(5) or SimpleNamespace(tick=5))
    agent.handle_event(new)

    assert submitted == [5]


def test_authentication_error_is_redacted_and_enters_error(tmp_path) -> None:
    class AuthenticationError(Exception):
        pass

    class FailingClient:
        def events(self):
            raise AuthenticationError("Bearer secret-key")

        def close(self):
            return None

    agent = runtime(tmp_path, FailingClient())

    agent.start()
    agent.join(timeout=2)

    snapshot = agent.snapshot()
    assert snapshot.status is RuntimeStatus.ERROR
    assert snapshot.error_code == "AUTHENTICATION_ERROR"
    assert "secret-key" not in (snapshot.error_message or "")
