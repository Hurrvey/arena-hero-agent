import pytest

from strategy_policy import StrategyProfile, internal_score


def test_default_profile_preserves_beacon_and_economy_floor():
    profile = StrategyProfile.default()
    assert profile.beacon_priority >= 0.75
    assert profile.economy_priority >= 0.75
    assert profile.worker_target >= 2


def test_profile_rejects_unknown_or_out_of_range_fields():
    with pytest.raises(ValueError):
        StrategyProfile.from_mapping({"beacon_priority": 2.0})
    with pytest.raises(ValueError):
        StrategyProfile.from_mapping({"unexpected": 1})


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
