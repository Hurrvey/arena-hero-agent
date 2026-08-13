import pytest

from app.adaptive.transport import ProviderUrlError, parse_json_object, validate_provider_url


def test_parser_requires_one_complete_json_object_without_prefix_or_suffix() -> None:
    assert parse_json_object('{"ok":true}') == {"ok": True}
    for invalid in ('prefix {"ok":true}', '{"ok":true} suffix', '```json\n{"ok":true}\n```', "[]"):
        with pytest.raises(ValueError):
            parse_json_object(invalid)


def test_disallowed_private_or_metadata_base_url_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.adaptive.transport.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("169.254.169.254", 443))],
    )
    with pytest.raises(ProviderUrlError):
        validate_provider_url("https://metadata.example/v1")
    with pytest.raises(ProviderUrlError):
        validate_provider_url("http://api.example/v1")


def test_local_http_requires_explicit_development_setting(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.adaptive.transport.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 8000))],
    )
    with pytest.raises(ProviderUrlError):
        validate_provider_url("http://127.0.0.1:8000/v1")
    assert validate_provider_url("http://127.0.0.1:8000/v1", allow_local_http=True).endswith("/v1")
