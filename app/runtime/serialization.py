"""Runtime event classification and public batch helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any
from uuid import UUID

from app.observability.redaction import PublicIdMapper, redact_public_payload

from .models import RuntimeBatch


def is_turn(event: object) -> bool:
    return hasattr(event, "submit") and hasattr(event, "tick")


def is_receipt(event: object) -> bool:
    kind = str(getattr(event, "kind", "")).upper()
    return kind == "RECEIVED" or (
        hasattr(event, "source")
        and not hasattr(event, "submit")
        and (
            hasattr(event, "accepted") or (hasattr(event, "received_at") and hasattr(event, "plan"))
        )
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


def serialize_public_plan(raw_plan: Mapping[str, Any], mapper: PublicIdMapper) -> dict[str, object]:
    """Redact a CommandPlan, including UUIDs used as unit-action mapping keys."""

    raw_actions = raw_plan.get("unit_actions", raw_plan.get("unitActions"))
    non_action_fields = {
        key: value for key, value in raw_plan.items() if key not in {"unit_actions", "unitActions"}
    }
    public = redact_public_payload(non_action_fields, mapper)
    if not isinstance(public, dict):
        return {}
    if isinstance(raw_actions, Mapping):
        public["unitActions"] = {
            mapper.short(identifier): redact_public_payload(action, mapper)
            for identifier, action in raw_actions.items()
        }
    return public


def serialize_public_explanation(explanation: object, mapper: PublicIdMapper) -> dict[str, object]:
    """Project planner reasoning without exposing stable Arena entity identifiers."""

    actions: list[dict[str, object]] = []
    for action in tuple(getattr(explanation, "actions", ()) or ()):
        identifier = getattr(action, "entity_id", None)
        if isinstance(identifier, bytes) and len(identifier) == 16:
            identifier = str(UUID(bytes=identifier))
        item: dict[str, object] = {
            "entityId": mapper.short(identifier),
            "actionType": str(getattr(action, "action_type", "WAIT")),
            "reasonCode": str(getattr(action, "reason_code", "UNSPECIFIED")),
            "riskBefore": int(getattr(action, "risk_before", 0)),
            "riskAfter": int(getattr(action, "risk_after", 0)),
        }
        target = getattr(action, "target", None)
        if target is not None:
            item["target"] = list(target)
        actions.append(item)
    return {"actions": actions}


def serialize_resolution_events(
    turn: object, mapper: PublicIdMapper
) -> tuple[dict[str, object], ...]:
    """Return browser-safe results from the plan resolved before this Turn."""

    observed_tick = int(getattr(turn, "tick", 0))
    result: list[dict[str, object]] = []
    for event in tuple(getattr(turn, "events", ()) or ())[:1000]:
        raw = json_value(event)
        if not isinstance(raw, dict):
            continue
        public = redact_public_payload(raw, mapper)
        if not isinstance(public, dict):
            continue
        event_type = str(public.get("eventType", "UNKNOWN"))
        resolved_tick = max(0, int(raw.get("tick", observed_tick - 1)))
        actor = public.get("actorId")
        target = public.get("targetId")
        item: dict[str, object] = {
            "plan_tick": resolved_tick,
            "observed_tick": observed_tick,
            "event_type": event_type,
        }
        if actor is not None:
            item["actor_id"] = actor
            item["short_id"] = actor
        elif target is not None:
            item["target_id"] = target
            item["short_id"] = target
        for public_name, storage_name in (
            ("reasonCode", "reason_code"),
            ("position", "position"),
        ):
            if public_name in public and public[public_name] is not None:
                item[storage_name] = public[public_name]
        values = public.get("values")
        if isinstance(values, dict):
            safe_values = {key: value for key, value in values.items() if key != "destroyedBy"}
            if safe_values:
                item["values"] = safe_values
        result.append(item)
    return tuple(result)
