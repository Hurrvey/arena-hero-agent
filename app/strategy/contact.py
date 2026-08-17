"""Current-visible contact assessment and bounded response geometry."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import IntEnum
import heapq
from itertools import count
from typing import Mapping, Sequence

from .models import CellRisk, EntityKind, EntitySnapshot, Position, validate_position

CARDINALS: tuple[Position, ...] = ((0, -1), (1, 0), (0, 1), (-1, 0))
RANGER_DIRECTIONS: tuple[Position, ...] = (
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
)


class ContactLevel(IntEnum):
    NONE = 0
    SPOTTED = 1
    THREATENING = 2
    ENGAGED = 3


@dataclass(frozen=True, slots=True)
class ContactAssessment:
    level: ContactLevel
    visible_enemy_ids: frozenset[bytes]
    threatening_enemy_ids: frozenset[bytes]
    threatened_friendly_ids: frozenset[bytes]
    currently_engaged_enemy_ids: frozenset[bytes]

    @classmethod
    def none(cls) -> "ContactAssessment":
        return cls(
            ContactLevel.NONE,
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
        )


@dataclass(slots=True)
class ContactMemory:
    responder_id: bytes | None = None
    last_seen_position: Position | None = None
    expires_tick: int = 0
    enemy_id: bytes | None = None


@dataclass(frozen=True, slots=True)
class ContactResponse:
    level: ContactLevel
    responder_id: bytes | None
    target_position: Position | None
    threatened_worker_ids: frozenset[bytes]
    reason_code: str | None


def assess_contact(
    *,
    core: EntitySnapshot,
    friendlies: Sequence[EntitySnapshot],
    visible_enemies: Sequence[EntitySnapshot],
    obstacles: frozenset[Position],
    protected_friendly_ids: frozenset[bytes],
) -> ContactAssessment:
    """Classify only living enemies in the current authoritative visibility."""

    if core.kind is not EntityKind.CORE or not core.controlled:
        raise ValueError("core must be a controlled Core snapshot")
    blocked = frozenset(obstacles)
    enemies = tuple(
        enemy
        for enemy in visible_enemies
        if not enemy.controlled
        and enemy.hp > 0
        and enemy.kind in {EntityKind.RANGER, EntityKind.VANGUARD}
    )
    visible_ids = frozenset(enemy.entity_id for enemy in enemies)
    friendly_by_id = {friendly.entity_id: friendly for friendly in friendlies}
    friendly_by_id[core.entity_id] = core
    protected = {
        identifier
        for identifier in protected_friendly_ids
        if identifier in friendly_by_id
    }
    threatening_enemies: set[bytes] = set()
    threatened_friendlies: set[bytes] = set()
    for enemy in enemies:
        for identifier in sorted(protected):
            friendly = friendly_by_id[identifier]
            if _can_attack_cell(enemy, friendly.position, blocked) or _can_attack_after_one_step(
                enemy,
                friendly.position,
                blocked,
            ):
                threatening_enemies.add(enemy.entity_id)
                threatened_friendlies.add(identifier)

    engaged = {
        enemy.entity_id
        for enemy in enemies
        if any(
            friendly.kind in {EntityKind.RANGER, EntityKind.VANGUARD}
            and _can_attack_cell(friendly, enemy.position, blocked)
            for friendly in friendlies
        )
    }
    if engaged:
        level = ContactLevel.ENGAGED
    elif threatening_enemies:
        level = ContactLevel.THREATENING
    elif enemies:
        level = ContactLevel.SPOTTED
    else:
        level = ContactLevel.NONE
    return ContactAssessment(
        level=level,
        visible_enemy_ids=visible_ids,
        threatening_enemy_ids=frozenset(threatening_enemies),
        threatened_friendly_ids=frozenset(threatened_friendlies),
        currently_engaged_enemy_ids=frozenset(engaged),
    )


def choose_worker_evasion(
    worker: EntitySnapshot,
    *,
    visible_enemies: Sequence[EntitySnapshot],
    obstacles: frozenset[Position],
    occupied: frozenset[Position],
    reserved: frozenset[Position],
    core_position: Position,
    risk_map: Mapping[Position, CellRisk] | None = None,
) -> Position | None:
    """Choose a strictly safer legal adjacent cell for one Worker."""

    if worker.kind is not EntityKind.WORKER:
        raise ValueError("worker must be a Worker snapshot")
    risk_map = risk_map or {}
    origin_threat = _evasion_threat(
        risk_map.get(worker.position),
        visible_enemies,
        worker.position,
        obstacles,
    )
    candidates: list[tuple[tuple[int, int, int, int], Position]] = []
    for order, (dx, dy) in enumerate(CARDINALS):
        destination = (worker.position[0] + dx, worker.position[1] + dy)
        if (
            destination in obstacles
            or destination in occupied
            or destination in reserved
            or destination == core_position
        ):
            continue
        threat = _evasion_threat(
            risk_map.get(destination),
            visible_enemies,
            destination,
            obstacles,
        )
        if threat >= origin_threat:
            continue
        nearest_enemy = min(
            (_manhattan(destination, enemy.position) for enemy in visible_enemies),
            default=10**9,
        )
        distance_penalty = max(
            0,
            _manhattan(destination, core_position)
            - _manhattan(worker.position, core_position),
        )
        candidates.append(
            (
                (
                    threat[0],
                    threat[1],
                    -nearest_enemy,
                    distance_penalty,
                    order,
                ),
                destination,
            )
        )
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def select_responder(
    units: Sequence[EntitySnapshot],
    *,
    enemy: EntitySnapshot,
    contact_level: ContactLevel,
    core_position: Position,
    defender_ids: frozenset[bytes],
    core_defense_level: object,
    obstacles: frozenset[Position],
    carrier_id: bytes | None = None,
) -> EntitySnapshot | None:
    """Select one mobile responder while preserving the Core defender floor."""

    if contact_level is ContactLevel.NONE:
        return None
    defense_name = _enum_name(core_defense_level)
    if defense_name in {"APPROACH", "ATTACK", "LETHAL"}:
        return None
    if enemy.kind not in {EntityKind.RANGER, EntityKind.VANGUARD}:
        return None
    eligible = [
        unit
        for unit in units
        if unit.controlled
        and unit.hp > 0
        and unit.kind in {EntityKind.RANGER, EntityKind.VANGUARD}
        and (carrier_id is None or unit.entity_id != carrier_id)
    ]
    if contact_level is ContactLevel.SPOTTED:
        eligible = [
            unit
            for unit in eligible
            if unit.kind is EntityKind.RANGER and unit.entity_id not in defender_ids
        ]
    defending_vanguard_ids = {
        unit.entity_id
        for unit in eligible
        if unit.kind is EntityKind.VANGUARD and unit.entity_id in defender_ids
    }
    eligible = [
        unit
        for unit in eligible
        if unit.kind is EntityKind.RANGER
        or unit.entity_id not in defender_ids
        or len(defending_vanguard_ids) >= 2
    ]
    if not eligible:
        return None
    ranked = [
        (
            _bounded_distance(
                unit.position,
                enemy.position,
                obstacles=obstacles,
                occupied=frozenset(),
                max_expansions=256,
            ),
            0 if unit.kind is EntityKind.RANGER else 1,
            -unit.hp,
            unit.entity_id,
            unit,
        )
        for unit in eligible
    ]
    return min(ranked, key=lambda item: item[:-1])[-1]


def ranger_intercept_goal(
    ranger: EntitySnapshot,
    enemy: EntitySnapshot,
    *,
    obstacles: frozenset[Position],
    occupied: frozenset[Position],
    reserved: frozenset[Position],
    search_radius: int = 8,
    risk_map: Mapping[Position, CellRisk] | None = None,
) -> Position | None:
    """Find an empty bounded cell from which a Ranger has a clear shot."""

    if ranger.kind is not EntityKind.RANGER:
        raise ValueError("ranger must be a Ranger snapshot")
    if enemy.kind not in {EntityKind.RANGER, EntityKind.VANGUARD}:
        return None
    if not isinstance(search_radius, int) or search_radius < 1:
        raise ValueError("search_radius must be positive")
    risk_map = risk_map or {}
    candidates: list[tuple[tuple[int, int, int, int], Position]] = []
    occupied_without_ranger = occupied - {ranger.position}
    for x in range(enemy.position[0] - search_radius, enemy.position[0] + search_radius + 1):
        for y in range(enemy.position[1] - search_radius, enemy.position[1] + search_radius + 1):
            candidate = (x, y)
            if (
                candidate in obstacles
                or candidate in occupied_without_ranger
                or candidate in reserved
                or candidate == enemy.position
                or not _line_is_clear(candidate, enemy.position, obstacles)
            ):
                continue
            path_cost = _bounded_distance(
                ranger.position,
                candidate,
                obstacles=obstacles,
                occupied=occupied_without_ranger,
                max_expansions=512,
            )
            if path_cost >= 10**9:
                continue
            risk = risk_map.get(candidate)
            candidates.append(
                ((path_cost, _risk_count(risk, (), candidate, obstacles), x, y), candidate)
            )
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def vanguard_intercept_goal(
    vanguard: EntitySnapshot,
    enemy: EntitySnapshot,
    *,
    threatened_position: Position,
    obstacles: frozenset[Position],
    occupied: frozenset[Position],
    reserved: frozenset[Position],
    risk_map: Mapping[Position, CellRisk] | None = None,
) -> Position | None:
    """Choose a safe cardinal interception cell between enemy and asset."""

    if vanguard.kind is not EntityKind.VANGUARD:
        raise ValueError("vanguard must be a Vanguard snapshot")
    risk_map = risk_map or {}
    current_risk = _risk_count(risk_map.get(vanguard.position), (), vanguard.position, obstacles)
    candidates: list[tuple[tuple[int, int, int, int], Position]] = []
    for order, (dx, dy) in enumerate(CARDINALS):
        candidate = (enemy.position[0] + dx, enemy.position[1] + dy)
        if (
            candidate in obstacles
            or candidate in occupied - {vanguard.position}
            or candidate in reserved
            or _manhattan(candidate, threatened_position)
            >= _manhattan(enemy.position, threatened_position)
        ):
            continue
        path_cost = _bounded_distance(
            vanguard.position,
            candidate,
            obstacles=obstacles,
            occupied=occupied - {vanguard.position},
            max_expansions=256,
        )
        if path_cost >= 10**9:
            continue
        candidate_risk = _risk_count(
            risk_map.get(candidate),
            (),
            candidate,
            obstacles,
        )
        if candidate_risk > current_risk:
            continue
        candidates.append(((path_cost, candidate_risk, order, candidate[0]), candidate))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def update_investigation(
    memory: ContactMemory,
    *,
    tick: int,
    visible_threat: EntitySnapshot | None,
    responder_id: bytes | None,
    current_visible_cells: frozenset[Position] | None = None,
) -> Position | None:
    """Maintain a three-Tick movement-only investigation lease."""

    if not isinstance(tick, int) or isinstance(tick, bool) or tick < 0:
        raise ValueError("tick must be a non-negative integer")
    if responder_id is not None and (not isinstance(responder_id, bytes) or not responder_id):
        raise ValueError("responder_id must be non-empty bytes")
    if visible_threat is not None:
        if visible_threat.controlled or visible_threat.hp <= 0:
            raise ValueError("visible_threat must be a living enemy")
        memory.last_seen_position = visible_threat.position
        memory.responder_id = responder_id
        memory.enemy_id = visible_threat.entity_id
        memory.expires_tick = tick + 3
        return visible_threat.position

    memory.enemy_id = None
    if memory.last_seen_position is None or memory.responder_id != responder_id:
        memory.last_seen_position = None
        memory.responder_id = None
        memory.expires_tick = 0
        return None
    if current_visible_cells is not None and memory.last_seen_position in current_visible_cells:
        memory.last_seen_position = None
        memory.responder_id = None
        memory.expires_tick = 0
        return None
    if tick > memory.expires_tick:
        memory.last_seen_position = None
        memory.responder_id = None
        memory.expires_tick = 0
        return None
    return memory.last_seen_position


def _can_attack_cell(
    attacker: EntitySnapshot,
    target: Position,
    obstacles: frozenset[Position],
) -> bool:
    if attacker.kind is EntityKind.VANGUARD:
        return _manhattan(attacker.position, target) == 1
    if attacker.kind is EntityKind.RANGER:
        return _line_is_clear(attacker.position, target, obstacles)
    return False


def _can_attack_after_one_step(
    attacker: EntitySnapshot,
    target: Position,
    obstacles: frozenset[Position],
) -> bool:
    if attacker.kind not in {EntityKind.RANGER, EntityKind.VANGUARD}:
        return False
    for dx, dy in CARDINALS:
        destination = (attacker.position[0] + dx, attacker.position[1] + dy)
        if destination in obstacles or destination == target:
            continue
        projected = EntitySnapshot(
            attacker.entity_id,
            attacker.kind,
            destination,
            hp=attacker.hp,
            shield=attacker.shield,
            controlled=attacker.controlled,
        )
        if _can_attack_cell(projected, target, obstacles):
            return True
    return False


def _line_is_clear(origin: Position, target: Position, obstacles: frozenset[Position]) -> bool:
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
        (origin[0] + step_x * step, origin[1] + step_y * step) not in obstacles
        for step in range(1, distance)
    )


def _bounded_distance(
    start: Position,
    target: Position,
    *,
    obstacles: frozenset[Position],
    occupied: frozenset[Position],
    max_expansions: int,
) -> int:
    if start == target:
        return 0
    queue: list[tuple[int, int, int, Position]] = []
    serial = count()
    heapq.heappush(queue, (_manhattan(start, target), 0, next(serial), start))
    costs = {start: 0}
    expansions = 0
    while queue and expansions < max_expansions:
        _, cost, _, current = heapq.heappop(queue)
        if cost != costs.get(current):
            continue
        expansions += 1
        for dx, dy in CARDINALS:
            neighbor = (current[0] + dx, current[1] + dy)
            if neighbor in obstacles or (neighbor in occupied and neighbor != target):
                continue
            next_cost = cost + 1
            if neighbor == target:
                return next_cost
            if next_cost >= costs.get(neighbor, 10**9):
                continue
            costs[neighbor] = next_cost
            heapq.heappush(
                queue,
                (next_cost + _manhattan(neighbor, target), next_cost, next(serial), neighbor),
            )
    return 10**9


def _risk_count(
    risk: CellRisk | None,
    enemies: Sequence[EntitySnapshot],
    position: Position,
    obstacles: frozenset[Position],
) -> int:
    if risk is not None:
        return risk.visible_attack_count
    return sum(
        _can_attack_cell(enemy, position, obstacles)
        for enemy in enemies
        if not enemy.controlled and enemy.hp > 0
    )


def _evasion_threat(
    risk: CellRisk | None,
    enemies: Sequence[EntitySnapshot],
    position: Position,
    obstacles: frozenset[Position],
) -> tuple[int, int]:
    current_attacks = _risk_count(risk, enemies, position, obstacles)
    next_step_attacks = sum(
        _can_attack_after_one_step(enemy, position, obstacles)
        for enemy in enemies
        if not enemy.controlled and enemy.hp > 0
    )
    return current_attacks, next_step_attacks


def _manhattan(left: Position, right: Position) -> int:
    return abs(left[0] - right[0]) + abs(left[1] - right[1])


def _enum_name(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(getattr(raw, "name", raw)).upper().rsplit(".", 1)[-1]


__all__ = [
    "ContactAssessment",
    "ContactLevel",
    "ContactMemory",
    "ContactResponse",
    "assess_contact",
    "choose_worker_evasion",
    "ranger_intercept_goal",
    "select_responder",
    "update_investigation",
    "vanguard_intercept_goal",
]
