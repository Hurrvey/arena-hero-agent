"""Immutable value objects at the Arena Hero strategy boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

Position: TypeAlias = tuple[int, int]


class EntityKind(StrEnum):
    CORE = "CORE"
    WORKER = "WORKER"
    VANGUARD = "VANGUARD"
    RANGER = "RANGER"


def validate_position(position: Position) -> None:
    if (
        not isinstance(position, tuple)
        or len(position) != 2
        or any(not isinstance(value, int) or isinstance(value, bool) for value in position)
    ):
        raise ValueError("position must contain exactly two integer coordinates")


def _validate_non_negative_integer(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class EntitySnapshot:
    entity_id: bytes
    kind: EntityKind
    position: Position
    hp: int
    shield: int = 0
    controlled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.entity_id, bytes) or not self.entity_id:
            raise TypeError("entity_id must be non-empty bytes")
        if not isinstance(self.kind, EntityKind):
            raise TypeError("kind must be an EntityKind")
        validate_position(self.position)
        _validate_non_negative_integer("hp", self.hp)
        _validate_non_negative_integer("shield", self.shield)
        if not isinstance(self.controlled, bool):
            raise TypeError("controlled must be a boolean")


@dataclass(frozen=True, slots=True)
class CellRisk:
    visible_attack_count: int = 0
    expected_damage: int = 0
    attackers: tuple[bytes, ...] = ()

    def __post_init__(self) -> None:
        _validate_non_negative_integer("visible_attack_count", self.visible_attack_count)
        _validate_non_negative_integer("expected_damage", self.expected_damage)
        if self.visible_attack_count != len(self.attackers):
            raise ValueError("visible_attack_count must equal the number of attackers")
        if self.expected_damage != self.visible_attack_count:
            raise ValueError("every current v0.14 combat attack contributes exactly one damage")
        if any(not isinstance(attacker, bytes) or not attacker for attacker in self.attackers):
            raise ValueError("attackers must contain non-empty raw identifiers")
        if tuple(sorted(set(self.attackers))) != self.attackers:
            raise ValueError("attackers must be unique and sorted by raw identifier")


def _enum_name(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").upper().rsplit(".", 1)[-1]


def _raw_identifier(identifier: object) -> bytes:
    raw = getattr(identifier, "bytes", None)
    if isinstance(raw, bytes) and raw:
        return raw
    if isinstance(identifier, bytes) and identifier:
        return identifier
    return str(identifier).encode("utf-8", "replace")


def entity_snapshot_from_view(
    view: object,
    *,
    controlled: bool | None = None,
) -> EntitySnapshot | None:
    """Project one living SDK Core/Unit view into the pure strategy model."""

    raw_kind = _enum_name(getattr(view, "kind", None))
    unit_type = _enum_name(getattr(view, "unit_type", None))
    if raw_kind == "CORE":
        kind = EntityKind.CORE
    elif unit_type in {"WORKER", "VANGUARD", "RANGER"}:
        kind = EntityKind(unit_type)
    else:
        return None

    try:
        hp = int(getattr(view, "hp"))
        position = tuple(getattr(view, "position"))
    except (AttributeError, TypeError, ValueError):
        return None
    if hp <= 0:
        return None
    try:
        validate_position(position)
    except ValueError:
        return None

    resolved_controlled = (
        getattr(view, "controlled", True) if controlled is None else controlled
    )
    if not isinstance(resolved_controlled, bool):
        raise TypeError("controlled must be a boolean")
    shield = 0
    if kind is EntityKind.CORE:
        try:
            shield = max(0, int(getattr(view, "shield", 0)))
        except (TypeError, ValueError):
            return None
    return EntitySnapshot(
        entity_id=_raw_identifier(getattr(view, "id", "")),
        kind=kind,
        position=position,
        hp=hp,
        shield=shield,
        controlled=resolved_controlled,
    )
