from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from adaptive_strategy import SkillBundle
from app.adaptive.coordinator import SqliteAdaptiveCoordinator
from app.storage import AdaptiveRepository, Database, StrategyRepository
from strategy_policy import StrategyProfile


class FakeTransport:
    def __init__(self, fingerprint: str) -> None:
        self.fingerprint = fingerprint
        self.calls = 0

    def complete(self, **_kwargs) -> str:
        self.calls += 1
        if self.calls % 2:
            return json.dumps(
                {
                    "summary": "经济正常，Beacon 需要加强",
                    "strengths": ["经济"],
                    "deficits": ["Beacon"],
                    "rule_risks": [],
                    "recommended_changes": {"beacon_priority": 1.1},
                    "confidence": 0.8,
                    "skill_fingerprint": self.fingerprint,
                }
            )
        profile = StrategyProfile.default().with_updates(beacon_priority=1.1)
        return json.dumps(
            {
                "profile": profile.to_mapping(),
                "rationale": "提高 Beacon 权重",
                "expected_tradeoffs": ["少量经济机会成本"],
                "guardrails_acknowledged": True,
                "skill_fingerprint": self.fingerprint,
            }
        )


def build(tmp_path, *, interval=2, samples=2):
    database = Database(tmp_path / "agent.db")
    database.initialize()
    strategies = StrategyRepository(database)
    strategies.ensure_initial(StrategyProfile.default())
    repository = AdaptiveRepository(database)
    bundle = SkillBundle("f" * 64, "rules")
    coordinator = SqliteAdaptiveCoordinator(
        repository=repository,
        strategies=strategies,
        transport=FakeTransport(bundle.fingerprint),
        skill_bundle=bundle,
        evaluator_model="eval",
        designer_model="design",
        interval_ticks=interval,
        minimum_samples=samples,
        auto_apply=False,
    )
    return database, strategies, repository, coordinator


def test_restart_resumes_after_persisted_cursor_without_tick_zero_replay(tmp_path) -> None:
    _db, strategies, repository, first = build(tmp_path)
    revision = strategies.current().revision
    first.observe_projected(
        {"tick": 1, "metrics": {"beacon_ticks": 1}, "defense": {"defense_level": "CLEAR"}}, revision
    )
    first.observe_projected(
        {"tick": 2, "metrics": {"resources_harvested": 1}, "defense": {"defense_level": "CLEAR"}},
        revision,
    )
    first.wait_for_idle()
    assert repository.windows()[0].end_tick == 2
    first.close()

    _db2, strategies2, repository2, second = build(tmp_path)
    second.observe_projected(
        {"tick": 3, "metrics": {"resources_harvested": 1}, "defense": {"defense_level": "CLEAR"}},
        strategies2.current().revision,
    )
    second.wait_for_idle()
    assert len(repository2.windows()) == 1
    second.close()


def test_candidate_is_manual_and_stale_fingerprint_or_lethal_state_blocks_apply(tmp_path) -> None:
    _db, strategies, repository, coordinator = build(tmp_path)
    revision = strategies.current().revision
    coordinator.observe_projected(
        {"tick": 1, "metrics": {"beacon_ticks": 1}, "defense": {"defense_level": "CLEAR"}}, revision
    )
    coordinator.observe_projected(
        {"tick": 2, "metrics": {"resources_harvested": 1}, "defense": {"defense_level": "CLEAR"}},
        revision,
    )
    coordinator.wait_for_idle()
    candidate = repository.candidates()[0]

    assert candidate["status"] == "REVIEW_REQUIRED"
    assert (
        coordinator.apply_candidate(
            candidate["candidateId"], expected_revision=revision, current_defense="LETHAL"
        )["applied"]
        is False
    )
    coordinator.skill_bundle = SkillBundle("a" * 64, "changed")
    assert (
        coordinator.apply_candidate(
            candidate["candidateId"], expected_revision=revision, current_defense="CLEAR"
        )["reason"]
        == "SKILL_FINGERPRINT_CHANGED"
    )
    coordinator.close()


