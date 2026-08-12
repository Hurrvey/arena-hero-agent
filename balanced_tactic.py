"""A deterministic, Beacon-first Arena Hero tactic.

The tactic is deliberately split into pure-ish decision helpers and the small
SDK control loop at the bottom of the file.  It never talks to the live game
while being imported, and it only acts on the complete Turn it receives.

The current game has three independent lifetime leaderboards.  Beacon ticks
are the only continuous score, so the policy protects a Beacon carrier first
and uses the remaining action slots for visible damage and Core-destruction
participation.  A Ranger's target-free cell shot and a Vanguard's empty-cell
Sweep are used only for positions derived from currently visible enemies.
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from getpass import getpass
from typing import Iterable

from arena_hero import (
    ArenaHeroClient,
    Direction,
    UnitType,
    core_resource_capacity,
    unit_cost,
)
from economic_strategy import (
    EconomyMemory,
    EconomySettings,
    advance_stalled_targets,
    assign_resource_targets,
    detect_two_cell_oscillation,
    refresh_economy_memory,
    scout_targets,
    update_runner_lease,
)
from defense_strategy import (
    DefenseAssessment,
    DefenderRoster,
    ThreatLevel,
    assess_core_defense,
    select_defenders,
)
from strategy_policy import StrategyProfile


DIRECTIONS = (Direction.UP, Direction.RIGHT, Direction.DOWN, Direction.LEFT)
_DIRECTION_DELTAS = {direction: direction.delta for direction in DIRECTIONS}
CORE_MAX_HP = 5

# These values are health caps, not upkeep or maintenance values.  Arena Hero
# v0.14 removed per-Tick upkeep; production prices are obtained through the
# official SDK's unit_cost helper instead of duplicating an old price formula.
UNIT_MAX_HP = {"WORKER": 2, "VANGUARD": 4, "RANGER": 2}
UNIT_BASE_COSTS = {
    UnitType.WORKER: 5,
    UnitType.VANGUARD: 10,
    UnitType.RANGER: 12,
}
# Backward-compatible name for callers of the pre-v0.14 tactic.  The planner
# itself never uses this table for production; ``unit_cost`` is authoritative.
UNIT_COSTS = UNIT_BASE_COSTS

# There is no maintenance reserve in v0.14.  Keeping a zero-valued name makes
# the policy's intent explicit for callers that imported the old constant.
CORE_RESERVE = 0


def _enum_name(value) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value)).upper()


def _uuid_key(identifier) -> bytes:
    """Return the server's deterministic raw-UUID ordering key."""

    raw = getattr(identifier, "bytes", None)
    if raw is not None:
        return raw
    return str(identifier).encode("ascii", "replace")


def _same_id(first, second) -> bool:
    return first is not None and second is not None and _uuid_key(first) == _uuid_key(second)


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
    """Check v0.14 Ranger geometry (only intermediate cells block)."""

    distance = _aligned_range(origin, target)
    if distance is None:
        return False
    obstacle_set = set(obstacles)
    if target in obstacle_set:
        return False
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
    distances = [
        _distance(position, enemy.position)
        for enemy in enemies
        if getattr(enemy, "position", None) is not None
    ]
    return min(distances, default=10**9)


def _step(position: tuple[int, int], direction: Direction) -> tuple[int, int] | None:
    dx, dy = _DIRECTION_DELTAS[direction]
    candidate = (position[0] + dx, position[1] + dy)
    if not all(-2**63 <= coordinate <= 2**63 - 1 for coordinate in candidate):
        return None
    return candidate


def _raw_kind(obj) -> str:
    return _enum_name(getattr(obj, "kind", ""))


def _kind(obj) -> str:
    """Return the useful combat kind for both SDK views and test doubles.

    The SDK deliberately uses ``kind == 'UNIT'`` as the discriminator and
    stores Worker/Vanguard/Ranger in ``unit_type``.  Tactics need the latter to
    reason about attack ranges, so normalize it here.
    """

    raw_kind = _raw_kind(obj)
    if raw_kind == "UNIT":
        return _enum_name(getattr(obj, "unit_type", "")) or raw_kind
    return raw_kind or _enum_name(getattr(obj, "unit_type", ""))


def _is_core_view(obj) -> bool:
    return _raw_kind(obj) == "CORE" or _kind(obj) == "CORE"


def _is_unit_view(obj) -> bool:
    return _raw_kind(obj) == "UNIT" or _kind(obj) in {"WORKER", "VANGUARD", "RANGER"}


def _obstacles_for(turn, memory: "TacticMemory | None" = None) -> set[tuple[int, int]]:
    obstacles = set(getattr(turn, "obstacle_cells", ()) or ())
    if memory is not None:
        obstacles.update(memory.known_obstacles)
    return obstacles


def _controlled_object(turn, identifier):
    if identifier is None:
        return None
    core = getattr(turn, "core", None)
    if core is not None and _same_id(core.id, identifier):
        return core
    for unit in getattr(turn, "units", ()):
        if _same_id(unit.id, identifier):
            return unit
    return None


def _controlled_ids(turn) -> set[bytes]:
    ids = {_uuid_key(unit.id) for unit in getattr(turn, "units", ())}
    core = getattr(turn, "core", None)
    if core is not None:
        ids.add(_uuid_key(core.id))
    return ids


@dataclass
class TacticMemory:
    """Small, safe cross-Turn memory.

    Obstacles are permanent and can be retained.  Unit/Core/resource positions
    are not retained.  ``carrier_id`` records the last event/visible hint for
    diagnostics only; it is never used as current Beacon truth while the
    Beacon status is hidden.  ``planned_carrier_id`` is a same-Tick intent set
    after this planner queues a visible ground pickup.
    """

    runner_id: object | None = None
    carrier_id: object | None = None
    planned_carrier_id: object | None = None
    planned_carrier_tick: int | None = None
    planned_carrier_move_tick: int | None = None
    known_obstacles: set[tuple[int, int]] = field(default_factory=set)
    processed_event_ids: set[bytes] = field(default_factory=set)
    processed_event_order: list[bytes] = field(default_factory=list)
    policy: StrategyProfile = field(default_factory=StrategyProfile.default)
    economy: EconomyMemory = field(default_factory=EconomyMemory)
    economy_diagnostics: dict[str, object] = field(default_factory=dict)
    defense: DefenseAssessment = field(default_factory=DefenseAssessment.clear)
    defenders: DefenderRoster = field(default_factory=DefenderRoster.empty)
    worker_evacuations: int = 0

    def observe(self, turn) -> None:
        # A plan is scoped to one complete Turn.  Never carry a speculative
        # pickup intent into a later state where the Beacon may have moved,
        # been stolen, or dropped outside our current vision.
        self.planned_carrier_id = None
        self.planned_carrier_tick = None
        self.planned_carrier_move_tick = None
        self.known_obstacles.update(getattr(turn, "obstacle_cells", ()) or ())

        for event in getattr(turn, "events", ()) or ():
            event_id = getattr(event, "event_id", None)
            event_key = _uuid_key(event_id) if event_id is not None else None
            if event_key is not None:
                if event_key in self.processed_event_ids:
                    continue
                self.processed_event_ids.add(event_key)
                self.processed_event_order.append(event_key)

            event_type = _enum_name(getattr(event, "event_type", ""))
            actor_id = getattr(event, "actor_id", None)
            if event_type == "BEACON_PICKED_UP":
                self.carrier_id = actor_id
                self.runner_id = None
            elif event_type in {"BEACON_DROPPED", "BEACON_DROPPED_ON_DEATH"}:
                if actor_id is None or _same_id(self.carrier_id, actor_id):
                    self.carrier_id = None
            elif event_type == "BEACON_PICKUP_FAILED":
                # A same-Tick optimistic pickup may have lost a UUID
                # contention or found a carrier already present.  Do not let
                # that failed attempt inflate the remembered shield cap.
                if actor_id is None or _same_id(self.carrier_id, actor_id):
                    self.carrier_id = None
            elif event_type == "CORE_RESPAWNED":
                # A respawn has fresh IDs and a dropped Beacon.  Do not chase a
                # stale role across the new Core generation.
                self.carrier_id = None
                self.runner_id = None
            elif event_type == "CORE_DESTROYED":
                target_id = getattr(event, "target_id", None)
                current_core = getattr(turn, "core", None)
                if (
                    target_id is None
                    or current_core is None
                    or _same_id(target_id, current_core.id)
                ):
                    self.carrier_id = None
                    self.runner_id = None

        # Current visibility overrides remembered Beacon state.  A visible
        # ground Beacon cannot still be carried; an enemy carrier also proves
        # that a remembered friendly carrier no longer owns it.
        beacon = getattr(turn, "beacon", None)
        status = _enum_name(getattr(beacon, "status", None))
        visible_carrier_id = getattr(beacon, "carrier_id", None)
        if status == "GROUND":
            self.carrier_id = None
        elif status == "CARRIED" and visible_carrier_id is not None:
            if _controlled_object(turn, visible_carrier_id) is not None:
                self.carrier_id = visible_carrier_id
                self.runner_id = None
            else:
                self.carrier_id = None

        alive = _controlled_ids(turn)
        if self.carrier_id is not None and _uuid_key(self.carrier_id) not in alive:
            self.carrier_id = None
        if self.runner_id is not None and _uuid_key(self.runner_id) not in alive:
            self.runner_id = None
        # A reconnect can replay many events.  Keep the dedupe set bounded;
        # object IDs and obstacle memory remain authoritative independently.
        if len(self.processed_event_order) > 4096:
            self.processed_event_order = self.processed_event_order[-2048:]
            self.processed_event_ids = set(self.processed_event_order)


def _beacon_is_ground(turn) -> bool:
    return _enum_name(getattr(getattr(turn, "beacon", None), "status", None)) == "GROUND"


def _beacon_is_carried(turn) -> bool:
    return _enum_name(getattr(getattr(turn, "beacon", None), "status", None)) == "CARRIED"


def _set_planned_carrier(memory: TacticMemory, turn, identifier) -> None:
    """Record a pickup intent without turning fog into authoritative state."""

    memory.carrier_id = identifier
    memory.planned_carrier_id = identifier
    memory.planned_carrier_tick = getattr(turn, "tick", None)
    memory.planned_carrier_move_tick = None


def _mark_planned_carrier_move(memory: TacticMemory, turn, unit) -> None:
    carrier = _owned_beacon_carrier(turn, memory)
    if carrier is not None and _same_id(carrier.id, unit.id):
        memory.planned_carrier_move_tick = getattr(turn, "tick", None)


def _owned_beacon_carrier(turn, memory: TacticMemory | None = None):
    beacon = getattr(turn, "beacon", None)
    status = _enum_name(getattr(beacon, "status", None))
    visible_id = getattr(beacon, "carrier_id", None)
    if status == "CARRIED" and visible_id is not None:
        return _controlled_object(turn, visible_id)
    if (
        memory is not None
        and memory.planned_carrier_id is not None
        and memory.planned_carrier_tick == getattr(turn, "tick", None)
    ):
        # This is only the optimistic result of an action queued for this
        # exact Turn; a later hidden state must return None instead.
        return _controlled_object(turn, memory.planned_carrier_id)
    return None


def _beacon_carrier_is_owned(turn, memory: TacticMemory | None = None) -> bool:
    return _owned_beacon_carrier(turn, memory) is not None


def _shield_cap(turn, memory: TacticMemory | None = None) -> int:
    carrier = _owned_beacon_carrier(turn, memory)
    if carrier is None:
        return 5
    if (
        memory is not None
        and memory.planned_carrier_move_tick == getattr(turn, "tick", None)
    ):
        # `_escape_core_cell` selected a safe destination for this carrier in
        # the current plan; evaluate the Beacon cap from its prospective cell
        # instead of the lethal pre-move snapshot.
        return 10
    core = getattr(turn, "core", None)
    if core is not None and _same_id(carrier.id, core.id):
        lethal = _visible_attack_count(
            carrier.position,
            getattr(turn, "visible_enemies", ()),
            _obstacles_for(turn, memory),
        ) >= carrier.hp + carrier.shield
    else:
        lethal = _visible_attack_count(
            carrier.position,
            getattr(turn, "visible_enemies", ()),
            _obstacles_for(turn, memory),
        ) >= carrier.hp
    # A carrier removed in combat drops the Beacon and the shield cap clamps
    # to five before the Core action.  Be conservative when its current
    # visible damage is already fatal so a stale REPAIR_SHIELD does not block
    # a production action that can still settle after the drop.
    return 5 if lethal else 10


