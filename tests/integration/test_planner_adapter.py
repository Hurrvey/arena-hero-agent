from types import SimpleNamespace
from uuid import UUID

from arena_hero import Direction, UnitType
from arena_hero.actions import CommandPlan, MoveAction

from app.strategy.planner import DecisionAction, DecisionExplanation, PlannerDiagnostics
from app.strategy.planner_adapter import apply_planner_result, plan_turn
from balanced_tactic import TacticMemory
from strategy_policy import StrategyProfile


class FakeController:
    def __init__(self, object_id: UUID) -> None:
        self.id = object_id
        self.position = (0, 0)
        self.hp = 2
        self.shield = 5
        self.unit_type = UnitType.WORKER
        self.cargo = 0
        self.view = SimpleNamespace(
            id=object_id,
            position=(0, 0),
            hp=2,
            shield=5,
            unit_type=UnitType.WORKER,
            state="NORMAL",
        )
        self.actions: list[tuple[object, ...]] = []

    def move(self, direction: Direction) -> None:
        self.actions.append(("MOVE", direction))

    def harvest(self) -> None:
        self.actions.append(("HARVEST",))

    def deposit(self) -> None:
        self.actions.append(("DEPOSIT",))

    def heal(self) -> None:
        self.actions.append(("HEAL",))

    def pickup_beacon(self) -> None:
        self.actions.append(("PICKUP_BEACON",))


def fake_turn() -> SimpleNamespace:
    worker = FakeController(UUID(int=1))
    core = FakeController(UUID(int=2))
    core.hp = 5
    core.spawn = lambda unit_type: core.actions.append(("SPAWN", unit_type))
    core.repair_shield = lambda: core.actions.append(("REPAIR_SHIELD",))
    state = SimpleNamespace(population=1, status="ACTIVE")
    turn = SimpleNamespace(
        tick=7,
        state=state,
        resources=0,
        resource_space=10,
        core=core,
        units=(worker,),
        workers=(worker,),
        vanguards=(),
        rangers=(),
        visible_enemies=(),
        resource_cells=frozenset({(0, 0)}),
        obstacle_cells=frozenset(),
        beacon=SimpleNamespace(position=(100, 100), status=None, carrier_id=None),
        events=(),
    )
    return turn


def test_same_turn_memory_and_profile_produce_identical_result() -> None:
    profile = StrategyProfile.default()
    first = plan_turn(fake_turn(), TacticMemory(), profile)
    second = plan_turn(fake_turn(), TacticMemory(), profile)

    assert first.public_mapping() == second.public_mapping()


def test_result_contains_public_action_reason_and_risk_delta() -> None:
    result = plan_turn(fake_turn(), TacticMemory(), StrategyProfile.default())

    assert result.tick == 7
    assert result.explanation.actions
    action = result.explanation.actions[0]
    assert action.action_type == "HARVEST"
    assert action.reason_code == "CURRENT_RESOURCE_HARVEST"
    assert action.risk_before == 0
    assert action.risk_after == 0


def test_defense_diagnostic_uses_named_threat_level() -> None:
    result = plan_turn(fake_turn(), TacticMemory(), StrategyProfile.default())

    assert result.diagnostics.defense["level"] == "CLEAR"


def test_frontier_reason_is_preserved_in_public_explanation() -> None:
    turn = fake_turn()
    turn.resource_cells = frozenset()
    turn.beacon = SimpleNamespace(
        position=(100, 100),
        status="CARRIED",
        carrier_id=turn.core.id,
    )
    turn.core.shield = 10
    memory = TacticMemory()

    result = plan_turn(turn, memory, StrategyProfile.default())

    assert result.explanation.actions
    assert result.explanation.actions[0].reason_code == "SCOUT_FRONTIER"
    assert result.diagnostics.exploration["frontier_assignments"] == 1


def test_validation_failure_degrades_one_entity_to_wait_without_second_submit() -> None:
    entity_id = UUID(int=1)
    result = SimpleNamespace(
        tick=4,
        plan=CommandPlan(
            tick=4,
            unit_actions={entity_id: MoveAction(direction=Direction.RIGHT)},
        ),
        explanation=DecisionExplanation(
            (
                DecisionAction(
                    entity_id=entity_id.bytes,
                    action_type="MOVE",
                    reason_code="TASK_PROGRESS",
                    risk_before=0,
                    risk_after=0,
                ),
            )
        ),
        diagnostics=PlannerDiagnostics(),
    )
    turn = SimpleNamespace(clear_calls=0, moves=[], waits=[])
    turn.clear = lambda: setattr(turn, "clear_calls", turn.clear_calls + 1)
    unit = SimpleNamespace(
        move=lambda direction: turn.moves.append(direction),
        wait=lambda: turn.waits.append(entity_id),
    )
    turn.unit = lambda ignored: unit

    apply_planner_result(turn, result, validator=lambda _plan: (_ for _ in ()).throw(ValueError()))

    assert turn.clear_calls == 1
    assert turn.moves == []
    assert turn.waits == [entity_id]
