from app.api.event_schema import EVENT_SCHEMA_VERSION, service_event_envelope
from app.storage.models import ServiceEvent


def test_every_service_event_uses_schema_version_one_envelope() -> None:
    envelope = service_event_envelope(
        ServiceEvent(
            seq=7,
            session_id="session",
            tick=42,
            event_type="plan.accepted",
            payload={"source": "AGENT"},
            created_at="2026-08-13T00:00:00+00:00",
        )
    )

    assert EVENT_SCHEMA_VERSION == 1
    assert envelope == {
        "schemaVersion": 1,
        "seq": 7,
        "type": "plan.accepted",
        "at": "2026-08-13T00:00:00+00:00",
        "runtimeId": "session",
        "tick": 42,
        "payload": {"source": "AGENT"},
    }