def _visible_enemy_carrier(turn):
    beacon = getattr(turn, "beacon", None)
    if _enum_name(getattr(beacon, "status", None)) != "CARRIED":
        return None
    carrier_id = getattr(beacon, "carrier_id", None)
    if carrier_id is None:
        return None
    for enemy in getattr(turn, "visible_enemies", ()):
        if _same_id(getattr(enemy, "id", None), carrier_id):
            return enemy
    return None


def _refresh_defense_state(turn, memory: TacticMemory) -> None:
    """Recompute defense strictly from this Turn's visible geometry."""

    core = getattr(turn, "core", None)
    if core is None:
        memory.defense = DefenseAssessment.clear()
        memory.defenders = DefenderRoster.empty()
        return
    memory.defense = assess_core_defense(
        core.position,
        int(getattr(core, "hp", 0) or 0),
        int(getattr(core, "shield", 0) or 0),
        getattr(turn, "visible_enemies", ()) or (),
        _obstacles_for(turn, memory),
        watch_radius=int(memory.policy.defense_watch_radius),
    )
    carrier = _owned_beacon_carrier(turn, memory)
    memory.defenders = select_defenders(
        core.position,
        tuple(getattr(turn, "units", ()) or ()),
        carrier_id=getattr(carrier, "id", None),
        vanguard_target=int(memory.policy.defender_vanguard_target),
        ranger_target=int(memory.policy.defender_ranger_target),
    )


def _update_economy_diagnostics(
    turn,
    memory: TacticMemory,
    *,
    acted: set[object],
    runner=None,
    carrier=None,
) -> None:
    """Store aggregate planner health without IDs, cells, or route targets."""

    modes: Counter[str] = Counter()
    idle = 0
    runner_id = getattr(runner, "id", None)
    carrier_id = getattr(carrier, "id", None)
    for worker in getattr(turn, "workers", ()) or ():
        worker_key = _uuid_key(worker.id)
        if carrier_id is not None and _same_id(worker.id, carrier_id):
            mode = "BEACON_CARRIER"
        elif runner_id is not None and _same_id(worker.id, runner_id):
            mode = "BEACON_RUNNER"
        elif int(getattr(worker, "cargo", 0) or 0) > 0:
            mode = "RETURN_CARGO"
        elif getattr(worker, "position", None) in set(
            getattr(turn, "resource_cells", ()) or ()
        ):
            mode = "HARVEST"
        elif worker_key in memory.economy.resource_intents:
            mode = "RESOURCE_ROUTE"
        else:
            mode = "SCOUT"
        modes[mode] += 1
        if worker.id not in acted:
            idle += 1

    resource_stalls = sum(
        progress.stalled_turns > 0
        for progress in memory.economy.resource_progress.values()
    )
    scout_stalls = sum(
        progress.stalled_turns > 0
        for progress in memory.economy.scout_progress.values()
    )
    lease = memory.economy.runner_lease
    runner_stall = int(lease is not None and lease.stalled_turns > 0)
    oscillations = sum(
        detect_two_cell_oscillation(history)
        for history in memory.economy.worker_history.values()
    )
    memory.economy_diagnostics = {
        "visible_resource_count": len(
            set(getattr(turn, "resource_cells", ()) or ())
        ),
        "worker_modes": dict(sorted(modes.items())),
        "idle_worker_ticks": idle,
        "route_stalls": resource_stalls + scout_stalls + runner_stall,
        "oscillation_ticks": oscillations,
        "runner_progress_ticks": int(
            lease is not None and lease.stalled_turns == 0
        ),
        "defense_level": memory.defense.level.name,
        "core_threat_ticks": int(memory.defense.level >= ThreatLevel.WATCH),
        "projected_lethal_ticks": int(
            memory.defense.level is ThreatLevel.LETHAL
        ),
        "incoming_core_damage": memory.defense.incoming_damage,
        "defender_coverage": len(memory.defenders.all_ids),
        "worker_evacuations": memory.worker_evacuations,
    }


def _enemy_can_attack_cell(enemy, cell, obstacles: Iterable[tuple[int, int]]) -> bool:
    enemy_kind = _kind(enemy)
    position = getattr(enemy, "position", None)
    if position is None:
        return False
    if enemy_kind == "UNIT":
        enemy_kind = _enum_name(getattr(enemy, "unit_type", ""))
    if enemy_kind == "VANGUARD":
        return _distance(position, cell) == 1
    if enemy_kind == "RANGER":
        return _line_is_clear(position, cell, obstacles)
    return False


def _cell_is_threatened(cell, enemies, obstacles) -> bool:
    for enemy in enemies:
        if _enemy_can_attack_cell(enemy, cell, obstacles):
            return True
        # Test doubles and forward-compatible SDK objects may expose only the
        # UNIT discriminator.  Keep a small conservative fallback for those,
        # but do not treat a known Worker/Core as an attacker.
        if (
            _raw_kind(enemy) == "UNIT"
            and not _enum_name(getattr(enemy, "unit_type", None))
            and getattr(enemy, "position", None) is not None
            and _distance(enemy.position, cell) <= 2
        ):
            return True
    return False


def _visible_attack_count(cell, enemies, obstacles) -> int:
    """Conservatively count visible Units that can damage ``cell`` now."""

    count = 0
    for enemy in enemies:
        if _enemy_can_attack_cell(enemy, cell, obstacles):
            count += 1
        elif (
            _raw_kind(enemy) == "UNIT"
            and not _enum_name(getattr(enemy, "unit_type", None))
            and getattr(enemy, "position", None) is not None
            and _distance(enemy.position, cell) <= 2
        ):
            count += 1
    return count


def _carrier_destination_is_safe(
    unit, destination, turn, obstacles, memory=None, *, strict: bool = False
) -> bool:
    """Apply the profile's small visible-threat buffer to carrier movement."""

    margin = int(getattr(getattr(memory, "policy", None), "carrier_safety_margin", 0))
    attacks = _visible_attack_count(
        destination, getattr(turn, "visible_enemies", ()), obstacles
    )
    if strict:
        return attacks + margin == 0
    return attacks + margin < max(1, int(getattr(unit, "hp", 0)))


def _candidate_steps(
    unit,
    turn,
    occupied: tuple[tuple[object, tuple[int, int]], ...],
    reserved_destinations: set[tuple[int, int]],
    obstacles: Iterable[tuple[int, int]] | None = None,
):
    obstacle_set = set(getattr(turn, "obstacle_cells", ()) or ())
    if obstacles is not None:
        obstacle_set.update(obstacles)
    enemy_cells = {
        enemy.position
        for enemy in getattr(turn, "visible_enemies", ())
        if getattr(enemy, "position", None) is not None
    }
    for index, direction in enumerate(DIRECTIONS):
        destination = _step(unit.position, direction)
        if (
            destination is None
            or destination in obstacle_set
            or destination in enemy_cells
            or destination in reserved_destinations
        ):
            continue
        other_count = sum(
            position == destination
            for object_id, position in occupied
            if not _same_id(object_id, unit.id)
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
    obstacles: Iterable[tuple[int, int]] | None = None,
) -> tuple[Direction, tuple[int, int]] | None:
    if unit.position == goal:
        return None
    current_distance = _distance(unit.position, goal)
    candidates = []
    for index, direction, destination, occupancy in _candidate_steps(
        unit, turn, occupied, reserved_destinations, obstacles
    ):
        progress = current_distance - _distance(destination, goal)
        enemy_distance = _nearest_enemy_distance(
            destination, getattr(turn, "visible_enemies", ())
        )
        if retreat:
            rank = (-enemy_distance, -progress, occupancy, index)
        else:
            rank = (-progress, -enemy_distance, occupancy, index)
        candidates.append((rank, direction, destination))
    if not candidates:
        return None
    _, direction, destination = min(candidates, key=lambda item: item[0])
    return direction, destination


def _occupied(turn) -> tuple[tuple[object, tuple[int, int]], ...]:
    occupied = []
    core = getattr(turn, "core", None)
    if core is not None:
        occupied.append((core.id, core.position))
    occupied.extend((unit.id, unit.position) for unit in getattr(turn, "units", ()))
    return tuple(occupied)


def _record_move(
    unit,
    goal: tuple[int, int],
    turn,
    acted: set[object],
    occupied: tuple[tuple[object, tuple[int, int]], ...],
    reserved_destinations: set[tuple[int, int]],
    planned_from_core: set[object],
    planned_into_core: set[object],
    *,
    retreat: bool,
    obstacles: Iterable[tuple[int, int]],
    planned_moves: dict[bytes, tuple[int, int]] | None = None,
    memory: TacticMemory | None = None,
) -> bool:
    movement = _move_to_goal(
        unit,
        goal,
        turn,
        occupied,
        reserved_destinations,
        retreat=retreat,
        obstacles=obstacles,
    )
    if movement is None:
        return False
    direction, destination = movement
    unit.move(direction)
    acted.add(unit.id)
    reserved_destinations.add(destination)
    other_count = sum(
        position == destination
        for object_id, position in occupied
        if not _same_id(object_id, unit.id)
    )
    if planned_moves is not None:
        # Treat only an immediately empty destination as a confirmed movement
        # survivor for production preview.  A move into a currently occupied
        # friendly cell may depend on that occupant leaving; discounting a
        # death before that dependency resolves would make dynamic pricing
        # optimistic and can queue a no-cost-failing spawn.
        if other_count == 0:
            planned_moves[_uuid_key(unit.id)] = destination
    core = getattr(turn, "core", None)
    core_destination = (
        core is not None
        and destination == getattr(core, "position", None)
        and other_count == 1
    )
    if (
        memory is not None
        and (other_count == 0 or core_destination)
        and _carrier_destination_is_safe(unit, destination, turn, obstacles, memory)
    ):
        _mark_planned_carrier_move(memory, turn, unit)
    if core is not None:
        if _same_id(unit.id, core.id):
            return True
        if unit.position == core.position:
            planned_from_core.add(unit.id)
        if destination == core.position:
            planned_into_core.add(unit.id)
    return True


def _escape_core_cell(
    unit,
    turn,
    acted: set[object],
    occupied: tuple[tuple[object, tuple[int, int]], ...],
    reserved_destinations: set[tuple[int, int]],
    planned_from_core: set[object],
    planned_into_core: set[object],
    obstacles: Iterable[tuple[int, int]],
    memory: TacticMemory | None = None,
    planned_moves: dict[bytes, tuple[int, int]] | None = None,
) -> bool:
    """Move an idle combat unit one safe step off a crowded Core cell."""

    core = getattr(turn, "core", None)
    if core is None or unit.position != core.position:
        return False
    enemies = tuple(getattr(turn, "visible_enemies", ()))
    candidates = list(
        _candidate_steps(unit, turn, occupied, reserved_destinations, obstacles)
    )
    candidates.sort(
        key=lambda item: (
            _visible_attack_count(item[2], enemies, obstacles),
            -_nearest_enemy_distance(item[2], enemies),
            item[3],
            item[0],
        )
    )
    if candidates:
        _, direction, destination, other_count = candidates[0]
        unit.move(direction)
        acted.add(unit.id)
        reserved_destinations.add(destination)
        planned_from_core.add(unit.id)
        if planned_moves is not None and other_count == 0:
            planned_moves[_uuid_key(unit.id)] = destination
        if (
            memory is not None
            and other_count == 0
            and _carrier_destination_is_safe(unit, destination, turn, obstacles, memory)
        ):
            _mark_planned_carrier_move(memory, turn, unit)
        return True
    return False


def _worker_evacuation_candidate(
    worker,
    turn,
    occupied: tuple[tuple[object, tuple[int, int]], ...],
    reserved_destinations: set[tuple[int, int]],
    obstacles: Iterable[tuple[int, int]],
) -> tuple[Direction, tuple[int, int], int] | None:
    """Choose a currently safe flank that does not enter the Core cell."""

    core = getattr(turn, "core", None)
    if core is None:
        return None
    enemies = tuple(getattr(turn, "visible_enemies", ()) or ())
    candidates = []
    for index, direction, destination, occupancy in _candidate_steps(
        worker,
        turn,
        occupied,
        reserved_destinations,
        obstacles,
    ):
        if destination == core.position:
            continue
        attacks = _visible_attack_count(destination, enemies, obstacles)
        if attacks != 0:
            continue
        candidates.append(
            (
                (
                    -_distance(destination, core.position),
                    -_nearest_enemy_distance(destination, enemies),
                    occupancy,
                    index,
                ),
                direction,
                destination,
                occupancy,
            )
        )
    if not candidates:
        return None
    _, direction, destination, occupancy = min(candidates, key=lambda item: item[0])
    return direction, destination, occupancy


