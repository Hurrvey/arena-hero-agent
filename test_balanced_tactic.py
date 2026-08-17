from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from arena_hero import Direction, UnitType, unit_cost
from arena_hero.models import UnitView

from balanced_tactic import (
    TacticMemory,
    _beacon_contest_visible,
    _core_recovery_reserve,
    _queue_beacon_pickup,
    _queue_unit_heals,
    _kind,
    _choose_runner,
    _desired_spawn_type,
    choose_actions,
    load_api_key,
    play,
)
from defense_strategy import ThreatLevel
from strategy_policy import StrategyProfile


class FakeController:
    def __init__(
        self,
        *,
        object_id: UUID,
        position: tuple[int, int] = (0, 0),
        hp: int = 1,
        shield: int = 5,
        unit_type: UnitType | None = None,
        cargo: int = 0,
        state: str = "NORMAL",
    ) -> None:
        self.id = object_id
        self.position = position
        self.hp = hp
        self.shield = shield
        self.unit_type = unit_type
        self.cargo = cargo
        self.view = SimpleNamespace(
            id=object_id,
            position=position,
            hp=hp,
            shield=shield,
            unit_type=unit_type,
            state=state,
        )
        self.actions: list[tuple[object, ...]] = []

    def _record(self, name: str, *args: object) -> None:
        self.actions.append((name, *args))

    def move(self, direction: Direction) -> None:
        self._record("MOVE", direction)

    def harvest(self) -> None:
        self._record("HARVEST")

    def deposit(self) -> None:
        self._record("DEPOSIT")

    def heal(self) -> None:
        self._record("HEAL")

    def sweep(self, direction: Direction) -> None:
        self._record("SWEEP", direction)

    def shoot_cell(self, position: tuple[int, int]) -> None:
        self._record("SHOOT", position)

    def shoot(self, target, *, expected_cell: tuple[int, int] | None = None) -> None:
        target_id = getattr(target, "id", target)
        position = expected_cell or getattr(target, "position", None)
        self._record("SHOOT", position, target_id)

    def pickup_beacon(self) -> None:
        self._record("PICKUP_BEACON")

    def repair_shield(self) -> None:
        self._record("REPAIR_SHIELD")

    def spawn(self, unit_type: UnitType) -> None:
        self._record("SPAWN", unit_type)


def pad_with_workers(
    units: tuple[FakeController, ...], population: int
) -> tuple[FakeController, ...]:
    """Build a complete legal population for high-population Turn fixtures.

    ``PlayerState.population`` counts every living friendly Unit.  Keep the
    extra Workers far from the Core so they do not occupy its spawn cell while
    preserving the composition under test.
    """

    if population < len(units):
        raise ValueError("population cannot be below the supplied Unit count")
    fillers = tuple(
        FakeController(
            object_id=UUID(int=1000 + index),
            position=(1000 + index, 1000),
            hp=2,
            unit_type=UnitType.WORKER,
        )
        for index in range(population - len(units))
    )
    return (*units, *fillers)


def make_turn(
    *,
    core: FakeController | None,
    units: tuple[FakeController, ...] = (),
    resources: int = 0,
    resource_cells: set[tuple[int, int]] | tuple[tuple[int, int], ...] = (),
    obstacle_cells: set[tuple[int, int]] | tuple[tuple[int, int], ...] = (),
    enemies: tuple[SimpleNamespace, ...] = (),
    beacon: SimpleNamespace | None = None,
    events: tuple[SimpleNamespace, ...] = (),
    population: int | None = None,
) -> SimpleNamespace:
    workers = tuple(unit for unit in units if unit.unit_type is UnitType.WORKER)
    vanguards = tuple(unit for unit in units if unit.unit_type is UnitType.VANGUARD)
    rangers = tuple(unit for unit in units if unit.unit_type is UnitType.RANGER)
    effective_population = len(units) if population is None else population
    capacity = max(10, effective_population * 5)
    state = SimpleNamespace(
        population=len(units) if population is None else population,
        status="ACTIVE" if core is not None else "RESPAWNING",
    )
    return SimpleNamespace(
        tick=1,
        state=state,
        resources=resources,
        resource_space=max(0, capacity - resources),
        core=core,
        units=tuple(units),
        workers=workers,
        vanguards=vanguards,
        rangers=rangers,
        visible_enemies=tuple(enemies),
        resource_cells=frozenset(resource_cells),
        obstacle_cells=frozenset(obstacle_cells),
        beacon=beacon
        or SimpleNamespace(position=(100, 100), status=None, carrier_id=None),
        events=events,
    )


def explore_square(memory: TacticMemory, radius: int = 4) -> None:
    memory.exploration.observe(
        visible_cells=frozenset(
            (x, y)
            for x in range(-radius, radius + 1)
            for y in range(-radius, radius + 1)
        ),
        visible_obstacles=frozenset(),
        tick=1,
    )


def test_respawning_turn_queues_no_invented_actions() -> None:
    turn = make_turn(core=None)

    result = choose_actions(turn)

    assert result is None
    assert turn.core is None


def test_ranger_chooses_a_legal_visible_core_cell() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(5, 5),
        hp=5,
    )
    ranger = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 3),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    enemy_core = SimpleNamespace(
        kind="CORE",
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(0, 0),
        hp=5,
    )
    turn = make_turn(core=core, units=(ranger,), enemies=(enemy_core,))

    choose_actions(turn)

    assert ranger.actions == [("SHOOT", (0, 0), enemy_core.id)]


def test_ranger_does_not_shoot_through_a_visible_obstacle() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
    )
    ranger = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(0, 3),
        hp=2,
    )
    turn = make_turn(
        core=core,
        units=(ranger,),
        enemies=(enemy,),
        obstacle_cells={(0, 1)},
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn)

    assert all(action[0] != "SHOOT" for action in ranger.actions)


def test_defense_memory_is_recomputed_from_each_visible_turn() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=10,
    )
    defender = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(6, 0),
        hp=4,
        unit_type=UnitType.VANGUARD,
    )
    watcher = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.VANGUARD,
        id=UUID("00000000-0000-0000-0000-000000000030"),
        position=(4, 0),
        hp=4,
        shield=0,
    )
    beacon = SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id)
    memory = TacticMemory()

    choose_actions(
        make_turn(core=core, units=(defender,), enemies=(watcher,), beacon=beacon),
        memory,
    )

    assert memory.defense.level is ThreatLevel.WATCH
    assert memory.defenders.vanguard_ids == frozenset({defender.id})

    choose_actions(
        make_turn(core=core, units=(defender,), enemies=(), beacon=beacon),
        memory,
    )

    assert memory.defense.level is ThreatLevel.CLEAR
    assert memory.defense.watch_ids == frozenset()


def test_same_tick_beacon_carrier_is_removed_from_defender_roster() -> None:
    core = FakeController(
        object_id=UUID(int=100), position=(0, 0), hp=5, shield=5
    )
    carrier = FakeController(
        object_id=UUID(int=1),
        position=(1, 0),
        hp=4,
        unit_type=UnitType.VANGUARD,
    )
    defender = FakeController(
        object_id=UUID(int=2),
        position=(2, 0),
        hp=4,
        unit_type=UnitType.VANGUARD,
    )
    memory = TacticMemory()
    turn = make_turn(
        core=core,
        units=(carrier, defender),
        beacon=SimpleNamespace(position=(1, 0), status="GROUND", carrier_id=None),
    )

    choose_actions(turn, memory)

    assert carrier.actions == [("PICKUP_BEACON",)]
    assert memory.defenders.vanguard_ids == frozenset({defender.id})


def test_ranger_shoots_a_lethal_core_attacker_before_enemy_core() -> None:
    core = FakeController(
        object_id=UUID(int=100), position=(0, 0), hp=1, shield=0
    )
    ranger = FakeController(
        object_id=UUID(int=1),
        position=(3, 0),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    attacker = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.VANGUARD,
        id=UUID(int=200),
        position=(1, 0),
        hp=4,
        shield=0,
    )
    enemy_core = SimpleNamespace(
        kind="CORE", id=UUID(int=300), position=(3, 3), hp=5, shield=5
    )
    turn = make_turn(
        core=core,
        units=(ranger,),
        enemies=(attacker, enemy_core),
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn)

    assert ranger.actions == [("SHOOT", attacker.position)]


def test_vanguard_sweeps_a_lethal_core_attacker_before_enemy_core() -> None:
    core = FakeController(
        object_id=UUID(int=100), position=(0, 0), hp=1, shield=0
    )
    vanguard = FakeController(
        object_id=UUID(int=1),
        position=(1, 1),
        hp=4,
        unit_type=UnitType.VANGUARD,
    )
    attacker = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.VANGUARD,
        id=UUID(int=200),
        position=(1, 0),
        hp=4,
        shield=0,
    )
    enemy_core = SimpleNamespace(
        kind="CORE", id=UUID(int=300), position=(2, 1), hp=5, shield=5
    )
    turn = make_turn(
        core=core,
        units=(vanguard,),
        enemies=(attacker, enemy_core),
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn)

    assert vanguard.actions == [("SWEEP", Direction.UP)]


