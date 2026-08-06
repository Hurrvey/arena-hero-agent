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
