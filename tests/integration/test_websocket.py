from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def settings(tmp_path):
    return Settings(
        database_path=tmp_path / "agent.db",
        lock_directory=tmp_path / "locks",
        static_directory=tmp_path / "frontend",
        asset_directory=tmp_path / "assets",
        dotenv_path=tmp_path / "missing.env",
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