def test_only_selected_clear_state_defender_is_recalled_to_core() -> None:
    core = FakeController(
        object_id=UUID(int=100), position=(0, 0), hp=5, shield=10
    )
    near = FakeController(
        object_id=UUID(int=1),
        position=(4, 0),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    far = FakeController(
        object_id=UUID(int=2),
        position=(6, 0),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    memory = TacticMemory(
        policy=StrategyProfile.default().with_updates(defender_ranger_target=1)
    )
    turn = make_turn(
        core=core,
        units=(near, far),
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn, memory)

    assert near.actions == [("MOVE", Direction.LEFT)]
    assert far.actions == []


def test_in_ring_defender_holds_position_instead_of_chasing_enemy_core() -> None:
    core = FakeController(
        object_id=UUID(int=100), position=(0, 0), hp=5, shield=10
    )
    ranger = FakeController(
        object_id=UUID(int=1),
        position=(3, 0),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    enemy_core = SimpleNamespace(
        kind="CORE", id=UUID(int=200), position=(8, 0), hp=5, shield=5
    )
    memory = TacticMemory(
        policy=StrategyProfile.default().with_updates(defender_ranger_target=1)
    )
    turn = make_turn(
        core=core,
        units=(ranger,),
        enemies=(enemy_core,),
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn, memory)

    assert ranger.actions == []


def test_too_close_defender_moves_out_to_its_defense_ring() -> None:
    core = FakeController(
        object_id=UUID(int=100), position=(0, 0), hp=5, shield=10
    )
    ranger = FakeController(
        object_id=UUID(int=1),
        position=(1, 0),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    memory = TacticMemory(
        policy=StrategyProfile.default().with_updates(defender_ranger_target=1)
    )
    turn = make_turn(
        core=core,
        units=(ranger,),
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn, memory)

    assert ranger.actions == [("MOVE", Direction.RIGHT)]


def test_approaching_enemy_recalls_noncarrier_combat_units_to_core() -> None:
    core = FakeController(
        object_id=UUID(int=100), position=(0, 0), hp=5, shield=10
    )
    carrier = FakeController(
        object_id=UUID(int=3),
        position=(8, 8),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    ranger = FakeController(
        object_id=UUID(int=1),
        position=(6, 0),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    vanguard = FakeController(
        object_id=UUID(int=2),
        position=(0, 6),
        hp=4,
        unit_type=UnitType.VANGUARD,
    )
    approacher = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.VANGUARD,
        id=UUID(int=200),
        position=(2, 0),
        hp=4,
        shield=0,
    )
    turn = make_turn(
        core=core,
        units=(ranger, vanguard, carrier),
        enemies=(approacher,),
        beacon=SimpleNamespace(
            position=carrier.position, status="CARRIED", carrier_id=carrier.id
        ),
    )

    choose_actions(turn)

    assert ranger.actions == [("MOVE", Direction.LEFT)]
    assert vanguard.actions == [("MOVE", Direction.UP)]


def test_approach_recall_outranks_unrelated_enemy_core_shot() -> None:
    core = FakeController(
        object_id=UUID(int=100), position=(0, 0), hp=5, shield=10
    )
    ranger = FakeController(
        object_id=UUID(int=1),
        position=(6, 0),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    approacher = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.VANGUARD,
        id=UUID(int=200),
        position=(2, 0),
        hp=4,
        shield=0,
    )
    enemy_core = SimpleNamespace(
        kind="CORE", id=UUID(int=201), position=(6, 3), hp=5, shield=5
    )
    turn = make_turn(
        core=core,
        units=(ranger,),
        enemies=(approacher, enemy_core),
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn)

    assert ranger.actions == [("MOVE", Direction.LEFT)]


def test_threatened_near_core_worker_evacuates_to_safe_flank() -> None:
    core = FakeController(
        object_id=UUID(int=100), position=(0, 0), hp=5, shield=5
    )
    worker = FakeController(
        object_id=UUID(int=1),
        position=(0, 1),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    attacker = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.RANGER,
        id=UUID(int=200),
        position=(0, 3),
        hp=2,
        shield=0,
    )
    turn = make_turn(
        core=core,
        units=(worker,),
        enemies=(attacker,),
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn)

    assert worker.actions == [("MOVE", Direction.RIGHT)]


def test_blocked_near_core_worker_waits_instead_of_entering_core_fire() -> None:
    core = FakeController(
        object_id=UUID(int=100), position=(0, 0), hp=5, shield=5
    )
    worker = FakeController(
        object_id=UUID(int=1),
        position=(0, 1),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    attacker = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.RANGER,
        id=UUID(int=200),
        position=(0, 3),
        hp=2,
        shield=0,
    )
    turn = make_turn(
        core=core,
        units=(worker,),
        enemies=(attacker,),
        obstacle_cells={(-1, 1), (1, 1)},
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn)

    assert worker.actions == []


def test_blocked_cargo_worker_at_core_deposits_when_evacuation_is_impossible() -> None:
    core = FakeController(
        object_id=UUID(int=100), position=(0, 0), hp=5, shield=10
    )
    worker = FakeController(
        object_id=UUID(int=1),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
        cargo=1,
    )
    attacker = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.RANGER,
        id=UUID(int=200),
        position=(0, 3),
        hp=2,
        shield=0,
    )
    turn = make_turn(
        core=core,
        units=(worker,),
        enemies=(attacker,),
        obstacle_cells={(-1, 0), (1, 0), (0, -1)},
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn)

    assert worker.actions == [("DEPOSIT",)]


def test_approach_state_pauses_workers_and_spawns_missing_defender() -> None:
    core = FakeController(
        object_id=UUID(int=100), position=(0, 0), hp=5, shield=10
    )
    workers = tuple(
        FakeController(
            object_id=UUID(int=index + 1),
            position=(10 + index, 10),
            hp=2,
            unit_type=UnitType.WORKER,
        )
        for index in range(4)
    )
    approacher = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.VANGUARD,
        id=UUID(int=200),
        position=(2, 0),
        hp=4,
        shield=0,
    )
    turn = make_turn(
        core=core,
        units=workers,
        resources=10,
        enemies=(approacher,),
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn)

    assert core.actions == [("SPAWN", UnitType.VANGUARD)]


def test_planner_exports_aggregate_defense_diagnostics() -> None:
    core = FakeController(
        object_id=UUID(int=100), position=(0, 0), hp=1, shield=1
    )
    vanguard = FakeController(
        object_id=UUID(int=1),
        position=(1, 1),
        hp=4,
        unit_type=UnitType.VANGUARD,
    )
    worker = FakeController(
        object_id=UUID(int=2),
        position=(0, 1),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    enemies = (
        SimpleNamespace(
            kind="UNIT",
            unit_type=UnitType.RANGER,
            id=UUID(int=200),
            position=(0, 3),
            hp=2,
            shield=0,
        ),
        SimpleNamespace(
            kind="UNIT",
            unit_type=UnitType.VANGUARD,
            id=UUID(int=201),
            position=(1, 0),
            hp=4,
            shield=0,
        ),
    )
    memory = TacticMemory()
    turn = make_turn(
        core=core,
        units=(vanguard, worker),
        enemies=enemies,
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn, memory)

    assert memory.economy_diagnostics["defense_level"] == "LETHAL"
    assert memory.economy_diagnostics["core_threat_ticks"] == 1
    assert memory.economy_diagnostics["projected_lethal_ticks"] == 1
    assert memory.economy_diagnostics["incoming_core_damage"] == 2
    assert memory.economy_diagnostics["defender_coverage"] == 1
    assert memory.economy_diagnostics["worker_evacuations"] == 1


def test_out_of_ring_defender_is_not_counted_as_coverage() -> None:
    core = FakeController(
        object_id=UUID(int=100), position=(0, 0), hp=5, shield=10
    )
    ranger = FakeController(
        object_id=UUID(int=1),
        position=(6, 0),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    memory = TacticMemory(
        policy=StrategyProfile.default().with_updates(defender_ranger_target=1)
    )
    turn = make_turn(
        core=core,
        units=(ranger,),
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn, memory)

    assert memory.defenders.ranger_ids == frozenset({ranger.id})
    assert memory.economy_diagnostics["defender_coverage"] == 0


def test_too_close_ranger_is_not_counted_as_defender_coverage() -> None:
    core = FakeController(
        object_id=UUID(int=100), position=(0, 0), hp=5, shield=10
    )
    ranger = FakeController(
        object_id=UUID(int=1),
        position=(1, 0),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    memory = TacticMemory(
        policy=StrategyProfile.default().with_updates(defender_ranger_target=1)
    )
    turn = make_turn(
        core=core,
        units=(ranger,),
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn, memory)

    assert memory.economy_diagnostics["defender_coverage"] == 0


def test_final_core_migration_assesses_threat_at_combat_destination() -> None:
    core = FakeController(
        object_id=UUID(int=100),
        position=(0, 0),
        hp=5,
        shield=5,
        state="MOVING",
    )
    core.view.kind = "CORE"
    core.view.destination = (1, 0)
    core.view.move_progress = 3
    core.view.move_required_ticks = 4
    enemy = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.VANGUARD,
        id=UUID(int=200),
        position=(2, 0),
        hp=4,
        shield=0,
    )
    memory = TacticMemory()
    turn = make_turn(
        core=core,
        enemies=(enemy,),
        beacon=SimpleNamespace(position=(100, 100), status=None, carrier_id=None),
    )

    choose_actions(turn, memory)

    assert memory.defense.level is ThreatLevel.ATTACK
    assert memory.defense.attacker_ids == frozenset({enemy.id})


def test_vanguard_sweeps_the_adjacent_cell_with_most_hostiles() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
    )
    vanguard = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=4,
        unit_type=UnitType.VANGUARD,
    )
    enemies = (
        SimpleNamespace(
            kind="UNIT",
            id=UUID("00000000-0000-0000-0000-000000000020"),
            position=(1, 0),
            hp=2,
        ),
        SimpleNamespace(
            kind="UNIT",
            id=UUID("00000000-0000-0000-0000-000000000021"),
            position=(1, 0),
            hp=1,
        ),
    )
    turn = make_turn(core=core, units=(vanguard,), enemies=enemies)

    choose_actions(turn)

    assert vanguard.actions == [("SWEEP", Direction.RIGHT)]


def test_worker_harvests_visible_resource_on_current_cell() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(1, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    # Keep the first Worker as the Beacon runner; the second Worker exercises
    # the ordinary economy loop without competing for the role.
    runner = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000002"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    turn = make_turn(
        core=core,
        units=(worker, runner),
        resources=5,
        resource_cells={(1, 0)},
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn)

    assert worker.actions == [("HARVEST",)]


def test_worker_deposits_cargo_only_at_stationary_core_with_space() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
        cargo=2,
    )
    turn = make_turn(
        core=core,
        units=(worker,),
        resources=5,
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn)

    assert worker.actions == [("DEPOSIT",)]


def test_worker_moves_around_visible_obstacle_toward_resource() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    turn = make_turn(
        core=core,
        units=(worker,),
        resources=5,
        resource_cells={(2, 0)},
        obstacle_cells={(1, 0)},
        beacon=SimpleNamespace(position=(2, 0), status=None, carrier_id=None),
    )

    choose_actions(turn)

    assert worker.actions == [("MOVE", Direction.UP)]


def test_worker_prefers_nearest_visible_resource_over_coordinate_order() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 1),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    runner = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000002"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    turn = make_turn(
        core=core,
        units=(worker, runner),
        resources=5,
        resource_cells={(-5, 1), (1, 1)},
    )

    choose_actions(turn)

    assert worker.actions == [("MOVE", Direction.RIGHT)]


