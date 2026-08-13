"""Current visible attack opportunities without fog-of-war inference."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import TypeAlias

from .models import CellRisk, EntityKind, EntitySnapshot, Position, validate_position

VisibleRiskMap: TypeAlias = Mapping[Position, CellRisk]

_CARDINAL_DIRECTIONS: tuple[Position, ...] = ((-1, 0), (0, -1), (0, 1), (1, 0))
_RANGER_DIRECTIONS: tuple[Position, ...] = (
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
)
_NO_RISK = CellRisk()


def _threatened_cells(
    enemy: EntitySnapshot,
    obstacles: frozenset[Position],
) -> tuple[Position, ...]:
    origin_x, origin_y = enemy.position
    if enemy.kind is EntityKind.VANGUARD:
        return tuple(
            (origin_x + offset_x, origin_y + offset_y)
            for offset_x, offset_y in _CARDINAL_DIRECTIONS
        )
    if enemy.kind is not EntityKind.RANGER:
        return ()

    threatened: list[Position] = []
    for step_x, step_y in _RANGER_DIRECTIONS:
        for distance in range(1, 4):
            cell = (origin_x + step_x * distance, origin_y + step_y * distance)
            threatened.append(cell)
            if cell in obstacles:
                break
    return tuple(threatened)


def build_visible_risk_map(
    friendly_objects: Iterable[EntitySnapshot],
    visible_enemies: Iterable[EntitySnapshot],
    obstacle_cells: Iterable[Position],
) -> VisibleRiskMap:
    """Build immutable one-Tick risk from visible living combat enemies only."""

    # Retained in the boundary because callers create both sides from one Turn;
    # attack opportunity itself does not depend on the friendly roster.
    tuple(friendly_objects)
    obstacles = frozenset(obstacle_cells)
    for obstacle in obstacles:
        validate_position(obstacle)

    attackers_by_cell: dict[Position, set[bytes]] = defaultdict(set)
    for enemy in visible_enemies:
        if enemy.controlled or enemy.hp <= 0:
            continue
        for cell in _threatened_cells(enemy, obstacles):
            attackers_by_cell[cell].add(enemy.entity_id)

    risks = {
        cell: CellRisk(
            visible_attack_count=len(attackers),
            expected_damage=len(attackers),
            attackers=tuple(sorted(attackers)),
        )
        for cell, attackers in attackers_by_cell.items()
    }
    return MappingProxyType(risks)


def risk_at(risk_map: VisibleRiskMap, position: Position) -> CellRisk:
    validate_position(position)
    return risk_map.get(position, _NO_RISK)
