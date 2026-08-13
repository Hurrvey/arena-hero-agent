from fastapi.testclient import TestClient

from app.main import create_app


def test_openapi_has_no_key_authorization_or_raw_uuid_fields(tmp_path) -> None:
    with TestClient(create_app()) as client:
        schema = client.get("/openapi.json").json()

    encoded = str(schema).lower()
    assert "api_key" not in encoded
    assert "authorization" not in encoded
    assert "raw_uuid" not in encoded


def test_security_headers_and_same_origin_csp_are_present() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
