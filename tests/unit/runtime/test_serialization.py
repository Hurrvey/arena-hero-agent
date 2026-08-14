from types import SimpleNamespace
from uuid import uuid4

from strategy_policy import StrategyProfile

from app.observability.redaction import PublicIdMapper
from app.runtime.models import RuntimeBatch
from app.runtime.service_factory import RuntimeServicesFactory
from app.runtime.serialization import (
    serialize_public_explanation,
    serialize_public_plan,
    serialize_resolution_events,
    serialize_resolution_service_payload,
    serialize_turn,
)
from app.storage import AdaptiveRepository, Database, MetricsRepository, RuntimeStore
from app.storage import StrategyRepository
from app.strategy.planner import (
    DecisionAction,
    DecisionExplanation,
    PlannerDiagnostics,
    PlannerResult,
)


def test_turn_projects_sdk_objects_into_dashboard_state() -> None:
    private_core = uuid4()
    private_worker = uuid4()
    private_enemy = uuid4()
    turn = SimpleNamespace(
        tick=100_217,
        state={
            "status": "ACTIVE",
            "resources": 3,
            "population": 9,
            "champion_beacon": {
                "position": (-1_130, -300),
                "status": "GROUND",
                "carrier_id": None,
            },
            "objects": [
                {
                    "kind": "OBSTACLE",
                    "positions": [(-1_140, -300), (-1_139, -300)],
                },
                {"kind": "RESOURCE", "positions": [(-1_136, -298)]},
                {
                    "kind": "CORE",
                    "id": private_core,
                    "controlled": True,
                    "position": (-1_139, -296),
                    "hp": 5,
                    "shield": 5,
                    "state": "NORMAL",
                },
                {
                    "kind": "UNIT",
                    "id": private_worker,
                    "controlled": True,
                    "position": (-1_138, -296),
                    "hp": 2,
                    "unit_type": "WORKER",
                    "cargo": 1,
                },
                {
                    "kind": "UNIT",
                    "id": private_enemy,
                    "controlled": False,
                    "position": (-1_135, -296),
                    "hp": 4,
                    "unit_type": "VANGUARD",
                },
            ],
            "events": [],
        },
    )

    raw, public = serialize_turn(turn, PublicIdMapper("session"))

    assert raw["tick"] == 100_217
    assert public["tick"] == 100_217
    assert public["resourceCapacity"] == 45
    assert public["core"] == {
        "kind": "CORE",
        "id": "E1",
        "controlled": True,
        "position": [-1_139, -296],
        "hp": 5,
        "shield": 5,
        "state": "NORMAL",
    }
    assert public["units"][0]["id"] == "E2"
    assert public["visibleEnemies"][0]["id"] == "E3"
    assert public["visibleEnemies"][0]["unitType"] == "VANGUARD"
    assert public["obstacleCells"] == [[-1_140, -300], [-1_139, -300]]
    assert public["resourceCells"] == [[-1_136, -298]]
    assert public["beacon"] == {
        "position": [-1_130, -300],
        "status": "GROUND",
        "carrierId": None,
    }
    assert str(private_core) not in str(public)
    assert str(private_worker) not in str(public)
    assert str(private_enemy) not in str(public)


def test_public_turn_does_not_duplicate_raw_resolution_events_or_usernames() -> None:
    turn = SimpleNamespace(
        tick=5,
        state={
            "status": "ACTIVE",
            "resources": 0,
            "population": 0,
            "objects": [],
            "events": [
                {
                    "event_id": uuid4(),
                    "event_type": "CORE_DESTROYED",
                    "values": {"destroyed_by": ["private-player"]},
                }
            ],
        },
    )

    _, public = serialize_turn(turn, PublicIdMapper("session"))

    assert "events" not in public
    assert "private-player" not in str(public)


def test_resolution_events_are_public_redacted_and_bound_to_previous_plan() -> None:
    private_actor = uuid4()
    turn = SimpleNamespace(
        tick=12,
        events=(
            SimpleNamespace(
                event_id=uuid4(),
                tick=11,
                event_type="HARVEST_SUCCEEDED",
                reason_code=None,
                actor_id=private_actor,
                target_id=None,
                position=(3, 4),
                values={"amount": 1},
            ),
        ),
    )

    events = serialize_resolution_events(turn, PublicIdMapper("session"))

    assert len(events) == 1
    event = events[0]
    assert event["plan_tick"] == 11
    assert event["event_type"] == "HARVEST_SUCCEEDED"
    assert event["short_id"].startswith("E")
    assert str(private_actor) not in str(event)


def test_resolution_event_tick_is_authoritative_for_plan_binding() -> None:
    turn = SimpleNamespace(
        tick=15,
        events=(
            SimpleNamespace(
                event_id=uuid4(),
                tick=12,
                event_type="MOVE_SUCCEEDED",
                reason_code=None,
                actor_id=uuid4(),
                target_id=None,
                position=(1, 2),
                values=None,
            ),
        ),
    )

    event = serialize_resolution_events(turn, PublicIdMapper("session"))[0]

    assert event["plan_tick"] == 12
    assert event["observed_tick"] == 15


