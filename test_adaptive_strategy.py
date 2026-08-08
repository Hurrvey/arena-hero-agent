import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from strategy_policy import StrategyProfile


def _event(event_id, event_type, *, values=None, reason_code=None):
    event = {"event_id": event_id, "event_type": event_type}
    if values is not None:
        event["values"] = values
    if reason_code is not None:
        event["reason_code"] = reason_code
    return event


def _minimal_skill_root(root: Path) -> Path:
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text("Arena Hero skill", encoding="utf-8")
    for name in (
        "game-rules.md",
        "reference-numbers.md",
        "reference-glossary.md",
        "tactic-authoring.md",
        "reference-source-and-version.md",
        "api-resolution-results.md",
    ):
        (root / "references" / name).write_text(name, encoding="utf-8")
    return root


def test_scorecard_counts_beacon_economy_combat_and_failures():
    from adaptive_strategy import Scorecard

    record = {
        "tick": 10,
        "beacon": {"status": "CARRIED", "controlled": True},
        "events": [
            _event("a", "BEACON_PICKED_UP"),
            _event("b", "BEACON_HARVEST_BONUS", values={"amount": 1}),
            _event("c", "HARVEST_SUCCEEDED", values={"amount": 2}),
            _event("d", "DEPOSIT_SUCCEEDED", values={"amount": 2}),
            _event("e", "SHOT_HIT", values={"damage": 1}),
            _event("f", "DESTRUCTION_PARTICIPATION", reason_code="CORE"),
            _event("g", "HARVEST_FAILED", reason_code="RESOURCE_DEPLETED"),
        ],
    }
    score = Scorecard.from_records([record, record])
    assert score.beacon_ticks_observed == 1
    assert score.beacon_bonus_resources == 1
    assert score.resources_harvested == 2
    assert score.resources_deposited == 2
    assert score.damage_dealt == 1
    assert score.core_participations == 1
    assert score.failed_actions == 1


def test_scorecard_counts_sweep_targets_and_ignores_unknown_events():
    from adaptive_strategy import Scorecard

    score = Scorecard.from_records(
        [{"tick": 1, "events": [
            _event("sweep", "SWEEP_RESOLVED", values={"targets_hit": 3}),
            _event("future", "FUTURE_EVENT", values={"damage": 99}),
        ]}]
    )
    assert score.sweep_resolved == 1
    assert score.damage_dealt == 3
    assert score.failed_actions == 0


def test_scorecard_does_not_treat_core_self_destruct_as_combat_loss():
    from adaptive_strategy import Scorecard

    score = Scorecard.from_records([{
        "tick": 1,
        "events": [
            _event("self", "CORE_DESTROYED", reason_code="SELF_DESTRUCT"),
            _event("attack", "CORE_DESTROYED", reason_code="ATTACK"),
        ],
    }])
    assert score.core_losses == 1


def test_scorecard_counts_unit_damaged_to_zero_as_unit_loss():
    from adaptive_strategy import Scorecard

    score = Scorecard.from_records([{
        "tick": 1,
        "events": [_event("kill", "UNIT_DAMAGED", values={"damage": 4, "hp": 0})],
    }])
    assert score.units_lost == 1


def test_turn_telemetry_contains_no_api_key_or_authorization_header():
    from adaptive_strategy import TurnTelemetry

    class Turn:
        tick = 7
        events = ()
        state = SimpleNamespace(status="ACTIVE", population=2, resources=3)

    record = TurnTelemetry.from_turn(
        Turn(), SimpleNamespace(accepted=True), StrategyProfile.default()
    )
    encoded = json.dumps(record)
    assert "ARENA_HERO_API_KEY" not in encoded
    assert "Authorization" not in encoded
    assert "secret" not in encoded.lower()


def test_turn_telemetry_whitelists_fields_and_json_serializes_model_dump():
    from adaptive_strategy import TurnTelemetry

    class Model:
        api_key = "secret"

        def model_dump(self, *, mode):
            assert mode == "json"
            return {"status": "ACTIVE", "population": 2, "resources": 5}

    class Turn:
        tick = 8
        state = Model()
        events = ()
        leaked = "must not appear"

    record = TurnTelemetry.from_turn(
        Turn(), SimpleNamespace(accepted=True), StrategyProfile.default()
    )
    encoded = json.dumps(record)
    assert "must not appear" not in encoded
    assert "secret" not in encoded
    assert record["state"]["population"] == 2


