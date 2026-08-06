from __future__ import annotations

import os
from getpass import getpass
from collections import defaultdict
from typing import Iterable

from arena_hero import ArenaHeroClient, Direction, UnitType


DIRECTIONS = (Direction.UP, Direction.RIGHT, Direction.DOWN, Direction.LEFT)
_DIRECTION_DELTAS = {direction: direction.delta for direction in DIRECTIONS}
CORE_RESERVE = 5
CORE_MAX_HP = 5
UNIT_MAX_HP = {"WORKER": 2, "VANGUARD": 4, "RANGER": 2}
UNIT_COSTS = {
    UnitType.WORKER: 5,
    UnitType.VANGUARD: 10,
    UnitType.RANGER: 12,
}


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


def _is_stationary_core(core) -> bool:
    state = getattr(getattr(core, "view", None), "state", None)
    if state is None:
        state = getattr(core, "state", "NORMAL")
    return _enum_name(state) == "NORMAL"


def _nearest_enemy_distance(position: tuple[int, int], enemies) -> int:
    distances = [_distance(position, enemy.position) for enemy in enemies]
    return min(distances, default=10**9)


def _step(position: tuple[int, int], direction: Direction) -> tuple[int, int] | None:
    dx, dy = _DIRECTION_DELTAS[direction]
    candidate = (position[0] + dx, position[1] + dy)
    if not all(-2**63 <= coordinate <= 2**63 - 1 for coordinate in candidate):
        return None
    return candidate


def _candidate_steps(
    unit,
    turn,
    occupied: tuple[tuple[object, tuple[int, int]], ...],
    reserved_destinations: set[tuple[int, int]],
):
    enemy_cells = {enemy.position for enemy in turn.visible_enemies}
    for index, direction in enumerate(DIRECTIONS):
        destination = _step(unit.position, direction)
        if (
            destination is None
            or destination in turn.obstacle_cells
            or destination in enemy_cells
            or destination in reserved_destinations
        ):
            continue
        other_count = sum(
            position == destination
            for object_id, position in occupied
            if object_id != unit.id
        )
        if other_count >= 2:
            continue
        yield index, direction, destination, other_count


def _move_to_goal(
    unit,
    goal: tuple[int, int],
    turn,
    occupied: tuple[tuple[object, tuple[int, int]], ...],
    reserved_destinations: set[tuple[int, int]],
    *,
    retreat: bool,
) -> tuple[Direction, tuple[int, int]] | None:
    if unit.position == goal:
        return None
    current_distance = _distance(unit.position, goal)
    candidates = []
    for index, direction, destination, occupancy in _candidate_steps(
        unit, turn, occupied, reserved_destinations
    ):
        progress = current_distance - _distance(destination, goal)
        enemy_distance = _nearest_enemy_distance(destination, turn.visible_enemies)
        if retreat:
            rank = (-enemy_distance, -progress, occupancy, index)
        else:
            rank = (-progress, -enemy_distance, occupancy, index)
        candidates.append((rank, direction, destination))
    if not candidates:
        return None
    _, direction, destination = min(candidates, key=lambda item: item[0])
    return direction, destination


def _queue_worker_actions(
    turn,
    acted: set[object],
    planned_from_core: set[object],
    planned_into_core: set[object],
) -> None:
    core = turn.core
    core_position = core.position
    stationary_core = _is_stationary_core(core)
    occupied = tuple(
        [(core.id, core.position)]
        + [(unit.id, unit.position) for unit in turn.units]
    )
    reserved_destinations: set[tuple[int, int]] = set()
    claimed_resources: set[tuple[int, int]] = set()
    resources = tuple(turn.resource_cells)

    for worker in sorted(turn.workers, key=lambda unit: _uuid_key(unit.id)):
        if worker.id in acted:
            continue
        if (
            worker.cargo
            and stationary_core
            and worker.position == core_position
            and turn.resource_space > 0
        ):
            worker.deposit()
            acted.add(worker.id)
            continue

        threatened = _nearest_enemy_distance(worker.position, turn.visible_enemies) <= 2
        goal: tuple[int, int] | None = None
        retreat = False
        if (
            not threatened
            and not worker.cargo
            and worker.position in turn.resource_cells
        ):
            if worker.position not in claimed_resources:
                worker.harvest()
                claimed_resources.add(worker.position)
                acted.add(worker.id)
                continue
        if (
            not threatened
            and not worker.cargo
            and _beacon_is_ground(turn)
            and worker.position == getattr(turn.beacon, "position", None)
            and worker.position not in turn.resource_cells
        ):
            continue
        if threatened:
            goal = core_position
            retreat = True
        elif worker.cargo:
            goal = core_position
        else:
            for resource_cell in sorted(
                resources,
                key=lambda cell: (
                    _distance(worker.position, cell),
                    cell[0],
                    cell[1],
                ),
            ):
                if resource_cell not in claimed_resources:
                    goal = resource_cell
                    break
            if goal is None:
                goal = core_position

        movement = _move_to_goal(
            worker,
            goal,
            turn,
            occupied,
            reserved_destinations,
            retreat=retreat,
        )
        if movement is None:
            continue
        direction, destination = movement
        worker.move(direction)
        acted.add(worker.id)
        reserved_destinations.add(destination)
        if worker.position == core_position:
            planned_from_core.add(worker.id)
        if destination == core_position:
            planned_into_core.add(worker.id)


