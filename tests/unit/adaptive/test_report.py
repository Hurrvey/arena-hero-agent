from types import SimpleNamespace

from app.api.adaptive import _report
from app.storage import Database, StrategyRepository
from strategy_policy import StrategyProfile


def test_report_marks_candidate_stale_when_active_revision_changed(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    strategies = StrategyRepository(database)
    base = strategies.ensure_initial(StrategyProfile.default())
    pending = strategies.create_revision(
        expected_revision=base.revision,
        profile=base.profile.with_updates(worker_target=22),
        source="USER",
        reason="new strategy",
    )
    strategies.activate_pending(tick=2)
    window = SimpleNamespace(
        cycle_id="cycle",
        start_tick=1,
        end_tick=2,
        sample_count=30,
        base_revision=base.revision,
        candidate_revision=None,
        skill_fingerprint="f" * 64,
        raw_score=1.0,
        normalized_score=0.5,
        status="REVIEW_REQUIRED",
    )
    candidate = {
        "candidateId": "candidate",
        "status": "REVIEW_REQUIRED",
        "profile": pending.profile.to_mapping(),
    }

    report = _report(window, candidate, "f" * 64, strategies)

    assert report["status"] == "STALE"
    assert report["disabledReason"] == "基准策略版本已变化，候选已过期"


def test_report_marks_activated_candidate_applied_instead_of_stale(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    strategies = StrategyRepository(database)
    base = strategies.ensure_initial(StrategyProfile.default())
    pending = strategies.create_revision(
        expected_revision=base.revision,
        profile=base.profile.with_updates(worker_target=22),
        source="ADAPTIVE",
        reason="candidate",
    )
    strategies.activate_pending(tick=3)
    window = SimpleNamespace(
        cycle_id="cycle",
        start_tick=1,
        end_tick=2,
        sample_count=30,
        base_revision=base.revision,
        candidate_revision=pending.revision,
        skill_fingerprint="f" * 64,
        raw_score=1.0,
        normalized_score=0.5,
        status="PENDING_ACTIVATION",
    )
    candidate = {
        "candidateId": "candidate",
        "status": "PENDING_ACTIVATION",
        "profile": pending.profile.to_mapping(),
    }

    report = _report(window, candidate, "f" * 64, strategies)

    assert report["status"] == "APPLIED"
    assert "disabledReason" not in report