def _evacuate_worker_from_core_front(
    worker,
    turn,
    memory: TacticMemory,
    acted: set[object],
    occupied: tuple[tuple[object, tuple[int, int]], ...],
    reserved_destinations: set[tuple[int, int]],
    planned_from_core: set[object],
    planned_into_core: set[object],
    planned_moves: dict[bytes, tuple[int, int]],
    obstacles: Iterable[tuple[int, int]],
) -> bool:
    """Move a threatened nearby Worker onto a currently safe flank."""

    candidate = _worker_evacuation_candidate(
        worker,
        turn,
        occupied,
        reserved_destinations,
        obstacles,
    )
    if candidate is None:
        return False
    direction, destination, occupancy = candidate
    core = turn.core
    worker.move(direction)
    acted.add(worker.id)
    reserved_destinations.add(destination)
    if worker.position == core.position:
        planned_from_core.add(worker.id)
    if destination == core.position:
        planned_into_core.add(worker.id)
    if occupancy == 0:
        planned_moves[_uuid_key(worker.id)] = destination
    memory.worker_evacuations += 1
    return True


def _core_ready_for_spawn(turn, memory: TacticMemory | None = None) -> bool:
    """Return whether recovery should not consume the Core action first."""

    core = getattr(turn, "core", None)
    if core is None or not _is_stationary_core(core):
        return False
    memory = memory or TacticMemory()
    return core.hp >= CORE_MAX_HP and core.shield >= _shield_cap(turn, memory)


def _unit_should_recover_at_core(turn, unit, memory: TacticMemory | None = None) -> bool:
    """Keep a non-carrier damaged Unit at Core for post-combat HEAL."""

    core = getattr(turn, "core", None)
    if core is None or not _is_stationary_core(core) or unit.position != core.position:
        return False
    maximum = UNIT_MAX_HP.get(_enum_name(getattr(unit, "unit_type", None)), 0)
    if getattr(unit, "hp", 0) >= maximum:
        return False
    return _visible_attack_count(
        unit.position,
        getattr(turn, "visible_enemies", ()),
        _obstacles_for(turn, memory),
    ) < unit.hp


def _should_preheal_beacon_carrier(
    turn,
    carrier,
    memory: TacticMemory | None = None,
    acted: set[object] | None = None,
    reserved_destinations: Iterable[tuple[int, int]] = (),
) -> bool:
    """Whether a full-HP Unit carrier should queue a speculative ``HEAL``.

    Unit healing is resolved after combat and may legally be queued while the
    Unit is still at full HP.  A Beacon carrier is the one case where spending
    that action is worthwhile before damage lands: preserve the score-critical
    carrier when a visible, non-lethal attack is incoming and no safe move is
    available from its stationary Core cell.  A carrier with a legal safe move
    remains free to move instead, and an already-acted carrier cannot queue a
    second action.
    """

    core = getattr(turn, "core", None)
    if (
        carrier is None
        or core is None
        or not _is_stationary_core(core)
        or not _is_unit_view(carrier)
        or _same_id(carrier.id, core.id)
        or carrier.position != core.position
        or carrier.id in (acted or set())
    ):
        return False
    maximum = UNIT_MAX_HP.get(_enum_name(getattr(carrier, "unit_type", None)), 0)
    if maximum <= 0 or carrier.hp < maximum:
        return False

    obstacles = _obstacles_for(turn, memory)
    enemies = getattr(turn, "visible_enemies", ())
    attacks = _visible_attack_count(carrier.position, enemies, obstacles)
    if attacks <= 0 or attacks >= carrier.hp:
        return False

    occupied = _occupied(turn)
    reserved = set(reserved_destinations or ())
    return not any(
        _carrier_destination_is_safe(
            carrier, destination, turn, obstacles, memory, strict=True
        )
        for _, _, destination, _ in _candidate_steps(
            carrier,
            turn,
            occupied,
            reserved,
            obstacles,
        )
    )


def _should_vacate_core_for_spawn(
    turn,
    unit,
    memory: TacticMemory | None = None,
) -> bool:
    """Whether an idle Core-cell Unit should prepare the next spawn.

    A carrier Core still benefits from a defensive escort.  Moving that escort
    away is worthwhile only when the cell is historically over capacity or the
    current inventory can pay for the next dynamic-price Unit.  Same-Tick
    deposits are intentionally not counted here because a Worker that has
    already acted cannot both deposit and vacate the Core cell.
    """

    core = getattr(turn, "core", None)
    if (
        core is None
        or not _core_ready_for_spawn(turn, memory)
        or unit.position != core.position
    ):
        return False
    unit_max_hp = UNIT_MAX_HP.get(_enum_name(getattr(unit, "unit_type", None)), 0)
    if getattr(unit, "hp", 0) < unit_max_hp:
        return False
    core_units = sum(
        other.position == core.position
        for other in getattr(turn, "units", ())
    )
    if core_units >= 2:
        return True
    population = int(getattr(turn.state, "population", len(turn.units)))
    cost = _unit_price(_desired_spawn_type(turn, memory=memory), population)
    available = int(getattr(turn, "resources", 0))
    aggression = float(getattr(getattr(memory, "policy", None), "spawn_aggression", 0.5))
    # Keep the old full-cost gate at the default.  Higher aggression may use
    # only a bounded fraction of the next price, but never bypasses capacity
    # or recovery checks above.
    threshold = max(0, int(cost * (1.0 - 0.25 * (aggression - 0.5))))
    return available >= threshold


def _choose_runner(turn, memory: TacticMemory):
    profile = getattr(memory, "policy", StrategyProfile.default())
    beacon = getattr(turn, "beacon", None)
    beacon_position = getattr(beacon, "position", None)
    status = _enum_name(getattr(beacon, "status", None))
    bootstrap = int(getattr(profile, "bootstrap_worker_target", 6))
    economic_ready = len(getattr(turn, "workers", ())) >= bootstrap
    near_radius = int(getattr(profile, "near_beacon_radius", 12))
    enemy_carrier = _visible_enemy_carrier(turn)

    # A fogged Beacon coordinate is a useful scouting direction but not proof
    # that a Worker can pick it up.  Never create or retain a permanent runner
    # from hidden status; economy/scouting will still spread toward new chunks.
    if beacon_position is None or status not in {"GROUND", "CARRIED"}:
        memory.runner_id = None
        memory.economy.runner_lease = None
        return None
    # Visible enemy carriers are combat interception targets.  Spending a
    # Worker action tailing them starves the economy and cannot pick up Beacon.
    if status == "CARRIED" and enemy_carrier is not None:
        memory.runner_id = None
        memory.economy.runner_lease = None
        return None

    if memory.runner_id is not None:
        runner = _controlled_object(turn, memory.runner_id)
        planned_carrier = _owned_beacon_carrier(turn, memory)
        if runner is not None and (
            planned_carrier is None or not _same_id(runner.id, planned_carrier.id)
        ):
            runner_near = _distance(runner.position, beacon_position) <= near_radius
            cargo = int(getattr(runner, "cargo", 0) or 0)
            if (
                cargo == 0
                and (economic_ready or (status == "GROUND" and runner_near))
                and update_runner_lease(
                    memory.economy,
                    runner=runner,
                    target=beacon_position,
                    tick=int(getattr(turn, "tick", 0)),
                    stall_limit=int(getattr(profile, "runner_stall_ticks", 6)),
                )
            ):
                return runner
        memory.runner_id = None

    if status != "GROUND":
        return None
    # Cargo must reach the stationary Core before it can become useful.  Never
    # turn a loaded Worker into a remote Beacon runner and strand its economy.
    candidates = [
        unit
        for unit in getattr(turn, "units", ())
        if not (
            _enum_name(getattr(unit, "unit_type", None)) == "WORKER"
            and int(getattr(unit, "cargo", 0) or 0) > 0
        )
    ]
    candidates = [
        unit
        for unit in candidates
        if memory.economy.runner_cooldowns.get(_uuid_key(unit.id), 0)
        <= int(getattr(turn, "tick", 0))
        and (
            economic_ready
            or _distance(unit.position, beacon_position) <= near_radius
        )
    ]
    if not candidates:
        return None

    # A damaged Unit sharing a stationary Core should spend the next action on
    # post-combat recovery instead of becoming the Beacon runner.  Prefer any
    # healthy candidate; if every Unit is damaged, leave Core-cell candidates
    # in place and let the heal phase handle them before resuming the route.
    healthy = [
        unit
        for unit in candidates
        if getattr(unit, "hp", 0)
        >= UNIT_MAX_HP.get(_enum_name(getattr(unit, "unit_type", None)), 0)
    ]
    if healthy:
        candidates = healthy
    else:
        core = getattr(turn, "core", None)
        stationary_core = core is not None and _is_stationary_core(core)
        recovery_candidates = [
            unit
            for unit in candidates
            if not (
                stationary_core
                and unit.position == core.position
                and getattr(unit, "hp", 0)
                < UNIT_MAX_HP.get(_enum_name(getattr(unit, "unit_type", None)), 0)
            )
        ]
        if recovery_candidates:
            candidates = recovery_candidates
        else:
            return None

    # Time-to-Beacon dominates.  On equal routes, a healthy Vanguard is the
    # safest carrier, then an empty Worker (economy), then a Ranger.
    beacon_priority = float(getattr(memory.policy, "beacon_priority", 1.0))
    economy_priority = float(getattr(memory.policy, "economy_priority", 1.0))
    # Preserve the default tie-break while allowing the bounded Beacon/economy
    # tradeoff to decide only equal-length routes.  Distance and visibility
    # remain authoritative; a profile can never abandon a closer Beacon.
    economy_tie = economy_priority > beacon_priority
    type_priority = {
        "VANGUARD": 1 if economy_tie else 0,
        "WORKER": 0 if economy_tie else 1,
        "RANGER": 2,
    }
    candidates.sort(
        key=lambda unit: (
            _distance(unit.position, beacon_position),
            type_priority.get(_enum_name(unit.unit_type), 3),
            0 if getattr(unit, "hp", 0) >= UNIT_MAX_HP.get(_enum_name(unit.unit_type), 0) else 1,
            -getattr(unit, "hp", 0),
            1 if getattr(unit, "cargo", 0) else 0,
            _uuid_key(unit.id),
        )
    )
    memory.runner_id = candidates[0].id
    update_runner_lease(
        memory.economy,
        runner=candidates[0],
        target=beacon_position,
        tick=int(getattr(turn, "tick", 0)),
        stall_limit=int(getattr(profile, "runner_stall_ticks", 6)),
    )
    return candidates[0]


def _pickup_rank(unit, turn, obstacles) -> tuple[object, ...]:
    unit_type = _enum_name(getattr(unit, "unit_type", None))
    # A full-health Vanguard is the most resilient carrier.  The threat bit is
    # intentionally first: if several actors share the cell, let the tankier
    # one take the risky Beacon action.
    type_priority = {"VANGUARD": 0, "RANGER": 1, "WORKER": 2}
    threatened = 1 if _cell_is_threatened(unit.position, turn.visible_enemies, obstacles) else 0
    return (
        threatened,
        type_priority.get(unit_type, 3),
        0 if getattr(unit, "hp", 0) >= UNIT_MAX_HP.get(unit_type, 0) else 1,
        -getattr(unit, "hp", 0),
        _uuid_key(unit.id),
    )


def _pickup_candidates(
    idle_units: Iterable[object],
    *,
    prefer_health: bool = False,
) -> list[object]:
    """Return same-cell Unit candidates in server UUID order.

    Beacon contention is settled by the raw carrier UUID, not by the order in
    which a client serializes its plan.  Keeping this order makes a deliberate
    tie-break choice deterministic; survivability is applied separately by the
    caller when no explicit contender is visible.
    """

    candidates = list(idle_units)
    if prefer_health:
        healthy = [
            unit
            for unit in candidates
            if getattr(unit, "hp", 0)
            >= UNIT_MAX_HP.get(_enum_name(getattr(unit, "unit_type", None)), 0)
        ]
        candidates = healthy
    return sorted(candidates, key=lambda unit: _uuid_key(unit.id))


