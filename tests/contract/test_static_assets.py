from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT, Settings
from app.main import create_app


def test_every_manifest_asset_is_served_with_expected_mime_type(tmp_path) -> None:
    settings = Settings(
        database_path=tmp_path / "agent.db",
        lock_directory=tmp_path / "locks",
        static_directory=PROJECT_ROOT / "frontend",
        asset_directory=PROJECT_ROOT / "arena-hero-ui-assets",
        dotenv_path=tmp_path / "missing.env",
        legacy_adaptive_directory=tmp_path / "missing-adaptive",
    )
    manifest = json.loads(
        (settings.asset_directory / "ASSET-MANIFEST.json").read_text(encoding="utf-8")
    )
    with TestClient(create_app(settings)) as client:
        for asset in manifest["assets"]:
            response = client.get("/assets/arena-hero/" + asset["path"])
            assert response.status_code == 200, asset["path"]
            expected = "image/" if asset["path"].endswith((".svg", ".png")) else "text/css"
            assert response.headers["content-type"].startswith(expected), asset["path"]


def test_public_shell_and_settings_contain_no_secret_or_full_uuid(tmp_path) -> None:
    settings = Settings(
        database_path=tmp_path / "agent.db",
        lock_directory=tmp_path / "locks",
        static_directory=PROJECT_ROOT / "frontend",
        asset_directory=PROJECT_ROOT / "arena-hero-ui-assets",
        dotenv_path=tmp_path / "missing.env",
        legacy_adaptive_directory=tmp_path / "missing-adaptive",
    )
    with TestClient(create_app(settings)) as client:
        payload = client.get("/").text + client.get("/api/v1/settings").text

    lowered = payload.lower()
    assert "authorization: bearer" not in lowered
    assert "arena_hero_api_key" not in lowered
    assert "arena_hero_llm_api_key" not in lowered
