"""Real SQLite-backed dashboard data contracts."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from tests.integration.test_api import settings


def test_strategy_schema_exposes_bounded_fields_and_conflict_returns_current(tmp_path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        current = client.get("/api/v1/strategy").json()
        schema = client.get("/api/v1/strategy/schema").json()
        payload = {
            "expectedRevision": current["revision"],
            "profile": {**current["profile"], "worker_target": 22},
            "reason": "first",
        }
        assert client.put("/api/v1/strategy", json=payload).status_code == 200
        conflict = client.put("/api/v1/strategy", json=payload)

    assert schema["fields"]["worker_target"] == {"minimum": 2, "maximum": 23, "kind": "integer"}
    assert conflict.status_code == 409
    assert conflict.json()["details"]["current"]["revision"] == current["revision"]


def test_metrics_adaptive_reports_and_settings_are_sqlite_backed_and_redacted(tmp_path) -> None:
    application = create_app(settings(tmp_path))
    with TestClient(application) as client:
        services = application.state.services
        session = services.runtime_store.create_session(account_hash="local")
        services.metrics.save(session.session_id, 10, {"resources": 4, "population": 2})
        services.metrics.save(session.session_id, 14, {"resources": 9, "population": 3})
        base = services.strategies.current()
        services.adaptive.close_window(
            start_tick=0,
            end_tick=14,
            sample_count=14,
            base_revision=base.revision,
            skill_fingerprint="f" * 64,
            raw_score=28,
            status="EVALUATED",
        )
        metrics = client.get("/api/v1/metrics/series").json()
        reports = client.get("/api/v1/adaptive/reports").json()
        safe_settings = client.get("/api/v1/settings")

    assert [point["tick"] for point in metrics["points"]] == [10, 14]
    assert reports["items"][0]["scorePerTick"] == 2
    assert "apiKey" not in safe_settings.text
    assert "baseUrl" not in safe_settings.text
