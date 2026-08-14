import json
import os
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
        "sdk-quickstart.md",
        "sdk-reference.md",
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


def test_telemetry_contains_aggregate_economy_modes_without_targets_or_ids():
    from adaptive_strategy import TurnTelemetry

    turn = SimpleNamespace(
        tick=12,
        state=SimpleNamespace(status="ACTIVE", population=2, resources=0),
        events=(),
    )
    diagnostics = {
        "visible_resource_count": 0,
        "worker_modes": {"SCOUT": 1, "IDLE": 1},
        "idle_worker_ticks": 1,
        "route_stalls": 1,
        "oscillation_ticks": 1,
        "runner_progress_ticks": 0,
        "target": [999, 999],
        "worker_id": "private-id",
    }

    record = TurnTelemetry.from_turn(
        turn,
        SimpleNamespace(accepted=True),
        StrategyProfile.default(),
        diagnostics=diagnostics,
    )

    assert record["economy"] == {
        "visible_resource_count": 0,
        "worker_modes": {"IDLE": 1, "SCOUT": 1},
        "idle_worker_ticks": 1,
        "route_stalls": 1,
        "oscillation_ticks": 1,
        "runner_progress_ticks": 0,
    }
    assert "private-id" not in json.dumps(record["economy"])
    assert "999" not in json.dumps(record["economy"])


def test_telemetry_contains_bounded_defense_diagnostics():
    from adaptive_strategy import TurnTelemetry

    turn = SimpleNamespace(
        tick=13,
        state=SimpleNamespace(status="ACTIVE", population=3, resources=2),
        events=(),
    )
    diagnostics = {
        "defense_level": "ATTACK",
        "core_threat_ticks": 1,
        "projected_lethal_ticks": 0,
        "incoming_core_damage": 2,
        "defender_coverage": 2,
        "worker_evacuations": 1,
        "attacker_ids": ["private-enemy"],
        "core_position": [99, 99],
    }

    record = TurnTelemetry.from_turn(
        turn,
        SimpleNamespace(accepted=True),
        StrategyProfile.default(),
        diagnostics=diagnostics,
    )

    assert record["defense"] == {
        "defense_level": "ATTACK",
        "core_threat_ticks": 1,
        "projected_lethal_ticks": 0,
        "incoming_core_damage": 2,
        "defender_coverage": 2,
        "worker_evacuations": 1,
    }
    encoded = json.dumps(record["defense"])
    assert "private-enemy" not in encoded
    assert "99" not in encoded


def test_turn_telemetry_whitelists_exploration_and_contact_aggregates() -> None:
    from adaptive_strategy import TurnTelemetry, _prompt_record

    diagnostics = {
        "exploration": {
            "newly_explored_cells": 4,
            "visible_cells": 57,
            "frontier_assignments": 2,
            "frontier_progress_ticks": 2,
            "oscillation_detections": 1,
            "oscillation_prevented_moves": 1,
            "scout_wait_ticks": 0,
            "frontier_coordinates": [[9, 9]],
            "account_scope": "never-send",
        },
        "contact": {
            "level": "THREATENING",
            "visible_enemy_count": 2,
            "threatened_workers": 1,
            "evading_workers": 1,
            "responding_combat_units": 1,
            "contact_attack_actions": 0,
            "contact_investigation_ticks": 0,
            "enemy_ids": ["never-send"],
            "last_seen_position": [9, 9],
        },
    }
    turn = SimpleNamespace(
        tick=77,
        state=SimpleNamespace(status="ACTIVE", population=1, resources=0),
        events=(),
    )
    record = TurnTelemetry.from_turn(
        turn,
        SimpleNamespace(accepted=True),
        StrategyProfile.default(),
        diagnostics=diagnostics,
    )
    prompt = _prompt_record(record)
    encoded = json.dumps(prompt, sort_keys=True)

    assert prompt["exploration"]["newly_explored_cells"] == 4
    assert prompt["contact"]["level"] == "THREATENING"
    assert "never-send" not in encoded
    assert "position" not in encoded
    assert "coordinates" not in encoded


