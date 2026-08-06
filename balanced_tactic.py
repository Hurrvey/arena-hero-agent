from __future__ import annotations

import os
from getpass import getpass
from collections import defaultdict
from typing import Iterable

from arena_hero import ArenaHeroClient, Direction, UnitType


DIRECTIONS = (Direction.UP, Direction.RIGHT, Direction.DOWN, Direction.LEFT)
_DIRECTION_DELTAS = {direction: direction.delta for direction in DIRECTIONS}


def _enum_name(value) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value)).upper()


def _uuid_key(identifier) -> bytes:
    raw = getattr(identifier, "bytes", None)
    if raw is not None:
        return raw
    return str(identifier).encode("ascii", "replace")


def _distance(first: tuple[int, int], second: tuple[int, int]) -> int:
    return abs(first[0] - second[0]) + abs(first[1] - second[1])


def _aligned_range(origin: tuple[int, int], target: tuple[int, int]) -> int | None:
    dx = abs(target[0] - origin[0])
    dy = abs(target[1] - origin[1])
    if dx == 0 and 1 <= dy <= 3:
        return dy
    if dy == 0 and 1 <= dx <= 3:
        return dx
    if dx == dy and 1 <= dx <= 3:
        return dx
    return None


def _line_is_clear(
    origin: tuple[int, int],
    target: tuple[int, int],
    obstacles: Iterable[tuple[int, int]],
) -> bool:
    distance = _aligned_range(origin, target)
    if distance is None:
        return False
    obstacle_set = set(obstacles)
    step_x = 0 if target[0] == origin[0] else (1 if target[0] > origin[0] else -1)
    step_y = 0 if target[1] == origin[1] else (1 if target[1] > origin[1] else -1)
    return all(
        (origin[0] + step_x * index, origin[1] + step_y * index)
        not in obstacle_set
        for index in range(1, distance)
    )


def _direction_to_adjacent(
    origin: tuple[int, int], target: tuple[int, int]
) -> Direction | None:
    delta = (target[0] - origin[0], target[1] - origin[1])
    for direction, direction_delta in _DIRECTION_DELTAS.items():
        if direction_delta == delta:
            return direction
    return None


def _queue_ranger_actions(turn, acted: set[object]) -> None:
    for ranger in sorted(turn.rangers, key=lambda unit: _uuid_key(unit.id)):
        if ranger.id in acted:
            continue
        legal_targets = [
            enemy
            for enemy in turn.visible_enemies
            if _line_is_clear(ranger.position, enemy.position, turn.obstacle_cells)
        ]
        if not legal_targets:
            continue
        target = min(
            legal_targets,
            key=lambda enemy: (
                0 if _enum_name(getattr(enemy, "kind", "")) == "CORE" else 1,
                getattr(enemy, "hp", 0),
                _uuid_key(enemy.id),
            ),
        )
        ranger.shoot_cell(target.position)
        acted.add(ranger.id)


def _queue_vanguard_actions(turn, acted: set[object]) -> None:
    direction_order = {direction: index for index, direction in enumerate(DIRECTIONS)}
    for vanguard in sorted(turn.vanguards, key=lambda unit: _uuid_key(unit.id)):
        if vanguard.id in acted:
            continue
        by_cell: dict[tuple[int, int], list[object]] = defaultdict(list)
        for enemy in turn.visible_enemies:
            if _distance(vanguard.position, enemy.position) == 1:
                by_cell[enemy.position].append(enemy)
        candidates: list[tuple[tuple[object, ...], Direction]] = []
        for cell, enemies in by_cell.items():
            direction = _direction_to_adjacent(vanguard.position, cell)
            if direction is None:
                continue
            contains_core = any(
                _enum_name(getattr(enemy, "kind", "")) == "CORE"
                for enemy in enemies
            )
            rank = (
                0 if contains_core else 1,
                -len(enemies),
                direction_order[direction],
                cell[0],
                cell[1],
            )
            candidates.append((rank, direction))
        if not candidates:
            continue
        _, direction = min(candidates, key=lambda item: item[0])
        vanguard.sweep(direction)
        acted.add(vanguard.id)


def choose_actions(turn) -> None:
    if turn.core is None:
        return None
    acted: set[object] = set()
    _queue_ranger_actions(turn, acted)
    _queue_vanguard_actions(turn, acted)


def load_api_key() -> str:
    return os.environ.get("ARENA_HERO_API_KEY") or getpass("Arena Hero API key: ")


def play(api_key: str | None = None) -> None:
    raise NotImplementedError
