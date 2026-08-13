from types import SimpleNamespace
from uuid import uuid4

from app.observability.redaction import PublicIdMapper
from app.runtime.serialization import (
    serialize_public_explanation,
    serialize_public_plan,
    serialize_resolution_events,
)
from app.strategy.planner import DecisionAction, DecisionExplanation


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