def test_turn_telemetry_marks_beacon_controlled_only_for_visible_owned_carrier():
    from adaptive_strategy import Scorecard, TurnTelemetry

    core = SimpleNamespace(id="core", controlled=True, position=(0, 0))
    unit = SimpleNamespace(id="carrier", controlled=True, position=(1, 0), hp=5, unit_type="WORKER")
    turn = SimpleNamespace(
        tick=9,
        state=SimpleNamespace(status="ACTIVE", population=1, resources=0),
        core=core,
        units=(unit,),
        visible_enemies=(),
        beacon=SimpleNamespace(status="CARRIED", carrier_id="carrier"),
        events=(),
    )
    record = TurnTelemetry.from_turn(turn, SimpleNamespace(accepted=True), StrategyProfile.default())
    assert record["beacon"]["controlled"] is True
    assert Scorecard.from_records([record]).beacon_ticks_observed == 1

    hidden = SimpleNamespace(**{**turn.__dict__, "beacon": SimpleNamespace(status=None, carrier_id=None)})
    hidden_record = TurnTelemetry.from_turn(hidden, SimpleNamespace(accepted=True), StrategyProfile.default())
    assert "controlled" not in hidden_record.get("beacon", {})
    assert Scorecard.from_records([hidden_record]).beacon_ticks_observed == 0


def test_skill_bundle_fingerprint_changes_when_rules_change(tmp_path):
    from adaptive_strategy import SkillBundle

    root = _minimal_skill_root(tmp_path / "skill")
    first = SkillBundle.load(root)
    (root / "references" / "game-rules.md").write_text("changed", encoding="utf-8")
    second = SkillBundle.load(root)
    assert first.fingerprint != second.fingerprint
    assert first.fingerprint in first.prompt_text
    assert "rules" in first.prompt_text.lower()


def test_skill_bundle_rejects_missing_documents(tmp_path):
    from adaptive_strategy import SkillBundle, SkillBundleError

    root = _minimal_skill_root(tmp_path / "skill")
    (root / "references" / "game-rules.md").unlink()
    with pytest.raises(SkillBundleError):
        SkillBundle.load(root)


def test_skill_bundle_wraps_invalid_utf8_as_skill_bundle_error(tmp_path):
    from adaptive_strategy import SkillBundle, SkillBundleError

    root = _minimal_skill_root(tmp_path / "skill")
    (root / "SKILL.md").write_bytes(b"\xff")
    with pytest.raises(SkillBundleError):
        SkillBundle.load(root)


def test_telemetry_store_appends_queries_and_writes_atomic_report(tmp_path):
    from adaptive_strategy import TelemetryStore

    store = TelemetryStore(tmp_path / "turns.jsonl")
    store.append({"tick": 1, "events": []})
    store.append({"tick": 3, "events": []})
    assert [row["tick"] for row in store.records_since(1)] == [3]
    report = store.write_report("cycle", {"ok": True})
    assert report.exists()
    assert json.loads(report.read_text(encoding="utf-8"))["ok"] is True


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, *, model, system, user, timeout=None):
        self.calls.append(SimpleNamespace(model=model, system=system, user=user, timeout=timeout))
        return self.responses.pop(0)


def _evaluation_json(skill_fingerprint=None):
    payload = {
        "summary": "steady",
        "strengths": ["beacon"],
        "deficits": ["economy"],
        "rule_risks": [],
        "recommended_changes": {"worker_target": 3},
        "confidence": 0.8,
    }
    if skill_fingerprint is not None:
        payload["skill_fingerprint"] = skill_fingerprint
    return json.dumps(payload)


def _designer_json(worker_target=3, skill_fingerprint=None):
    payload = {
        "profile": StrategyProfile.default().with_updates(worker_target=worker_target).to_mapping(),
        "rationale": "more workers",
        "expected_tradeoffs": ["less combat"],
        "guardrails_acknowledged": True,
    }
    if skill_fingerprint is not None:
        payload["skill_fingerprint"] = skill_fingerprint
    return json.dumps(payload)