def _beacon_contest_visible(turn, beacon_position: tuple[int, int]) -> bool:
    """Return whether the snapshot contains an explicit pickup contender.

    Different-player objects normally cannot finish a Tick in one cell, so a
    visible enemy on the Beacon cell is unusual (for example, a historical
    over-capacity or recovery snapshot).  Treat that explicit evidence as a
    contention case; otherwise carrier survivability is more valuable than
    burning several action slots to chase a theoretical UUID tie.
    """

    return any(
        getattr(enemy, "position", None) == beacon_position
        for enemy in getattr(turn, "visible_enemies", ())
    )


def _queue_beacon_pickup(
    turn,
    memory: TacticMemory,
    acted: set[object],
    core_action_selected: bool,
) -> bool:
    """Queue a score-first ground pickup before Worker actions.

    The Beacon resolver picks the lowest raw UUID among same-Tick contenders.
    In the normal legal state one player's object occupies the cell, so the
    tactic chooses one durable carrier and preserves other action slots.  An
    explicit visible contender switches that choice to our lowest UUID.  A
    healthy Core already on the Beacon cell is the preferred carrier because
    it protects the objective without consuming a Unit action or occupying the
    Unit slot.  Pickup does consume the Core's action, so it intentionally
    replaces same-Tick recovery or spawning.  If that Core needs healing or
    shield repair, a same-cell Unit carries while the Core keeps its recovery
    action.
    """

    if not _beacon_is_ground(turn):
        return core_action_selected
    beacon_position = turn.beacon.position
    obstacles = _obstacles_for(turn, memory)
    idle_units = [
        unit
        for unit in getattr(turn, "units", ())
        if unit.id not in acted and unit.position == beacon_position
    ]
    core = getattr(turn, "core", None)

    core_on_beacon = (
        core is not None
        and _is_stationary_core(core)
        and core.position == beacon_position
    )
    core_threatened = bool(
        core_on_beacon
        and _cell_is_threatened(
            core.position,
            getattr(turn, "visible_enemies", ()),
            obstacles,
        )
    )
    core_lethal_threat = bool(
        core_on_beacon
        and _visible_attack_count(
            core.position,
            getattr(turn, "visible_enemies", ()),
            obstacles,
        )
        >= core.hp + core.shield
    )
    core_needs_recovery = bool(
        core_on_beacon
        and (
            core.hp < CORE_MAX_HP
            or core.shield < _shield_cap(turn, memory)
        )
    )
    explicit_contest = _beacon_contest_visible(turn, beacon_position)

    # In an explicit same-cell contest the server compares every submitted
    # pickup by raw UUID, including a normal Core.  Select the actual lowest
    # eligible contender instead of letting the healthy-Core shortcut lose a
    # deterministic tie to an enemy or to a lower-UUID Unit.
    if explicit_contest and core_on_beacon and not core_action_selected:
        contenders = [(core.id, core)] + [
            (unit.id, unit) for unit in idle_units
        ]
        contender = min(contenders, key=lambda item: _uuid_key(item[0]))[1]
        contender.pickup_beacon()
        _set_planned_carrier(memory, turn, contender.id)
        memory.runner_id = None
        if contender is not core:
            acted.add(contender.id)
        return contender is core

    # A healthy Core is the safest Beacon carrier and preserves every Unit
    # action.  A nonlethal visible attacker does not justify forfeiting a
    # guaranteed Beacon Tick.  When recovery is useful or incoming damage is
    # visibly lethal, first give a same-cell healthy Unit the carrier role so
    # the Core can keep its post-combat action.
    if (
        core_on_beacon
        and not core_action_selected
        and not core_needs_recovery
        and not core_lethal_threat
    ):
        core.pickup_beacon()
        _set_planned_carrier(memory, turn, core.id)
        memory.runner_id = None
        return True

    # If the Core is recovering (or under direct threat), use a Unit as the
    # carrier so the Core action remains available.  A damaged Unit is still a
    # better Beacon attempt than silently abandoning an exposed objective when
    # no healthy alternative exists.
    if idle_units:
        all_candidates = _pickup_candidates(idle_units)
        candidates = all_candidates
        if not explicit_contest and (core_needs_recovery or core_threatened):
            healthy_candidates = _pickup_candidates(
                idle_units,
                prefer_health=True,
            )
            if healthy_candidates:
                candidates = healthy_candidates
        if core_on_beacon and (core_needs_recovery or core_threatened):
            healthy = _pickup_candidates(candidates, prefer_health=True)
            # Preserve the Core recovery action, but avoid turning a visibly
            # dying Unit into a doomed Beacon carrier when every candidate is
            # already damaged.  In that case the Core is the safer fallback;
            # its post-combat HEAL/REPAIR could not prevent a lethal hit anyway.
            if healthy and not explicit_contest:
                candidates = healthy
            elif not healthy and not explicit_contest:
                candidates = []

        if not candidates:
            if core_on_beacon and not core_action_selected:
                core.pickup_beacon()
                _set_planned_carrier(memory, turn, core.id)
                memory.runner_id = None
                return True
            return core_action_selected

        # The resolver uses raw UUIDs only when multiple players actually
        # submit a pickup.  In the normal legal state there is one player's
        # object on the cell, so choose one durable carrier and leave the other
        # Units free to escort, harvest, or vacate a Core spawn cell.  If an
        # explicit enemy contender is visible, use our lowest UUID to maximize
        # the deterministic tie-break instead of sacrificing several actions.
        if explicit_contest:
            unit = min(candidates, key=lambda item: _uuid_key(item.id))
        else:
            unit = min(candidates, key=lambda item: _pickup_rank(item, turn, obstacles))
        unit.pickup_beacon()
        acted.add(unit.id)
        _set_planned_carrier(memory, turn, unit.id)
        memory.runner_id = None
        return core_action_selected

    # No Unit can carry.  Secure the Beacon with the Core even under visible
    # threat: Core recovery resolves after combat and cannot prevent a lethal
    # hit, while pickup can still earn the Tick if the attack does not land.
    if (
        core_on_beacon
        and not core_action_selected
    ):
        core.pickup_beacon()
        _set_planned_carrier(memory, turn, core.id)
        memory.runner_id = None
        return True
    return core_action_selected


def _queue_runner_action(
    turn,
    runner,
    memory: TacticMemory,
    acted: set[object],
    occupied: tuple[tuple[object, tuple[int, int]], ...],
    reserved_destinations: set[tuple[int, int]],
    planned_from_core: set[object],
    planned_into_core: set[object],
    planned_moves: dict[bytes, tuple[int, int]] | None = None,
) -> None:
    if runner is None or runner.id in acted or _owned_beacon_carrier(turn, memory) is not None:
        return
    beacon_position = getattr(getattr(turn, "beacon", None), "position", None)
    core = getattr(turn, "core", None)
    if beacon_position is None or core is None:
        return
    # Status GROUND at the same cell was handled by _queue_beacon_pickup.  An
    # unknown status must never be treated as permission to pick up.
    if _beacon_is_ground(turn) and runner.position == beacon_position:
        return
    threatened = _cell_is_threatened(
        runner.position,
        getattr(turn, "visible_enemies", ()),
        _obstacles_for(turn, memory),
    )
    goal = core.position if threatened else beacon_position
    _record_move(
        runner,
        goal,
        turn,
        acted,
        occupied,
        reserved_destinations,
        planned_from_core,
        planned_into_core,
        retreat=threatened,
        obstacles=_obstacles_for(turn, memory),
        planned_moves=planned_moves,
        memory=memory,
    )


def _enemy_effective_hp(enemy) -> int:
    return int(getattr(enemy, "hp", 0)) + int(getattr(enemy, "shield", 0) or 0)


def _id_in(identifier, identifiers: Iterable[object]) -> bool:
    return any(_same_id(identifier, candidate) for candidate in identifiers)


def _combat_target_rank(
    enemy,
    turn,
    memory: TacticMemory | None = None,
    obstacles: Iterable[tuple[int, int]] = (),
) -> tuple[object, ...]:
    carrier = _visible_enemy_carrier(turn)
    is_carrier = carrier is not None and _same_id(getattr(enemy, "id", None), carrier.id)
    kind = _kind(enemy)
    own_carrier = _owned_beacon_carrier(turn, memory)
    threatens_carrier = (
        own_carrier is not None
        and _enemy_can_attack_cell(enemy, own_carrier.position, obstacles)
    )
    is_core_attacker = _id_in(
        getattr(enemy, "id", None),
        memory.defense.attacker_ids if memory is not None else (),
    )
    is_core_approacher = _id_in(
        getattr(enemy, "id", None),
        memory.defense.approacher_ids if memory is not None else (),
    )
    lethal_core_attacker = bool(
        memory is not None
        and memory.defense.level is ThreatLevel.LETHAL
        and is_core_attacker
    )
    # Only a visible attacker that can remove the Core this Tick outranks the
    # enemy Beacon carrier.  Otherwise preserve Beacon control, then remove
    # threats to our carrier/Core before ordinary Core pressure.
    priority = (
        0
        if lethal_core_attacker
        else 1
        if is_carrier
        else 2
        if threatens_carrier
        else 3
        if is_core_attacker
        else 4
        if is_core_approacher
        else 5
        if kind == "CORE"
        else 6
    )
    return (priority, _enemy_effective_hp(enemy), _uuid_key(enemy.id))


def _predictable_destinations(
    enemy,
    turn,
    obstacles,
    blocked_cells: Iterable[tuple[int, int]] = (),
) -> tuple[tuple[int, int], ...]:
    """Derive conservative legal one-step cells from visible enemy state.

    A target-free shot or Sweep is only useful if the enemy can actually
    finish movement there.  Current friendly occupants are treated as blocked
    because this planner has not yet established a same-Tick dependency chain
    that would vacate them; that avoids spending combat actions on cells that
    are visibly sealed by our own fleet.
    """

    position = getattr(enemy, "position", None)
    if position is None:
        return ()
    enemy_kind = _kind(enemy)
    blocked = set(blocked_cells)
    if _is_unit_view(enemy):
        cells = []
        obstacle_set = set(obstacles)
        for direction in DIRECTIONS:
            destination = _step(position, direction)
            if (
                destination is not None
                and destination not in obstacle_set
                and destination not in blocked
            ):
                cells.append(destination)
        return tuple(cells)

    # A moving Core exposes its destination.  It only reaches that cell on the
    # fourth progress Tick, so include it when the next resolution completes.
    view = getattr(enemy, "view", None)
    state = _enum_name(getattr(enemy, "state", getattr(view, "state", None)))
    destination = getattr(enemy, "destination", getattr(view, "destination", None))
    progress = getattr(enemy, "move_progress", getattr(view, "move_progress", None))
    required = getattr(
        enemy,
        "move_required_ticks",
        getattr(view, "move_required_ticks", None),
    )
    if (
        state == "MOVING"
        and destination is not None
        and progress is not None
        and required is not None
    ):
        if (
            int(progress) + 1 >= int(required)
            and destination not in set(obstacles)
            and destination not in set(getattr(turn, "resource_cells", ()))
            and destination not in blocked
            and not _visible_destination_conflict(enemy, destination, turn)
        ):
            return (destination,)
    return ()


def _visible_destination_conflict(enemy, destination, turn) -> bool:
    """Return whether a known object prevents a Core's final migration step."""

    moving_id = getattr(enemy, "id", None)
    for object_id, position in _occupied(turn):
        if not _same_id(object_id, moving_id) and position == destination:
            return True
    for other in getattr(turn, "visible_enemies", ()):
        if (
            not _same_id(getattr(other, "id", None), moving_id)
            and getattr(other, "position", None) == destination
        ):
            return True
    return False