def test_applied_candidate_cannot_be_applied_or_rejected_again(tmp_path) -> None:
    _db, strategies, repository, coordinator = build(tmp_path)
    revision = strategies.current().revision
    coordinator.observe_projected(
        {"tick": 1, "metrics": {"beacon_ticks": 1}, "defense": {"defense_level": "CLEAR"}},
        revision,
    )
    coordinator.observe_projected(
        {"tick": 2, "metrics": {"resources_harvested": 1}, "defense": {"defense_level": "CLEAR"}},
        revision,
    )
    coordinator.wait_for_idle()
    candidate = repository.candidates()[0]
    first = coordinator.apply_candidate(
        candidate["candidateId"], expected_revision=revision, current_defense="CLEAR"
    )

    second = coordinator.apply_candidate(
        candidate["candidateId"], expected_revision=revision, current_defense="CLEAR"
    )

    assert first["applied"] is True
    assert second == {"applied": False, "reason": "CANDIDATE_STATE_PENDING_ACTIVATION"}
    assert repository.reject_candidate(candidate["candidateId"]) is False
    assert repository.candidate(candidate["candidateId"])["status"] == "PENDING_ACTIVATION"
    coordinator.close()


def test_concurrent_apply_keeps_one_pending_revision_and_never_marks_it_stale(tmp_path) -> None:
    _db, strategies, repository, coordinator = build(tmp_path)
    revision = strategies.current().revision
    coordinator.observe_projected(
        {"tick": 1, "metrics": {"beacon_ticks": 1}, "defense": {"defense_level": "CLEAR"}},
        revision,
    )
    coordinator.observe_projected(
        {"tick": 2, "metrics": {"resources_harvested": 1}, "defense": {"defense_level": "CLEAR"}},
        revision,
    )
    coordinator.wait_for_idle()
    candidate_id = repository.candidates()[0]["candidateId"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                coordinator.apply_candidate,
                candidate_id,
                expected_revision=revision,
                current_defense="CLEAR",
            )
            for _ in range(2)
        ]
    results = [future.result() for future in futures]

    candidate = repository.candidate(candidate_id)
    pending = strategies.pending()
    assert any(result["applied"] is True for result in results)
    assert candidate["status"] == "PENDING_ACTIVATION"
    assert pending is not None
    assert candidate["candidateRevision"] == pending.revision
    coordinator.close()


def test_candidate_and_pending_revision_roll_back_together_on_finalize_failure(tmp_path) -> None:
    database, strategies, repository, coordinator = build(tmp_path)
    revision = strategies.current().revision
    coordinator.observe_projected(
        {"tick": 1, "metrics": {"beacon_ticks": 1}, "defense": {"defense_level": "CLEAR"}},
        revision,
    )
    coordinator.observe_projected(
        {"tick": 2, "metrics": {"resources_harvested": 1}, "defense": {"defense_level": "CLEAR"}},
        revision,
    )
    coordinator.wait_for_idle()
    candidate = repository.candidates()[0]
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_candidate_finalize
            BEFORE UPDATE OF status ON adaptive_candidates
            WHEN NEW.status = 'PENDING_ACTIVATION'
            BEGIN
                SELECT RAISE(ABORT, 'simulated finalize failure');
            END
            """
        )
        connection.commit()

    try:
        repository.apply_candidate_revision(
            candidate["candidateId"],
            expected_revision=revision,
            profile=candidate["profile"],
        )
    except sqlite3.IntegrityError as exc:
        assert "simulated finalize failure" in str(exc)
    else:  # pragma: no cover - the trigger must exercise the rollback path
        raise AssertionError("fault injection did not abort the adaptive transaction")

    assert strategies.pending() is None
    assert repository.candidate(candidate["candidateId"])["status"] == "REVIEW_REQUIRED"
    coordinator.close()


def test_stale_apply_race_cannot_overwrite_successful_pending_candidate(tmp_path) -> None:
    _db, strategies, repository, coordinator = build(tmp_path)
    revision = strategies.current().revision
    coordinator.observe_projected(
        {"tick": 1, "metrics": {"beacon_ticks": 1}, "defense": {"defense_level": "CLEAR"}},
        revision,
    )
    coordinator.observe_projected(
        {"tick": 2, "metrics": {"resources_harvested": 1}, "defense": {"defense_level": "CLEAR"}},
        revision,
    )
    coordinator.wait_for_idle()
    candidate_id = repository.candidates()[0]["candidateId"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        correct = pool.submit(
            coordinator.apply_candidate,
            candidate_id,
            expected_revision=revision,
            current_defense="CLEAR",
        )
        stale = pool.submit(
            coordinator.apply_candidate,
            candidate_id,
            expected_revision=999,
            current_defense="CLEAR",
        )
    results = [correct.result(), stale.result()]

    candidate = repository.candidate(candidate_id)
    pending = strategies.pending()
    if pending is None:
        assert candidate["status"] == "STALE"
        assert all(result["applied"] is False for result in results)
    else:
        assert any(result.get("revision") == pending.revision for result in results)
        assert candidate["status"] == "PENDING_ACTIVATION"
        assert candidate["candidateRevision"] == pending.revision
    coordinator.close()
