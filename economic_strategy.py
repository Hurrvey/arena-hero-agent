"""Bounded deterministic economy, resource, and scouting memory.

This module never queues Arena Hero actions.  It turns authoritative Turn
observations plus explicitly fallible short-lived hints into assignments that
the tactic can consume inside the command window.
"""

from __future__ import annotations

import heapq
import sys
from collections import deque
from collections.abc import Iterable, Mapping, Sequence, Set as AbstractSet
from dataclasses import dataclass, field


Position = tuple[int, int]
SCOUT_VECTORS: tuple[Position, ...] = (
    (1, 0),
    (1, 1),
    (0, 1),
    (-1, 1),
    (-1, 0),
    (-1, -1),
    (0, -1),
    (1, -1),
)
_CARDINAL_STEPS: tuple[Position, ...] = ((0, -1), (1, 0), (0, 1), (-1, 0))
_PATH_COST_UNREACHABLE = 1_000_000
_PATH_MAX_EXPANSIONS = 512
_RESOURCE_STICKY_BONUS = 2


def _uuid_key(identifier: object) -> bytes:
    raw = getattr(identifier, "bytes", None)
    return raw if isinstance(raw, bytes) else str(identifier).encode("ascii", "replace")


def _distance(left: Position, right: Position) -> int:
    return abs(left[0] - right[0]) + abs(left[1] - right[1])


def _chunk(position: Position) -> Position:
    return position[0] // 32, position[1] // 32


