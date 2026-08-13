from __future__ import annotations

import json

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
    first.observe_projected({"tick": 1, "metrics": {"beacon_ticks": 1}, "defense": {"defense_level": "CLEAR"}}, revision)
    first.observe_projected({"tick": 2, "metrics": {"resources_harvested": 1}, "defense": {"defense_level": "CLEAR"}}, revision)
    first.wait_for_idle()
    assert repository.windows()[0].end_tick == 2
    first.close()

    _db2, strategies2, repository2, second = build(tmp_path)
    second.observe_projected({"tick": 3, "metrics": {"resources_harvested": 1}, "defense": {"defense_level": "CLEAR"}}, strategies2.current().revision)
    second.wait_for_idle()
    assert len(repository2.windows()) == 1
    second.close()


def test_candidate_is_manual_and_stale_fingerprint_or_lethal_state_blocks_apply(tmp_path) -> None:
    _db, strategies, repository, coordinator = build(tmp_path)
    revision = strategies.current().revision
    coordinator.observe_projected({"tick": 1, "metrics": {"beacon_ticks": 1}, "defense": {"defense_level": "CLEAR"}}, revision)
    coordinator.observe_projected({"tick": 2, "metrics": {"resources_harvested": 1}, "defense": {"defense_level": "CLEAR"}}, revision)
    coordinator.wait_for_idle()
    candidate = repository.candidates()[0]

    assert candidate["status"] == "REVIEW_REQUIRED"
    assert coordinator.apply_candidate(candidate["candidateId"], expected_revision=revision, current_defense="LETHAL")["applied"] is False
    coordinator.skill_bundle = SkillBundle("a" * 64, "changed")
    assert coordinator.apply_candidate(candidate["candidateId"], expected_revision=revision, current_defense="CLEAR")["reason"] == "SKILL_FINGERPRINT_CHANGED"
    coordinator.close()