def _sample_record(tick=1):
    return {"tick": tick, "events": [], "beacon": {"status": "IDLE"}}


def test_cycle_calls_evaluator_then_designer_and_applies_profile(tmp_path):
    from adaptive_strategy import AdaptiveCoordinator, SkillBundle
    fingerprint = SkillBundle.load().fingerprint
    transport = FakeTransport([
        _evaluation_json(fingerprint),
        _designer_json(worker_target=3, skill_fingerprint=fingerprint),
    ])
    coordinator = AdaptiveCoordinator(
        transport=transport, state_dir=tmp_path, interval_ticks=1,
        min_seconds=0, evaluator_model="critic", designer_model="architect",
        auto_apply=True,
    )
    coordinator.ingest_record(_sample_record(1))
    coordinator.run_cycle()
    assert [call.model for call in transport.calls] == ["critic", "architect"]
    assert coordinator.current_profile().worker_target == 3
    coordinator.close()


def test_invalid_designer_json_keeps_previous_profile(tmp_path):
    from adaptive_strategy import AdaptiveCoordinator, SkillBundle
    transport = FakeTransport([_evaluation_json(SkillBundle.load().fingerprint), "not json"])
    coordinator = AdaptiveCoordinator(transport=transport, state_dir=tmp_path, min_seconds=0, auto_apply=True)
    coordinator.ingest_record(_sample_record(1))
    coordinator.run_cycle()
    assert coordinator.current_profile() == StrategyProfile.default()
    coordinator.close()


def test_canary_score_drop_restores_previous_profile(tmp_path):
    from adaptive_strategy import AdaptiveCoordinator
    coordinator = AdaptiveCoordinator(transport=FakeTransport([]), state_dir=tmp_path, rollback_ratio=0.15)
    coordinator.activate_profile(StrategyProfile.default(), baseline_score=100.0)
    coordinator.record_canary_score(70.0)
    coordinator.rollback_if_needed()
    assert coordinator.current_profile() == StrategyProfile.default()
    coordinator.close()


def test_coordinator_due_check_does_not_block_observation(tmp_path):
    from adaptive_strategy import AdaptiveCoordinator
    coordinator = AdaptiveCoordinator(transport=FakeTransport([]), state_dir=tmp_path, interval_ticks=60)
    started = time.monotonic()
    coordinator.observe(SimpleNamespace(tick=1, state=SimpleNamespace(status="ACTIVE"), events=()), SimpleNamespace(accepted=True))
    assert time.monotonic() - started < 0.2
    coordinator.close()


def test_parse_json_object_rejects_prefix_and_trailing_text():
    from adaptive_strategy import parse_json_object

    with pytest.raises(ValueError):
        parse_json_object("Here is the result: {\"ok\": true}")
    with pytest.raises(ValueError):
        parse_json_object('{"ok": true} trailing prose')
    with pytest.raises(ValueError):
        parse_json_object('[1, 2]')


def test_evaluator_requires_current_skill_fingerprint():
    from adaptive_strategy import validate_evaluation

    payload = {
        "summary": "steady",
        "strengths": [],
        "deficits": [],
        "rule_risks": [],
        "recommended_changes": {},
        "confidence": 0.5,
    }
    with pytest.raises(ValueError):
        validate_evaluation(payload, skill_fingerprint="current-fingerprint")


def test_negative_baseline_rollback_uses_absolute_regression(tmp_path):
    from adaptive_strategy import AdaptiveCoordinator

    coordinator = AdaptiveCoordinator(
        transport=FakeTransport([]), state_dir=tmp_path, rollback_ratio=0.15
    )
    candidate = StrategyProfile.default().with_updates(worker_target=3)
    coordinator.activate_profile(candidate, baseline_score=-100.0)
    coordinator.record_canary_score(-90.0)
    assert coordinator.rollback_if_needed() is False
    coordinator.record_canary_score(-120.0)
    assert coordinator.rollback_if_needed() is True
    assert coordinator.current_profile() == StrategyProfile.default()
    coordinator.close()


