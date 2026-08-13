"""Runtime event classification and public batch helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from app.observability.redaction import PublicIdMapper, redact_public_payload

from .models import RuntimeBatch


def is_turn(event: object) -> bool:
    return hasattr(event, "submit") and hasattr(event, "tick")


def is_receipt(event: object) -> bool:
    kind = str(getattr(event, "kind", "")).upper()
    return kind == "RECEIVED" or (
        hasattr(event, "source") and hasattr(event, "accepted") and not hasattr(event, "submit")
    )


def receipt_batch(event: object) -> RuntimeBatch:
    source = str(getattr(getattr(event, "source", None), "value", getattr(event, "source", "")))
    return RuntimeBatch(
        "RECEIPT",
        int(getattr(event, "tick", 0)),
        receipt=event,
        source=source,
    )


def json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return json_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [json_value(item) for item in value]
    if hasattr(value, "__dict__"):
        return {
            key: json_value(item) for key, item in vars(value).items() if not key.startswith("_")
        }
    return value


def serialize_turn(
    turn: object, mapper: PublicIdMapper
) -> tuple[dict[str, object], dict[str, object]]:
    state = getattr(turn, "state", turn)
    raw = json_value(state)
    if not isinstance(raw, dict):
        raw = {"tick": int(getattr(turn, "tick", 0)), "state": raw}
    raw.setdefault("tick", int(getattr(turn, "tick", 0)))
    public = redact_public_payload(raw, mapper)
    return raw, public