def test_scorecard_penalizes_core_damage_and_lethal_exposure():
    from adaptive_strategy import Scorecard

    owned = {
        "tick": 1,
        "core": {"id": "own-core"},
        "defense": {
            "defense_level": "LETHAL",
            "core_threat_ticks": 1,
            "projected_lethal_ticks": 1,
            "incoming_core_damage": 2,
            "defender_coverage": 1,
            "worker_evacuations": 1,
        },
        "events": [
            {
                **_event(
                    "core-hit",
                    "CORE_DAMAGED",
                    values={"damage": 2, "shield_damage": 1, "hp_damage": 1},
                ),
                "target_id": "own-core",
            }
        ],
    }
    enemy = {
        "tick": 2,
        "core": {"id": "own-core"},
        "events": [
            {
                **_event("enemy-hit", "CORE_DAMAGED", values={"damage": 9}),
                "target_id": "enemy-core",
            }
        ],
    }
    score = Scorecard.from_records([owned, enemy])

    mapping = score.to_mapping()
    assert score.core_threat_ticks == 1
    assert score.projected_lethal_ticks == 1
    assert score.incoming_core_damage == 2
    assert score.defender_coverage == 1
    assert score.worker_evacuations == 1
    assert score.core_damage_taken == 2
    assert mapping["internal_score"] < 0


def test_scorecard_keeps_destroyed_controlled_core_id_across_respawn():
    from adaptive_strategy import Scorecard

    records = [
        {"tick": 1, "core": {"id": "old-core"}, "events": []},
        {
            "tick": 2,
            "core": {"id": "new-core"},
            "events": [
                {
                    **_event("fatal", "CORE_DAMAGED", values={"damage": 5}),
                    "target_id": "old-core",
                }
            ],
        },
    ]

    assert Scorecard.from_records(records).core_damage_taken == 5


def test_scorecard_recovers_destroyed_core_id_when_window_starts_after_respawn():
    from adaptive_strategy import Scorecard

    record = {
        "tick": 2,
        "core": {"id": "new-core"},
        "events": [
            {
                **_event("fatal", "CORE_DAMAGED", values={"damage": 5}),
                "target_id": "old-core",
            },
            {
                **_event("destroyed", "CORE_DESTROYED", reason_code="ATTACK"),
                "target_id": "old-core",
            },
        ],
    }

    score = Scorecard.from_records([record])

    assert score.core_damage_taken == 5
    assert score.core_losses == 1


def test_llm_prompt_records_include_only_aggregate_defense_data():
    from adaptive_strategy import _bounded_prompt_records

    records, _ = _bounded_prompt_records([{
        "tick": 1,
        "defense": {
            "defense_level": "APPROACH",
            "core_threat_ticks": 1,
            "defender_coverage": 2,
            "attacker_id": "private-id",
            "target": [8, 8],
        },
    }])

    encoded = json.dumps(records)
    assert "APPROACH" in encoded
    assert "defender_coverage" in encoded
    assert "private-id" not in encoded
    assert "[8, 8]" not in encoded


def test_scorecard_counts_zero_resource_stalls_oscillation_and_progress():
    from adaptive_strategy import Scorecard

    score = Scorecard.from_records([
        {
            "tick": 1,
            "state": {"resources": 0},
            "economy": {
                "idle_worker_ticks": 1,
                "route_stalls": 2,
                "oscillation_ticks": 1,
                "runner_progress_ticks": 0,
            },
            "events": [],
        },
        {
            "tick": 2,
            "state": {"resources": 3},
            "economy": {
                "idle_worker_ticks": 0,
                "route_stalls": 0,
                "oscillation_ticks": 0,
                "runner_progress_ticks": 1,
            },
            "events": [],
        },
    ])

    assert score.zero_resource_ticks == 1
    assert score.idle_worker_ticks == 1
    assert score.route_stalls == 2
    assert score.oscillation_ticks == 1
    assert score.runner_progress_ticks == 1
    assert score.to_mapping()["internal_score"] < 0