def _core_completes_move_next_tick(enemy, turn, obstacles) -> bool:
    """Whether a visible moving Core will leave its current cell this Tick."""

    if not _is_core_view(enemy):
        return False
    view = getattr(enemy, "view", None)
    state = _enum_name(getattr(enemy, "state", getattr(view, "state", None)))
    destination = getattr(enemy, "destination", getattr(view, "destination", None))
    progress = getattr(enemy, "move_progress", getattr(view, "move_progress", None))
    required = getattr(
        enemy,
        "move_required_ticks",
        getattr(view, "move_required_ticks", None),
    )
    return bool(
        state == "MOVING"
        and destination is not None
        and progress is not None
        and required is not None
        and int(progress) + 1 >= int(required)
        and destination not in set(obstacles)
        and destination not in set(getattr(turn, "resource_cells", ()))
        and destination != getattr(enemy, "position", None)
        and not _visible_destination_conflict(enemy, destination, turn)
    )


def _predicted_cells(turn, origin, memory: TacticMemory):
    obstacles = _obstacles_for(turn, memory)
    blocked_cells = {position for _, position in _occupied(turn)}
    grouped: dict[tuple[int, int], list[object]] = defaultdict(list)
    for enemy in getattr(turn, "visible_enemies", ()):
        for cell in _predictable_destinations(
            enemy,
            turn,
            obstacles,
            blocked_cells,
        ):
            if _line_is_clear(origin, cell, obstacles):
                grouped[cell].append(enemy)
    return grouped


def _defense_goal(unit, turn, memory: TacticMemory):
    core = getattr(turn, "core", None)
    if core is None:
        return None
    carrier = _owned_beacon_carrier(turn, memory)
    if carrier is not None and _same_id(unit.id, carrier.id):
        return None
    unit_type = _enum_name(getattr(unit, "unit_type", None))
    defensive_radius = 2 if unit_type == "VANGUARD" else 3
    selected = _id_in(unit.id, memory.defenders.all_ids)
    full_recall = memory.defense.level >= ThreatLevel.APPROACH
    if (selected or full_recall) and _distance(unit.position, core.position) > defensive_radius:
        return core.position, False
    return None


def _combat_goal(unit, turn, memory: TacticMemory, runner):
    defense_goal = _defense_goal(unit, turn, memory)
    if memory.defense.level >= ThreatLevel.APPROACH and defense_goal is not None:
        return defense_goal

    enemy_carrier = _visible_enemy_carrier(turn)
    if enemy_carrier is not None:
        return enemy_carrier.position, False

    own_carrier = _owned_beacon_carrier(turn, memory)
    core_is_carrier = bool(
        own_carrier is not None
        and getattr(turn, "core", None) is not None
        and _same_id(own_carrier.id, turn.core.id)
    )
    if own_carrier is not None:
        if _same_id(unit.id, own_carrier.id):
            core = turn.core
            if core is not None and unit.position != core.position:
                return core.position, True
            return None
        # When the Core carries the Beacon, a small assigned defense ring is
        # sufficient.  Pulling every combat Unit onto the Core would abandon
        # economy pressure and prevent production.
        if not core_is_carrier and _distance(unit.position, own_carrier.position) > 1:
            return own_carrier.position, False
        if not core_is_carrier:
            return None

    if defense_goal is not None:
        return defense_goal

    enemy_cores = [enemy for enemy in turn.visible_enemies if _is_core_view(enemy)]
    if enemy_cores:
        target = min(
            enemy_cores,
            key=lambda enemy: (_enemy_effective_hp(enemy), _uuid_key(enemy.id)),
        )
        return target.position, False

    if runner is not None:
        if _same_id(unit.id, runner.id):
            return getattr(turn.beacon, "position", None), False
        # One combat escort follows the runner; the rest can continue probing
        # visible enemy territory once it appears.
        if _distance(unit.position, runner.position) > 2:
            return runner.position, False
    return None


def _queue_ranger_actions(
    turn,
    memory: TacticMemory,
    acted: set[object],
    occupied: tuple[tuple[object, tuple[int, int]], ...],
    reserved_destinations: set[tuple[int, int]],
    planned_from_core: set[object],
    planned_into_core: set[object],
    runner,
    planned_moves: dict[bytes, tuple[int, int]] | None = None,
) -> None:
    obstacles = _obstacles_for(turn, memory)
    enemies = tuple(getattr(turn, "visible_enemies", ()))
    for ranger in sorted(turn.rangers, key=lambda unit: _uuid_key(unit.id)):
        if ranger.id in acted:
            continue
        own_carrier = _owned_beacon_carrier(turn, memory)
        if (
            (own_carrier is None or not _same_id(ranger.id, own_carrier.id))
            and _unit_should_recover_at_core(turn, ranger, memory)
        ):
            continue
        if own_carrier is not None and _same_id(ranger.id, own_carrier.id):
            core = turn.core
            threatened = _cell_is_threatened(ranger.position, enemies, obstacles)
            lethal_threat = (
                _visible_attack_count(ranger.position, enemies, obstacles)
                >= ranger.hp
            )
            if (
                core is not None
                and ranger.position == core.position
                and (
                    lethal_threat
                    or _should_vacate_core_for_spawn(turn, ranger, memory)
                )
                and _escape_core_cell(
                    ranger,
                    turn,
                    acted,
                    occupied,
                    reserved_destinations,
                    planned_from_core,
                    planned_into_core,
                    obstacles,
                    memory,
                    planned_moves,
                )
            ):
                continue
            if core is not None and threatened and ranger.position != core.position:
                if _record_move(
                    ranger,
                    core.position,
                    turn,
                    acted,
                    occupied,
                    reserved_destinations,
                    planned_from_core,
                    planned_into_core,
                    retreat=True,
                    obstacles=obstacles,
                    planned_moves=planned_moves,
                    memory=memory,
                ):
                    continue
            if _should_preheal_beacon_carrier(
                turn,
                ranger,
                memory,
                acted,
                reserved_destinations,
            ):
                continue
        legal_targets = [
            enemy
            for enemy in enemies
            if getattr(enemy, "position", None) not in obstacles
            and not _core_completes_move_next_tick(enemy, turn, obstacles)
            and _line_is_clear(ranger.position, enemy.position, obstacles)
        ]
        if legal_targets:
            target = min(
                legal_targets,
                key=lambda enemy: _combat_target_rank(enemy, turn, memory, obstacles),
            )
            enemy_carrier = _visible_enemy_carrier(turn)
            if (
                _is_core_view(target)
                or (
                    enemy_carrier is not None
                    and _same_id(target.id, enemy_carrier.id)
                )
            ):
                # Cell-fire may retarget a lower-HP escort in the same cell.
                # Precision fire protects the two score objectives: the
                # visible Beacon carrier and Core-destruction participation.
                ranger.shoot(target.id, expected_cell=target.position)
            else:
                ranger.shoot_cell(target.position)
            acted.add(ranger.id)
            continue

        predicted = _predicted_cells(turn, ranger.position, memory)
        if (
            predicted
            and memory.defense.level < ThreatLevel.APPROACH
            and float(memory.policy.combat_priority) >= 0.75
        ):
            def prediction_rank(item):
                cell, possible = item
                has_carrier = any(
                    _same_id(enemy.id, getattr(_visible_enemy_carrier(turn), "id", None))
                    for enemy in possible
                )
                has_core = any(_is_core_view(enemy) for enemy in possible)
                return (
                    0 if has_carrier else 1 if has_core else 2,
                    -len(possible),
                    _distance(ranger.position, cell),
                    cell[0],
                    cell[1],
                )

            cell, _ = min(predicted.items(), key=prediction_rank)
            ranger.shoot_cell(cell)
            acted.add(ranger.id)
            continue

        if (
            _should_vacate_core_for_spawn(turn, ranger, memory)
            and _escape_core_cell(
                ranger,
                turn,
                acted,
                occupied,
                reserved_destinations,
                planned_from_core,
                planned_into_core,
                obstacles,
                memory,
                planned_moves,
            )
        ):
            continue

        goal = _combat_goal(ranger, turn, memory, runner)
        if goal is not None:
            _record_move(
                ranger,
                goal[0],
                turn,
                acted,
                occupied,
                reserved_destinations,
                planned_from_core,
                planned_into_core,
                retreat=goal[1],
                obstacles=obstacles,
                planned_moves=planned_moves,
                memory=memory,
            )


def _queue_vanguard_actions(
    turn,
    memory: TacticMemory,
    acted: set[object],
    occupied: tuple[tuple[object, tuple[int, int]], ...],
    reserved_destinations: set[tuple[int, int]],
    planned_from_core: set[object],
    planned_into_core: set[object],
    runner,
    planned_moves: dict[bytes, tuple[int, int]] | None = None,
) -> None:
    obstacles = _obstacles_for(turn, memory)
    direction_order = {direction: index for index, direction in enumerate(DIRECTIONS)}
    enemies = tuple(getattr(turn, "visible_enemies", ()))
    for vanguard in sorted(turn.vanguards, key=lambda unit: _uuid_key(unit.id)):
        if vanguard.id in acted:
            continue
        own_carrier = _owned_beacon_carrier(turn, memory)
        if (
            (own_carrier is None or not _same_id(vanguard.id, own_carrier.id))
            and _unit_should_recover_at_core(turn, vanguard, memory)
        ):
            continue
        if own_carrier is not None and _same_id(vanguard.id, own_carrier.id):
            core = turn.core
            threatened = _cell_is_threatened(vanguard.position, enemies, obstacles)
            lethal_threat = (
                _visible_attack_count(vanguard.position, enemies, obstacles)
                >= vanguard.hp
            )
            if (
                core is not None
                and vanguard.position == core.position
                and (
                    lethal_threat
                    or _should_vacate_core_for_spawn(turn, vanguard, memory)
                )
                and _escape_core_cell(
                    vanguard,
                    turn,
                    acted,
                    occupied,
                    reserved_destinations,
                    planned_from_core,
                    planned_into_core,
                    obstacles,
                    memory,
                    planned_moves,
                )
            ):
                continue
            if core is not None and threatened and vanguard.position != core.position:
                if _record_move(
                    vanguard,
                    core.position,
                    turn,
                    acted,
                    occupied,
                    reserved_destinations,
                    planned_from_core,
                    planned_into_core,
                    retreat=True,
                    obstacles=obstacles,
                    planned_moves=planned_moves,
                    memory=memory,
                ):
                    continue
            if _should_preheal_beacon_carrier(
                turn,
                vanguard,
                memory,
                acted,
                reserved_destinations,
            ):
                continue
        by_cell: dict[tuple[int, int], list[object]] = defaultdict(list)
        for enemy in enemies:
            if (
                _distance(vanguard.position, enemy.position) == 1
                and not _core_completes_move_next_tick(enemy, turn, obstacles)
            ):
                by_cell[enemy.position].append(enemy)
        candidates: list[tuple[tuple[object, ...], Direction]] = []
        for cell, cell_enemies in by_cell.items():
            if cell in obstacles:
                continue
            direction = _direction_to_adjacent(vanguard.position, cell)
            if direction is None:
                continue
            rank = (
                min(
                    _combat_target_rank(enemy, turn, memory, obstacles)
                    for enemy in cell_enemies
                ),
                -len(cell_enemies),
                direction_order[direction],
                cell[0],
                cell[1],
            )
            candidates.append((rank, direction))
        if candidates:
            _, direction = min(candidates, key=lambda item: item[0])
            vanguard.sweep(direction)
            acted.add(vanguard.id)
            continue

        # v0.14 permits an empty sweep.  Use it only for a cell an observed
        # enemy can legally enter in one cardinal move.
        predicted_cells: dict[tuple[int, int], list[object]] = defaultdict(list)
        blocked_cells = {position for _, position in occupied}
        for enemy in enemies:
            for cell in _predictable_destinations(
                enemy,
                turn,
                obstacles,
                blocked_cells,
            ):
                if _distance(vanguard.position, cell) == 1 and cell not in obstacles:
                    predicted_cells[cell].append(enemy)
        if (
            predicted_cells
            and memory.defense.level < ThreatLevel.APPROACH
            and float(memory.policy.combat_priority) >= 0.75
        ):
            cell = min(
                predicted_cells,
                key=lambda position: (
                    0
                    if any(
                        _same_id(enemy.id, getattr(_visible_enemy_carrier(turn), "id", None))
                        for enemy in predicted_cells[position]
                    )
                    else 1,
                    -len(predicted_cells[position]),
                    position[0],
                    position[1],
                ),
            )
            direction = _direction_to_adjacent(vanguard.position, cell)
            if direction is not None:
                vanguard.sweep(direction)
                acted.add(vanguard.id)
                continue

        if (
            _should_vacate_core_for_spawn(turn, vanguard, memory)
            and _escape_core_cell(
                vanguard,
                turn,
                acted,
                occupied,
                reserved_destinations,
                planned_from_core,
                planned_into_core,
                obstacles,
                memory,
                planned_moves,
            )
        ):
            continue

        goal = _combat_goal(vanguard, turn, memory, runner)
        if goal is not None:
            _record_move(
                vanguard,
                goal[0],
                turn,
                acted,
                occupied,
                reserved_destinations,
                planned_from_core,
                planned_into_core,
                retreat=goal[1],
                obstacles=obstacles,
                planned_moves=planned_moves,
                memory=memory,
            )


