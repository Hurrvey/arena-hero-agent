import json
import math

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


def test_projection_whitelists_bounded_exploration_and_contact_aggregates() -> None:
    record = {
        "exploration": {
            "newly_explored_cells": 4,
            "visible_cells": 57,
            "frontier_assignments": 2,
            "frontier_progress_ticks": 3,
            "oscillation_detections": 1,
            "oscillation_prevented_moves": 1,
            "scout_wait_ticks": 0,
            "frontier_coordinates": [[9, 9]],
            "account_scope": "never-send",
            "ignored_boolean": True,
            "ignored_negative": -1,
            "ignored_infinite": math.inf,
        },
        "contact": {
            "level": "THREATENING",
            "visible_enemy_count": 2,
            "threatened_workers": 1,
            "evading_workers": 1,
            "responding_combat_units": 1,
            "contact_attack_actions": 2,
            "contact_investigation_ticks": 3,
            "enemy_ids": ["never-send"],
            "last_seen_position": [9, 9],
            "ignored_boolean": True,
            "ignored_negative": -1,
            "ignored_infinite": math.inf,
            "ignored_label": "X" * 65,
        },
    }

    projected = project_record(record)
    encoded = json.dumps(projected, sort_keys=True)

    assert projected["exploration"] == {
        "frontier_assignments": 2.0,
        "frontier_progress_ticks": 3.0,
        "newly_explored_cells": 4.0,
        "oscillation_detections": 1.0,
        "oscillation_prevented_moves": 1.0,
        "scout_wait_ticks": 0.0,
        "visible_cells": 57.0,
    }
    assert projected["contact"] == {
        "contact_attack_actions": 2.0,
        "contact_investigation_ticks": 3.0,
        "evading_workers": 1.0,
        "level": "THREATENING",
        "responding_combat_units": 1.0,
        "threatened_workers": 1.0,
        "visible_enemy_count": 2.0,
    }
    for secret in ("never-send", "position", "coordinates", "account_scope", "ignored"):
        assert secret not in encoded