def test_llm_prompt_records_remove_identifiers_coordinates_and_targets():
    from adaptive_strategy import _bounded_prompt_records

    records, _ = _bounded_prompt_records([{
        "tick": 1,
        "state": {"resources": 0, "population": 2},
        "core": {"id": "core-private", "owner_username": "private-user", "position": [9, 9], "hp": 5},
        "units": [{"id": "unit-private", "position": [8, 8], "unit_type": "WORKER", "cargo": 0}],
        "visible_enemies": [{"id": "enemy-private", "position": [7, 7], "unit_type": "RANGER"}],
        "events": [{"event_id": "event-private", "actor_id": "actor-private", "event_type": "HARVEST_SUCCEEDED", "values": {"amount": 1}}],
        "economy": {"worker_modes": {"SCOUT": 1}, "oscillation_ticks": 1},
    }])
    encoded = json.dumps(records)

    for private in ("core-private", "private-user", "unit-private", "enemy-private", "event-private", "actor-private", "[9, 9]", "[8, 8]", "[7, 7]"):
        assert private not in encoded
    assert "HARVEST_SUCCEEDED" in encoded
    assert "oscillation_ticks" in encoded


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


def test_default_skill_bundle_loads_the_project_packet():
    from adaptive_strategy import SkillBundle, _PROJECT_SKILL_ROOT

    bundle = SkillBundle.load()

    assert _PROJECT_SKILL_ROOT == Path(__file__).resolve().parent / "skills" / "arena-hero"
    assert "Arena Hero v0.14 game rules" in bundle.prompt_text
    assert "SDK |" in bundle.prompt_text


def test_project_skill_packet_precedes_legacy_user_roots(tmp_path, monkeypatch):
    from adaptive_strategy import SkillBundle, _PROJECT_SKILL_ROOT

    legacy = _minimal_skill_root(tmp_path / "legacy")
    (legacy / "SKILL.md").write_text("legacy marker", encoding="utf-8")
    monkeypatch.setattr("adaptive_strategy._LEGACY_SKILL_ROOTS", (legacy,))

    bundle = SkillBundle.load()

    assert _PROJECT_SKILL_ROOT.exists()
    assert "legacy marker" not in bundle.prompt_text


def test_both_llm_roles_receive_same_project_skill_fingerprint(tmp_path):
    from adaptive_strategy import AdaptiveCoordinator, SkillBundle

    fingerprint = SkillBundle.load().fingerprint
    transport = FakeTransport([
        _evaluation_json(fingerprint),
        _designer_json(worker_target=18, skill_fingerprint=fingerprint),
    ])
    coordinator = AdaptiveCoordinator(
        transport=transport,
        state_dir=tmp_path,
        interval_ticks=1,
        min_seconds=0,
        auto_apply=False,
    )
    coordinator.ingest_record({"tick": 1, "events": []})

    coordinator.run_cycle()

    assert len(transport.calls) == 2
    assert all(fingerprint in call.system for call in transport.calls)
    coordinator.close()


def test_both_llm_roles_are_told_to_balance_defense_beacon_and_economy(tmp_path):
    from adaptive_strategy import AdaptiveCoordinator, SkillBundle

    fingerprint = SkillBundle.load().fingerprint
    transport = FakeTransport([
        _evaluation_json(fingerprint),
        _designer_json(worker_target=18, skill_fingerprint=fingerprint),
    ])
    coordinator = AdaptiveCoordinator(
        transport=transport,
        state_dir=tmp_path,
        interval_ticks=1,
        min_seconds=0,
        auto_apply=False,
    )
    coordinator.ingest_record({"tick": 1, "events": []})

    coordinator.run_cycle()

    evaluator, designer = transport.calls
    assert "Core defense/survival" in evaluator.system
    assert "Beacon control" in evaluator.system
    assert "economic growth" in evaluator.system
    assert "preserve the Core" in designer.system
    assert "permanent turtle" in designer.system
    coordinator.close()


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


def test_invalid_activation_baseline_does_not_mutate_profile(tmp_path):
    from adaptive_strategy import AdaptiveCoordinator

    coordinator = AdaptiveCoordinator(FakeTransport([]), tmp_path)
    before = coordinator.current_profile()
    candidate = before.with_updates(worker_target=3)
    with pytest.raises(ValueError):
        coordinator.activate_profile(candidate, baseline_score=float("inf"))
    assert coordinator.current_profile() == before
    coordinator.close()


