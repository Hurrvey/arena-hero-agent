import json
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
