"""Exact fixed-window selection and deterministic Arena Hero scoring."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from strategy_policy import internal_score

from .models import WindowScore


def select_window(
    records: Iterable[Mapping[str, object]],
    *,
    start_tick: int,
    end_tick: int,
) -> list[Mapping[str, object]]:
    if end_tick <= start_tick:
        raise ValueError("window end must follow start")
    selected = [
        record
        for record in records
        if type(record.get("tick")) is int and start_tick < int(record["tick"]) <= end_tick
    ]
    return sorted(selected, key=lambda record: int(record["tick"]))


def score_window(
    records: Iterable[Mapping[str, object]],
    *,
    start_tick: int,
    end_tick: int,
) -> WindowScore:
    selected = list(records)
    totals: dict[str, float] = {}
    for record in selected:
        metrics = record.get("metrics")
        if not isinstance(metrics, Mapping):
            continue
        for name, value in metrics.items():
            if isinstance(name, str) and isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[name] = totals.get(name, 0.0) + float(value)
    raw_score = internal_score(totals)
    sample_count = len(selected)
    return WindowScore(
        start_tick,
        end_tick,
        sample_count,
        raw_score,
        raw_score / max(1, sample_count),
    )


def is_regression(*, baseline: float, canary: float, ratio: float) -> bool:
    if not 0 <= ratio <= 1:
        raise ValueError("rollback ratio must be within [0, 1]")
    return canary < baseline - abs(baseline) * ratio