def test_resolution_event_keeps_both_public_actor_and_target() -> None:
    actor = uuid4()
    target = uuid4()
    turn = SimpleNamespace(
        tick=15,
        events=(
            SimpleNamespace(
                event_type="SHOT_HIT",
                actor_id=actor,
                target_id=target,
                position=(1, 2),
                values={"damage": 1},
            ),
        ),
    )

    event = serialize_resolution_events(turn, PublicIdMapper("session"))[0]

    assert event["actor_id"] == "E1"
    assert event["target_id"] == "E2"
    assert event["short_id"] == "E1"


def test_resolution_service_payload_exposes_individual_public_results() -> None:
    events = (
        {
            "plan_tick": 12,
            "observed_tick": 13,
            "event_type": "SHOT_HIT",
            "actor_id": "E2",
            "target_id": "E9",
            "short_id": "E2",
            "position": [4, 5],
            "values": {"damage": 1},
        },
    )

    payload = serialize_resolution_service_payload(events)

    assert payload == {
        "count": 1,
        "planTicks": [12],
        "events": [
            {
                "planTick": 12,
                "observedTick": 13,
                "eventType": "SHOT_HIT",
                "actorId": "E2",
                "targetId": "E9",
                "shortId": "E2",
                "position": [4, 5],
                "values": {"damage": 1},
            }
        ],
    }


def test_resolution_values_remove_destroyed_by_usernames() -> None:
    turn = SimpleNamespace(
        tick=4,
        events=(
            SimpleNamespace(
                event_id=uuid4(),
                tick=3,
                event_type="CORE_DESTROYED",
                reason_code="ATTACK",
                actor_id=None,
                target_id=uuid4(),
                position=(1, 2),
                values={"destroyed_by": ["private-player"], "damage": 2},
            ),
        ),
    )

    event = serialize_resolution_events(turn, PublicIdMapper("session"))[0]

    assert event["values"] == {"damage": 2}
    assert "private-player" not in str(event)


def test_public_plan_replaces_uuid_dictionary_keys_with_short_ids() -> None:
    private_unit = uuid4()
    private_target = uuid4()
    raw_plan = {
        "tick": 7,
        "unit_actions": {
            str(private_unit): {
                "type": "SHOOT",
                "target_id": str(private_target),
            }
        },
        "core_action": None,
    }

    public = serialize_public_plan(raw_plan, PublicIdMapper("session"))

    assert public == {
        "tick": 7,
        "unitActions": {"E1": {"type": "SHOOT", "targetId": "E2"}},
        "coreAction": None,
    }
    assert str(private_unit) not in str(public)
    assert str(private_target) not in str(public)


def test_public_explanation_reuses_plan_short_id_and_hides_uuid_bytes() -> None:
    private_unit = uuid4()
    mapper = PublicIdMapper("session")
    public_plan = serialize_public_plan(
        {"tick": 4, "unit_actions": {str(private_unit): {"type": "MOVE"}}},
        mapper,
    )
    explanation = DecisionExplanation(
        actions=(
            DecisionAction(
                entity_id=private_unit.bytes,
                action_type="MOVE",
                reason_code="DEFEND_CORE",
                risk_before=2,
                risk_after=1,
                target=(2, 3),
            ),
        )
    )

    public = serialize_public_explanation(explanation, mapper)

    assert public_plan["unitActions"] == {"E1": {"type": "MOVE"}}
    assert public == {
        "actions": [
            {
                "entityId": "E1",
                "actionType": "MOVE",
                "reasonCode": "DEFEND_CORE",
                "riskBefore": 2,
                "riskAfter": 1,
                "target": [2, 3],
            }
        ]
    }
    assert private_unit.hex not in str(public)
    assert str(private_unit) not in str(public)


def test_post_submit_persistence_publishes_current_visibility_and_revision(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    runtime_store = RuntimeStore(database)
    strategies = StrategyRepository(database)
    strategies.ensure_initial(StrategyProfile.default())
    session = runtime_store.create_session(account_hash="hashed-account")
    persisted = []
    factory = RuntimeServicesFactory(
        settings=SimpleNamespace(),
        runtime_store=runtime_store,
        strategies=strategies,
        metrics=MetricsRepository(database),
        adaptive=AdaptiveRepository(database),
        broadcaster=SimpleNamespace(publish_committed=lambda event: None),
    )
    factory._session_id = session.session_id
    factory._mapper = PublicIdMapper(session.session_id)
    factory._exploration_runtime = SimpleNamespace(
        persist=lambda observation: persisted.append(observation) or 4
    )
    observation = SimpleNamespace(
        tick=7,
        current_cells=frozenset({(2, 1), (1, 1)}),
    )
    turn = SimpleNamespace(
        tick=7,
        state={
            "status": "ACTIVE",
            "resources": 0,
            "population": 0,
            "objects": [],
            "events": [],
        },
        events=(),
    )
    result = PlannerResult(
        tick=7,
        plan={"tick": 7},
        explanation=DecisionExplanation(),
        diagnostics=PlannerDiagnostics(),
    )

    factory.persist(
        RuntimeBatch(
            "TURN_SUBMITTED",
            7,
            turn=turn,
            result=result,
            source="AGENT",
            exploration=observation,
        )
    )

    assert persisted == [observation]
    assert runtime_store.current_state(session.session_id)["visibility"] == {
        "tick": 7,
        "currentCells": [[1, 1], [2, 1]],
        "explorationRevision": 4,
    }