def test_threatened_worker_retreats_before_harvesting() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(1, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(2, 0),
        hp=2,
    )
    turn = make_turn(
        core=core,
        units=(worker,),
        resources=5,
        resource_cells={(1, 0)},
        enemies=(enemy,),
    )

    choose_actions(turn)

    assert worker.actions == [("MOVE", Direction.LEFT)]


def test_same_cell_workers_use_raw_uuid_order_for_harvest_contention() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(5, 5),
        hp=5,
    )
    lower_id_worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(1, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    higher_id_worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000002"),
        position=(1, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    runner = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000003"),
        position=(9, 10),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    turn = make_turn(
        core=core,
        units=(higher_id_worker, lower_id_worker, runner),
        resources=5,
        resource_cells={(1, 0)},
        beacon=SimpleNamespace(position=(10, 10), status=None, carrier_id=None),
    )

    choose_actions(turn)

    assert lower_id_worker.actions == [("HARVEST",)]
    assert higher_id_worker.actions != [("HARVEST",)]


def test_damaged_unit_at_stationary_core_heals_before_idle_movement() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
    )
    ranger = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=1,
        unit_type=UnitType.RANGER,
    )
    turn = make_turn(
        core=core,
        units=(ranger,),
        resources=2,
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn)

    assert ranger.actions == [("HEAL",)]


def test_core_repairs_shield_before_spawning_when_hp_is_full() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=4,
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(1, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    turn = make_turn(core=core, units=(worker,), resources=6)

    choose_actions(turn)

    assert core.actions == [("REPAIR_SHIELD",)]


def test_core_spawns_worker_only_with_reserve_and_cell_room() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
    )
    turn = make_turn(core=core, units=(), resources=10)

    choose_actions(turn)

    assert core.actions == [("SPAWN", UnitType.WORKER)]


def test_ground_beacon_is_picked_up_only_when_already_visible_and_idle() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(1, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    beacon = SimpleNamespace(position=(1, 0), status="GROUND", carrier_id=None)
    turn = make_turn(core=core, units=(worker,), resources=5, beacon=beacon)

    choose_actions(turn)

    assert worker.actions == [("PICKUP_BEACON",)]


def test_priority_does_not_replace_worker_deposit_with_retreat() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
        cargo=1,
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(1, 0),
        hp=2,
    )
    turn = make_turn(core=core, units=(worker,), resources=5, enemies=(enemy,))

    choose_actions(turn)

    assert worker.actions == [("DEPOSIT",)]


def test_load_api_key_prefers_environment_without_printing(monkeypatch) -> None:
    monkeypatch.setenv("ARENA_HERO_API_KEY", "secret-test-key")

    assert load_api_key() == "secret-test-key"


def test_load_api_key_prompts_when_environment_is_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ARENA_HERO_API_KEY", raising=False)
    monkeypatch.setattr("adaptive_strategy._DEFAULT_DOTENV_PATH", tmp_path / "missing.env")
    monkeypatch.setattr("balanced_tactic.getpass", lambda prompt: "prompted-key")

    assert load_api_key() == "prompted-key"


def test_load_api_key_reads_an_explicit_dotenv_file(tmp_path, monkeypatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("ARENA_HERO_API_KEY=dotenv-key\n", encoding="utf-8")
    monkeypatch.delenv("ARENA_HERO_API_KEY", raising=False)
    monkeypatch.setattr(
        "balanced_tactic.getpass",
        lambda prompt: pytest.fail(f"unexpected prompt: {prompt}"),
    )

    assert load_api_key(dotenv) == "dotenv-key"


def test_load_api_key_uses_the_project_dotenv_by_default(tmp_path, monkeypatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("ARENA_HERO_API_KEY=default-dotenv-key\n", encoding="utf-8")
    monkeypatch.delenv("ARENA_HERO_API_KEY", raising=False)
    monkeypatch.setattr("adaptive_strategy._DEFAULT_DOTENV_PATH", dotenv)
    monkeypatch.setattr(
        "balanced_tactic.getpass",
        lambda prompt: pytest.fail(f"unexpected prompt: {prompt}"),
    )

    assert load_api_key() == "default-dotenv-key"


def test_play_submits_one_complete_plan_for_each_turn(tmp_path, monkeypatch, capsys) -> None:
    submissions: list[int] = []

    class FakeTurn:
        tick = 7
        core = None

        def submit(self):
            submissions.append(self.tick)
            return SimpleNamespace(tick=self.tick, accepted=True)

    class FakeGame:
        def __init__(self, *, api_key):
            assert api_key == "provided-key"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def turns(self):
            yield FakeTurn()

    monkeypatch.setattr("balanced_tactic.ArenaHeroClient", FakeGame)
    # Keep this deterministic loop test independent of a user's real local
    # .env, which may opt into the background LLM coordinator.
    monkeypatch.setattr("adaptive_strategy._DEFAULT_DOTENV_PATH", tmp_path / "missing.env")

    play("provided-key")

    assert submissions == [7]
    assert capsys.readouterr().out == "tick=7 accepted=True\n"


def test_profile_worker_target_changes_spawn_preference() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0), hp=5, shield=5,
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(3, 3), hp=2, unit_type=UnitType.WORKER,
    )
    turn = make_turn(core=core, units=(worker,), resources=10, population=2)
    memory = TacticMemory(policy=StrategyProfile.default().with_updates(worker_target=3))

    choose_actions(turn, memory)

    assert core.actions == [("SPAWN", UnitType.WORKER)]


def test_profile_carrier_margin_requires_a_safer_retreat() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0), hp=5, shield=5,
    )
    carrier = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0), hp=2, unit_type=UnitType.WORKER,
    )
    enemy = SimpleNamespace(kind="UNIT", unit_type=UnitType.RANGER,
                            id=UUID("00000000-0000-0000-0000-000000000020"),
                            position=(0, 3), hp=2)
    turn = make_turn(
        core=core, units=(carrier,), enemies=(enemy,),
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=carrier.id),
    )
    memory = TacticMemory(policy=StrategyProfile.default().with_updates(carrier_safety_margin=1))

    choose_actions(turn, memory)

    assert carrier.actions or core.actions


def test_profile_economy_priority_breaks_equal_runner_tie() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0), hp=5, shield=5,
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(1, 0), hp=2, unit_type=UnitType.WORKER,
    )
    vanguard = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000002"),
        position=(0, 1), hp=4, unit_type=UnitType.VANGUARD,
    )
    turn = make_turn(
        core=core,
        units=(worker, vanguard),
        beacon=SimpleNamespace(position=(2, 1), status="GROUND", carrier_id=None),
    )
    memory = TacticMemory(
        policy=StrategyProfile.default().with_updates(economy_priority=1.5)
    )

    assert _choose_runner(turn, memory) is worker


def test_play_without_adaptive_coordinator_keeps_one_submission(monkeypatch, capsys) -> None:
    submissions = []

    class FakeTurn:
        tick = 7
        core = None

        def submit(self):
            submissions.append(self.tick)
            return SimpleNamespace(tick=self.tick, accepted=True)

    class FakeGame:
        def __init__(self, *, api_key):
            assert api_key == "provided-key"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def turns(self):
            yield FakeTurn()

    class DisabledCoordinator:
        def current_profile(self):
            return StrategyProfile.default()

        def observe(self, turn, accepted):
            return None

        def close(self):
            return None

    monkeypatch.setattr("balanced_tactic.ArenaHeroClient", FakeGame)

    play("provided-key", adaptive=DisabledCoordinator())

    assert submissions == [7]
    assert capsys.readouterr().out == "tick=7 accepted=True\n"


def test_adaptive_observation_failure_does_not_stop_submissions(monkeypatch, capsys) -> None:
    submissions: list[int] = []

    class FakeTurn:
        tick = 8
        core = None

        def submit(self):
            submissions.append(self.tick)
            return SimpleNamespace(tick=self.tick, accepted=True)

    class FakeGame:
        def __init__(self, *, api_key):
            assert api_key == "provided-key"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def turns(self):
            yield FakeTurn()

    class FailingCoordinator:
        def current_profile(self):
            return StrategyProfile.default()

        def observe(self, turn, accepted):
            raise OSError("telemetry disk unavailable")

        def close(self):
            return None

    monkeypatch.setattr("balanced_tactic.ArenaHeroClient", FakeGame)
    play("provided-key", adaptive=FailingCoordinator())

    assert submissions == [8]
    assert capsys.readouterr().out == "tick=8 accepted=True\n"


def test_play_uses_the_turn_profile_snapshot_for_adaptive_observation(monkeypatch, capsys) -> None:
    snapshots = []

    class FakeTurn:
        tick = 9
        core = None

        def submit(self):
            return SimpleNamespace(tick=self.tick, accepted=True)

    class FakeGame:
        def __init__(self, *, api_key):
            assert api_key == "provided-key"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def turns(self):
            yield FakeTurn()

    class SnapshotCoordinator:
        def current_profile(self):
            return StrategyProfile.default().with_updates(worker_target=3)

        def observe(self, turn, accepted):
            raise AssertionError("legacy observe should not be used when snapshot API exists")

        def observe_snapshot(self, turn, accepted, profile):
            snapshots.append(profile)

        def close(self):
            return None

    monkeypatch.setattr("balanced_tactic.ArenaHeroClient", FakeGame)
    play("provided-key", adaptive=SnapshotCoordinator())

    assert snapshots and snapshots[0].worker_target == 3
    assert capsys.readouterr().out == "tick=9 accepted=True\n"


