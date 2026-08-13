import pytest

from app.storage.adaptive_repository import AdaptiveRepository
from app.storage.database import Database
from app.storage.strategy_repository import StrategyRepository
from strategy_policy import StrategyProfile


def test_fixed_window_persists_normalized_score_and_exact_bounds(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    strategies = StrategyRepository(database)
    base = strategies.ensure_initial(StrategyProfile.default())
    repository = AdaptiveRepository(database)

    window = repository.close_window(
        start_tick=40,
        end_tick=100,
        sample_count=60,
        base_revision=base.revision,
        skill_fingerprint="a" * 64,
        raw_score=120.0,
        status="EVALUATED",
    )

    assert (window.start_tick, window.end_tick) == (40, 100)
    assert window.normalized_score == 2.0


def test_fixed_window_rejects_nonfinite_score_and_empty_bounds(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    repository = AdaptiveRepository(database)

    for start, end, score in ((5, 5, 1.0), (5, 4, 1.0), (0, 1, float("nan"))):
        with pytest.raises(ValueError):
            repository.close_window(
                start_tick=start,
                end_tick=end,
                sample_count=1,
                base_revision=1,
                skill_fingerprint="a" * 64,
                raw_score=score,
                status="EVALUATED",
            )
