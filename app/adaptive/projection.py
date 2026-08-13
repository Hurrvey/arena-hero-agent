"""Bounded allowlist projection from private telemetry to untrusted LLM data."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping

MAX_EVENTS = 64
MAX_LABEL = 64
_LABEL = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_METRIC_NAMES = {
    "beacon_ticks",
    "resources_harvested",
    "resources_deposited",
    "resources_captured",
    "damage_dealt",
    "core_participations",
    "units_lost",
    "core_losses",
    "failed_actions",
    "overflow_destroyed",
    "zero_resource_ticks",
    "idle_worker_ticks",
    "route_stalls",
    "oscillation_ticks",
    "runner_progress_ticks",
    "core_threat_ticks",
    "projected_lethal_ticks",
    "core_damage_taken",
    "defender_coverage",
    "worker_evacuations",
}


def _label(value: object) -> str | None:
    normalized = str(value or "").strip().upper()
    return normalized if _LABEL.fullmatch(normalized) else None


def _counts(items: object) -> dict[str, int]:
    result: dict[str, int] = {}
    if not isinstance(items, (list, tuple)):
        return result
    for item in items:
        if not isinstance(item, Mapping):
            continue
        kind = _label(item.get("unit_type", item.get("unitType", item.get("kind"))))
        if kind:
            result[kind] = result.get(kind, 0) + 1
    return dict(sorted(result.items())[:32])


def _nonnegative_numbers(values: object, allowed: set[str]) -> dict[str, float]:
    if not isinstance(values, Mapping):
        return {}
    result: dict[str, float] = {}
    for name in sorted(allowed):
        value = values.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            number = float(value)
            if math.isfinite(number) and number >= 0:
                result[name] = number
    return result


def project_record(record: Mapping[str, object]) -> dict[str, object]:
    """Return only bounded facts needed to score Beacon/economy/defense/combat."""

    result: dict[str, object] = {"untrusted": True}
    tick = record.get("tick")
    if type(tick) is int and tick >= 0:
        result["tick"] = tick
    state = record.get("state")
    if isinstance(state, Mapping):
        safe_state = _nonnegative_numbers(
            state,
            {"resources", "resource_capacity", "resource_space", "population"},
        )
        if safe_state:
            result["state"] = safe_state
    core = record.get("core")
    if isinstance(core, Mapping):
        safe_core = _nonnegative_numbers(core, {"hp", "shield", "move_progress", "move_required_ticks"})
        kind = _label(core.get("state"))
        if kind:
            safe_core["state"] = kind
        if safe_core:
            result["core"] = safe_core
    for source, target in (("units", "unit_counts"), ("visible_enemies", "visible_enemy_counts")):
        counts = _counts(record.get(source))
        if counts:
            result[target] = counts
    beacon = record.get("beacon")
    if isinstance(beacon, Mapping):
        status = _label(beacon.get("status"))
        if status:
            result["beacon"] = {"status": status, "controlled": beacon.get("controlled") is True}
    events: list[dict[str, object]] = []
    for event in (record.get("events") or ())[:MAX_EVENTS]:
        if not isinstance(event, Mapping) or not (event_type := _label(event.get("event_type"))):
            continue
        row: dict[str, object] = {"event_type": event_type}
        reason = _label(event.get("reason_code"))
        if reason:
            row["reason_code"] = reason
        values = _nonnegative_numbers(
            event.get("values"),
            {"amount", "damage", "targets_hit", "hp_damage", "shield_damage"},
        )
        if values:
            row["values"] = values
        events.append(row)
    if events:
        result["events"] = events
    metrics = _nonnegative_numbers(record.get("metrics"), _METRIC_NAMES)
    if metrics:
        result["metrics"] = metrics
    defense = record.get("defense")
    if isinstance(defense, Mapping):
        safe_defense = _nonnegative_numbers(
            defense,
            {"core_threat_ticks", "projected_lethal_ticks", "incoming_core_damage", "defender_coverage", "worker_evacuations"},
        )
        level = _label(defense.get("defense_level"))
        if level in {"CLEAR", "WATCH", "APPROACH", "ATTACK", "LETHAL"}:
            safe_defense["defense_level"] = level
        if safe_defense:
            result["defense"] = safe_defense
    return result


def bounded_records(
    records: list[dict[str, object]],
    *,
    max_records: int = 24,
    max_chars: int = 12_000,
) -> list[dict[str, object]]:
    """Bound the full serialized telemetry packet, not only its row count."""

    safe = records[-max_records:]
    while safe and len(json.dumps(safe, sort_keys=True, separators=(",", ":"))) > max_chars:
        safe = safe[1:]
    return safe