def test_play_passes_aggregate_economy_defense_exploration_and_contact_diagnostics(
    monkeypatch, capsys
) -> None:
    observed = []

    class FakeTurn:
        tick = 10
        core = None

        def submit(self):
            return SimpleNamespace(tick=self.tick, accepted=True)

    class FakeGame:
        def __init__(self, *, api_key):
            assert api_key == "provided-key"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def turns(self):
            yield FakeTurn()

    class DiagnosticCoordinator:
        def current_profile(self):
            return StrategyProfile.default()

        def observe_snapshot_with_diagnostics(
            self, turn, accepted, profile, diagnostics
        ):
            observed.append(diagnostics)

        def close(self):
            return None

    def fake_choose_actions(turn, memory):
        memory.economy_diagnostics = {
            "visible_resource_count": 0,
            "worker_modes": {"SCOUT": 2},
            "idle_worker_ticks": 0,
            "route_stalls": 0,
            "oscillation_ticks": 0,
            "runner_progress_ticks": 0,
        }
        memory.exploration_diagnostics = {
            "newly_explored_cells": 4,
            "frontier_assignments": 2,
        }
        memory.contact_diagnostics = {
            "level": "THREATENING",
            "visible_enemy_count": 1,
            "responding_combat_units": 1,
        }

    monkeypatch.setattr("balanced_tactic.ArenaHeroClient", FakeGame)
    monkeypatch.setattr("balanced_tactic.choose_actions", fake_choose_actions)

    play("provided-key", adaptive=DiagnosticCoordinator())

    assert observed == [{
        "visible_resource_count": 0,
        "worker_modes": {"SCOUT": 2},
        "idle_worker_ticks": 0,
        "route_stalls": 0,
        "oscillation_ticks": 0,
        "runner_progress_ticks": 0,
        "defense_level": "CLEAR",
        "incoming_core_damage": 0,
        "exploration": {
            "newly_explored_cells": 4,
            "frontier_assignments": 2,
        },
        "contact": {
            "level": "THREATENING",
            "visible_enemy_count": 1,
            "responding_combat_units": 1,
        },
    }]
    assert capsys.readouterr().out == "tick=10 accepted=True\n"


def test_choose_actions_records_aggregate_worker_economy_health() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=5,
    )
    first = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(2, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    second = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000002"),
        position=(0, 2),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    turn = make_turn(core=core, units=(first, second), resources=0)
    memory = TacticMemory()

    choose_actions(turn, memory)

    diagnostics = memory.economy_diagnostics
    assert diagnostics["visible_resource_count"] == 0
    assert diagnostics["worker_modes"] == {"SCOUT": 2}
    assert diagnostics["idle_worker_ticks"] == 0
    encoded = repr(diagnostics)
    assert str(first.id) not in encoded
    assert str(second.id) not in encoded
    assert "(2, 0)" not in encoded
    assert "(0, 2)" not in encoded


def test_core_beacon_pickup_is_not_replaced_by_production() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=5,
    )
    beacon = SimpleNamespace(position=(0, 0), status="GROUND", carrier_id=None)
    turn = make_turn(core=core, units=(), resources=10, beacon=beacon)

    choose_actions(turn)

    assert core.actions == [("PICKUP_BEACON",)]


def test_unit_heal_does_not_starve_core_recovery() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=4,
        shield=5,
    )
    ranger = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=1,
        unit_type=UnitType.VANGUARD,
    )
    turn = make_turn(
        core=core,
        units=(ranger,),
        resources=1,
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn)

    assert ranger.actions == []
    assert core.actions == [("HEAL",)]


def test_moving_core_does_not_receive_deposit_or_production() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=4,
        state="MOVING",
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
        cargo=1,
    )
    turn = make_turn(
        core=core,
        units=(worker,),
        resources=10,
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn)

    assert worker.actions == []
    assert core.actions == []


def test_dynamic_price_is_used_after_population_twenty() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
    )
    worker_one = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(3, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    worker_two = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000002"),
        position=(4, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    units = pad_with_workers((worker_one, worker_two), population=20)
    turn = make_turn(core=core, units=units, resources=16)

    choose_actions(turn)

    # At the first dynamic price band the staged economy already has its
    # Worker goal but still lacks the first durable screen.
    assert core.actions == [("SPAWN", UnitType.VANGUARD)]


def test_unknown_remote_beacon_does_not_lease_worker_before_bootstrap() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(5, 5),
        hp=5,
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(5, 5),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    turn = make_turn(
        core=core,
        units=(worker,),
        beacon=SimpleNamespace(position=(8, 5), status=None, carrier_id=None),
    )

    memory = TacticMemory()
    choose_actions(turn, memory)

    assert worker.actions and worker.actions[-1][0] == "MOVE"
    assert all(action[0] != "PICKUP_BEACON" for action in worker.actions)
    assert memory.runner_id is None
    assert memory.planned_reason_codes[worker.id] == "SCOUT_FRONTIER"


def test_runner_harvests_current_resource_before_resuming_beacon_route() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=5,
    )
    workers = tuple(
        FakeController(
            object_id=UUID(int=index + 1),
            position=(index, 0),
            hp=2,
            unit_type=UnitType.WORKER,
        )
        for index in range(6)
    )
    runner = workers[-1]
    turn = make_turn(
        core=core,
        units=workers,
        resources=0,
        resource_cells={runner.position},
        beacon=SimpleNamespace(position=(20, 0), status=None, carrier_id=None),
    )
    memory = TacticMemory(runner_id=runner.id)

    choose_actions(turn, memory)

    assert runner.actions == [("HARVEST",)]


def test_near_visible_ground_beacon_allows_opportunistic_runner() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0), hp=5, shield=5,
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(1, 0), hp=2, unit_type=UnitType.WORKER,
    )
    turn = make_turn(
        core=core,
        units=(worker,),
        beacon=SimpleNamespace(position=(3, 0), status="GROUND", carrier_id=None),
    )
    memory = TacticMemory()

    choose_actions(turn, memory)

    assert memory.runner_id == worker.id
    assert worker.actions == [("MOVE", Direction.RIGHT)]


def test_staged_production_order_builds_economy_and_defense() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0), hp=5, shield=5,
    )

    def units(workers: int, vanguards: int, rangers: int):
        result = []
        index = 100
        for count, unit_type, hp in (
            (workers, UnitType.WORKER, 2),
            (vanguards, UnitType.VANGUARD, 4),
            (rangers, UnitType.RANGER, 2),
        ):
            for _ in range(count):
                result.append(FakeController(
                    object_id=UUID(int=index), position=(index, 100), hp=hp,
                    unit_type=unit_type,
                ))
                index += 1
        return tuple(result)

    cases = (
        ((5, 0, 0), UnitType.WORKER),
        ((6, 0, 0), UnitType.VANGUARD),
        ((6, 1, 0), UnitType.RANGER),
        ((6, 1, 1), UnitType.WORKER),
        ((12, 1, 1), UnitType.VANGUARD),
        ((12, 3, 1), UnitType.RANGER),
        ((12, 3, 4), UnitType.WORKER),
    )
    for composition, expected in cases:
        fleet = units(*composition)
        turn = make_turn(core=core, units=fleet, resources=100)
        assert _desired_spawn_type(turn, memory=TacticMemory()) is expected


def test_beacon_pickup_precedes_harvest_for_cargo_worker() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(5, 5),
        hp=5,
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(1, 0),
        hp=2,
        unit_type=UnitType.WORKER,
        cargo=1,
    )
    beacon = SimpleNamespace(position=(1, 0), status="GROUND", carrier_id=None)
    turn = make_turn(
        core=core,
        units=(worker,),
        resources=5,
        resource_cells={(1, 0)},
        beacon=beacon,
    )

    choose_actions(turn)

    assert worker.actions == [("PICKUP_BEACON",)]


def test_explicit_beacon_contest_uses_lowest_raw_uuid_across_core_and_unit() -> None:
    # Synthetic helper-only fixture (not an authoritative Turn): it deliberately
    # models a historical over-capacity snapshot so the resolver's UUID tie-break
    # can be tested in isolation from cell admission and cross-player movement.
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-0000000000f0"),
        position=(0, 0),
        hp=5,
        shield=5,
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.WORKER,
        id=UUID("00000000-0000-0000-0000-0000000000e0"),
        position=(0, 0),
        hp=2,
    )
    beacon = SimpleNamespace(position=(0, 0), status="GROUND", carrier_id=None)
    turn = make_turn(core=core, units=(worker,), enemies=(enemy,), beacon=beacon)
    acted: set[object] = set()
    memory = TacticMemory()

    assert _beacon_contest_visible(turn, (0, 0))
    assert not _queue_beacon_pickup(turn, memory, acted, False)
    assert worker.actions == [("PICKUP_BEACON",)]
    assert core.actions == []


def test_beacon_worker_harvests_before_returning_to_core() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(5, 5),
        hp=5,
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(1, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    beacon = SimpleNamespace(
        position=(1, 0),
        status="CARRIED",
        carrier_id=worker.id,
    )
    turn = make_turn(
        core=core,
        units=(worker,),
        resource_cells={(1, 0)},
        beacon=beacon,
    )

    choose_actions(turn)

    assert worker.actions == [("HARVEST",)]


def test_lethally_threatened_worker_carrier_escapes_core_before_healing() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=5,
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=1,
        unit_type=UnitType.WORKER,
        cargo=1,
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.RANGER,
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(1, 0),
        hp=2,
    )
    turn = make_turn(
        core=core,
        units=(worker,),
        resources=0,
        enemies=(enemy,),
        beacon=SimpleNamespace(
            position=(0, 0),
            status="CARRIED",
            carrier_id=worker.id,
        ),
    )

    choose_actions(turn)

    assert worker.actions and worker.actions[0][0] == "MOVE"