def _beacon_is_ground(turn) -> bool:
    return _enum_name(getattr(turn.beacon, "status", None)) == "GROUND"


def _beacon_carrier_is_owned(turn) -> bool:
    carrier_id = getattr(turn.beacon, "carrier_id", None)
    if carrier_id is None:
        return False
    if turn.core is not None and carrier_id == turn.core.id:
        return True
    return any(carrier_id == unit.id for unit in turn.units)


def _shield_cap(turn) -> int:
    return 10 if _beacon_carrier_is_owned(turn) else 5


def _queue_unit_heals(turn, acted: set[object], budget: int) -> int:
    core = turn.core
    if core is None or not _is_stationary_core(core):
        return budget
    for unit in sorted(turn.units, key=lambda item: _uuid_key(item.id)):
        if unit.id in acted or unit.position != core.position:
            continue
        if _enum_name(getattr(unit, "unit_type", None)) == "WORKER" and getattr(unit, "cargo", 0):
            continue
        maximum = UNIT_MAX_HP.get(_enum_name(unit.unit_type))
        if maximum is None or unit.hp >= maximum or budget <= 0:
            continue
        unit.heal()
        acted.add(unit.id)
        budget -= min(maximum - unit.hp, budget)
    return budget


def _desired_spawn_type(turn):
    workers = sum(_enum_name(unit.unit_type) == "WORKER" for unit in turn.units)
    rangers = sum(_enum_name(unit.unit_type) == "RANGER" for unit in turn.units)
    vanguards = sum(_enum_name(unit.unit_type) == "VANGUARD" for unit in turn.units)
    if workers < 3:
        return UnitType.WORKER
    if rangers == 0:
        return UnitType.RANGER
    if vanguards == 0:
        return UnitType.VANGUARD
    return UnitType.RANGER if rangers <= vanguards else UnitType.VANGUARD


def _upkeep_for(population: int) -> int:
    tier = population // 20
    return tier * (tier + 1) // 2


def _queue_beacon_action(turn, acted: set[object], core_action_selected: bool) -> bool:
    if not _beacon_is_ground(turn):
        return core_action_selected
    beacon_position = turn.beacon.position
    idle_units = [
        unit
        for unit in turn.units
        if unit.id not in acted
        and unit.position == beacon_position
        and not (
            _enum_name(unit.unit_type) == "WORKER" and getattr(unit, "cargo", 0)
        )
    ]
    if idle_units:
        unit = min(idle_units, key=lambda item: _uuid_key(item.id))
        unit.pickup_beacon()
        acted.add(unit.id)
        return core_action_selected
    core = turn.core
    if (
        core is not None
        and not core_action_selected
        and _is_stationary_core(core)
        and core.position == beacon_position
        and core.hp >= CORE_MAX_HP
        and core.shield >= _shield_cap(turn)
    ):
        core.pickup_beacon()
        return True
    return core_action_selected


def _queue_core_action(
    turn,
    budget: int,
    planned_from_core: set[object],
    planned_into_core: set[object],
    core_action_selected: bool,
) -> bool:
    core = turn.core
    if core is None or core_action_selected or not _is_stationary_core(core):
        return core_action_selected
    if core.hp < CORE_MAX_HP and budget > 0:
        core.heal()
        return True
    if core.shield < _shield_cap(turn) and budget > 0:
        core.repair_shield()
        return True

    population = getattr(turn.state, "population", len(turn.units))
    unit_type = _desired_spawn_type(turn)
    cost = UNIT_COSTS[unit_type]
    projected_upkeep = _upkeep_for(population + 1)
    if budget < cost + projected_upkeep + CORE_RESERVE:
        return False
    current_core_units = sum(unit.position == core.position for unit in turn.units)
    post_movement_occupancy = (
        1
        + current_core_units
        - len(planned_from_core)
        + len(planned_into_core)
    )
    if post_movement_occupancy >= 2:
        return False
    core.spawn(unit_type)
    return True


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
    planned_from_core: set[object] = set()
    planned_into_core: set[object] = set()
    _queue_ranger_actions(turn, acted)
    _queue_vanguard_actions(turn, acted)
    budget = max(0, turn.resources - getattr(turn.state, "upkeep_next_tick", 0))
    budget = _queue_unit_heals(turn, acted, budget)
    _queue_worker_actions(turn, acted, planned_from_core, planned_into_core)
    core_action_selected = _queue_beacon_action(turn, acted, False)
    _queue_core_action(
        turn,
        budget,
        planned_from_core,
        planned_into_core,
        core_action_selected,
    )


def load_api_key() -> str:
    return os.environ.get("ARENA_HERO_API_KEY") or getpass("Arena Hero API key: ")


def play(api_key: str | None = None) -> None:
    raise NotImplementedError