def _queue_worker_actions(
    turn,
    acted: set[object],
    planned_from_core: set[object],
    planned_into_core: set[object],
    reserved_destinations: set[tuple[int, int]] | None = None,
    memory: TacticMemory | None = None,
    runner=None,
    carrier=None,
    planned_moves: dict[bytes, tuple[int, int]] | None = None,
) -> int:
    """Queue Worker economy and return an estimate of pending deposits."""

    core = turn.core
    if core is None:
        return 0
    memory = memory or TacticMemory()
    reserved_destinations = reserved_destinations if reserved_destinations is not None else set()
    planned_moves = planned_moves if planned_moves is not None else {}
    obstacles = _obstacles_for(turn, memory)
    occupied = _occupied(turn)
    core_position = core.position
    stationary_core = _is_stationary_core(core)
    claimed_resources: set[tuple[int, int]] = set()
    pending_deposit = 0
    pending_space = max(0, int(getattr(turn, "resource_space", 0)))
    carrier_id = getattr(carrier, "id", None)
    runner_id = getattr(runner, "id", None)

    settings = EconomySettings(
        resource_memory_ttl=int(getattr(memory.policy, "resource_memory_ttl", 64)),
        resource_stall_ticks=int(getattr(memory.policy, "resource_stall_ticks", 6)),
        scout_ring_step=int(getattr(memory.policy, "scout_ring_step", 10)),
    )
    blocked_routes = set(obstacles) | {
        enemy.position
        for enemy in getattr(turn, "visible_enemies", ())
        if getattr(enemy, "position", None) is not None
    }
    economic_workers = [
        worker
        for worker in turn.workers
        if not worker.cargo
        and not (
            carrier_id is not None and _same_id(worker.id, carrier_id)
        )
        and not (
            runner_id is not None and _same_id(worker.id, runner_id)
        )
    ]
    existing_scouts = [
        worker
        for worker in economic_workers
        if _uuid_key(worker.id) not in memory.economy.resource_intents
    ]
    previous_scout_targets = scout_targets(
        memory.economy,
        existing_scouts,
        core_position=core_position,
        tick=int(getattr(turn, "tick", 0)),
        settings=settings,
    )
    advance_stalled_targets(
        memory.economy,
        economic_workers,
        tick=int(getattr(turn, "tick", 0)),
        blocked=blocked_routes,
        scout_assignments=previous_scout_targets,
        settings=settings,
    )
    resource_assignments = assign_resource_targets(
        memory.economy,
        economic_workers,
        tick=int(getattr(turn, "tick", 0)),
        blocked=blocked_routes,
    )
    scouting_workers = [
        worker
        for worker in economic_workers
        if _uuid_key(worker.id) not in resource_assignments
    ]
    worker_scout_targets = scout_targets(
        memory.economy,
        scouting_workers,
        core_position=core_position,
        tick=int(getattr(turn, "tick", 0)),
        settings=settings,
    )

    # The Worker loop is UUID-ordered.  A cargo Worker at the Core may be
    # examined before a later, visibly doomed Worker gets its safe retreat
    # queued.  Pre-project only ordinary threatened Workers whose deterministic
    # retreat has an immediately survivable destination; this prevents an
    # unplanned N-1 price from suppressing a deposit.  Keep the real reservation
    # set untouched so the actual loop can still choose the same destinations.
    projected_reservations = set(reserved_destinations)
    for candidate in sorted(turn.workers, key=lambda unit: _uuid_key(unit.id)):
        if candidate.id in acted:
            continue
        if (
            (runner_id is not None and _same_id(candidate.id, runner_id))
            or (carrier_id is not None and _same_id(candidate.id, carrier_id))
            or not _cell_is_threatened(
                candidate.position,
                turn.visible_enemies,
                obstacles,
            )
        ):
            continue
        defense_evacuation = bool(
            memory.defense.level >= ThreatLevel.ATTACK
            and _distance(candidate.position, core_position)
            <= int(memory.policy.worker_evacuation_radius)
        )
        if defense_evacuation:
            evacuation = _worker_evacuation_candidate(
                candidate,
                turn,
                occupied,
                projected_reservations,
                obstacles,
            )
            movement = (
                (evacuation[0], evacuation[1])
                if evacuation is not None
                else None
            )
        else:
            movement = _move_to_goal(
                candidate,
                core_position,
                turn,
                occupied,
                projected_reservations,
                retreat=True,
                obstacles=obstacles,
            )
        if movement is None:
            continue
        _, destination = movement
        if (
            _visible_attack_count(destination, turn.visible_enemies, obstacles)
            >= candidate.hp
        ):
            continue
        other_count = sum(
            position == destination
            for object_id, position in occupied
            if not _same_id(object_id, candidate.id)
        )
        core_destination = (
            stationary_core
            and destination == core_position
            and other_count == 1
        )
        if other_count != 0 and not core_destination:
            continue
        planned_moves[_uuid_key(candidate.id)] = destination
        projected_reservations.add(destination)

    for worker in sorted(turn.workers, key=lambda unit: _uuid_key(unit.id)):
        if worker.id in acted:
            continue
        is_runner = runner_id is not None and _same_id(worker.id, runner_id)
        is_carrier = carrier_id is not None and _same_id(worker.id, carrier_id)
        same_tick_core_pickup_escort = bool(
            memory.planned_carrier_tick == getattr(turn, "tick", None)
            and carrier is core
            and worker.position == core_position
            and not worker.cargo
        )
        if same_tick_core_pickup_escort:
            continue
        worker_threatened = _cell_is_threatened(
            worker.position,
            turn.visible_enemies,
            obstacles,
        )
        lethal_carrier_threat = bool(
            is_carrier
            and _visible_attack_count(
                worker.position,
                turn.visible_enemies,
                obstacles,
            )
            >= worker.hp
        )
        preheal_carrier = bool(
            is_carrier
            and _should_preheal_beacon_carrier(
                turn,
                worker,
                memory,
                acted,
                reserved_destinations,
            )
        )
        defensive_evacuation = bool(
            not is_carrier
            and not is_runner
            and memory.defense.level >= ThreatLevel.ATTACK
            and _distance(worker.position, core_position)
            <= int(memory.policy.worker_evacuation_radius)
            and worker_threatened
        )
        if defensive_evacuation:
            _evacuate_worker_from_core_front(
                worker,
                turn,
                memory,
                acted,
                occupied,
                reserved_destinations,
                planned_from_core,
                planned_into_core,
                planned_moves,
                obstacles,
            )
            # If every flank is blocked or visibly threatened, WAIT instead
            # of leading the Worker onto the attacked Core cell.
            continue
        (
            _preview_type,
            _current_spawn_cost,
            _settled_spawn_cost,
            preview_dead_ids,
            _preview_core_deaths,
        ) = _spawn_preview(turn, memory, planned_from_core, planned_moves)
        spawn_cost = (
            _settled_spawn_cost if preview_dead_ids else _current_spawn_cost
        )
        safe_exit_available = any(
            _visible_attack_count(destination, turn.visible_enemies, obstacles) == 0
            for _, _, destination, _ in _candidate_steps(
                worker,
                turn,
                occupied,
                reserved_destinations,
                obstacles,
            )
        )
        spawn_funded_without_this_deposit = bool(
            stationary_core
            and worker.position == core_position
            and not worker_threatened
            and safe_exit_available
            and _core_ready_for_spawn(turn, memory)
            and _anticipated_core_hp_damage(turn, memory) == 0
            and int(getattr(turn, "resources", 0)) + pending_deposit >= spawn_cost
        )

        # Deposit is an economic action for a Beacon runner as well.  It must
        # not be suppressed merely because an unknown Beacon status left the
        # runner waiting on the public coordinate.
        if (
            worker.cargo
            and stationary_core
            and worker.position == core_position
            and pending_space > 0
            and not lethal_carrier_threat
            and not preheal_carrier
            and not spawn_funded_without_this_deposit
        ):
            worker.deposit()
            acted.add(worker.id)
            deposited = min(int(worker.cargo or 0), pending_space)
            pending_deposit += deposited
            pending_space -= deposited
            continue

        # The Beacon carrier's safety and bonus harvest outrank ordinary
        # resource routing.  Deposit is legal even with cargo and is resolved
        # after Beacon pickup, so pickup has already won this Tick if needed.
        if is_carrier:
            threatened = worker_threatened
            if (
                not worker.cargo
                and not threatened
                and worker.position in turn.resource_cells
                and worker.position not in claimed_resources
            ):
                # Beacon pickup resolves first, so this harvest receives the
                # same-Tick two-resource bonus even though the view still
                # shows the Beacon as ground.
                worker.harvest()
                claimed_resources.add(worker.position)
                acted.add(worker.id)
                continue
            if worker.position != core_position:
                _record_move(
                    worker,
                    core_position,
                    turn,
                    acted,
                    occupied,
                    reserved_destinations,
                    planned_from_core,
                    planned_into_core,
                    retreat=threatened,
                    obstacles=obstacles,
                    planned_moves=planned_moves,
                    memory=memory,
                )
                continue
            if not worker.cargo and worker.position in turn.resource_cells:
                if worker.position not in claimed_resources:
                    worker.harvest()
                    claimed_resources.add(worker.position)
                    acted.add(worker.id)
                    continue
            escaped = bool(
                worker.position == core_position
                and (
                    lethal_carrier_threat
                    or _should_vacate_core_for_spawn(turn, worker, memory)
                )
                and _escape_core_cell(
                    worker,
                    turn,
                    acted,
                    occupied,
                    reserved_destinations,
                    planned_from_core,
                    planned_into_core,
                    obstacles,
                    memory,
                    planned_moves,
                )
            )
            if escaped:
                continue
            if (
                lethal_carrier_threat
                and worker.cargo
                and stationary_core
                and pending_space > 0
            ):
                # If every exit is blocked or still illegal, DEPOSIT at least
                # saves cargo before combat removes the carrier.  Escape is
                # attempted first because preserving the Beacon is worth more.
                worker.deposit()
                acted.add(worker.id)
                deposited = min(int(worker.cargo or 0), pending_space)
                pending_deposit += deposited
                pending_space -= deposited
            continue

        if is_runner and _owned_beacon_carrier(turn, memory) is None:
            if not worker.cargo and worker.position in turn.resource_cells:
                if worker.position not in claimed_resources:
                    worker.harvest()
                    claimed_resources.add(worker.position)
                    acted.add(worker.id)
                    continue
            _queue_runner_action(
                turn,
                worker,
                memory,
                acted,
                occupied,
                reserved_destinations,
                planned_from_core,
                planned_into_core,
                planned_moves,
            )
            if worker.id in acted:
                continue
            # Unknown Beacon status at the public coordinate is intentionally a
            # WAIT, not a speculative pickup or harvest.
            if not _beacon_is_ground(turn) and worker.position == turn.beacon.position:
                continue

        threatened = worker_threatened
        if not threatened and not worker.cargo and worker.position in turn.resource_cells:
            if worker.position not in claimed_resources:
                worker.harvest()
                claimed_resources.add(worker.position)
                acted.add(worker.id)
                continue

        goal: tuple[int, int] | None = None
        retreat = False
        if threatened:
            goal = core_position
            retreat = True
        elif worker.cargo:
            goal = core_position
        else:
            worker_key = _uuid_key(worker.id)
            goal = resource_assignments.get(worker_key)
            if goal is None:
                goal = worker_scout_targets.get(worker_key, core_position)

        # A Core cell may hold only one Core plus one Unit.  If an economy
        # Worker is parked on the Core while another object is already there,
        # take one safe exploration step so the next combat spawn is not
        # permanently blocked by CELL_UNIT_LIMIT.
        if (
            goal == core_position
            and worker.position == core_position
            # A full Core cannot accept this cargo.  If a spawn is affordable,
            # moving the Worker out is the only way to free the Unit slot;
            # the cargo remains on the Worker and can be deposited later.
            and (
                not worker.cargo
                or pending_space <= 0
                or spawn_funded_without_this_deposit
            )
            and not threatened
            and sum(unit.position == core_position for unit in turn.units) >= 1
            and _core_ready_for_spawn(turn, memory)
            and (
                int(getattr(turn, "resources", 0)) + pending_deposit
                >= spawn_cost
            )
        ):
            if _escape_core_cell(
                worker,
                turn,
                acted,
                occupied,
                reserved_destinations,
                planned_from_core,
                planned_into_core,
                obstacles,
                memory,
                planned_moves,
            ):
                continue

        if goal is not None:
            _record_move(
                worker,
                goal,
                turn,
                acted,
                occupied,
                reserved_destinations,
                planned_from_core,
                planned_into_core,
                retreat=retreat,
                obstacles=obstacles,
                planned_moves=planned_moves,
                memory=memory,
            )
    return pending_deposit