def test_hidden_beacon_status_does_not_use_stale_carrier_memory() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=5,
    )
    carrier = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(1, 0),
        hp=4,
        unit_type=UnitType.VANGUARD,
    )
    memory = TacticMemory(carrier_id=carrier.id)
    turn = make_turn(
        core=core,
        units=(carrier,),
        resources=1,
        beacon=SimpleNamespace(position=(100, 100), status=None, carrier_id=None),
    )

    choose_actions(turn, memory)

    assert core.actions == []


def test_beacon_drop_event_clears_carrier_memory() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(5, 5),
        hp=5,
    )
    carrier = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(5, 5),
        hp=4,
        unit_type=UnitType.VANGUARD,
    )
    event = SimpleNamespace(
        event_id=UUID("00000000-0000-0000-0000-000000000099"),
        event_type="BEACON_DROPPED",
        actor_id=carrier.id,
    )
    memory = TacticMemory(carrier_id=carrier.id)
    turn = make_turn(
        core=core,
        units=(carrier,),
        beacon=SimpleNamespace(position=(8, 5), status=None, carrier_id=None),
        events=(event,),
    )

    choose_actions(turn, memory)

    assert memory.carrier_id is None
    # A hidden status is only a scouting hint; a combat Unit must not blindly
    # chase the public coordinate after dropping Beacon. It may take one step
    # to establish its assigned Vanguard defense ring around the Core.
    assert carrier.actions == [("MOVE", Direction.RIGHT)]


def test_visible_enemy_beacon_carrier_is_highest_ranger_target() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
    )
    ranger = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    enemy_carrier = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.WORKER,
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(0, 3),
        hp=2,
    )
    lower_hp_escort = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.WORKER,
        id=UUID("00000000-0000-0000-0000-000000000022"),
        position=(0, 3),
        hp=1,
    )
    enemy_core = SimpleNamespace(
        kind="CORE",
        id=UUID("00000000-0000-0000-0000-000000000021"),
        position=(3, 0),
        hp=1,
        shield=0,
    )
    turn = make_turn(
        core=core,
        units=(ranger,),
        enemies=(enemy_core, enemy_carrier, lower_hp_escort),
        beacon=SimpleNamespace(
            position=(0, 3), status="CARRIED", carrier_id=enemy_carrier.id
        ),
    )

    choose_actions(turn)

    assert ranger.actions == [("SHOOT", (0, 3), enemy_carrier.id)]


def test_ranger_uses_a_target_free_prediction_from_visible_unit() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
    )
    ranger = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.WORKER,
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(0, 4),
        hp=2,
    )
    turn = make_turn(core=core, units=(ranger,), enemies=(enemy,))

    choose_actions(turn)

    assert ranger.actions == [("SHOOT", (0, 3))]


def test_ranger_prediction_respects_intermediate_obstacle() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
    )
    ranger = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.WORKER,
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(0, 4),
        hp=2,
    )
    turn = make_turn(
        core=core,
        units=(ranger,),
        enemies=(enemy,),
        obstacle_cells={(0, 1)},
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn)

    assert all(action[0] != "SHOOT" for action in ranger.actions)


def test_ranger_keeps_shooting_a_moving_core_when_destination_is_occupied() -> None:
    own_core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 1),
        hp=5,
        shield=10,
    )
    ranger = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 3),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    enemy_core = SimpleNamespace(
        kind="CORE",
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(0, 2),
        hp=5,
        shield=5,
        state="MOVING",
        destination=(0, 1),
        move_progress=3,
        move_required_ticks=4,
    )
    turn = make_turn(
        core=own_core,
        units=(ranger,),
        enemies=(enemy_core,),
        beacon=SimpleNamespace(
            position=(0, 1), status="CARRIED", carrier_id=own_core.id
        ),
    )

    choose_actions(turn)

    assert ranger.actions == [("SHOOT", (0, 2), enemy_core.id)]


def test_vanguard_sweeps_a_predicted_empty_cell() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
    )
    vanguard = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=4,
        unit_type=UnitType.VANGUARD,
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.WORKER,
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(0, 2),
        hp=2,
    )
    turn = make_turn(core=core, units=(vanguard,), enemies=(enemy,))

    choose_actions(turn)

    assert vanguard.actions == [("SWEEP", Direction.DOWN)]


def test_vanguard_intercepts_visible_threat_to_beacon_carrier_before_core() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(1, 1),
        hp=5,
        shield=5,
    )
    carrier = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000002"),
        position=(1, 1),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    vanguard = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=4,
        unit_type=UnitType.VANGUARD,
    )
    enemy_threat = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.VANGUARD,
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(0, 1),
        hp=4,
    )
    enemy_core = SimpleNamespace(
        kind="CORE",
        id=UUID("00000000-0000-0000-0000-000000000021"),
        position=(1, 0),
        hp=5,
        shield=5,
    )
    turn = make_turn(
        core=core,
        units=(carrier, vanguard),
        enemies=(enemy_core, enemy_threat),
        beacon=SimpleNamespace(
            position=(1, 1),
            status="CARRIED",
            carrier_id=carrier.id,
        ),
    )

    choose_actions(turn)

    assert vanguard.actions == [("SWEEP", Direction.DOWN)]


def test_selected_defender_establishes_ring_before_chasing_enemy_core() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
    )
    ranger = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    enemy_core = SimpleNamespace(
        kind="CORE",
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(0, 5),
        hp=5,
        shield=5,
    )
    turn = make_turn(core=core, units=(ranger,), enemies=(enemy_core,))

    choose_actions(turn)

    assert ranger.actions == [("MOVE", Direction.RIGHT)]


def test_zero_observed_resources_still_queues_a_unit_heal() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=10,
    )
    ranger = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=1,
        unit_type=UnitType.RANGER,
    )
    turn = make_turn(
        core=core,
        units=(ranger,),
        resources=0,
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn)

    assert ranger.actions == [("HEAL",)]


def test_damaged_core_cell_ranger_heals_before_beacon_route() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
    )
    ranger = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=1,
        unit_type=UnitType.RANGER,
    )
    enemy_core = SimpleNamespace(
        kind="CORE",
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(0, 5),
        hp=5,
        shield=5,
    )
    turn = make_turn(
        core=core,
        units=(ranger,),
        resources=2,
        enemies=(enemy_core,),
        beacon=SimpleNamespace(position=(8, 8), status=None, carrier_id=None),
    )

    choose_actions(turn)

    assert ranger.actions == [("HEAL",)]


def test_population_twenty_still_uses_dynamic_price_for_next_spawn() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
    )
    workers = tuple(
        FakeController(
            object_id=UUID(f"00000000-0000-0000-0000-0000000000{i:02x}"),
            position=(i + 1, 0),
            hp=2,
            unit_type=UnitType.WORKER,
        )
        for i in range(1, 3)
    )
    units = pad_with_workers(workers, population=20)
    turn = make_turn(core=core, units=units, resources=16)

    choose_actions(turn)

    assert core.actions == [("SPAWN", UnitType.VANGUARD)]


def test_two_worker_economy_continues_bootstrap_before_defense() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=5,
    )
    workers = tuple(
        FakeController(
            object_id=UUID(f"00000000-0000-0000-0000-0000000000{i:02x}"),
            position=(i + 1, 0),
            hp=2,
            unit_type=UnitType.WORKER,
        )
        for i in range(1, 3)
    )
    turn = make_turn(
        core=core,
        units=workers,
        resources=10,
        population=2,
        beacon=SimpleNamespace(position=(8, 0), status=None, carrier_id=None),
    )

    choose_actions(turn)

    assert core.actions == [("SPAWN", UnitType.WORKER)]


def test_dynamic_price_fallback_avoids_capacity_deadlock_at_high_population() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=5,
    )
    workers = tuple(
        FakeController(
            object_id=UUID(f"00000000-0000-0000-0000-0000000000{i:02x}"),
            position=(i + 1, 0),
            hp=2,
            unit_type=UnitType.WORKER,
        )
        for i in range(1, 3)
    )
    units = pad_with_workers(workers, population=90)
    turn = make_turn(
        core=core,
        units=units,
        resources=256,
        beacon=SimpleNamespace(position=(8, 0), status=None, carrier_id=None),
    )

    choose_actions(turn)

    # At N=90, Ranger/Vanguard prices exceed the 450-resource capacity; the
    # Worker fallback is the only affordable way to keep expanding.
    assert core.actions == [("SPAWN", UnitType.WORKER)]


def test_core_cell_beacon_pickup_preserves_the_unit_action() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=5,
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    beacon = SimpleNamespace(position=(0, 0), status="GROUND", carrier_id=None)
    turn = make_turn(
        core=core,
        units=(worker,),
        resources=10,
        beacon=beacon,
    )

    choose_actions(turn)

    assert core.actions == [("PICKUP_BEACON",)]
    # The Core has just raised its Beacon shield cap and owns the only Core
    # action; keep the Worker as an escort instead of roaming without a
    # currently legal spawn.
    assert worker.actions == []


def test_v014_unit_cost_boundaries_are_the_sdk_values() -> None:
    assert unit_cost(UnitType.RANGER, 19) == 12
    assert unit_cost(UnitType.RANGER, 20) == 16
    assert unit_cost(UnitType.RANGER, 25) == 20
    assert unit_cost(UnitType.VANGUARD, 30) == 22


def test_sdk_unit_view_is_normalized_from_unit_discriminator() -> None:
    view = UnitView(
        kind="UNIT",
        id=UUID("00000000-0000-0000-0000-000000000030"),
        controlled=False,
        position=(2, 3),
        hp=2,
        unit_type=UnitType.RANGER,
    )

    assert _kind(view) == "RANGER"


def test_damaged_core_keeps_recovery_action_while_unit_carries_beacon() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=4,
        shield=5,
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    turn = make_turn(
        core=core,
        units=(worker,),
        resources=0,
        beacon=SimpleNamespace(position=(0, 0), status="GROUND", carrier_id=None),
    )

    choose_actions(turn)

    assert worker.actions == [("PICKUP_BEACON",)]
    assert core.actions == [("HEAL",)]