def test_observe_snapshot_persists_profile_used_for_the_turn(tmp_path):
    from adaptive_strategy import AdaptiveCoordinator

    coordinator = AdaptiveCoordinator(
        FakeTransport([]), tmp_path, interval_ticks=100, min_seconds=900
    )
    snapshot = StrategyProfile.default().with_updates(worker_target=3)
    coordinator.observe_snapshot(
        SimpleNamespace(tick=1, state=SimpleNamespace(status="ACTIVE"), events=()),
        SimpleNamespace(accepted=True),
        snapshot,
    )
    rows = coordinator.store.records_since(0)
    assert rows[0]["profile"]["worker_target"] == 3
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


def test_disabled_factory_is_used_without_opt_in(tmp_path, monkeypatch):
    from adaptive_strategy import AdaptiveCoordinator, DisabledAdaptiveCoordinator

    monkeypatch.setattr("adaptive_strategy._DEFAULT_DOTENV_PATH", tmp_path / "missing.env")
    monkeypatch.delenv("ARENA_HERO_ADAPTIVE", raising=False)
    monkeypatch.delenv("ARENA_HERO_LLM_API_KEY", raising=False)
    coordinator = AdaptiveCoordinator.from_env()
    assert isinstance(coordinator, DisabledAdaptiveCoordinator)
    assert coordinator.current_profile() == StrategyProfile.default()
    coordinator.close()


def test_load_dotenv_reads_arena_settings_without_overriding_process_env(tmp_path, monkeypatch):
    from adaptive_strategy import load_dotenv

    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\ufeff# local-only settings\n"
        "export ARENA_HERO_ADAPTIVE = 1\n"
        "ARENA_HERO_LLM_API_KEY='file-secret'\n"
        "ARENA_HERO_LLM_BASE_URL=\"https://llm.example/v1#stable\"\n"
        "ARENA_HERO_ADAPTIVE_INTERVAL_TICKS=30 # comment\n"
        "NOT_ARENA_SETTING=should-not-load\n"
        "ARENA_HERO_BROKEN='unterminated\n"
        "ARENA_HERO_NUL=bad\x00value\n",
        encoding="utf-8",
    )
    for name in (
        "ARENA_HERO_ADAPTIVE",
        "ARENA_HERO_LLM_API_KEY",
        "ARENA_HERO_LLM_BASE_URL",
        "ARENA_HERO_ADAPTIVE_INTERVAL_TICKS",
        "NOT_ARENA_SETTING",
        "ARENA_HERO_BROKEN",
        "ARENA_HERO_NUL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ARENA_HERO_LLM_API_KEY", "process-secret")

    load_dotenv(dotenv)

    assert os.environ["ARENA_HERO_ADAPTIVE"] == "1"
    assert os.environ["ARENA_HERO_LLM_API_KEY"] == "process-secret"
    assert os.environ["ARENA_HERO_LLM_BASE_URL"] == "https://llm.example/v1#stable"
    assert os.environ["ARENA_HERO_ADAPTIVE_INTERVAL_TICKS"] == "30"
    assert "NOT_ARENA_SETTING" not in os.environ
    assert "ARENA_HERO_BROKEN" not in os.environ
    assert "ARENA_HERO_NUL" not in os.environ


