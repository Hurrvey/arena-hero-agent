import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "agent.db",
        lock_directory=tmp_path / "locks",
        static_directory=tmp_path / "frontend",
        asset_directory=tmp_path / "assets",
        dotenv_path=tmp_path / "missing.env",
        legacy_adaptive_directory=tmp_path / "missing-adaptive",
    )


def test_health_ready_without_running_agent(tmp_path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_state_current_returns_404_when_unavailable(tmp_path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.get("/api/v1/state/current")

    assert response.status_code == 404
    assert response.json()["code"] == "STATE_NOT_AVAILABLE"


def test_start_without_key_returns_redacted_configuration_error(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ARENA_HERO_API_KEY", raising=False)
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post("/api/v1/agent/start")

    assert response.status_code == 503
    assert response.json()["code"] == "ARENA_HERO_KEY_MISSING"
    assert "key=" not in response.text.lower()


def test_lifecycle_endpoints_are_idempotent(tmp_path) -> None:
    class Manager:
        def __init__(self):
            self.status_value = "STOPPED"

        def status(self):
            return {"runtimeId": "test", "status": self.status_value}

        def start(self):
            self.status_value = "RUNNING"
            return self.status()

        def pause(self):
            self.status_value = "PAUSED"
            return self.status()

        def resume(self):
            self.status_value = "RUNNING"
            return self.status()

        def stop(self):
            self.status_value = "STOPPED"
            return self.status()

    manager = Manager()
    with TestClient(
        create_app(settings(tmp_path), services={"runtime_manager": manager})
    ) as client:
        assert client.post("/api/v1/agent/start").json()["status"] == "RUNNING"
        assert client.post("/api/v1/agent/start").json()["status"] == "RUNNING"
        assert client.post("/api/v1/agent/pause").json()["status"] == "PAUSED"
        assert client.post("/api/v1/agent/pause").json()["status"] == "PAUSED"
        assert client.post("/api/v1/agent/resume").json()["status"] == "RUNNING"
        assert client.post("/api/v1/agent/stop").json()["status"] == "STOPPED"


def test_strategy_conflict_is_409_and_preserves_newer_revision(tmp_path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        current = client.get("/api/v1/strategy").json()
        payload = {
            "expectedRevision": current["revision"],
            "profile": {**current["profile"], "worker_target": 22},
            "reason": "dashboard",
        }
        first = client.put("/api/v1/strategy", json=payload)
        second = client.put("/api/v1/strategy", json=payload)

    assert first.status_code == 200
    assert first.json()["status"] == "PENDING"
    assert second.status_code == 409
    assert second.json()["code"] == "STRATEGY_REVISION_CONFLICT"


def test_all_errors_have_code_message_request_id_and_details(tmp_path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.get("/api/v1/state/current")

    assert set(response.json()) == {"code", "message", "requestId", "details"}


def test_events_tail_rebases_to_the_latest_bounded_window(tmp_path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        store = client.app.state.services.runtime_store
        session = store.create_session(account_hash="tail-test")
        created_at = "2026-08-13T00:00:00+00:00"
        with store.database.connect() as connection:
            connection.executemany(
                """
                INSERT INTO service_events(
                    session_id, tick, event_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (
                        session.session_id,
                        tick,
                        "state.snapshot",
                        json.dumps({"tick": tick}),
                        created_at,
                    )
                    for tick in range(1, 1401)
                ),
            )
            connection.commit()

        response = client.get("/api/v1/events?tail=true&limit=300")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["events"]) == 300
    assert payload["events"][0]["seq"] == 1101
    assert payload["events"][-1]["seq"] == 1400
    assert payload["lastSeq"] == 1400