def test_nonlethal_core_threat_still_secures_beacon_tick() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=5,
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.VANGUARD,
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(1, 0),
        hp=4,
    )
    turn = make_turn(
        core=core,
        resources=5,
        enemies=(enemy,),
        beacon=SimpleNamespace(position=(0, 0), status="GROUND", carrier_id=None),
    )

    choose_actions(turn)

    assert core.actions == [("PICKUP_BEACON",)]


def test_critically_threatened_core_secures_beacon_without_safe_unit() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=1,
        shield=0,
    )
    damaged_worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=1,
        unit_type=UnitType.WORKER,
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.VANGUARD,
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(1, 0),
        hp=4,
    )
    turn = make_turn(
        core=core,
        units=(damaged_worker,),
        resources=0,
        enemies=(enemy,),
        beacon=SimpleNamespace(position=(0, 0), status="GROUND", carrier_id=None),
    )

    choose_actions(turn)

    # The visible Vanguard attack is fatal for this one-HP Worker; healing is
    # resolved after combat and must not consume the speculative action.
    assert damaged_worker.actions == []
    assert core.actions == [("PICKUP_BEACON",)]


def test_core_cell_worker_scouts_without_visible_resources() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=10,
    )
    worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    turn = make_turn(
        core=core,
        units=(worker,),
        resources=0,
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    memory = TacticMemory()
    choose_actions(turn, memory)

    assert worker.actions and worker.actions[-1][0] == "MOVE"
    assert memory.planned_reason_codes[worker.id] == "SCOUT_FRONTIER"
    assert core.actions == []


def test_two_empty_workers_without_visible_resources_explore_distinctly() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=10,
    )
    first = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(1, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    second = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000002"),
        position=(0, 1),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    turn = make_turn(
        core=core,
        units=(first, second),
        resources=0,
        beacon=SimpleNamespace(position=(100, 100), status=None, carrier_id=None),
    )

    memory = TacticMemory()
    choose_actions(turn, memory)

    assert all(unit.actions and unit.actions[-1][0] == "MOVE" for unit in (first, second))
    assert memory.planned_reason_targets[first.id] != memory.planned_reason_targets[second.id]


def test_visible_resource_targets_are_unique_across_workers() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=10,
    )
    first = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    second = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000002"),
        position=(4, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    turn = make_turn(
        core=core,
        units=(first, second),
        resources=0,
        resource_cells={(1, 0), (5, 0)},
        beacon=SimpleNamespace(position=(100, 100), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn, TacticMemory())

    assert first.actions == [("MOVE", Direction.RIGHT)]
    assert second.actions == [("MOVE", Direction.RIGHT)]


def test_cargo_worker_still_returns_before_scouting() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=10,
    )
    cargo = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(2, 0),
        hp=2,
        unit_type=UnitType.WORKER,
        cargo=1,
    )
    turn = make_turn(
        core=core,
        units=(cargo,),
        resources=0,
        beacon=SimpleNamespace(position=(100, 100), status=None, carrier_id=None),
    )

    choose_actions(turn, TacticMemory())

    assert cargo.actions == [("MOVE", Direction.LEFT)]


def test_two_cell_worker_oscillation_changes_scout_route() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=10,
    )
    memory = TacticMemory()
    positions = ((1, 0), (2, 0), (1, 0), (2, 0))

    for tick, position in enumerate(positions, start=1):
        unit = FakeController(
            object_id=UUID("00000000-0000-0000-0000-000000000001"),
            position=position,
            hp=2,
            unit_type=UnitType.WORKER,
        )
        turn = make_turn(
            core=core,
            units=(unit,),
            resources=0,
            obstacle_cells={(3, 0)},
            beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
        )
        turn.tick = tick
        choose_actions(turn, memory)

    assert memory.frontier.oscillation_detections >= 1
    assert unit.actions and unit.actions[-1] != ("MOVE", Direction.RIGHT)


def test_idle_combat_unit_vacates_core_for_affordable_spawn() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=10,
    )
    ranger = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    turn = make_turn(
        core=core,
        units=(ranger,),
        resources=5,
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn)

    assert ranger.actions and ranger.actions[0][0] == "MOVE"
    assert core.actions == [("SPAWN", UnitType.WORKER)]


def test_funded_bootstrap_spawn_vacates_worker_before_partial_deposit() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=5,
    )
    cargo_worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
        cargo=5,
    )
    remote_worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000002"),
        position=(3, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    turn = make_turn(
        core=core,
        units=(cargo_worker, remote_worker),
        resources=8,
        population=2,
        beacon=SimpleNamespace(position=(8, 0), status=None, carrier_id=None),
    )

    choose_actions(turn)

    # Current inventory already funds the 5-resource bootstrap Worker.  Moving
    # preserves cargo and frees the only Unit slot for same-Tick production.
    assert cargo_worker.actions and cargo_worker.actions[0][0] == "MOVE"
    assert core.actions == [("SPAWN", UnitType.WORKER)]


def test_funded_spawn_vacates_cargo_worker_before_partial_deposit() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=10,
    )
    cargo_worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
        cargo=5,
    )
    remote_worker = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000002"),
        position=(3, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    vanguard = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000003"),
        position=(4, 0),
        hp=4,
        unit_type=UnitType.VANGUARD,
    )
    turn = make_turn(
        core=core,
        units=(cargo_worker, remote_worker, vanguard),
        resources=12,
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn)

    assert cargo_worker.actions and cargo_worker.actions[0][0] == "MOVE"
    assert core.actions == [("SPAWN", UnitType.WORKER)]


def test_cargo_worker_vacates_for_a_price_lowered_by_remote_death() -> None:
    core = FakeController(
        object_id=UUID(int=1000),
        position=(0, 0),
        hp=5,
        shield=10,
    )
    cargo_worker = FakeController(
        object_id=UUID(int=1),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
        cargo=1,
    )
    remote_worker = FakeController(
        object_id=UUID(int=2),
        position=(10, 10),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    doomed_ranger = FakeController(
        object_id=UUID(int=3),
        position=(1, 0),
        hp=1,
        unit_type=UnitType.RANGER,
    )
    rangers = tuple(
        FakeController(
            object_id=UUID(int=10 + index),
            position=(20 + index, 20),
            hp=2,
            unit_type=UnitType.RANGER,
        )
        for index in range(17)
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.VANGUARD,
        id=UUID(int=2000),
        position=(2, 0),
        hp=4,
    )
    turn = make_turn(
        core=core,
        units=(cargo_worker, remote_worker, doomed_ranger, *rangers),
        resources=12,
        enemies=(enemy,),
        population=20,
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn)

    assert cargo_worker.actions and cargo_worker.actions[0][0] == "MOVE"
    assert core.actions == [("SPAWN", UnitType.VANGUARD)]


def test_safe_worker_move_is_not_counted_as_a_dynamic_price_death() -> None:
    """A planned safe retreat must not make this Tick's spawn cheaper.

    The server snapshots population after combat, but movement resolves first.
    This Worker is visibly threatened at its current cell and the planner has
    a legal safe step toward the Core.  It therefore survives the Tick; using
    the lower N-1 price would queue a no-cost-failing speculative spawn.
    """

    core = FakeController(
        object_id=UUID(int=1000),
        position=(0, 0),
        hp=5,
        shield=10,
    )
    retreating_worker = FakeController(
        object_id=UUID(int=1),
        position=(6, 5),
        hp=1,
        unit_type=UnitType.WORKER,
    )
    workers = (
        retreating_worker,
        FakeController(
            object_id=UUID(int=2),
            position=(20, 20),
            hp=2,
            unit_type=UnitType.WORKER,
        ),
    )
    rangers = tuple(
        FakeController(
            object_id=UUID(int=10 + index),
            position=(30 + index, 20),
            hp=2,
            unit_type=UnitType.RANGER,
        )
        for index in range(9)
    )
    vanguards = tuple(
        FakeController(
            object_id=UUID(int=30 + index),
            position=(40 + index, 20),
            hp=4,
            unit_type=UnitType.VANGUARD,
        )
        for index in range(9)
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.VANGUARD,
        id=UUID(int=2000),
        position=(7, 5),
        hp=4,
    )
    turn = make_turn(
        core=core,
        units=workers + rangers + vanguards,
        resources=5,
        population=20,
        enemies=(enemy,),
        beacon=SimpleNamespace(
            position=(0, 0), status="CARRIED", carrier_id=core.id
        ),
    )

    choose_actions(turn)

    assert retreating_worker.actions and retreating_worker.actions[0][0] == "MOVE"
    # At the actual post-combat population N=20 the selected Ranger costs 16;
    # resources=5 cannot fund it.  A false death preview leaves one Worker and
    # would incorrectly request a replacement Worker at the N=19 price of 5.
    assert core.actions == []


def test_core_preemptively_heals_visible_nonfatal_hp_damage() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=0,
    )
    enemies = tuple(
        SimpleNamespace(
            kind="UNIT",
            unit_type=UnitType.RANGER,
            id=UUID(int=100 + index),
            position=position,
            hp=2,
        )
        for index, position in enumerate(((0, 3), (3, 0), (3, 3)))
    )
    turn = make_turn(core=core, resources=3, enemies=enemies)

    choose_actions(turn)

    assert core.actions == [("HEAL",)]


def test_core_preemptively_repairs_beacon_shield_after_visible_hit() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=10,
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.RANGER,
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(0, 3),
        hp=2,
    )
    turn = make_turn(
        core=core,
        resources=1,
        enemies=(enemy,),
        beacon=SimpleNamespace(
            position=(0, 0), status="CARRIED", carrier_id=core.id
        ),
    )

    choose_actions(turn)

    assert core.actions == [("REPAIR_SHIELD",)]