@dataclass(frozen=True, slots=True)
class EconomySettings:
    resource_memory_ttl: int = 64
    resource_stall_ticks: int = 6
    resource_cooldown_ticks: int = 8
    scout_stall_ticks: int = 3
    scout_ring_step: int = 10

    def __post_init__(self) -> None:
        for name in (
            "resource_memory_ttl",
            "resource_stall_ticks",
            "resource_cooldown_ticks",
            "scout_stall_ticks",
            "scout_ring_step",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(slots=True)
class ResourceProgress:
    target: Position
    best_cost: int
    stalled_turns: int = 0


@dataclass(slots=True)
class ScoutProgress:
    target: Position
    best_cost: int
    stalled_turns: int = 0


@dataclass(slots=True)
class RunnerLease:
    unit_id: bytes
    target: Position
    best_distance: int
    stalled_turns: int = 0


@dataclass(slots=True)
class EconomyMemory:
    resource_last_seen: dict[Position, int] = field(default_factory=dict)
    resource_intents: dict[bytes, Position] = field(default_factory=dict)
    resource_progress: dict[bytes, ResourceProgress] = field(default_factory=dict)
    resource_cooldowns: dict[tuple[bytes, Position], int] = field(default_factory=dict)
    scout_slots: dict[bytes, int] = field(default_factory=dict)
    scout_stages: dict[bytes, int] = field(default_factory=dict)
    scout_progress: dict[bytes, ScoutProgress] = field(default_factory=dict)
    worker_history: dict[bytes, deque[Position]] = field(default_factory=dict)
    chunk_last_seen: dict[Position, int] = field(default_factory=dict)
    runner_lease: RunnerLease | None = None
    runner_cooldowns: dict[bytes, int] = field(default_factory=dict)


def detect_two_cell_oscillation(positions: Iterable[Position]) -> bool:
    history = tuple(positions)
    return (
        len(history) >= 4
        and history[-4] == history[-2]
        and history[-3] == history[-1]
        and history[-4] != history[-3]
    )


def refresh_economy_memory(
    memory: EconomyMemory,
    *,
    tick: int,
    workers: Sequence[object],
    visible_resources: Iterable[Position],
    visible_cells: AbstractSet[Position],
    settings: EconomySettings,
) -> None:
    """Merge one Turn observation and remove stale or contradicted hints."""

    current_resources = set(visible_resources)
    for cell in current_resources:
        memory.resource_last_seen[cell] = tick
    for cell, last_seen in tuple(memory.resource_last_seen.items()):
        expired = tick - last_seen > settings.resource_memory_ttl
        definitely_visible = cell in visible_cells
        if expired or (definitely_visible and cell not in current_resources):
            memory.resource_last_seen.pop(cell, None)

    living = {_uuid_key(worker.id) for worker in workers}
    for mapping in (
        memory.resource_intents,
        memory.resource_progress,
        memory.scout_slots,
        memory.scout_stages,
        memory.scout_progress,
        memory.worker_history,
    ):
        for worker_key in tuple(mapping):
            if worker_key not in living:
                mapping.pop(worker_key, None)
    for key, retry_tick in tuple(memory.resource_cooldowns.items()):
        worker_key, target = key
        if worker_key not in living or retry_tick <= tick or target not in memory.resource_last_seen:
            memory.resource_cooldowns.pop(key, None)
    for worker_key, retry_tick in tuple(memory.runner_cooldowns.items()):
        if worker_key not in living or retry_tick <= tick:
            memory.runner_cooldowns.pop(worker_key, None)
    if memory.runner_lease is not None and memory.runner_lease.unit_id not in living:
        memory.runner_lease = None
    for worker_key, target in tuple(memory.resource_intents.items()):
        if target not in memory.resource_last_seen:
            memory.resource_intents.pop(worker_key, None)
            memory.resource_progress.pop(worker_key, None)

    used_slots = set(memory.scout_slots.values())
    for worker in sorted(workers, key=lambda item: _uuid_key(item.id)):
        worker_key = _uuid_key(worker.id)
        if worker_key not in memory.scout_slots:
            slot = 0
            while slot in used_slots:
                slot += 1
            memory.scout_slots[worker_key] = slot
            memory.scout_stages[worker_key] = 0
            used_slots.add(slot)
        history = memory.worker_history.get(worker_key)
        if history is None:
            history = deque(maxlen=4)
            memory.worker_history[worker_key] = history
        history.append(tuple(worker.position))
        memory.chunk_last_seen[_chunk(tuple(worker.position))] = tick


def invalidate_resource_targets(
    memory: EconomyMemory,
    positions: Iterable[Position],
) -> None:
    """Clear disproven resource hints and every assignment derived from them."""

    invalid = set(positions)
    for position in invalid:
        memory.resource_last_seen.pop(position, None)
    for worker_key, target in tuple(memory.resource_intents.items()):
        if target in invalid:
            memory.resource_intents.pop(worker_key, None)
            memory.resource_progress.pop(worker_key, None)


def _estimated_path_cost(
    start: Position,
    target: Position,
    blocked: set[Position],
) -> int:
    if start == target:
        return 0
    if target in blocked:
        return _PATH_COST_UNREACHABLE
    frontier: list[tuple[int, int, Position]] = [(_distance(start, target), 0, start)]
    best: dict[Position, int] = {start: 0}
    expansions = 0
    while frontier and expansions < _PATH_MAX_EXPANSIONS:
        _, cost, current = heapq.heappop(frontier)
        if cost != best.get(current):
            continue
        expansions += 1
        for dx, dy in _CARDINAL_STEPS:
            destination = current[0] + dx, current[1] + dy
            if destination in blocked:
                continue
            next_cost = cost + 1
            if next_cost >= best.get(destination, sys.maxsize):
                continue
            if destination == target:
                return next_cost
            best[destination] = next_cost
            heapq.heappush(
                frontier,
                (next_cost + _distance(destination, target), next_cost, destination),
            )
    if not frontier:
        return _PATH_COST_UNREACHABLE
    return min(priority for priority, _, _ in frontier)


def _minimum_cost_assignment(costs: Sequence[Sequence[int]]) -> tuple[int, ...]:
    if not costs:
        return ()
    rows = len(costs)
    columns = len(costs[0])
    if columns < rows or any(len(row) != columns for row in costs):
        raise ValueError("assignment matrix must be rectangular with rows <= columns")
    row_potential = [0] * (rows + 1)
    column_potential = [0] * (columns + 1)
    matched_row = [0] * (columns + 1)
    previous = [0] * (columns + 1)
    for row_index in range(1, rows + 1):
        matched_row[0] = row_index
        current_column = 0
        minimum_slack = [sys.maxsize] * (columns + 1)
        visited = [False] * (columns + 1)
        while True:
            visited[current_column] = True
            current_row = matched_row[current_column]
            delta = sys.maxsize
            next_column = 0
            for column_index in range(1, columns + 1):
                if visited[column_index]:
                    continue
                reduced = (
                    costs[current_row - 1][column_index - 1]
                    - row_potential[current_row]
                    - column_potential[column_index]
                )
                if reduced < minimum_slack[column_index]:
                    minimum_slack[column_index] = reduced
                    previous[column_index] = current_column
                if minimum_slack[column_index] < delta:
                    delta = minimum_slack[column_index]
                    next_column = column_index
            for column_index in range(columns + 1):
                if visited[column_index]:
                    row_potential[matched_row[column_index]] += delta
                    column_potential[column_index] -= delta
                else:
                    minimum_slack[column_index] -= delta
            current_column = next_column
            if matched_row[current_column] == 0:
                break
        while True:
            next_column = previous[current_column]
            matched_row[current_column] = matched_row[next_column]
            current_column = next_column
            if current_column == 0:
                break
    assignment = [-1] * rows
    for column_index in range(1, columns + 1):
        row_index = matched_row[column_index]
        if row_index:
            assignment[row_index - 1] = column_index - 1
    return tuple(assignment)


def assign_resource_targets(
    memory: EconomyMemory,
    workers: Sequence[object],
    *,
    tick: int,
    blocked: Iterable[Position],
) -> dict[bytes, Position]:
    blocked_set = set(blocked)
    resources = sorted(
        cell for cell in memory.resource_last_seen if cell not in blocked_set
    )
    ordered_workers = sorted(workers, key=lambda item: _uuid_key(item.id))
    if not ordered_workers or not resources:
        memory.resource_intents = {}
        return {}
    unassigned = _PATH_COST_UNREACHABLE * (len(ordered_workers) + 1)
    forbidden = unassigned * 2
    matrix: list[list[int]] = []
    for worker in ordered_workers:
        worker_key = _uuid_key(worker.id)
        row: list[int] = []
        for cell in resources:
            if memory.resource_cooldowns.get((worker_key, cell), 0) > tick:
                row.append(forbidden)
                continue
            path_cost = _estimated_path_cost(tuple(worker.position), cell, blocked_set)
            if path_cost >= _PATH_COST_UNREACHABLE:
                row.append(forbidden)
                continue
            age = max(0, tick - memory.resource_last_seen[cell])
            stale_penalty = 0 if age == 0 else min(6, 2 + age // 8)
            sticky = _RESOURCE_STICKY_BONUS if memory.resource_intents.get(worker_key) == cell else 0
            row.append(max(0, path_cost + stale_penalty - sticky))
        row.extend([unassigned] * len(ordered_workers))
        matrix.append(row)
    assignments: dict[bytes, Position] = {}
    for row_index, (worker, column_index) in enumerate(
        zip(ordered_workers, _minimum_cost_assignment(matrix), strict=True)
    ):
        if column_index >= len(resources) or matrix[row_index][column_index] >= forbidden:
            continue
        assignments[_uuid_key(worker.id)] = resources[column_index]
    memory.resource_intents = assignments
    return assignments


def scout_targets(
    memory: EconomyMemory,
    workers: Sequence[object],
    *,
    core_position: Position,
    tick: int,
    settings: EconomySettings,
) -> dict[bytes, Position]:
    """Return legacy radial targets for compatibility-only callers.

    The live tactic uses persistent explored/unknown frontiers instead.  This
    helper remains callable so older integrations do not break while they
    migrate away from fixed rings.
    """

    targets: dict[bytes, Position] = {}
    claimed: set[Position] = set()
    for worker in sorted(workers, key=lambda item: _uuid_key(item.id)):
        worker_key = _uuid_key(worker.id)
        slot = memory.scout_slots.setdefault(worker_key, len(memory.scout_slots))
        stage = memory.scout_stages.setdefault(worker_key, 0)
        for offset in range(len(SCOUT_VECTORS)):
            vector = SCOUT_VECTORS[(slot + stage + offset) % len(SCOUT_VECTORS)]
            ring = 1 + slot // len(SCOUT_VECTORS) + stage // len(SCOUT_VECTORS)
            radius = settings.scout_ring_step * ring
            divisor = abs(vector[0]) + abs(vector[1])
            scale = max(1, radius // divisor)
            candidate = (
                core_position[0] + vector[0] * scale,
                core_position[1] + vector[1] * scale,
            )
            if candidate not in claimed:
                targets[worker_key] = candidate
                claimed.add(candidate)
                break
    return targets


def record_worker_progress(
    memory: EconomyMemory,
    worker_id: object,
    position: Position,
    *,
    tick: int,
) -> bool:
    worker_key = _uuid_key(worker_id)
    history = memory.worker_history.get(worker_key)
    if history is None:
        history = deque(maxlen=4)
        memory.worker_history[worker_key] = history
    if not history or history[-1] != position:
        history.append(position)
    memory.chunk_last_seen[_chunk(position)] = tick
    return detect_two_cell_oscillation(history)


def update_runner_lease(
    memory: EconomyMemory,
    *,
    runner: object,
    target: Position,
    tick: int,
    stall_limit: int,
) -> bool:
    """Refresh one progress lease, releasing a stuck or oscillating runner."""

    if type(stall_limit) is not int or stall_limit < 1:
        raise ValueError("stall_limit must be a positive integer")
    worker_key = _uuid_key(runner.id)
    distance = _distance(tuple(runner.position), target)
    lease = memory.runner_lease
    if lease is None or lease.unit_id != worker_key or lease.target != target:
        memory.runner_lease = RunnerLease(worker_key, target, distance)
        return True
    if distance < lease.best_distance:
        lease.best_distance = distance
        lease.stalled_turns = 0
        return True
    lease.stalled_turns += 1
    oscillating = detect_two_cell_oscillation(memory.worker_history.get(worker_key, ()))
    if not oscillating and lease.stalled_turns < stall_limit:
        return True
    memory.runner_lease = None
    memory.runner_cooldowns[worker_key] = tick + stall_limit
    return False


def advance_stalled_targets(
    memory: EconomyMemory,
    workers: Sequence[object],
    *,
    tick: int,
    blocked: Iterable[Position],
    scout_assignments: Mapping[bytes, Position],
    settings: EconomySettings,
) -> None:
    blocked_set = set(blocked)
    for worker in workers:
        worker_key = _uuid_key(worker.id)
        resource_target = memory.resource_intents.get(worker_key)
        if resource_target is not None:
            cost = _estimated_path_cost(tuple(worker.position), resource_target, blocked_set)
            progress = memory.resource_progress.get(worker_key)
            if progress is None or progress.target != resource_target:
                memory.resource_progress[worker_key] = ResourceProgress(resource_target, cost)
            elif cost < progress.best_cost:
                progress.best_cost = cost
                progress.stalled_turns = 0
            else:
                progress.stalled_turns += 1
                if progress.stalled_turns >= settings.resource_stall_ticks:
                    memory.resource_intents.pop(worker_key, None)
                    memory.resource_progress.pop(worker_key, None)
                    memory.resource_cooldowns[(worker_key, resource_target)] = (
                        tick + settings.resource_cooldown_ticks
                    )

        scout_target = scout_assignments.get(worker_key)
        if scout_target is None:
            memory.scout_progress.pop(worker_key, None)
            continue
        cost = _estimated_path_cost(tuple(worker.position), scout_target, blocked_set)
        scout = memory.scout_progress.get(worker_key)
        oscillating = detect_two_cell_oscillation(memory.worker_history.get(worker_key, ()))
        if scout is None or scout.target != scout_target:
            memory.scout_progress[worker_key] = ScoutProgress(scout_target, cost)
        elif cost < scout.best_cost and not oscillating:
            scout.best_cost = cost
            scout.stalled_turns = 0
        else:
            scout.stalled_turns += 1
            if oscillating or scout.stalled_turns >= settings.scout_stall_ticks:
                memory.scout_progress.pop(worker_key, None)
                memory.scout_stages[worker_key] = (
                    memory.scout_stages.get(worker_key, 0) + 1
                ) % len(SCOUT_VECTORS)