def test_scorecard_ignores_nonfinite_or_negative_event_numbers():
    from adaptive_strategy import Scorecard

    score = Scorecard.from_records([{
        "tick": 1,
        "events": [
            _event("nan", "HARVEST_SUCCEEDED", values={"amount": float("nan")}),
            _event("negative", "DEPOSIT_SUCCEEDED", values={"amount": -3}),
            _event("damage", "SHOT_HIT", values={"damage": float("inf")}),
        ],
    }])
    assert score.resources_harvested == 0
    assert score.resources_deposited == 0
    assert score.damage_dealt == 0
    assert score.to_mapping()["internal_score"] == 0


def test_disabled_factory_is_used_without_opt_in(monkeypatch):
    from adaptive_strategy import AdaptiveCoordinator, DisabledAdaptiveCoordinator

    monkeypatch.delenv("ARENA_HERO_ADAPTIVE", raising=False)
    monkeypatch.delenv("ARENA_HERO_LLM_API_KEY", raising=False)
    coordinator = AdaptiveCoordinator.from_env()
    assert isinstance(coordinator, DisabledAdaptiveCoordinator)
    assert coordinator.current_profile() == StrategyProfile.default()
    coordinator.close()


def test_openai_transport_rejects_malformed_choices_without_leaking_details(monkeypatch):
    from adaptive_strategy import LLMError, OpenAICompatibleTransport

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, *args):
            return b'{"choices": {"0": {}}}'

    monkeypatch.setattr("adaptive_strategy.urlrequest.urlopen", lambda *args, **kwargs: Response())
    with pytest.raises(LLMError, match="LLM request failed"):
        OpenAICompatibleTransport("https://example.invalid/v1", "top-secret").complete(
            model="m", system="s", user="u"
        )


def test_cycle_bounds_untrusted_records_before_llm_prompt(tmp_path, monkeypatch):
    from adaptive_strategy import AdaptiveCoordinator, SkillBundle

    bundle = SkillBundle("fingerprint", "rules")
    monkeypatch.setattr("adaptive_strategy.SkillBundle.load", classmethod(lambda cls, root=None: bundle))
    transport = FakeTransport([
        json.dumps({
            "summary": "steady", "strengths": [], "deficits": [],
            "rule_risks": [], "recommended_changes": {}, "confidence": 0.5,
            "skill_fingerprint": "fingerprint",
        }),
        json.dumps({
            "profile": StrategyProfile.default().to_mapping(),
            "rationale": "unchanged", "expected_tradeoffs": [],
            "guardrails_acknowledged": True, "skill_fingerprint": "fingerprint",
        }),
    ])
    coordinator = AdaptiveCoordinator(
        transport=transport, state_dir=tmp_path, interval_ticks=1,
        min_seconds=0, auto_apply=False,
    )
    for tick in range(1, 401):
        coordinator.ingest_record({"tick": tick, "events": [{"text": "x" * 2000}]})
    coordinator.run_cycle()
    assert transport.calls
    assert len(transport.calls[0].user) <= 120_000
    coordinator.close()


def test_observe_fails_open_when_telemetry_storage_is_unavailable(tmp_path, monkeypatch):
    from adaptive_strategy import AdaptiveCoordinator

    coordinator = AdaptiveCoordinator(FakeTransport([]), tmp_path, min_seconds=0)
    monkeypatch.setattr(coordinator, "ingest_record", lambda record: (_ for _ in ()).throw(OSError("disk")))
    coordinator.observe(
        SimpleNamespace(tick=1, state=SimpleNamespace(status="ACTIVE"), events=()),
        SimpleNamespace(accepted=True),
    )
    coordinator.close()


def test_coordinator_rejects_nonfinite_rollback_ratio(tmp_path):
    from adaptive_strategy import AdaptiveCoordinator

    with pytest.raises(ValueError):
        AdaptiveCoordinator(FakeTransport([]), tmp_path, rollback_ratio=float("nan"))


def test_coordinator_interval_is_measured_in_complete_ticks(tmp_path):
    from adaptive_strategy import AdaptiveCoordinator

    coordinator = AdaptiveCoordinator(
        FakeTransport([]), tmp_path, interval_ticks=60, min_seconds=0
    )
    coordinator.ingest_record({"tick": 59, "events": []})
    assert coordinator._due() is False
    coordinator.ingest_record({"tick": 60, "events": []})
    assert coordinator._due() is True
    coordinator.close()