def test_damaged_beacon_carrier_heals_before_core_hp_recovery() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=4,
        shield=10,
    )
    carrier = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=1,
        unit_type=UnitType.RANGER,
    )
    turn = make_turn(
        core=core,
        units=(carrier,),
        resources=1,
        beacon=SimpleNamespace(
            position=(0, 0), status="CARRIED", carrier_id=carrier.id
        ),
    )

    choose_actions(turn)

    assert carrier.actions == [("HEAL",)]
    assert core.actions == [("HEAL",)]


def test_full_beacon_carrier_preheals_when_nonlethal_threat_has_no_safe_move() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=5,
    )
    carrier = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.VANGUARD,
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(1, 0),
        hp=4,
    )
    turn = make_turn(
        core=core,
        units=(carrier,),
        resources=2,
        enemies=(enemy,),
        obstacle_cells={(0, 1), (0, -1), (-1, 0)},
        beacon=SimpleNamespace(
            position=(0, 0), status="CARRIED", carrier_id=carrier.id
        ),
    )

    choose_actions(turn)

    assert carrier.actions == [("HEAL",)]
    assert core.actions == [("REPAIR_SHIELD",)]


def test_core_beacon_carrier_does_not_require_unit_type_for_preheal_probe() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=5,
    )
    del core.unit_type
    turn = make_turn(
        core=core,
        resources=0,
        beacon=SimpleNamespace(
            position=(0, 0), status="CARRIED", carrier_id=core.id
        ),
    )

    choose_actions(turn)

    assert core.actions == [("REPAIR_SHIELD",)]


def test_safe_noncore_beacon_carrier_move_keeps_beacon_shield_cap() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=5,
    )
    carrier = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(1, 0),
        hp=1,
        unit_type=UnitType.WORKER,
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.VANGUARD,
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(2, 0),
        hp=4,
    )
    turn = make_turn(
        core=core,
        units=(carrier,),
        resources=1,
        enemies=(enemy,),
        beacon=SimpleNamespace(
            position=(1, 0), status="CARRIED", carrier_id=carrier.id
        ),
    )

    choose_actions(turn)

    assert carrier.actions == [("MOVE", Direction.LEFT)]
    assert core.actions == [("REPAIR_SHIELD",)]


def test_acted_carrier_does_not_cancel_core_shield_reserve() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=5,
    )
    carrier = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=1,
        unit_type=UnitType.RANGER,
    )
    turn = make_turn(
        core=core,
        units=(carrier,),
        resources=1,
        beacon=SimpleNamespace(
            position=(0, 0), status="CARRIED", carrier_id=carrier.id
        ),
    )

    reserve = _core_recovery_reserve(
        turn,
        TacticMemory(),
        core_action_selected=False,
        acted={carrier.id},
    )

    assert reserve == 1


def test_fatal_carrier_drop_uses_post_drop_shield_cap_for_core_action() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=5,
    )
    carrier = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=1,
        unit_type=UnitType.WORKER,
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.VANGUARD,
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(1, 0),
        hp=2,
    )
    turn = make_turn(
        core=core,
        units=(carrier,),
        resources=5,
        enemies=(enemy,),
        obstacle_cells={(0, 1), (0, -1), (-1, 0)},
        beacon=SimpleNamespace(
            position=(0, 0), status="CARRIED", carrier_id=carrier.id
        ),
    )

    choose_actions(turn)

    assert carrier.actions == []
    # Active Core pressure pauses Worker production. The five resources cannot
    # fund a combat defender, so waiting is preferable to economic expansion.
    assert core.actions == []


def test_safe_carrier_escape_keeps_beacon_shield_cap_for_core_repair() -> None:
    core = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000010"),
        position=(0, 0),
        hp=5,
        shield=5,
    )
    carrier = FakeController(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        position=(0, 0),
        hp=1,
        unit_type=UnitType.RANGER,
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.RANGER,
        id=UUID("00000000-0000-0000-0000-000000000020"),
        position=(0, 3),
        hp=2,
    )
    turn = make_turn(
        core=core,
        units=(carrier,),
        resources=1,
        enemies=(enemy,),
        beacon=SimpleNamespace(
            position=(0, 0), status="CARRIED", carrier_id=carrier.id
        ),
    )

    choose_actions(turn)

    assert carrier.actions and carrier.actions[0][0] == "MOVE"
    assert core.actions == [("REPAIR_SHIELD",)]


