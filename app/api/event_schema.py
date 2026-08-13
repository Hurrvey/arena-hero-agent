"""One canonical public envelope for committed service events."""

from __future__ import annotations

EVENT_SCHEMA_VERSION = 1


def service_event_envelope(event) -> dict[str, object]:
    return {
        "schemaVersion": EVENT_SCHEMA_VERSION,
        "seq": int(event.seq),
        "type": str(event.event_type),
        "at": str(event.created_at),
        "runtimeId": str(event.session_id),
        "tick": event.tick,
        "payload": dict(event.payload),
    }
