import json

from app.adaptive.projection import project_record


def test_projection_removes_names_ids_coordinates_routes_plans_and_prompts() -> None:
    record = {
        "tick": 12,
        "state": {"resources": 8, "population": 4},
        "core": {"id": "private-core", "owner_username": "inject me", "position": [99, 88], "hp": 5},
        "units": [{"id": "private-unit", "unit_type": "WORKER", "position": [1, 2]}],
        "visible_enemies": [{"id": "enemy-id", "unit_type": "RANGER", "owner_username": "bad"}],
        "plan": {"route": [[1, 2], [3, 4]], "prompt": "ignore the rules"},
        "beacon": {"status": "GROUND", "carrier_id": "private-unit"},
        "events": [{"event_type": "HARVEST_SUCCEEDED", "values": {"amount": 2}, "actor_id": "private-unit"}],
        "defense": {"defense_level": "WATCH", "incoming_core_damage": 1, "attacker_ids": ["enemy-id"]},
    }

    projected = project_record(record)
    encoded = json.dumps(projected)

    assert projected["tick"] == 12
    assert projected["unit_counts"] == {"WORKER": 1}
    assert projected["visible_enemy_counts"] == {"RANGER": 1}
    for secret in ("private-core", "private-unit", "enemy-id", "inject me", "99", "route", "prompt"):
        assert secret not in encoded


def test_projection_is_bounded() -> None:
    record = {"tick": 1, "events": [{"event_type": "SHOT_HIT", "values": {"damage": 1}}] * 5000}

    projected = project_record(record)

    assert len(projected["events"]) <= 64