def _queue_unit_heals(
    turn,
    acted: set[object],
    budget: int | None = None,
    memory: TacticMemory | None = None,
    reserved_destinations: Iterable[tuple[int, int]] = (),
) -> int:
    """Queue safe post-combat heals; v0.14 has no upkeep budget.

    ``budget`` is retained as a compatibility/estimation argument for callers
    of the old helper.  A zero budget still queues the highest-priority heal,
    because same-Tick deposits and Core captures resolve before healing.
    """

    core = turn.core
    if core is None or not _is_stationary_core(core):
        return max(0, budget or 0)
    memory = memory or TacticMemory()
    carrier = _owned_beacon_carrier(turn, memory)
    estimated = max(0, int(turn.resources if budget is None else budget))
    obstacles = _obstacles_for(turn, memory)
    preheal_carrier = (
        carrier
        if _should_preheal_beacon_carrier(
            turn,
            carrier,
            memory,
            acted,
            reserved_destinations,
        )
        else None
    )
    candidates = [
        unit
        for unit in turn.units
        if unit.id not in acted
        and unit.position == core.position
        and (
            (
                unit.hp < UNIT_MAX_HP.get(_enum_name(unit.unit_type), 0)
                and _visible_attack_count(
                    unit.position,
                    turn.visible_enemies,
                    obstacles,
                )
                < unit.hp
            )
            or (
                preheal_carrier is not None
                and _same_id(unit.id, preheal_carrier.id)
            )
        )
    ]
    candidates.sort(
        key=lambda unit: (
            0 if carrier is not None and _same_id(unit.id, carrier.id) else 1,
            0 if _enum_name(unit.unit_type) in {"RANGER", "VANGUARD"} else 1,
            _uuid_key(unit.id),
        )
    )

    # Unit heals settle by raw UUID, not by the order in which this plan is
    # serialized.  If the Beacon carrier is hurt and the observed balance
    # cannot pay every queued heal, queue only the carrier so a lower UUID
    # cannot consume the resources before the score-critical object.
    carrier_candidate = next(
        (
            unit
            for unit in candidates
            if carrier is not None and _same_id(unit.id, carrier.id)
        ),
        None,
    )
    if carrier_candidate is not None:
        total_missing = sum(
            UNIT_MAX_HP.get(_enum_name(unit.unit_type), 0) - unit.hp
            for unit in candidates
        )
        if estimated < total_missing:
            candidates = [carrier_candidate]
        else:
            # Keep the carrier first for readability; all candidates are funded
            # so UUID settlement order cannot starve it.
            candidates = [carrier_candidate] + [
                unit for unit in candidates if unit is not carrier_candidate
            ]
    elif candidates:
        total_missing = sum(
            UNIT_MAX_HP.get(_enum_name(unit.unit_type), 0) - unit.hp
            for unit in candidates
        )
        if estimated < total_missing:
            # Queue only the highest-value target when the observed balance is
            # partial.  Raw UUID order matters among queued heals; an omitted
            # lower-UUID Unit cannot consume resources.  The list is already
            # ordered carrier, combat type, then UUID.
            candidates = [candidates[0]]

    queued_any = False
    for unit in candidates:
        missing = UNIT_MAX_HP.get(_enum_name(unit.unit_type), 0) - unit.hp
        is_preheal = bool(
            preheal_carrier is not None
            and _same_id(unit.id, preheal_carrier.id)
        )
        if missing <= 0 and not is_preheal:
            continue
        if is_preheal and missing <= 0:
            unit.heal()
            acted.add(unit.id)
            queued_any = True
            continue
        if budget is not None and estimated > 0 and missing > estimated:
            if queued_any:
                continue
        elif budget is not None and estimated == 0 and queued_any:
            # Queue one critical heal at zero observed resources; dynamic
            # capture/deposit can fund it, while avoiding UUID-order waste.
            continue
        unit.heal()
        acted.add(unit.id)
        queued_any = True
        estimated = max(0, estimated - missing)
    return estimated


def _core_recovery_reserve(
    turn,
    memory: TacticMemory,
    core_action_selected: bool,
    acted: set[object] | None = None,
) -> int:
    """Reserve observed resources for the Core action that follows Unit heals."""

    core = getattr(turn, "core", None)
    if (
        core is None
        or core_action_selected
        or not _is_stationary_core(core)
    ):
        return 0
    acted = acted or set()
    carrier = _owned_beacon_carrier(turn, memory)
    if (
        carrier is not None
        and not _same_id(carrier.id, core.id)
        and carrier.id not in acted
        and carrier.position == core.position
        and carrier.hp
        < UNIT_MAX_HP.get(_enum_name(getattr(carrier, "unit_type", None)), 0)
    ):
        # A damaged Beacon Unit is the score-critical recovery target.  Let it
        # consume the observed balance before optional Core HP/shield work;
        # the Core action may still be queued speculatively and use later loot.
        return 0

    incoming = _anticipated_core_hp_damage(turn, memory)
    missing_hp = CORE_MAX_HP - core.hp
    if missing_hp > 0 or incoming > 0:
        # If the currently visible attack is lethal, recovery happens too late
        # to save this Core.  Do not reserve several resources that another
        # living Unit can use; a speculative Core HEAL/REPAIR may still queue.
        if _visible_attack_count(
            core.position,
            getattr(turn, "visible_enemies", ()),
            _obstacles_for(turn, memory),
        ) >= core.hp + core.shield:
            return 0
        return missing_hp + incoming
    if _anticipated_core_shield_damage(turn, memory) > 0:
        return 1
    if core.shield < _shield_cap(turn, memory):
        return 1
    return 0


def _anticipated_core_hp_damage(
    turn,
    memory: TacticMemory | None = None,
) -> int:
    """Estimate visible, nonfatal damage that can penetrate Core shield.

    Core HEAL resolves after combat and may legally be queued while HP is
    currently full.  Reserve only for a nonfatal visible attack: lethal damage
    removes the Core before recovery, while shield-only damage creates no HP
    for HEAL to restore.
    """

    core = getattr(turn, "core", None)
    if core is None or not _is_stationary_core(core):
        return 0
    obstacles = _obstacles_for(turn, memory)
    incoming_attacks = _visible_attack_count(
        core.position,
        getattr(turn, "visible_enemies", ()),
        obstacles,
    )
    penetrating = max(0, incoming_attacks - max(0, int(core.shield)))
    if penetrating >= core.hp:
        return 0
    return penetrating


def _anticipated_core_shield_damage(
    turn,
    memory: TacticMemory | None = None,
) -> int:
    """Estimate visible nonfatal attacks that will remove Core shield."""

    core = getattr(turn, "core", None)
    if core is None or not _is_stationary_core(core) or core.shield <= 0:
        return 0
    carrier = _owned_beacon_carrier(turn, memory)
    if (
        carrier is not None
        and not _same_id(carrier.id, core.id)
        and _shield_cap(turn, memory) == 5
        and core.shield >= 5
    ):
        # A lethal Unit carrier will drop the Beacon and clamp this Core to
        # the ordinary five-shield cap before the Core action; no repair can
        # preserve a shield point above that cap.
        return 0
    attacks = _visible_attack_count(
        core.position,
        getattr(turn, "visible_enemies", ()),
        _obstacles_for(turn, memory),
    )
    if attacks >= core.hp + core.shield:
        return 0
    return min(attacks, core.shield)


def _pending_resource_estimate(turn) -> int:
    """Estimate only the cargo that can actually fit in a same-Tick deposit.

    This helper is retained for callers that use the lower-level planner.  The
    main loop receives the exact amount returned by ``_queue_worker_actions``;
    this fallback deliberately models the server's strict Core capacity rather
    than summing cargo that would overflow.  Callers that already assigned a
    conflicting Unit action should prefer the exact return value from the
    Worker phase.
    """

    core = turn.core
    if core is None or not _is_stationary_core(core):
        return 0
    remaining = max(0, int(getattr(turn, "resource_space", 0)))
    accepted = 0
    for worker in sorted(turn.workers, key=lambda unit: _uuid_key(unit.id)):
        if remaining <= 0:
            break
        if worker.position != core.position:
            continue
        cargo = max(0, int(getattr(worker, "cargo", 0) or 0))
        amount = min(cargo, remaining)
        accepted += amount
        remaining -= amount
    return accepted


def _desired_spawn_type(
    turn,
    excluded_ids: Iterable[object] = (),
    population: int | None = None,
    memory: TacticMemory | None = None,
):
    excluded = {_uuid_key(identifier) for identifier in excluded_ids}
    living_units = [
        unit for unit in turn.units if _uuid_key(unit.id) not in excluded
    ]
    workers = sum(_enum_name(unit.unit_type) == "WORKER" for unit in living_units)
    rangers = sum(_enum_name(unit.unit_type) == "RANGER" for unit in living_units)
    vanguards = sum(_enum_name(unit.unit_type) == "VANGUARD" for unit in living_units)
    if population is None:
        population = int(getattr(turn.state, "population", len(living_units)))
    population = max(0, int(population))
    capacity = core_resource_capacity(population)

    profile = getattr(memory, "policy", StrategyProfile.default())
    defense = getattr(memory, "defense", DefenseAssessment.clear())
    if memory is not None and defense.level >= ThreatLevel.APPROACH:
        if vanguards < profile.defender_vanguard_target:
            priority = [UnitType.VANGUARD, UnitType.RANGER]
        elif rangers < profile.defender_ranger_target:
            priority = [UnitType.RANGER, UnitType.VANGUARD]
        elif rangers <= (
            profile.ranger_ratio / profile.defense_priority
        ) * max(1, vanguards):
            priority = [UnitType.RANGER, UnitType.VANGUARD]
        else:
            priority = [UnitType.VANGUARD, UnitType.RANGER]
        for unit_type in priority:
            if _unit_price(unit_type, population) <= capacity:
                return unit_type
        # Wartime pauses Workers even when every combat price has exceeded
        # storage capacity. Returning the cheaper combat type makes the Core
        # safely WAIT instead of expanding economy during an active approach.
        return min(
            priority,
            key=lambda unit_type: _unit_price(unit_type, population),
        )

    bootstrap_target = min(profile.worker_target, profile.bootstrap_worker_target)
    mature_worker_target = min(profile.worker_target, 12)
    if workers < bootstrap_target:
        priority = [UnitType.WORKER, UnitType.VANGUARD, UnitType.RANGER]
    elif vanguards < 1:
        priority = [UnitType.VANGUARD, UnitType.RANGER, UnitType.WORKER]
    elif rangers < 1:
        priority = [UnitType.RANGER, UnitType.VANGUARD, UnitType.WORKER]
    elif workers < mature_worker_target:
        priority = [UnitType.WORKER, UnitType.VANGUARD, UnitType.RANGER]
    elif vanguards < 3:
        priority = [UnitType.VANGUARD, UnitType.RANGER, UnitType.WORKER]
    elif rangers < 4:
        priority = [UnitType.RANGER, UnitType.VANGUARD, UnitType.WORKER]
    elif workers < profile.worker_target:
        priority = [UnitType.WORKER, UnitType.VANGUARD, UnitType.RANGER]
    elif rangers <= profile.ranger_ratio * max(1, vanguards):
        priority = [UnitType.RANGER, UnitType.VANGUARD, UnitType.WORKER]
    else:
        priority = [UnitType.VANGUARD, UnitType.RANGER, UnitType.WORKER]

    # Dynamic prices eventually exceed storage capacity for combat types.  Do
    # not get stuck requesting an impossible spawn forever; choose the first
    # composition candidate that can fit in the current Core capacity.
    for unit_type in priority:
        if _unit_price(unit_type, population) <= capacity:
            return unit_type
    return min(priority, key=lambda unit_type: _unit_price(unit_type, population))


