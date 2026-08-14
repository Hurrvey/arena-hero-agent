from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from app.main import create_app
from app.strategy.exploration import ExplorationDelta
from tests.integration.test_api import settings
from tests.unit.storage.test_exploration_repository import chunk


def activate_scope(client: TestClient, scope: str) -> None:
    services = client.app.state.services
    session = services.runtime_store.create_session(account_hash=scope)
    services.session_id = session.session_id
    services.runtime_factory._session_id = session.session_id
    services.runtime_factory._account_scope = scope


def test_exploration_endpoint_returns_only_current_account_window(tmp_path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        services = client.app.state.services
        services.exploration.merge_delta(
            "account-a",
            ExplorationDelta(1, (chunk(explored=1),)),
        )
        services.exploration.merge_delta(
            "account-b",
            ExplorationDelta(1, (chunk(explored=2),)),
        )
        activate_scope(client, "account-a")

        response = client.get(
            "/api/v1/exploration?minX=0&minY=0&maxX=1&maxY=0"
        )

    assert response.status_code == 200
    assert response.json() == {
        "revision": 1,
        "bounds": {"minX": 0, "minY": 0, "maxX": 1, "maxY": 0},
        "exploredCells": [[0, 0]],
        "knownObstacleCells": [],
    }
    assert "account" not in response.text.lower()


def test_exploration_endpoint_rejects_missing_session_and_oversized_window(
    tmp_path,
) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        missing = client.get(
            "/api/v1/exploration?minX=0&minY=0&maxX=1&maxY=1"
        )
        activate_scope(client, "account-a")
        oversized = client.get(
            "/api/v1/exploration?minX=0&minY=0&maxX=96&maxY=95"
        )

    assert missing.status_code == 404
    assert missing.json()["code"] == "EXPLORATION_NOT_AVAILABLE"
    assert oversized.status_code == 422
    assert oversized.json()["code"] == "EXPLORATION_WINDOW_INVALID"


def test_etag_varies_by_revision_and_normalized_window(tmp_path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        services = client.app.state.services
        services.exploration.merge_delta(
            "account-a",
            ExplorationDelta(1, (chunk(explored=1),)),
        )
        activate_scope(client, "account-a")
        first = client.get(
            "/api/v1/exploration?minX=0&minY=0&maxX=1&maxY=0"
        )
        cached = client.get(
            "/api/v1/exploration?minX=0&minY=0&maxX=1&maxY=0",
            headers={"If-None-Match": first.headers["etag"]},
        )
        other_window = client.get(
            "/api/v1/exploration?minX=0&minY=0&maxX=2&maxY=0"
        )

    assert cached.status_code == 304
    assert cached.content == b""
    assert first.headers["etag"] != other_window.headers["etag"]
    expected_etag = '"' + hashlib.sha256(b"1:0:0:1:0").hexdigest() + '"'
    assert first.headers["etag"] == expected_etag
