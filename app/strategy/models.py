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
