from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from arena_hero import Direction, UnitType

from balanced_tactic import choose_actions


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

    def pickup_beacon(self) -> None:
        self._record("PICKUP_BEACON")

    def repair_shield(self) -> None:
        self._record("REPAIR_SHIELD")

    def spawn(self, unit_type: UnitType) -> None:
        self._record("SPAWN", unit_type)


def make_turn(
    *,
    core: FakeController | None,
    units: tuple[FakeController, ...] = (),
    resources: int = 0,
    upkeep_next_tick: int = 0,
    resource_cells: set[tuple[int, int]] | tuple[tuple[int, int], ...] = (),
    obstacle_cells: set[tuple[int, int]] | tuple[tuple[int, int], ...] = (),
    enemies: tuple[SimpleNamespace, ...] = (),
    beacon: SimpleNamespace | None = None,
) -> SimpleNamespace:
    workers = tuple(unit for unit in units if unit.unit_type is UnitType.WORKER)
    vanguards = tuple(unit for unit in units if unit.unit_type is UnitType.VANGUARD)
    rangers = tuple(unit for unit in units if unit.unit_type is UnitType.RANGER)
    capacity = max(10, len(units) * 5)
    state = SimpleNamespace(
        population=len(units),
        upkeep_next_tick=upkeep_next_tick,
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
        or SimpleNamespace(position=(0, 0), status=None, carrier_id=None),
        events=(),
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

    assert ranger.actions == [("SHOOT", (0, 0))]


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
    )

    choose_actions(turn)

    assert ranger.actions == []


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
    turn = make_turn(core=core, units=(worker,), resources=5, resource_cells={(1, 0)})

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
    turn = make_turn(core=core, units=(worker,), resources=5)

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
        position=(0, 0),
        hp=2,
        unit_type=UnitType.WORKER,
    )
    turn = make_turn(
        core=core,
        units=(worker,),
        resources=5,
        resource_cells={(-5, 0), (1, 0)},
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
    turn = make_turn(
        core=core,
        units=(higher_id_worker, lower_id_worker),
        resources=5,
        resource_cells={(1, 0)},
    )

    choose_actions(turn)

    assert lower_id_worker.actions == [("HARVEST",)]
    assert higher_id_worker.actions != [("HARVEST",)]