def test_env_factory_loads_an_explicit_dotenv_file_and_keeps_optional_defaults(
    tmp_path, monkeypatch
):
    from adaptive_strategy import AdaptiveCoordinator, DisabledAdaptiveCoordinator

    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "ARENA_HERO_ADAPTIVE=true\n"
        "ARENA_HERO_LLM_API_KEY=llm-file-key\n"
        "ARENA_HERO_EVALUATOR_MODEL=critic-file\n"
        "ARENA_HERO_DESIGNER_MODEL=designer-file\n"
        "ARENA_HERO_LLM_MODEL_VERBOSITY=HIGH\n"
        "ARENA_HERO_LLM_MODEL_REASONING_EFFORT=xhigh\n"
        "ARENA_HERO_LLM_BASE_URL=\n"
        f"ARENA_HERO_ADAPTIVE_STATE_DIR={tmp_path / 'state'}\n",
        encoding="utf-8",
    )
    for name in (
        "ARENA_HERO_ADAPTIVE",
        "ARENA_HERO_LLM_API_KEY",
        "ARENA_HERO_EVALUATOR_MODEL",
        "ARENA_HERO_DESIGNER_MODEL",
        "ARENA_HERO_LLM_MODEL_VERBOSITY",
        "ARENA_HERO_LLM_MODEL_REASONING_EFFORT",
        "ARENA_HERO_LLM_BASE_URL",
        "ARENA_HERO_ADAPTIVE_STATE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ARENA_HERO_LLM_MODEL_VERBOSITY", "low")

    coordinator = AdaptiveCoordinator.from_env(dotenv)
    assert isinstance(coordinator, AdaptiveCoordinator)
    assert not isinstance(coordinator, DisabledAdaptiveCoordinator)
    assert coordinator.transport.api_key == "llm-file-key"
    assert coordinator.evaluator_model == "critic-file"
    assert coordinator.designer_model == "designer-file"
    assert coordinator.transport.base_url == "https://api.openai.com/v1"
    assert coordinator.transport.model_verbosity == "low"
    assert coordinator.transport.model_reasoning_effort == "xhigh"
    assert coordinator.state_dir == tmp_path / "state"
    coordinator.close()


