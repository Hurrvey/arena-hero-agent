import pytest

from strategy_policy import StrategyProfile, internal_score


def test_default_profile_preserves_beacon_and_economy_floor():
    profile = StrategyProfile.default()
    assert profile.beacon_priority >= 0.75
    assert profile.economy_priority >= 0.75
    assert profile.worker_target == 23
    assert profile.bootstrap_worker_target == 6
    assert profile.near_beacon_radius == 12
    assert profile.runner_stall_ticks == 6
    assert profile.resource_memory_ttl == 64
    assert profile.resource_stall_ticks == 6
    assert profile.scout_ring_step == 10
    assert profile.defense_priority == 1.0
    assert profile.defender_vanguard_target == 1
    assert profile.defender_ranger_target == 2
    assert profile.defense_watch_radius == 5
    assert profile.worker_evacuation_radius == 3


def test_profile_rejects_unknown_or_out_of_range_fields():
    with pytest.raises(ValueError):
        StrategyProfile.from_mapping({"beacon_priority": 2.0})
    with pytest.raises(ValueError):
        StrategyProfile.from_mapping({"unexpected": 1})
    with pytest.raises(ValueError):
        StrategyProfile.from_mapping({"bootstrap_worker_target": 1})
    with pytest.raises(ValueError):
        StrategyProfile.from_mapping({"runner_stall_ticks": 99})


def test_profile_round_trip_includes_bounded_dominance_parameters():
    profile = StrategyProfile.default().with_updates(
        worker_target=18,
        bootstrap_worker_target=7,
        near_beacon_radius=10,
        runner_stall_ticks=5,
        resource_memory_ttl=80,
        resource_stall_ticks=7,
        scout_ring_step=12,
        defense_priority=1.25,
        defender_vanguard_target=2,
        defender_ranger_target=3,
        defense_watch_radius=7,
        worker_evacuation_radius=4,
    )

    assert StrategyProfile.from_mapping(profile.to_mapping()) == profile


def test_profile_rejects_out_of_range_defense_controls():
    for changes in (
        {"defense_priority": 2.0},
        {"defender_vanguard_target": 0},
        {"defender_ranger_target": 5},
        {"defense_watch_radius": 3},
        {"worker_evacuation_radius": 6},
    ):
        with pytest.raises(ValueError):
            StrategyProfile.from_mapping(changes)


def test_profile_round_trips_as_json_safe_mapping():
    profile = StrategyProfile.default()
    assert StrategyProfile.from_mapping(profile.to_mapping()) == profile


def test_internal_score_keeps_beacon_and_survival_separate_from_economy():
    beacon = internal_score({"beacon_ticks": 3})
    economy = internal_score({"resources_harvested": 20})
    assert beacon > economy


def test_profile_rejects_wrong_types_nonfinite_and_schema_versions():
    with pytest.raises(ValueError):
        StrategyProfile.from_mapping({"worker_target": True})
    with pytest.raises(ValueError):
        StrategyProfile.from_mapping({"ranger_ratio": float("nan")})
    with pytest.raises(ValueError):
        StrategyProfile.from_mapping({"schema_version": 2})


def test_internal_score_applies_all_metric_weights():
    metrics = {
        "beacon_ticks": 1,
        "resources_harvested": 2,
        "resources_deposited": 3,
        "resources_captured": 4,
        "damage_dealt": 5,
        "core_participations": 1,
        "units_lost": 2,
        "core_losses": 1,
        "failed_actions": 2,
    }
    assert internal_score(metrics) == pytest.approx(10 + 2 + 3 + 4 + 5 + 20 - 8 - 100 - 1)


def test_direct_constructor_validates_invariants():
    with pytest.raises(ValueError):
        StrategyProfile(beacon_priority=2.0)
    with pytest.raises(ValueError):
        StrategyProfile(worker_target=True)


def test_profile_rejects_non_string_mapping_keys_without_sorting_error():
    with pytest.raises(ValueError):
        StrategyProfile.from_mapping({"unexpected": 1, 7: 2})


def test_internal_score_uses_exact_beacon_weight_without_tiebreaker():
    assert internal_score({"beacon_ticks": 2}) == 20.0


def test_internal_score_rejects_negative_observation_counts():
    with pytest.raises(ValueError):
        internal_score({"beacon_ticks": -1})


def test_internal_score_penalizes_economic_stagnation_and_rewards_progress():
    stalled = internal_score({
        "zero_resource_ticks": 10,
        "idle_worker_ticks": 4,
        "route_stalls": 3,
        "oscillation_ticks": 2,
    })
    progressing = internal_score({
        "resources_harvested": 4,
        "resources_deposited": 4,
        "runner_progress_ticks": 4,
    })

    assert stalled < 0
    assert progressing > 8


def test_internal_score_values_defense_without_rewarding_permanent_turtling():
    exposed = internal_score({
        "core_threat_ticks": 2,
        "projected_lethal_ticks": 1,
        "core_damage_taken": 3,
    })
    defended = internal_score({
        "defender_coverage": 3,
        "worker_evacuations": 1,
    })

    assert exposed < -10
    assert 0 < defended < 2
