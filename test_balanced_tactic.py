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
    choose_actions,
    load_api_key,
    play,
)
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


def test_play_submits_one_complete_plan_for_each_turn(monkeypatch, capsys) -> None:
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

    assert core.actions == [("SPAWN", UnitType.RANGER)]


def test_unknown_beacon_dispatches_runner_without_speculative_pickup() -> None:
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

    choose_actions(turn)

    assert worker.actions == [("MOVE", Direction.RIGHT)]
    assert all(action[0] != "PICKUP_BEACON" for action in worker.actions)


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


def test_combat_unit_moves_toward_visible_enemy_core_when_out_of_range() -> None:
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

    assert ranger.actions == [("MOVE", Direction.DOWN)]


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

    assert core.actions == [("SPAWN", UnitType.RANGER)]


def test_two_worker_economy_builds_capacity_bridge_before_ranger() -> None:
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

    assert core.actions == [("SPAWN", UnitType.VANGUARD)]


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


def test_core_cell_worker_does_not_roam_without_spawn_pressure() -> None:
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

    choose_actions(turn)

    assert worker.actions == []
    assert core.actions == []


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


def test_capacity_limited_deposit_is_not_double_counted_for_dynamic_spawn() -> None:
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

    # Capacity is 10, so only two of the five cargo resources can settle;
    # 8 + 2 is below the Ranger price at population 2 (12).
    assert cargo_worker.actions == [("DEPOSIT",)]
    assert all(action[0] != "SPAWN" for action in core.actions)


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
    assert core.actions == [("SPAWN", UnitType.RANGER)]


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
    assert core.actions == [("SPAWN", UnitType.WORKER)]


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

    assert core.actions == [("SPAWN", UnitType.VANGUARD)]


def test_cargo_deposit_is_not_suppressed_by_an_unplanned_safe_worker_retreat() -> None:
    """Do not price a later Worker death before that Worker gets its MOVE."""

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

    # At N=20 the current Ranger price is 16.  The threatened remote Worker
    # safely retreats, so it must not make the earlier cargo Worker believe a
    # cheaper N=19 replacement is already funded.  Deposit the resource now;
    # the remaining Core-cell occupant correctly prevents SPAWN this Tick.
    assert cargo_worker.actions == [("DEPOSIT",)]
    assert retreating_worker.actions and retreating_worker.actions[0][0] == "MOVE"
    assert core.actions == []