def test_spawn_uses_post_combat_population_price_after_visible_death() -> None:
    core = FakeController(
        object_id=UUID(int=1000),
        position=(0, 0),
        hp=5,
        shield=10,
    )
    doomed_ranger = FakeController(
        object_id=UUID(int=1),
        position=(5, 0),
        hp=1,
        unit_type=UnitType.RANGER,
    )
    remote_worker = FakeController(
        object_id=UUID(int=2),
        position=(10, 10),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    remote_worker_two = FakeController(
        object_id=UUID(int=3),
        position=(11, 10),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    rangers = tuple(
        FakeController(
            object_id=UUID(int=10 + index),
            position=(20 + index, 20),
            hp=2,
            unit_type=UnitType.RANGER,
        )
        for index in range(17)
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.VANGUARD,
        id=UUID(int=2000),
        position=(6, 0),
        hp=4,
    )
    turn = make_turn(
        core=core,
        units=(doomed_ranger, remote_worker, remote_worker_two, *rangers),
        resources=12,
        enemies=(enemy,),
        population=20,
        beacon=SimpleNamespace(position=(0, 0), status="CARRIED", carrier_id=core.id),
    )

    choose_actions(turn)

    assert core.actions == [("SPAWN", UnitType.WORKER)]


def test_funded_bootstrap_spawn_survives_a_remote_worker_retreat() -> None:
    """A safe remote retreat must not block already funded Worker growth."""

    core = FakeController(
        object_id=UUID(int=1000),
        position=(0, 0),
        hp=5,
        shield=10,
    )
    cargo_worker = FakeController(
        object_id=UUID(int=1),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
        cargo=1,
    )
    retreating_worker = FakeController(
        object_id=UUID(int=2),
        position=(6, 5),
        hp=1,
        unit_type=UnitType.WORKER,
    )
    rangers = tuple(
        FakeController(
            object_id=UUID(int=10 + index),
            position=(20 + index, 20),
            hp=2,
            unit_type=UnitType.RANGER,
        )
        for index in range(9)
    )
    vanguards = tuple(
        FakeController(
            object_id=UUID(int=30 + index),
            position=(40 + index, 20),
            hp=4,
            unit_type=UnitType.VANGUARD,
        )
        for index in range(9)
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.VANGUARD,
        id=UUID(int=2000),
        position=(7, 5),
        hp=4,
    )
    turn = make_turn(
        core=core,
        units=(cargo_worker, retreating_worker, *rangers, *vanguards),
        resources=15,
        population=20,
        enemies=(enemy,),
        beacon=SimpleNamespace(
            position=(0, 0), status="CARRIED", carrier_id=core.id
        ),
    )

    choose_actions(turn)

    # The mature policy still has fewer than six Workers.  Fifteen resources
    # funds a Worker at N=20 without relying on the threatened Unit's death, so
    # clear the Core slot and keep the cargo for a later deposit.
    assert cargo_worker.actions and cargo_worker.actions[0][0] == "MOVE"
    assert retreating_worker.actions and retreating_worker.actions[0][0] == "MOVE"
    assert core.actions == [("SPAWN", UnitType.WORKER)]


def test_deposit_is_deferred_when_visible_death_would_destroy_overflow() -> None:
    core = FakeController(
        object_id=UUID(int=100),
        position=(0, 0),
        hp=5,
        shield=5,
    )
    depositor = FakeController(
        object_id=UUID(int=1),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
        cargo=5,
    )
    doomed = FakeController(
        object_id=UUID(int=2),
        position=(3, 0),
        hp=1,
        unit_type=UnitType.WORKER,
    )
    safe = FakeController(
        object_id=UUID(int=3),
        position=(8, 8),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    enemy = SimpleNamespace(
        kind="UNIT",
        unit_type=UnitType.RANGER,
        id=UUID(int=200),
        position=(3, 3),
        hp=2,
    )
    turn = make_turn(
        core=core,
        units=(depositor, doomed, safe),
        resources=10,
        enemies=(enemy,),
        obstacle_cells={(4, 0), (3, -1), (2, 0)},
        beacon=SimpleNamespace(position=(100, 100), status=None, carrier_id=None),
    )

    choose_actions(turn)

    assert ("DEPOSIT",) not in depositor.actions


def test_idle_workers_move_to_distinct_real_frontiers_not_radial_fallbacks() -> None:
    core = FakeController(
        object_id=UUID(int=100),
        position=(0, 0),
        hp=5,
        shield=10,
    )
    first = FakeController(
        object_id=UUID(int=1),
        position=(0, 1),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    second = FakeController(
        object_id=UUID(int=2),
        position=(1, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    turn = make_turn(
        core=core,
        units=(first, second),
        resources=0,
        beacon=SimpleNamespace(
            position=(100, 100),
            status="CARRIED",
            carrier_id=core.id,
        ),
    )
    memory = TacticMemory()
    explore_square(memory)
    memory.exploration_observed_tick = turn.tick
    memory.current_visible_cells = frozenset(
        {core.position, first.position, second.position}
    )

    choose_actions(turn, memory)

    assert all(unit.actions[-1][0] == "MOVE" for unit in (first, second))
    assert memory.planned_reason_codes[first.id] == "SCOUT_FRONTIER"
    assert memory.planned_reason_codes[second.id] == "SCOUT_FRONTIER"
    assert memory.planned_reason_targets[first.id] != memory.planned_reason_targets[second.id]


def test_live_frontier_progress_never_scans_between_distant_workers(monkeypatch) -> None:
    core = FakeController(
        object_id=UUID(int=100),
        position=(500, 500),
        hp=5,
        shield=10,
    )
    first = FakeController(
        object_id=UUID(int=1),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    second = FakeController(
        object_id=UUID(int=2),
        position=(1000, 1000),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    turn = make_turn(
        core=core,
        units=(first, second),
        resources=0,
        beacon=SimpleNamespace(
            position=(500, 500),
            status="CARRIED",
            carrier_id=core.id,
        ),
    )
    memory = TacticMemory()
    memory.exploration.observe(
        visible_cells=frozenset(
            {
                (x, y)
                for center_x, center_y in ((0, 0), (1000, 1000))
                for x in range(center_x - 3, center_x + 4)
                for y in range(center_y - 3, center_y + 4)
            }
        ),
        visible_obstacles=frozenset(),
        tick=turn.tick,
    )
    memory.exploration_observed_tick = turn.tick
    real_window = type(memory.exploration).window

    def bounded_window(exploration, *, min_x, min_y, max_x, max_y):
        assert max_x - min_x <= 80
        assert max_y - min_y <= 80
        return real_window(
            exploration,
            min_x=min_x,
            min_y=min_y,
            max_x=max_x,
            max_y=max_y,
        )

    monkeypatch.setattr(type(memory.exploration), "window", bounded_window)

    choose_actions(turn, memory)


def test_worker_does_not_repeat_a_b_a_after_oscillation_is_observed() -> None:
    memory = TacticMemory()
    explore_square(memory)
    worker_id = UUID(int=1)
    for tick, position in enumerate(((0, 0), (1, 0), (0, 0)), start=10):
        core = FakeController(
            object_id=UUID(int=100),
            position=(0, 2),
            hp=5,
            shield=10,
        )
        worker = FakeController(
            object_id=worker_id,
            position=position,
            hp=2,
            unit_type=UnitType.WORKER,
        )
        turn = make_turn(
            core=core,
            units=(worker,),
            resources=0,
            beacon=SimpleNamespace(
                position=(100, 100),
                status="CARRIED",
                carrier_id=core.id,
            ),
        )
        turn.tick = tick
        memory.exploration_observed_tick = tick
        memory.current_visible_cells = frozenset({position, core.position})
        choose_actions(turn, memory)

    assert worker.actions == [] or worker.actions[-1] != ("MOVE", Direction.RIGHT)
    assert memory.exploration_diagnostics["oscillation_detections"] >= 1


def test_worker_waits_when_every_frontier_route_is_blocked_or_attacked() -> None:
    core = FakeController(
        object_id=UUID(int=100),
        position=(0, 0),
        hp=5,
        shield=10,
    )
    worker = FakeController(
        object_id=UUID(int=1),
        position=(0, 1),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    turn = make_turn(
        core=core,
        units=(worker,),
        resources=0,
        obstacle_cells={(1, 1), (-1, 1), (0, 2)},
        beacon=SimpleNamespace(
            position=(100, 100),
            status="CARRIED",
            carrier_id=core.id,
        ),
    )
    memory = TacticMemory()
    explore_square(memory)
    memory.exploration_observed_tick = turn.tick
    memory.current_visible_cells = frozenset({worker.position, core.position})

    choose_actions(turn, memory)

    assert worker.actions == []
    assert memory.planned_reason_codes[worker.id] == "SCOUT_WAIT_NO_SAFE_FRONTIER"


def test_remote_enemy_threatening_workers_triggers_evasion_and_ranger_interception() -> None:
    core = FakeController(object_id=UUID(int=100), position=(0, 0), hp=5, shield=5)
    worker = FakeController(
        object_id=UUID(int=1),
        position=(-7, -9),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    ranger = FakeController(
        object_id=UUID(int=2),
        position=(2, 0),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    guard = FakeController(
        object_id=UUID(int=3),
        position=(1, 0),
        hp=4,
        unit_type=UnitType.VANGUARD,
    )
    enemy = SimpleNamespace(
        id=UUID(int=200),
        kind="UNIT",
        unit_type=UnitType.RANGER,
        position=(-10, -9),
        hp=2,
    )
    turn = make_turn(
        core=core,
        units=(worker, ranger, guard),
        enemies=(enemy,),
        resources=0,
        beacon=SimpleNamespace(
            position=(100, 100),
            status=None,
            carrier_id=None,
        ),
    )
    memory = TacticMemory()

    choose_actions(turn, memory)

    assert worker.actions and worker.actions[-1][0] == "MOVE"
    assert ranger.actions and ranger.actions[-1][0] == "MOVE"
    assert guard.actions == []
    assert memory.defense.level.name == "CLEAR"
    assert memory.contact_assessment.level.name == "THREATENING"
    assert memory.planned_reason_codes[worker.id] == "CONTACT_EVADE"
    assert memory.planned_reason_codes[ranger.id] == "CONTACT_INTERCEPT"
    assert memory.planned_reason_codes[guard.id] == "DEFENSE_HOLD"


def test_approach_core_recall_overrides_remote_contact_interception() -> None:
    core = FakeController(object_id=UUID(int=100), position=(0, 0), hp=5, shield=5)
    ranger = FakeController(
        object_id=UUID(int=2),
        position=(8, 0),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    guard = FakeController(
        object_id=UUID(int=3),
        position=(7, 0),
        hp=4,
        unit_type=UnitType.VANGUARD,
    )
    near_enemy = SimpleNamespace(
        id=UUID(int=200),
        kind="UNIT",
        unit_type=UnitType.VANGUARD,
        position=(2, 0),
        hp=4,
    )
    remote_enemy = SimpleNamespace(
        id=UUID(int=201),
        kind="UNIT",
        unit_type=UnitType.RANGER,
        position=(12, 0),
        hp=2,
    )
    turn = make_turn(
        core=core,
        units=(ranger, guard),
        enemies=(near_enemy, remote_enemy),
        resources=0,
        beacon=SimpleNamespace(
            position=(100, 100),
            status="CARRIED",
            carrier_id=core.id,
        ),
    )
    memory = TacticMemory()

    choose_actions(turn, memory)

    assert memory.defense.level.name in {"APPROACH", "ATTACK"}
    assert ranger.actions and ranger.actions[-1][0] == "MOVE"
    assert guard.actions and guard.actions[-1][0] == "MOVE"
    assert memory.contact_response is None
    assert memory.planned_reason_codes.get(ranger.id) != "CONTACT_INTERCEPT"
    assert memory.planned_reason_codes.get(guard.id) != "CONTACT_INTERCEPT"


def test_visible_enemy_in_legal_range_is_attacked_before_intercept_move() -> None:
    core = FakeController(object_id=UUID(int=100), position=(0, 5), hp=5, shield=5)
    ranger = FakeController(
        object_id=UUID(int=2),
        position=(0, 0),
        hp=2,
        unit_type=UnitType.RANGER,
    )
    enemy = SimpleNamespace(
        id=UUID(int=200),
        kind="UNIT",
        unit_type=UnitType.RANGER,
        position=(3, 0),
        hp=2,
    )
    turn = make_turn(
        core=core,
        units=(ranger,),
        enemies=(enemy,),
        resources=0,
        beacon=SimpleNamespace(
            position=(100, 100),
            status=None,
            carrier_id=None,
        ),
    )
    memory = TacticMemory()

    choose_actions(turn, memory)

    assert ranger.actions and ranger.actions[-1][0] == "SHOOT"
    assert memory.planned_reason_codes[ranger.id] == "CONTACT_ATTACK"


def test_hidden_contact_uses_three_tick_move_only_investigation_then_expires() -> None:
    memory = TacticMemory()
    current_ranger_position = (2, 0)
    current_worker_position = (-7, -9)
    for tick in range(20, 25):
        core = FakeController(
            object_id=UUID(int=100),
            position=(0, 0),
            hp=5,
            shield=5,
        )
        worker = FakeController(
            object_id=UUID(int=1),
            position=current_worker_position,
            hp=2,
            unit_type=UnitType.WORKER,
        )
        ranger = FakeController(
            object_id=UUID(int=2),
            position=current_ranger_position,
            hp=2,
            unit_type=UnitType.RANGER,
        )
        guard = FakeController(
            object_id=UUID(int=3),
            position=(1, 0),
            hp=4,
            unit_type=UnitType.VANGUARD,
        )
        enemies = ()
        if tick == 20:
            enemies = (
                SimpleNamespace(
                    id=UUID(int=200),
                    kind="UNIT",
                    unit_type=UnitType.RANGER,
                    position=(-10, -9),
                    hp=2,
                ),
            )
        turn = make_turn(
            core=core,
            units=(worker, ranger, guard),
            enemies=enemies,
            resources=0,
            beacon=SimpleNamespace(
                position=(100, 100),
                status=None,
                carrier_id=None,
            ),
        )
        turn.tick = tick
        choose_actions(turn, memory)
        if ranger.actions and ranger.actions[-1][0] == "MOVE":
            dx, dy = ranger.actions[-1][1].delta
            current_ranger_position = (
                current_ranger_position[0] + dx,
                current_ranger_position[1] + dy,
            )
        if worker.actions and worker.actions[-1][0] == "MOVE":
            dx, dy = worker.actions[-1][1].delta
            current_worker_position = (
                current_worker_position[0] + dx,
                current_worker_position[1] + dy,
            )
        if tick in {21, 22, 23}:
            assert all(action[0] != "SHOOT" for action in ranger.actions)
        if tick == 24:
            assert memory.contact.enemy_id is None
            assert memory.contact_response is None


def test_no_legal_evasion_or_intercept_records_controlled_wait_reason() -> None:
    core = FakeController(
        object_id=UUID(int=100),
        position=(10, 10),
        hp=5,
        shield=5,
    )
    worker = FakeController(
        object_id=UUID(int=1),
        position=(0, 1),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    enemy = SimpleNamespace(
        id=UUID(int=200),
        kind="UNIT",
        unit_type=UnitType.RANGER,
        position=(0, 4),
        hp=2,
    )
    turn = make_turn(
        core=core,
        units=(worker,),
        enemies=(enemy,),
        resources=0,
        obstacle_cells={(-1, 1), (1, 1), (0, 0)},
        beacon=SimpleNamespace(
            position=(100, 100),
            status=None,
            carrier_id=None,
        ),
    )
    memory = TacticMemory()

    choose_actions(turn, memory)

    assert worker.actions == []
    assert memory.planned_reason_codes[worker.id] == "CONTACT_WAIT_NO_SAFE_RESPONSE"