def _unit_price(unit_type: UnitType, population: int) -> int:
    """Use the official v0.14 exact dynamic-price helper."""

    return int(unit_cost(unit_type, max(0, int(population))))


def _spawn_preview(
    turn,
    memory: TacticMemory | None = None,
    planned_from_core: Iterable[object] = (),
    planned_moves: dict[bytes, tuple[int, int]] | None = None,
):
    """Preview composition, price, and occupancy after visible Unit deaths.

    Production settles after movement and combat.  Evaluate a confirmed
    empty-destination move at its planned cell, then treat only a Unit whose
    visible attack count there reaches its HP as doomed.  This avoids spending
    the Core action on a lower-price spawn that cannot settle when a threatened
    Unit already moved to safety.
    """

    memory = memory or TacticMemory()
    core = getattr(turn, "core", None)
    population = int(getattr(turn.state, "population", len(turn.units)))
    obstacles = _obstacles_for(turn, memory)
    moved_from_core = tuple(planned_from_core)
    planned_moves = planned_moves or {}
    dead_ids: list[object] = []
    core_deaths = 0
    for unit in getattr(turn, "units", ()):
        projected_position = planned_moves.get(_uuid_key(unit.id), unit.position)
        if (
            _visible_attack_count(projected_position, turn.visible_enemies, obstacles)
            < unit.hp
        ):
            continue
        dead_ids.append(unit.id)
        if (
            core is not None
            and projected_position == core.position
            and not (
                unit.position == core.position
                and any(_same_id(unit.id, moved_id) for moved_id in moved_from_core)
            )
        ):
            core_deaths += 1
    settled_population = max(0, population - len(dead_ids))
    unit_type = _desired_spawn_type(
        turn,
        excluded_ids=dead_ids,
        population=settled_population,
        memory=memory,
    )
    return (
        unit_type,
        _unit_price(unit_type, population),
        _unit_price(unit_type, settled_population),
        tuple(dead_ids),
        core_deaths,
    )


def _queue_core_action(
    turn,
    budget: int | None = None,
    planned_from_core: set[object] | None = None,
    planned_into_core: set[object] | None = None,
    core_action_selected: bool = False,
    memory: TacticMemory | None = None,
    pending_resources: int | None = None,
    acted: set[object] | None = None,
    planned_moves: dict[bytes, tuple[int, int]] | None = None,
) -> bool:
    core = turn.core
    if core is None or core_action_selected or not _is_stationary_core(core):
        return core_action_selected
    memory = memory or TacticMemory()
    planned_from_core = planned_from_core or set()
    planned_into_core = planned_into_core or set()

    # Survival and Beacon shield cap come before production.  Queueing a heal
    # with zero observed resources is valid and can be funded by same-Tick loot.
    if core.hp < CORE_MAX_HP or _anticipated_core_hp_damage(turn, memory) > 0:
        core.heal()
        return True
    if (
        core.shield < _shield_cap(turn, memory)
        or _anticipated_core_shield_damage(turn, memory) > 0
    ):
        core.repair_shield()
        return True

    observed_resources = int(getattr(turn, "resources", 0))
    if budget is None:
        # Direct helper callers have not run the Worker phase yet, so include
        # only a capacity-aware estimate.  The normal loop passes the exact
        # post-heal balance as ``budget`` and therefore needs no re-estimate.
        observed_resources += (
            _pending_resource_estimate(turn)
            if pending_resources is None
            else max(0, int(pending_resources))
        )
    else:
        observed_resources = max(0, int(budget))
    # Production settles after combat.  A currently unaffordable plan may
    # become valid when a visibly doomed Unit dies and lowers both the dynamic
    # price and the Core-cell occupancy.  Queueing it is harmless if the
    # prediction is wrong: the server reports a private, no-cost spawn failure.
    (
        unit_type,
        cost,
        settled_cost,
        expected_dead_ids,
        expected_core_deaths,
    ) = _spawn_preview(turn, memory, planned_from_core, planned_moves)
    expected_deaths = len(expected_dead_ids)
    if observed_resources < cost + CORE_RESERVE:
        if expected_deaths == 0 or observed_resources < settled_cost + CORE_RESERVE:
            return False

    # Spawn after same-Tick movement.  A unit vacating the Core frees a slot.
    current_core_units = sum(unit.position == core.position for unit in turn.units)
    planned_from_core = planned_from_core or set()
    planned_into_core = planned_into_core or set()
    post_movement_occupancy = (
        1
        + current_core_units
        - len(planned_from_core)
        + len(planned_into_core)
        - expected_core_deaths
    )
    if post_movement_occupancy >= 2:
        return False

    core.spawn(unit_type)
    return True


def choose_actions(turn, memory: TacticMemory | None = None) -> None:
    """Queue one complete, deterministic plan for the current Turn."""

    memory = memory or TacticMemory()
    memory.observe(turn)
    memory.worker_evacuations = 0
    if turn.core is None:
        _refresh_defense_state(turn, memory)
        memory.economy_diagnostics = {
            "visible_resource_count": len(
                set(getattr(turn, "resource_cells", ()) or ())
            ),
            "worker_modes": {},
            "idle_worker_ticks": 0,
            "route_stalls": 0,
            "oscillation_ticks": 0,
            "runner_progress_ticks": 0,
        }
        return None

    _refresh_defense_state(turn, memory)

    refresh_economy_memory(
        memory.economy,
        tick=int(getattr(turn, "tick", 0)),
        workers=tuple(getattr(turn, "workers", ())),
        visible_resources=getattr(turn, "resource_cells", ()),
        friendly_positions=(
            turn.core.position,
            *(unit.position for unit in getattr(turn, "units", ())),
        ),
        settings=EconomySettings(
            resource_memory_ttl=int(
                getattr(memory.policy, "resource_memory_ttl", 64)
            ),
            resource_stall_ticks=int(
                getattr(memory.policy, "resource_stall_ticks", 6)
            ),
            scout_ring_step=int(getattr(memory.policy, "scout_ring_step", 10)),
        ),
    )

    acted: set[object] = set()
    planned_from_core: set[object] = set()
    planned_into_core: set[object] = set()
    planned_moves: dict[bytes, tuple[int, int]] = {}
    reserved_destinations: set[tuple[int, int]] = set()
    occupied = _occupied(turn)

    # Beacon phase is resolved before Worker actions.  Queue pickup first so a
    # Worker standing on Beacon+RESOURCE never spends the Tick harvesting.
    core_action_selected = _queue_beacon_pickup(turn, memory, acted, False)
    own_carrier = _owned_beacon_carrier(turn, memory)
    # The submitted pickup is not reflected in this Turn's authoritative view
    # yet.  Treat the selected controlled object as the prospective carrier so
    # we do not assign a second runner or spend its action on economy.
    runner = None if own_carrier is not None else _choose_runner(turn, memory)

    # A Worker runner must physically travel toward the public coordinate; a
    # combat runner is handled by the combat movement code below.
    if runner is not None and _enum_name(getattr(runner, "unit_type", None)) == "WORKER":
        # Economy actions outrank travel: a runner on a visible node harvests
        # now, and a loaded legacy runner is released by _choose_runner.
        if not runner.cargo and runner.position not in turn.resource_cells:
            _queue_runner_action(
                turn,
                runner,
                memory,
                acted,
                occupied,
                reserved_destinations,
                planned_from_core,
                planned_into_core,
                planned_moves,
            )

    _queue_ranger_actions(
        turn,
        memory,
        acted,
        occupied,
        reserved_destinations,
        planned_from_core,
        planned_into_core,
        runner,
        planned_moves,
    )
    _queue_vanguard_actions(
        turn,
        memory,
        acted,
        occupied,
        reserved_destinations,
        planned_from_core,
        planned_into_core,
        runner,
        planned_moves,
    )

    pending_deposit = _queue_worker_actions(
        turn,
        acted,
        planned_from_core,
        planned_into_core,
        reserved_destinations,
        memory,
        runner,
        own_carrier,
        planned_moves,
    )

    # Unit heals resolve before the Core action.  Reserve enough observed
    # balance for score-critical Core HP/shield recovery, then let eligible
    # Units consume only the surplus.  Captured loot can still fund the queued
    # Core recovery when the observed balance is zero.
    available = max(0, int(turn.resources)) + pending_deposit
    core_reserve = _core_recovery_reserve(
        turn,
        memory,
        core_action_selected,
        acted,
    )
    unit_budget = max(0, available - core_reserve)
    preheal_pending = _should_preheal_beacon_carrier(
        turn,
        own_carrier,
        memory,
        acted,
        reserved_destinations,
    )
    if unit_budget > 0 or core_reserve == 0 or preheal_pending:
        unit_remaining = _queue_unit_heals(
            turn,
            acted,
            unit_budget,
            memory,
            reserved_destinations,
        )
        unit_spent = max(0, unit_budget - unit_remaining)
    else:
        unit_spent = 0
    remaining = max(0, available - unit_spent)
    core_action_selected = _queue_core_action(
        turn,
        remaining,
        planned_from_core,
        planned_into_core,
        core_action_selected,
        memory,
        acted=acted,
        planned_moves=planned_moves,
    )
    _update_economy_diagnostics(
        turn,
        memory,
        acted=acted,
        runner=runner,
        carrier=own_carrier,
    )
    return None


def load_api_key(env_path=None) -> str:
    # Keep dotenv optional and lazy so importing the deterministic planner has
    # no configuration side effects. Explicit process variables win over file
    # values, and the loader fails open on a missing local file.
    from adaptive_strategy import load_dotenv

    load_dotenv(env_path)
    return os.environ.get("ARENA_HERO_API_KEY") or getpass("Arena Hero API key: ")


def play(api_key: str | None = None, adaptive=None) -> None:
    coordinator = adaptive
    try:
        key = api_key or load_api_key()
        if coordinator is None:
            # Import lazily: importing the tactic never initializes adaptive
            # threads or reads credentials.
            from adaptive_strategy import AdaptiveCoordinator

            coordinator = AdaptiveCoordinator.from_env()
        memory = TacticMemory()
        with ArenaHeroClient(api_key=key) as game:
            for turn in game.turns():
                # Snapshot one validated profile at the Turn boundary.  Any
                # LLM work is queued only after this Turn is submitted.
                try:
                    profile = coordinator.current_profile()
                    profile.validate()
                except Exception:
                    # Adaptive state is optional; a corrupt or unavailable
                    # profile must never prevent the deterministic tactic
                    # from submitting its next legal plan.
                    profile = StrategyProfile.default()
                memory.policy = profile
                choose_actions(turn, memory)
                accepted = turn.submit()
                print(f"tick={accepted.tick} accepted={accepted.accepted}")
                try:
                    # Observation is deliberately after submit.  Any local
                    # telemetry or background LLM failure is fail-open.
                    diagnostic_observer = getattr(
                        coordinator,
                        "observe_snapshot_with_diagnostics",
                        None,
                    )
                    if callable(diagnostic_observer):
                        diagnostic_observer(
                            turn,
                            accepted,
                            profile,
                            memory.economy_diagnostics,
                        )
                    elif callable(
                        snapshot_observer := getattr(
                            coordinator,
                            "observe_snapshot",
                            None,
                        )
                    ):
                        snapshot_observer(turn, accepted, profile)
                    else:
                        # Preserve compatibility with injected coordinators
                        # implementing the original two-argument interface.
                        coordinator.observe(turn, accepted)
                except Exception:
                    continue
    except KeyboardInterrupt:
        return
    except Exception as exc:
        raise SystemExit(f"Arena Hero stopped: {type(exc).__name__}") from None
    finally:
        if coordinator is not None:
            try:
                coordinator.close()
            except Exception:
                pass


if __name__ == "__main__":
    play()