def test_env_factory_ignores_invalid_optional_model_controls(tmp_path, monkeypatch):
    from adaptive_strategy import AdaptiveCoordinator

    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "ARENA_HERO_ADAPTIVE=1\n"
        "ARENA_HERO_LLM_API_KEY=llm-file-key\n"
        "ARENA_HERO_EVALUATOR_MODEL=critic\n"
        "ARENA_HERO_DESIGNER_MODEL=designer\n"
        "ARENA_HERO_LLM_MODEL_VERBOSITY=novel-length\n"
        "ARENA_HERO_LLM_MODEL_REASONING_EFFORT=maximum-ish\n"
        f"ARENA_HERO_ADAPTIVE_STATE_DIR={tmp_path / 'state'}\n",
        encoding="utf-8",
    )
    for name in (
        "ARENA_HERO_ADAPTIVE",
        "ARENA_HERO_LLM_API_KEY",
        "ARENA_HERO_EVALUATOR_MODEL",
        "ARENA_HERO_DESIGNER_MODEL",
        "ARENA_HERO_LLM_MODEL_VERBOSITY",
        "ARENA_HERO_LLM_MODEL_REASONING_EFFORT",
        "ARENA_HERO_ADAPTIVE_STATE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    coordinator = AdaptiveCoordinator.from_env(dotenv)
    assert coordinator.transport.model_verbosity is None
    assert coordinator.transport.model_reasoning_effort is None
    coordinator.close()


def test_env_factory_uses_separate_llm_credential_and_background_defaults(tmp_path, monkeypatch):
    from adaptive_strategy import AdaptiveCoordinator, DisabledAdaptiveCoordinator

    monkeypatch.setenv("ARENA_HERO_ADAPTIVE", "1")
    monkeypatch.setenv("ARENA_HERO_LLM_API_KEY", "llm-only-secret")
    monkeypatch.setenv("ARENA_HERO_EVALUATOR_MODEL", "critic")
    monkeypatch.setenv("ARENA_HERO_DESIGNER_MODEL", "architect")
    monkeypatch.setenv("ARENA_HERO_ADAPTIVE_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("ARENA_HERO_API_KEY", raising=False)

    coordinator = AdaptiveCoordinator.from_env()
    assert isinstance(coordinator, AdaptiveCoordinator)
    assert not isinstance(coordinator, DisabledAdaptiveCoordinator)
    assert coordinator.transport.api_key == "llm-only-secret"
    assert coordinator.evaluator_model == "critic"
    assert coordinator.designer_model == "architect"
    assert coordinator.min_seconds == 900.0
    coordinator.close()


def test_env_factory_defaults_to_project_adaptive_directory_from_any_cwd(
    tmp_path, monkeypatch
):
    from adaptive_strategy import AdaptiveCoordinator, _PROJECT_ROOT

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARENA_HERO_ADAPTIVE", "1")
    monkeypatch.setenv("ARENA_HERO_LLM_API_KEY", "llm-only-secret")
    monkeypatch.setenv("ARENA_HERO_EVALUATOR_MODEL", "critic")
    monkeypatch.setenv("ARENA_HERO_DESIGNER_MODEL", "architect")
    monkeypatch.delenv("ARENA_HERO_ADAPTIVE_STATE_DIR", raising=False)

    coordinator = AdaptiveCoordinator.from_env(tmp_path / "missing.env")

    assert coordinator.state_dir == _PROJECT_ROOT / "adaptive"
    coordinator.close()


def test_env_factory_resolves_relative_state_directory_from_project_root(
    tmp_path, monkeypatch
):
    from adaptive_strategy import AdaptiveCoordinator, _PROJECT_ROOT

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARENA_HERO_ADAPTIVE", "1")
    monkeypatch.setenv("ARENA_HERO_LLM_API_KEY", "llm-only-secret")
    monkeypatch.setenv("ARENA_HERO_EVALUATOR_MODEL", "critic")
    monkeypatch.setenv("ARENA_HERO_DESIGNER_MODEL", "architect")
    monkeypatch.setenv("ARENA_HERO_ADAPTIVE_STATE_DIR", "adaptive-relative")

    coordinator = AdaptiveCoordinator.from_env(tmp_path / "missing.env")

    assert coordinator.state_dir == _PROJECT_ROOT / "adaptive-relative"
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


def test_openai_transport_sends_model_verbosity_and_reasoning_effort(monkeypatch):
    from adaptive_strategy import OpenAICompatibleTransport

    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, *args):
            return b'{"choices":[{"message":{"content":"{}"}}]}'

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("adaptive_strategy.urlrequest.urlopen", fake_urlopen)
    transport = OpenAICompatibleTransport(
        "https://example.invalid/v1",
        "top-secret",
        model_verbosity="HIGH",
        model_reasoning_effort="xhigh",
    )

    assert transport.complete(model="m", system="s", user="u") == "{}"
    assert captured["payload"]["verbosity"] == "high"
    assert captured["payload"]["reasoning_effort"] == "xhigh"
    assert "temperature" not in captured["payload"]
    assert captured["timeout"] == 30.0


def test_openai_transport_rejects_invalid_direct_model_controls():
    from adaptive_strategy import OpenAICompatibleTransport

    with pytest.raises(ValueError, match="model_verbosity"):
        OpenAICompatibleTransport(
            "https://example.invalid/v1", "secret", model_verbosity="verbose"
        )
    with pytest.raises(ValueError, match="model_reasoning_effort"):
        OpenAICompatibleTransport(
            "https://example.invalid/v1",
            "secret",
            model_reasoning_effort="maximum",
        )


def test_openai_transport_keeps_legacy_temperature_when_controls_are_unset(monkeypatch):
    from adaptive_strategy import OpenAICompatibleTransport

    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, *args):
            return b'{"choices":[{"message":{"content":"{}"}}]}'

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return Response()

    monkeypatch.setattr("adaptive_strategy.urlrequest.urlopen", fake_urlopen)
    transport = OpenAICompatibleTransport("https://example.invalid/v1", "secret")

    assert transport.complete(model="m", system="s", user="u") == "{}"
    assert captured["payload"]["temperature"] == 0
    assert "verbosity" not in captured["payload"]
    assert "reasoning_effort" not in captured["payload"]


def test_openai_transport_omits_temperature_for_verbosity_only(monkeypatch):
    from adaptive_strategy import OpenAICompatibleTransport

    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, *args):
            return b'{"choices":[{"message":{"content":"{}"}}]}'

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return Response()

    monkeypatch.setattr("adaptive_strategy.urlrequest.urlopen", fake_urlopen)
    OpenAICompatibleTransport(
        "https://example.invalid/v1", "secret", model_verbosity="high"
    ).complete(model="m", system="s", user="u")

    assert captured["payload"]["verbosity"] == "high"
    assert "temperature" not in captured["payload"]


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


def test_readme_documents_adaptive_safety_contract():
    text = Path("README.md").read_text(encoding="utf-8")
    for phrase in (
        "ARENA_HERO_ADAPTIVE",
        "ARENA_HERO_LLM_API_KEY",
        "回滚",
        "不会执行 LLM 生成的 Python",
    ):
        assert phrase in text
