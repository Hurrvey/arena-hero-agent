from app.adaptive.scoring import score_window, select_window


def test_window_is_start_exclusive_end_inclusive_and_score_is_per_tick() -> None:
    records = [
        {"tick": 10, "metrics": {"resources_harvested": 99}},
        {"tick": 11, "metrics": {"beacon_ticks": 1}},
        {"tick": 15, "metrics": {"resources_harvested": 5}},
        {"tick": 16, "metrics": {"resources_harvested": 99}},
    ]

    selected = select_window(records, start_tick=10, end_tick=15)
    result = score_window(selected, start_tick=10, end_tick=15)

    assert [record["tick"] for record in selected] == [11, 15]
    assert result.sample_count == 2
    assert result.raw_score == 15
    assert result.score_per_tick == 7.5


def test_negative_baseline_rollback_threshold_is_symmetric() -> None:
    from app.adaptive.scoring import is_regression

    assert is_regression(baseline=-100, canary=-116, ratio=0.15) is True
    assert is_regression(baseline=-100, canary=-90, ratio=0.15) is False
