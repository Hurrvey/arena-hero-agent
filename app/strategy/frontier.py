"""Deterministic frontier leasing and bounded anti-oscillation routing."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import heapq
from itertools import count
from typing import Mapping

from .exploration import ExplorationMap
from .models import CellRisk, Position, validate_position

CARDINALS: tuple[Position, ...] = ((0, -1), (1, 0), (0, 1), (-1, 0))
WORKER_VISION_RADIUS = 3


def _positive_integer(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _non_negative_integer(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _validate_unit_id(unit_id: bytes) -> None:
    if not isinstance(unit_id, bytes) or not unit_id:
        raise ValueError("unit_id must be non-empty bytes")


@dataclass(frozen=True, slots=True)
class FrontierSettings:
    search_radius: int = 48
    candidate_limit: int = 256
    route_expansions: int = 512
    lease_stall_ticks: int = 3
    edge_cooldown_ticks: int = 4

    def __post_init__(self) -> None:
        for name in (
            "search_radius",
            "candidate_limit",
            "route_expansions",
            "lease_stall_ticks",
            "edge_cooldown_ticks",
        ):
            _positive_integer(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class ScoutSnapshot:
    entity_id: bytes
    position: Position

    def __post_init__(self) -> None:
        _validate_unit_id(self.entity_id)
        validate_position(self.position)


@dataclass(slots=True)
class FrontierLease:
    target: Position
    best_distance: int
    best_explored_count: int
    stalled_ticks: int
    created_tick: int
    failed_until: int = 0

    def __post_init__(self) -> None:
        validate_position(self.target)
        for name in (
            "best_distance",
            "best_explored_count",
            "stalled_ticks",
            "created_tick",
            "failed_until",
        ):
            _non_negative_integer(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class FrontierAssignment:
    unit_id: bytes
    target: Position
    expected_gain: int
    path_cost: int
    reason_code: str

    def __post_init__(self) -> None:
        _validate_unit_id(self.unit_id)
        validate_position(self.target)
        _non_negative_integer("expected_gain", self.expected_gain)
        _non_negative_integer("path_cost", self.path_cost)
        if self.reason_code not in {"SCOUT_FRONTIER", "SCOUT_REASSIGNED"}:
            raise ValueError("unsupported frontier assignment reason")


@dataclass(slots=True)
class FrontierMemory:
    leases: dict[bytes, FrontierLease] = field(default_factory=dict)
    histories: dict[bytes, deque[Position]] = field(default_factory=dict)
    taboo_edges: dict[tuple[bytes, Position, Position], int] = field(
        default_factory=dict
    )
    failed_targets: dict[tuple[bytes, Position], int] = field(default_factory=dict)
    oscillation_detections: int = 0
    oscillation_prevented_moves: int = 0
    frontier_progress_ticks: int = 0
    scout_wait_ticks: int = 0
    observed_ticks: dict[bytes, int] = field(default_factory=dict)

    def ensure_lease(
        self,
        unit_id: bytes,
        *,
        target: Position,
        distance: int,
        explored_count: int,
        tick: int,
    ) -> FrontierLease:
        _validate_unit_id(unit_id)
        validate_position(target)
        _non_negative_integer("distance", distance)
        _non_negative_integer("explored_count", explored_count)
        _non_negative_integer("tick", tick)
        lease = self.leases.get(unit_id)
        if lease is None or lease.target != target:
            lease = FrontierLease(
                target=target,
                best_distance=distance,
                best_explored_count=explored_count,
                stalled_ticks=0,
                created_tick=tick,
            )
            self.leases[unit_id] = lease
        return lease


def frontier_cells(
    exploration: ExplorationMap,
    *,
    min_x: int,
    min_y: int,
    max_x: int,
    max_y: int,
    obstacles: frozenset[Position],
    limit: int,
) -> tuple[Position, ...]:
    """Return sorted explored cells that border at least one unknown cell."""

    if not isinstance(exploration, ExplorationMap):
        raise TypeError("exploration must be an ExplorationMap")
    validate_position((min_x, min_y))
    validate_position((max_x, max_y))
    if min_x > max_x or min_y > max_y:
        raise ValueError("frontier bounds must be ordered")
    if not isinstance(obstacles, frozenset):
        raise TypeError("obstacles must be a frozenset")
    for obstacle in obstacles:
        validate_position(obstacle)
    _positive_integer("limit", limit)

    candidates: list[Position] = []
    for x in range(min_x, max_x + 1):
        for y in range(min_y, max_y + 1):
            position = (x, y)
            if position in obstacles or exploration.is_known_obstacle(position):
                continue
            if not exploration.is_explored(position):
                continue
            if any(
                not exploration.is_explored((x + dx, y + dy))
                for dx, dy in CARDINALS
            ):
                candidates.append(position)
                if len(candidates) == limit:
                    return tuple(candidates)
    return tuple(candidates)


def assign_frontiers(
    memory: FrontierMemory,
    scouts: tuple[ScoutSnapshot, ...],
    *,
    exploration: ExplorationMap,
    risk_map: Mapping[Position, CellRisk],
    obstacles: frozenset[Position],
    occupied: frozenset[Position],
    tick: int,
    settings: FrontierSettings,
) -> dict[bytes, FrontierAssignment]:
    """Assign distinct, low-overlap frontier leases in raw UUID order."""

    _non_negative_integer("tick", tick)
    if not isinstance(settings, FrontierSettings):
        raise TypeError("settings must be FrontierSettings")
    ordered = tuple(sorted(scouts, key=lambda item: item.entity_id))
    if len({scout.entity_id for scout in ordered}) != len(ordered):
        raise ValueError("scout identifiers must be unique")
    active_ids = {scout.entity_id for scout in ordered}
    _prune_inactive_scouts(memory, active_ids)
    if not ordered:
        return {}
    _prune_cooldowns(memory, tick)

    candidates_by_scout: dict[bytes, tuple[Position, ...]] = {}
    explored_count_by_scout: dict[bytes, int] = {}
    for scout in ordered:
        min_x = scout.position[0] - settings.search_radius
        min_y = scout.position[1] - settings.search_radius
        max_x = scout.position[0] + settings.search_radius
        max_y = scout.position[1] + settings.search_radius
        candidates_by_scout[scout.entity_id] = frontier_cells(
            exploration,
            min_x=min_x,
            min_y=min_y,
            max_x=max_x,
            max_y=max_y,
            obstacles=obstacles,
            limit=settings.candidate_limit,
        )
        explored_count_by_scout[scout.entity_id] = _explored_count(
            exploration,
            min_x=min_x,
            min_y=min_y,
            max_x=max_x,
            max_y=max_y,
        )
    assignments: dict[bytes, FrontierAssignment] = {}
    claimed_targets: set[Position] = set()
    claimed_unknown: set[Position] = set()

    for scout in ordered:
        candidates = candidates_by_scout[scout.entity_id]
        candidate_set = set(candidates)
        explored_count = explored_count_by_scout[scout.entity_id]
        lease = memory.leases.get(scout.entity_id)
        had_failure = any(key[0] == scout.entity_id for key in memory.failed_targets)
        kept_path: tuple[Position, ...] | None = None
        if (
            lease is not None
            and lease.target in candidate_set
            and lease.target not in claimed_targets
            and memory.failed_targets.get((scout.entity_id, lease.target), 0) <= tick
        ):
            kept_path = _bounded_path(
                scout.entity_id,
                scout.position,
                lease.target,
                obstacles=obstacles,
                occupied=occupied - {scout.position},
                reserved=frozenset(),
                risk_map=risk_map,
                tick=tick,
                max_expansions=settings.route_expansions,
                memory=memory,
                reject_risk=True,
                reject_taboo=True,
            )
        if kept_path is not None:
            gain_cells = _unknown_in_worker_radius(exploration, lease.target)
            overlap = len(gain_cells & claimed_unknown)
            assignment = FrontierAssignment(
                unit_id=scout.entity_id,
                target=lease.target,
                expected_gain=max(0, len(gain_cells) - overlap),
                path_cost=max(0, len(kept_path) - 1),
                reason_code="SCOUT_FRONTIER",
            )
            assignments[scout.entity_id] = assignment
            claimed_targets.add(lease.target)
            claimed_unknown.update(gain_cells)
            continue

        if lease is not None:
            memory.leases.pop(scout.entity_id, None)

        ranked: list[
            tuple[tuple[int, int, int, int], Position, tuple[Position, ...], set[Position]]
        ] = []
        for candidate in candidates:
            if candidate in claimed_targets or candidate in occupied:
                continue
            if memory.failed_targets.get((scout.entity_id, candidate), 0) > tick:
                continue
            path = _bounded_path(
                scout.entity_id,
                scout.position,
                candidate,
                obstacles=obstacles,
                occupied=occupied - {scout.position},
                reserved=frozenset(),
                risk_map=risk_map,
                tick=tick,
                max_expansions=settings.route_expansions,
                memory=memory,
                reject_risk=False,
                reject_taboo=False,
            )
            if path is None:
                continue
            gain_cells = _unknown_in_worker_radius(exploration, candidate)
            gain = len(gain_cells)
            overlap = len(gain_cells & claimed_unknown)
            path_cost = len(path) - 1
            last_seen = exploration.last_seen_tick(candidate)
            age_bonus = 20 if last_seen == 0 else min(20, max(0, tick - last_seen))
            first_step = path[1] if len(path) > 1 else scout.position
            risk = _attack_count(risk_map.get(candidate)) + _attack_count(
                risk_map.get(first_step)
            )
            reverse_penalty = int(
                memory.taboo_edges.get(
                    (scout.entity_id, scout.position, first_step),
                    0,
                )
                > tick
            )
            utility = (
                5 * gain
                + age_bonus
                - path_cost
                - 3 * overlap
                - 8 * risk
                - 20 * reverse_penalty
            )
            rank = (-utility, path_cost, candidate[0], candidate[1])
            ranked.append((rank, candidate, path, gain_cells))
        if not ranked:
            continue

        _, target, path, gain_cells = min(ranked, key=lambda item: item[0])
        path_cost = len(path) - 1
        overlap = len(gain_cells & claimed_unknown)
        memory.ensure_lease(
            scout.entity_id,
            target=target,
            distance=path_cost,
            explored_count=explored_count,
            tick=tick,
        )
        assignments[scout.entity_id] = FrontierAssignment(
            unit_id=scout.entity_id,
            target=target,
            expected_gain=max(0, len(gain_cells) - overlap),
            path_cost=path_cost,
            reason_code="SCOUT_REASSIGNED" if had_failure else "SCOUT_FRONTIER",
        )
        claimed_targets.add(target)
        claimed_unknown.update(gain_cells)

    return assignments


def record_scout_observation(
    memory: FrontierMemory,
    unit_id: bytes,
    position: Position,
    *,
    explored_count: int,
    tick: int,
    settings: FrontierSettings,
) -> None:
    """Record one authoritative scout position and release stalled/cyclic leases."""

    _validate_unit_id(unit_id)
    validate_position(position)
    _non_negative_integer("explored_count", explored_count)
    _non_negative_integer("tick", tick)
    if not isinstance(settings, FrontierSettings):
        raise TypeError("settings must be FrontierSettings")
    _prune_cooldowns(memory, tick)
    if memory.observed_ticks.get(unit_id) == tick:
        return
    memory.observed_ticks[unit_id] = tick
    history = memory.histories.setdefault(unit_id, deque(maxlen=8))
    history.append(position)

    cycle = (
        len(history) >= 3
        and history[-3] == history[-1]
        and history[-2] != history[-1]
    ) or (
        len(history) >= 4
        and history[-4] == history[-2]
        and history[-3] != history[-1]
    )
    if cycle:
        expiry = tick + settings.edge_cooldown_ticks
        previous = history[-2]
        memory.taboo_edges[(unit_id, position, previous)] = expiry
        lease = memory.leases.pop(unit_id, None)
        if lease is not None:
            memory.failed_targets[(unit_id, lease.target)] = expiry
        memory.oscillation_detections += 1
        return

    lease = memory.leases.get(unit_id)
    if lease is None:
        return
    distance = _manhattan(position, lease.target)
    progressed = (
        distance < lease.best_distance
        or explored_count > lease.best_explored_count
    )
    lease.best_distance = min(lease.best_distance, distance)
    lease.best_explored_count = max(lease.best_explored_count, explored_count)
    if progressed:
        lease.stalled_ticks = 0
        memory.frontier_progress_ticks += 1
        return
    lease.stalled_ticks += 1
    if lease.stalled_ticks >= settings.lease_stall_ticks:
        expiry = tick + settings.edge_cooldown_ticks
        memory.leases.pop(unit_id, None)
        memory.failed_targets[(unit_id, lease.target)] = expiry


def next_frontier_step(
    scout: ScoutSnapshot,
    *,
    target: Position,
    memory: FrontierMemory,
    risk_map: Mapping[Position, CellRisk],
    obstacles: frozenset[Position],
    occupied: frozenset[Position],
    reserved: frozenset[Position],
    tick: int,
    max_expansions: int,
) -> Position | None:
    """Return the next safe cardinal cell, or ``None`` for an explicit wait."""

    validate_position(target)
    _non_negative_integer("tick", tick)
    _positive_integer("max_expansions", max_expansions)
    _prune_cooldowns(memory, tick)
    path = _bounded_path(
        scout.entity_id,
        scout.position,
        target,
        obstacles=obstacles,
        occupied=occupied - {scout.position},
        reserved=reserved,
        risk_map=risk_map,
        tick=tick,
        max_expansions=max_expansions,
        memory=memory,
        reject_risk=True,
        reject_taboo=True,
    )
    if path is not None and len(path) > 1:
        return path[1]
    if path is not None:
        return None

    if _taboo_was_only_geometric_step(
        scout,
        target,
        memory=memory,
        risk_map=risk_map,
        obstacles=obstacles,
        occupied=occupied,
        reserved=reserved,
        tick=tick,
    ):
        memory.oscillation_prevented_moves += 1
    memory.scout_wait_ticks += 1
    return None


def _bounded_path(
    unit_id: bytes,
    start: Position,
    target: Position,
    *,
    obstacles: frozenset[Position],
    occupied: frozenset[Position],
    reserved: frozenset[Position],
    risk_map: Mapping[Position, CellRisk],
    tick: int,
    max_expansions: int,
    memory: FrontierMemory,
    reject_risk: bool,
    reject_taboo: bool,
) -> tuple[Position, ...] | None:
    if start == target:
        return (start,)
    queue: list[tuple[int, int, int, Position]] = []
    serial = count()
    heapq.heappush(queue, (_manhattan(start, target), 0, next(serial), start))
    came_from: dict[Position, Position] = {}
    best_cost: dict[Position, int] = {start: 0}
    expansions = 0
    while queue and expansions < max_expansions:
        _, cost, _, current = heapq.heappop(queue)
        if cost != best_cost.get(current):
            continue
        expansions += 1
        if current == target:
            return _reconstruct_path(came_from, current)
        for dx, dy in CARDINALS:
            neighbor = (current[0] + dx, current[1] + dy)
            if neighbor in obstacles or neighbor in occupied or neighbor in reserved:
                continue
            if reject_risk and _expected_damage(risk_map.get(neighbor)) >= 1:
                continue
            if reject_taboo and memory.taboo_edges.get(
                (unit_id, current, neighbor),
                0,
            ) > tick:
                continue
            next_cost = cost + 1
            if next_cost >= best_cost.get(neighbor, 2**63 - 1):
                continue
            best_cost[neighbor] = next_cost
            came_from[neighbor] = current
            heapq.heappush(
                queue,
                (
                    next_cost + _manhattan(neighbor, target),
                    next_cost,
                    next(serial),
                    neighbor,
                ),
            )
    return None


def _reconstruct_path(
    came_from: Mapping[Position, Position],
    current: Position,
) -> tuple[Position, ...]:
    path = [current]
    while current in came_from:
        current = came_from[current]
        path.append(current)
    path.reverse()
    return tuple(path)


def _unknown_in_worker_radius(
    exploration: ExplorationMap,
    center: Position,
) -> set[Position]:
    cells: set[Position] = set()
    for dx in range(-WORKER_VISION_RADIUS, WORKER_VISION_RADIUS + 1):
        remaining = WORKER_VISION_RADIUS - abs(dx)
        for dy in range(-remaining, remaining + 1):
            position = (center[0] + dx, center[1] + dy)
            if not exploration.is_explored(position):
                cells.add(position)
    return cells


def _explored_count(
    exploration: ExplorationMap,
    *,
    min_x: int,
    min_y: int,
    max_x: int,
    max_y: int,
) -> int:
    return len(
        exploration.window(
            min_x=min_x,
            min_y=min_y,
            max_x=max_x,
            max_y=max_y,
        ).explored_cells
    )


def _prune_cooldowns(memory: FrontierMemory, tick: int) -> None:
    memory.taboo_edges = {
        key: expiry for key, expiry in memory.taboo_edges.items() if expiry > tick
    }
    memory.failed_targets = {
        key: expiry for key, expiry in memory.failed_targets.items() if expiry > tick
    }


def _prune_inactive_scouts(
    memory: FrontierMemory,
    active_ids: set[bytes],
) -> None:
    memory.leases = {
        unit_id: lease
        for unit_id, lease in memory.leases.items()
        if unit_id in active_ids
    }
    memory.histories = {
        unit_id: history
        for unit_id, history in memory.histories.items()
        if unit_id in active_ids
    }
    memory.observed_ticks = {
        unit_id: tick
        for unit_id, tick in memory.observed_ticks.items()
        if unit_id in active_ids
    }
    memory.taboo_edges = {
        key: expiry
        for key, expiry in memory.taboo_edges.items()
        if key[0] in active_ids
    }
    memory.failed_targets = {
        key: expiry
        for key, expiry in memory.failed_targets.items()
        if key[0] in active_ids
    }


def _taboo_was_only_geometric_step(
    scout: ScoutSnapshot,
    target: Position,
    *,
    memory: FrontierMemory,
    risk_map: Mapping[Position, CellRisk],
    obstacles: frozenset[Position],
    occupied: frozenset[Position],
    reserved: frozenset[Position],
    tick: int,
) -> bool:
    geometric: list[Position] = []
    for dx, dy in CARDINALS:
        neighbor = (scout.position[0] + dx, scout.position[1] + dy)
        if _manhattan(neighbor, target) >= _manhattan(scout.position, target):
            continue
        if (
            neighbor in obstacles
            or neighbor in occupied
            or neighbor in reserved
            or _expected_damage(risk_map.get(neighbor)) >= 1
        ):
            continue
        geometric.append(neighbor)
    return bool(geometric) and all(
        memory.taboo_edges.get(
            (scout.entity_id, scout.position, neighbor),
            0,
        )
        > tick
        for neighbor in geometric
    )


def _manhattan(left: Position, right: Position) -> int:
    return abs(left[0] - right[0]) + abs(left[1] - right[1])


def _attack_count(risk: CellRisk | None) -> int:
    return 0 if risk is None else risk.visible_attack_count


def _expected_damage(risk: CellRisk | None) -> int:
    return 0 if risk is None else risk.expected_damage


__all__ = [
    "FrontierAssignment",
    "FrontierLease",
    "FrontierMemory",
    "FrontierSettings",
    "ScoutSnapshot",
    "assign_frontiers",
    "frontier_cells",
    "next_frontier_step",
    "record_scout_observation",
]
