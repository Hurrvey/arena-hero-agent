import asyncio
import threading

from fastapi.testclient import TestClient

from app.api.websocket import CommittedEventBroadcaster
from app.config import Settings
from app.main import create_app


def settings(tmp_path):
    return Settings(
        database_path=tmp_path / "agent.db",
        lock_directory=tmp_path / "locks",
        static_directory=tmp_path / "frontend",
        asset_directory=tmp_path / "assets",
        dotenv_path=tmp_path / "missing.env",
        legacy_adaptive_directory=tmp_path / "missing-adaptive",
    )


def test_websocket_hello_contains_current_max_seq(tmp_path) -> None:
    app = create_app(settings(tmp_path))
    with TestClient(app) as client, client.websocket_connect("/ws/v1/live") as websocket:
        hello = websocket.receive_json()

    assert hello == {"type": "hello", "schemaVersion": 1, "maxSeq": 0}


def test_events_after_replays_monotonic_events_without_duplicates(tmp_path) -> None:
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        store = app.state.services.runtime_store
        session = store.create_session(account_hash="account")
        store.save_turn_batch(
            session_id=session.session_id,
            tick=1,
            raw_snapshot={"tick": 1},
            public_snapshot={"tick": 1},
            raw_plan={"tick": 1},
            public_plan={"tick": 1},
            explanation={},
            resolution_events=(),
            service_events=(("ONE", {}), ("TWO", {})),
        )
        with client.websocket_connect("/ws/v1/live?afterSeq=0") as websocket:
            assert websocket.receive_json()["maxSeq"] == 2
            first = websocket.receive_json()
            second = websocket.receive_json()

    assert [first["seq"], second["seq"]] == [1, 2]
    assert [first["type"], second["type"]] == ["ONE", "TWO"]
    assert "eventType" not in first


def test_old_after_seq_returns_event_gap(tmp_path) -> None:
    configured = settings(tmp_path)
    configured = configured.with_updates(websocket_replay_limit=1)
    app = create_app(configured)
    with TestClient(app) as client:
        store = app.state.services.runtime_store
        session = store.create_session(account_hash="account")
        store.save_turn_batch(
            session_id=session.session_id,
            tick=1,
            raw_snapshot={},
            public_snapshot={},
            raw_plan={},
            public_plan={},
            explanation={},
            resolution_events=(),
            service_events=(("ONE", {}), ("TWO", {})),
        )
        with client.websocket_connect("/ws/v1/live?afterSeq=0") as websocket:
            websocket.receive_json()
            gap = websocket.receive_json()

    assert gap["type"] == "eventGap"


def test_only_committed_events_are_broadcast(tmp_path) -> None:
    app = create_app(settings(tmp_path))
    with TestClient(app) as client, client.websocket_connect("/ws/v1/live") as websocket:
        websocket.receive_json()
        assert app.state.services.broadcaster.publish_uncommitted({"seq": 99}) is False


def test_websocket_hello_uses_the_true_max_seq_after_more_than_one_page(tmp_path) -> None:
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        store = app.state.services.runtime_store
        session = store.create_session(account_hash="account")
        store.save_turn_batch(
            session_id=session.session_id,
            tick=1,
            raw_snapshot={},
            public_snapshot={},
            raw_plan={},
            public_plan={},
            explanation={},
            resolution_events=(),
            service_events=tuple(("event", {"index": index}) for index in range(1001)),
        )
        with client.websocket_connect("/ws/v1/live?afterSeq=1001") as websocket:
            hello = websocket.receive_json()

    assert hello["maxSeq"] == 1001


def test_background_runtime_thread_can_publish_to_websocket_event_loop(tmp_path) -> None:
    app = create_app(settings(tmp_path))
    with TestClient(app) as client, client.websocket_connect("/ws/v1/live") as websocket:
        websocket.receive_json()
        broadcaster = app.state.services.broadcaster
        error: list[BaseException] = []

        def publish() -> None:
            try:
                broadcaster.publish_committed(
                    {
                        "schemaVersion": 1,
                        "seq": 1,
                        "type": "runtime.status",
                        "at": "2026-08-13T00:00:00Z",
                        "runtimeId": "test",
                        "tick": 1,
                        "payload": {},
                    }
                )
            except RuntimeError as exc:
                error.append(exc)

        thread = threading.Thread(target=publish)
        thread.start()
        thread.join(1)
        event = websocket.receive_json()

    assert error == []
    assert event["type"] == "runtime.status"


def test_slow_subscriber_overflow_receives_gap_signal_instead_of_hanging() -> None:
    result: list[dict[str, object]] = []
    errors: list[BaseException] = []

    async def scenario() -> None:
        broadcaster = CommittedEventBroadcaster(queue_size=1)
        subscriber = broadcaster.subscribe()
        broadcaster.publish_committed({"seq": 1, "type": "one"})
        await asyncio.sleep(0)
        broadcaster.publish_committed({"seq": 2, "type": "two"})
        await asyncio.sleep(0)
        result.append(await asyncio.wait_for(subscriber.queue.get(), timeout=1))

    def run() -> None:
        asyncio.run(scenario())

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(2)

    assert not worker.is_alive()
    assert errors == []
    assert result == [{"type": "_eventGap", "maxSeq": 2}]
