"""Authoritative v0.14 visibility derived only from friendly living objects."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeAlias

from .models import EntityKind, EntitySnapshot, Position, validate_position

VisibilityMap: TypeAlias = frozenset[Position]

VISION_RADIUS: dict[EntityKind, int] = {
    EntityKind.CORE: 5,
    EntityKind.WORKER: 3,
    EntityKind.VANGUARD: 4,
    EntityKind.RANGER: 5,
}


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


def supercover_cells(origin: Position, target: Position) -> tuple[Position, ...]:
    """Return every grid cell touched from ``origin`` to ``target``.

    The origin is omitted and the target is included. When the centre-to-centre
    segment crosses a corner, both side cells and the diagonal cell are included.
    This is the blocking geometry required by the v0.14 visibility contract.
    """

    validate_position(origin)
    validate_position(target)
    if origin == target:
        return ()

    x, y = origin
    target_x, target_y = target
    width = abs(target_x - x)
    height = abs(target_y - y)
    step_x = _sign(target_x - x)
    step_y = _sign(target_y - y)
    crossed_x = 0
    crossed_y = 0
    touched: list[Position] = []

    while crossed_x < width or crossed_y < height:
        decision = (1 + 2 * crossed_x) * height - (1 + 2 * crossed_y) * width
        if decision == 0:
            touched.append((x + step_x, y))
            touched.append((x, y + step_y))
            x += step_x
            y += step_y
            crossed_x += 1
            crossed_y += 1
            touched.append((x, y))
        elif decision < 0:
            x += step_x
            crossed_x += 1
            touched.append((x, y))
        else:
            y += step_y
            crossed_y += 1
            touched.append((x, y))

    return tuple(dict.fromkeys(touched))


def _has_line_of_sight(
    origin: Position,
    target: Position,
    obstacles: frozenset[Position],
) -> bool:
    touched = supercover_cells(origin, target)
    return all(cell not in obstacles for cell in touched if cell != target)


def compute_visible_cells(
    friendly_objects: Iterable[EntitySnapshot],
    known_obstacles: Iterable[Position],
) -> VisibilityMap:
    """Return the union of all living friendly objects' current visible cells."""

    obstacles = frozenset(known_obstacles)
    for obstacle in obstacles:
        validate_position(obstacle)

    visible: set[Position] = set()
    for entity in friendly_objects:
        if not entity.controlled or entity.hp <= 0:
            continue
        radius = VISION_RADIUS[entity.kind]
        origin_x, origin_y = entity.position
        for offset_x in range(-radius, radius + 1):
            remaining = radius - abs(offset_x)
            for offset_y in range(-remaining, remaining + 1):
                target = (origin_x + offset_x, origin_y + offset_y)
                if _has_line_of_sight(entity.position, target, obstacles):
                    visible.add(target)
    return frozenset(visible)
