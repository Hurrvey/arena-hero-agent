"""Pure, deterministic Core-defense assessment for Arena Hero tactics.

This module deliberately contains no SDK action construction and no memory of
previous Turns. It only classifies the current visible geometry and selects a
stable local defender roster. The live tactic remains responsible for turning
that assessment into legal actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable, Sequence


Cell = tuple[int, int]
CARDINAL_STEPS: tuple[Cell, ...] = ((0, -1), (1, 0), (0, 1), (-1, 0))


class ThreatLevel(IntEnum):
    """Ordered Core threat states for the current visible Turn."""

    CLEAR = 0
    WATCH = 1
    APPROACH = 2
    ATTACK = 3
    LETHAL = 4


@dataclass(frozen=True, slots=True)
class DefenseAssessment:
    level: ThreatLevel
    attacker_ids: frozenset[object]
    approacher_ids: frozenset[object]
    watch_ids: frozenset[object]
    incoming_damage: int
    core_effective_hp: int

    @classmethod
    def clear(cls, core_effective_hp: int = 0) -> "DefenseAssessment":
        return cls(
            level=ThreatLevel.CLEAR,
            attacker_ids=frozenset(),
            approacher_ids=frozenset(),
            watch_ids=frozenset(),
            incoming_damage=0,
            core_effective_hp=max(0, int(core_effective_hp)),
        )


@dataclass(frozen=True, slots=True)
class DefenderRoster:
    vanguard_ids: frozenset[object]
    ranger_ids: frozenset[object]

    @property
    def all_ids(self) -> frozenset[object]:
        return self.vanguard_ids | self.ranger_ids

    @classmethod
    def empty(cls) -> "DefenderRoster":
        return cls(frozenset(), frozenset())


def _enum_name(value: object) -> str:
    if value is None:
        return ""
    raw = getattr(value, "value", value)
    raw = getattr(raw, "name", raw)
    return str(raw).upper().rsplit(".", 1)[-1]


def _unit_type(unit: object) -> str:
    return _enum_name(getattr(unit, "unit_type", None))


def _distance(left: Cell, right: Cell) -> int:
    return abs(left[0] - right[0]) + abs(left[1] - right[1])


def _raw_id_key(identifier: object) -> bytes:
    raw = getattr(identifier, "bytes", None)
    if isinstance(raw, bytes):
        return raw
    if isinstance(identifier, bytes):
        return identifier
    if isinstance(identifier, int):
        return f"{identifier:+040d}".encode("ascii")
    return str(identifier).encode("utf-8", "surrogatepass")


def _same_id(left: object, right: object) -> bool:
    return _raw_id_key(left) == _raw_id_key(right)


def _line_is_clear(origin: Cell, target: Cell, obstacles: set[Cell]) -> bool:
    dx = target[0] - origin[0]
    dy = target[1] - origin[1]
    distance = max(abs(dx), abs(dy))
    if distance < 1 or distance > 3:
        return False
    if not (dx == 0 or dy == 0 or abs(dx) == abs(dy)):
        return False
    step_x = 0 if dx == 0 else (1 if dx > 0 else -1)
    step_y = 0 if dy == 0 else (1 if dy > 0 else -1)
    return all(
        (origin[0] + step_x * step, origin[1] + step_y * step)
        not in obstacles
        for step in range(1, distance)
    )


def enemy_can_attack_cell(enemy: object, cell: Cell, obstacles: set[Cell]) -> bool:
    """Return whether a visible combat Unit can legally attack ``cell`` now."""

    position = getattr(enemy, "position", None)
    if position is None:
        return False
    unit_type = _unit_type(enemy)
    if unit_type == "VANGUARD":
        return _distance(position, cell) == 1
    if unit_type == "RANGER":
        return _line_is_clear(position, cell, obstacles)
    return False


@dataclass(frozen=True, slots=True)
class _ProjectedUnit:
    unit_type: str
    position: Cell


def _can_attack_after_one_step(
    enemy: object,
    cell: Cell,
    obstacles: set[Cell],
) -> bool:
    position = getattr(enemy, "position", None)
    unit_type = _unit_type(enemy)
    if position is None or unit_type not in {"VANGUARD", "RANGER"}:
        return False
    for step_x, step_y in CARDINAL_STEPS:
        destination = (position[0] + step_x, position[1] + step_y)
        if destination == cell or destination in obstacles:
            continue
        if enemy_can_attack_cell(
            _ProjectedUnit(unit_type, destination),
            cell,
            obstacles,
        ):
            return True
    return False


def assess_core_defense(
    core_position: Cell,
    core_hp: int,
    core_shield: int,
    visible_enemies: Iterable[object],
    obstacles: Iterable[Cell],
    *,
    watch_radius: int,
) -> DefenseAssessment:
    """Classify current visible danger without cross-Turn enemy memory."""

    blocked = set(obstacles)
    effective_hp = max(0, int(core_hp)) + max(0, int(core_shield))
    attackers: set[object] = set()
    approachers: set[object] = set()
    watched: set[object] = set()

    for enemy in visible_enemies:
        if _unit_type(enemy) not in {"VANGUARD", "RANGER"}:
            continue
        identifier = getattr(enemy, "id", None)
        position = getattr(enemy, "position", None)
        if identifier is None or position is None:
            continue
        if _distance(position, core_position) <= watch_radius:
            watched.add(identifier)
        if enemy_can_attack_cell(enemy, core_position, blocked):
            attackers.add(identifier)
        elif _can_attack_after_one_step(enemy, core_position, blocked):
            approachers.add(identifier)

    incoming = len(attackers)
    if attackers:
        level = (
            ThreatLevel.LETHAL
            if effective_hp > 0 and incoming >= effective_hp
            else ThreatLevel.ATTACK
        )
    elif approachers:
        level = ThreatLevel.APPROACH
    elif watched:
        level = ThreatLevel.WATCH
    else:
        level = ThreatLevel.CLEAR

    return DefenseAssessment(
        level=level,
        attacker_ids=frozenset(attackers),
        approacher_ids=frozenset(approachers),
        watch_ids=frozenset(watched),
        incoming_damage=incoming,
        core_effective_hp=effective_hp,
    )


def select_defenders(
    core_position: Cell,
    units: Sequence[object],
    *,
    carrier_id: object | None,
    vanguard_target: int,
    ranger_target: int,
) -> DefenderRoster:
    """Select a stable nearest roster, never assigning the Beacon carrier."""

    def candidates(unit_type: str) -> list[object]:
        matching = [
            unit
            for unit in units
            if _unit_type(unit) == unit_type
            and getattr(unit, "position", None) is not None
            and (
                carrier_id is None
                or not _same_id(getattr(unit, "id", None), carrier_id)
            )
        ]
        return sorted(
            matching,
            key=lambda unit: (
                _distance(getattr(unit, "position"), core_position),
                _raw_id_key(getattr(unit, "id", None)),
            ),
        )

    vanguards = candidates("VANGUARD")[: max(0, int(vanguard_target))]
    rangers = candidates("RANGER")[: max(0, int(ranger_target))]
    return DefenderRoster(
        vanguard_ids=frozenset(getattr(unit, "id") for unit in vanguards),
        ranger_ids=frozenset(getattr(unit, "id") for unit in rangers),
    )
