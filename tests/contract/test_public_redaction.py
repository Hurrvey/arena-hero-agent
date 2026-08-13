from app.observability.redaction import PublicIdMapper, redact_public_payload


def test_public_state_and_events_replace_ids_with_session_short_ids() -> None:
    mapper = PublicIdMapper("session")
    payload = redact_public_payload(
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "target_id": "00000000-0000-0000-0000-000000000002",
            "owner_username": "private-player",
            "authorization": "Bearer secret",
            "api_key": "secret",
        },
        mapper,
    )

    assert payload == {"id": "E1", "targetId": "E2"}
