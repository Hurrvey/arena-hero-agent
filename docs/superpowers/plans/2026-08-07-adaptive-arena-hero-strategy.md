# Adaptive Arena Hero Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in, asynchronous evaluator/designer loop that scores Beacon and economy performance under the bundled Arena Hero v0.14 rules and safely applies a bounded, rollback-capable strategy profile.

**Architecture:** Keep `balanced_tactic.py` as the deterministic action authority. Add `strategy_policy.py` for an immutable bounded profile and `adaptive_strategy.py` for redacted telemetry, deterministic scorecards, skill-bundle prompts, an OpenAI-compatible HTTP adapter, and the two-role coordinator. The play loop reads one immutable profile at each Turn boundary and hands a serialized snapshot to the background coordinator only after submission.

**Tech Stack:** Python 3.11+, standard-library dataclasses/json/urllib/threading, official `arena-hero` SDK 0.2.9, pytest, local bundled Arena Hero v0.14 documentation.

## Global Constraints

- Use only current authoritative Turn state and `turn.events`; never infer fogged Beacon, enemy, resource, UUID, or map facts.
- Never call an LLM or perform blocking disk/network work before the current Turn plan is submitted.
- Never execute, import, or apply LLM-generated Python; candidates are versioned JSON profiles only.
- Preserve the official `arena-hero>=0.2.9,<0.3` dependency and SDK connection/retry implementation.
- Keep Beacon and economy floors: `beacon_priority >= 0.75`, `economy_priority >= 0.75`, and `worker_target >= 2`.
- All new tests run without `ARENA_HERO_API_KEY`, `ARENA_HERO_LLM_API_KEY`, or a live Arena Hero connection.
- Keep runtime state under `.codex_tmp/adaptive/`; never commit telemetry, prompts, or credentials.
- Run the focused tests after each task and `python -m pytest -q`, `python -m compileall -q .`, `python -m pip check`, and `git diff --check` before completion.

---

### Task 1: Bounded strategy profile

**Files:**
- Create: `strategy_policy.py`
- Create: `test_strategy_policy.py`

**Interfaces:**
- Produce `StrategyProfile` as a frozen dataclass with fields `schema_version`, `beacon_priority`, `economy_priority`, `combat_priority`, `worker_target`, `ranger_ratio`, `carrier_safety_margin`, and `spawn_aggression`.
- Produce `StrategyProfile.from_mapping(mapping) -> StrategyProfile`, `to_mapping() -> dict[str, object]`, `default() -> StrategyProfile`, and `validate() -> None`.
- Produce `internal_score(metrics: Mapping[str, float]) -> float` with explicit Beacon/economy/combat/survival weights.

- [ ] **Step 1: Write the failing tests**

```python
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
    beacon = internal_score({"beacon_ticks": 2})
    economy = internal_score({"resources_harvested": 20})
    assert beacon > economy
```

- [ ] **Step 2: Run the focused tests to verify RED**

Run: `python -m pytest -q test_strategy_policy.py`

Expected: collection/import failure because `strategy_policy.py` does not yet
exist.

- [ ] **Step 3: Implement the minimal profile and validator**

Implement the frozen dataclass with exact bounds:

```python
PROFILE_BOUNDS = {
    "beacon_priority": (0.75, 1.50),
    "economy_priority": (0.75, 1.50),
    "combat_priority": (0.50, 1.25),
    "ranger_ratio": (1.0, 3.0),
    "spawn_aggression": (0.0, 1.0),
}

PROFILE_INT_BOUNDS = {
    "worker_target": (2, 3),
    "carrier_safety_margin": (0, 1),
}
```

Reject unknown keys, non-finite numbers, wrong types, unsupported schema
versions, and values outside the bounds. `internal_score()` must use these
weights: Beacon ticks × 10, resources harvested/deposited/captured × 1,
damage × 1, Core participation × 20, units lost × −4, Core losses × −100,
and failed actions × −0.5.

- [ ] **Step 4: Run the focused tests to verify GREEN**

Run: `python -m pytest -q test_strategy_policy.py`

Expected: all profile tests pass.

- [ ] **Step 5: Commit**

```powershell
git add strategy_policy.py test_strategy_policy.py
git commit -m "feat: add bounded adaptive strategy profile"
```

### Task 2: Telemetry, scorecard, and skill packet

**Files:**
- Create: `adaptive_strategy.py`
- Create: `test_adaptive_strategy.py`

**Interfaces:**
- Produce `TurnTelemetry.from_turn(turn, accepted, profile) -> dict[str, object]`.
- Produce `Scorecard.ingest(record) -> None`, `Scorecard.to_mapping() -> dict`, and `Scorecard.from_records(records) -> Scorecard`.
- Produce `SkillBundle.load(root: Path | None = None) -> SkillBundle` with `fingerprint` and `prompt_text` fields.
- Produce `TelemetryStore(path).append(record)`, `.records_since(tick)`, and `.write_report(name, payload)`.

- [ ] **Step 1: Write failing tests for event accounting and redaction**

```python
def test_scorecard_counts_beacon_economy_combat_and_failures():
    record = {"tick": 10, "beacon": {"status": "CARRIED", "controlled": True},
              "events": [
                  {"event_id": "a", "event_type": "BEACON_PICKED_UP"},
                  {"event_id": "b", "event_type": "BEACON_HARVEST_BONUS", "values": {"amount": 1}},
                  {"event_id": "c", "event_type": "HARVEST_SUCCEEDED", "values": {"amount": 2}},
                  {"event_id": "d", "event_type": "DEPOSIT_SUCCEEDED", "values": {"amount": 2}},
                  {"event_id": "e", "event_type": "SHOT_HIT", "values": {"damage": 1}},
                  {"event_id": "f", "event_type": "DESTRUCTION_PARTICIPATION", "reason_code": "CORE"},
                  {"event_id": "g", "event_type": "HARVEST_FAILED", "reason_code": "RESOURCE_DEPLETED"},
              ]}
    score = Scorecard.from_records([record, record])
    assert score.beacon_ticks_observed == 1
    assert score.beacon_bonus_resources == 1
    assert score.resources_harvested == 2
    assert score.resources_deposited == 2
    assert score.damage_dealt == 1
    assert score.core_participations == 1
    assert score.failed_actions == 1


def test_turn_telemetry_contains_no_api_key_or_authorization_header(fake_turn):
    record = TurnTelemetry.from_turn(fake_turn, SimpleNamespace(accepted=True), StrategyProfile.default())
    encoded = json.dumps(record)
    assert "ARENA_HERO_API_KEY" not in encoded
    assert "Authorization" not in encoded


def test_skill_bundle_fingerprint_changes_when_rules_change(tmp_path):
    root = make_minimal_skill_root(tmp_path)
    first = SkillBundle.load(root)
    (root / "references" / "game-rules.md").write_text("changed", encoding="utf-8")
    second = SkillBundle.load(root)
    assert first.fingerprint != second.fingerprint
```

- [ ] **Step 2: Run focused tests to verify RED**

Run: `python -m pytest -q test_adaptive_strategy.py -k 'scorecard or telemetry or skill'`

Expected: import/attribute failures because the telemetry classes do not yet
exist.

- [ ] **Step 3: Implement redacted serialization and deterministic accounting**

Use `model_dump(mode="json")` when available, then recursively convert UUIDs,
Enums, tuples, and datetimes. Include only Turn fields listed in the design:
tick, state population/status, resources/capacity/space, Core summary,
controlled Unit summaries, visible enemy summaries, Beacon status/carrier only
when present, queued plan, acceptance, and events. Deduplicate events by
`event_id`. Do not serialize arbitrary object attributes or environment values.

`Scorecard` must count only event types documented in
`api-resolution-results.md`; unknown future events increment no metric and do
not fail the loop. Count a Beacon-carried tick only when the record explicitly
marks the controlled carrier visible. `Scorecard.to_mapping()` must expose
both raw counters and `internal_score`.

`SkillBundle.load()` must read exactly `SKILL.md` plus the six rule/tactic
references listed in the design, calculate SHA-256 over filenames and bytes,
and raise `SkillBundleError` if any file is missing. The prompt text must label
the documents as rules, not as executable instructions.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `python -m pytest -q test_adaptive_strategy.py -k 'scorecard or telemetry or skill'`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add adaptive_strategy.py test_adaptive_strategy.py
git commit -m "feat: add Arena Hero telemetry and skill packet"
```

### Task 3: Two-role LLM cycle and rollback store

**Files:**
- Modify: `adaptive_strategy.py`
- Modify: `test_adaptive_strategy.py`

**Interfaces:**
- Produce `LLMTransport.complete(model, system, user, timeout) -> str`.
- Produce `OpenAICompatibleTransport(base_url: str, api_key: str).complete(*, model: str, system: str, user: str, timeout: float | None = None) -> str` using only stdlib HTTP.
- Produce `AdaptiveCoordinator(transport, state_dir, interval_ticks, min_seconds, evaluator_model, designer_model, auto_apply, rollback_ratio)` with `current_profile()`, `observe(turn, accepted)`, `close()`, and `run_cycle()`.
- Produce `parse_json_object(text) -> dict` and `validate_evaluation(payload) -> dict`.

- [ ] **Step 1: Write failing tests for sequencing and rollback**

```python
def test_cycle_calls_evaluator_then_designer_and_applies_profile(tmp_path):
    transport = FakeTransport([
        evaluation_json(),
        designer_json(worker_target=3),
    ])
    coordinator = AdaptiveCoordinator(
        transport=transport, state_dir=tmp_path, interval_ticks=1,
        min_seconds=0, evaluator_model="critic", designer_model="architect",
        auto_apply=True,
    )
    coordinator.ingest_record(sample_record(tick=1))
    coordinator.run_cycle()
    assert [call.model for call in transport.calls] == ["critic", "architect"]
    assert coordinator.current_profile().worker_target == 3


def test_invalid_designer_json_keeps_previous_profile(tmp_path):
    transport = FakeTransport([evaluation_json(), "not json"])
    coordinator = make_coordinator(tmp_path, transport)
    coordinator.run_cycle()
    assert coordinator.current_profile() == StrategyProfile.default()


def test_canary_score_drop_restores_previous_profile(tmp_path):
    transport = FakeTransport([evaluation_json(), designer_json(worker_target=3)])
    coordinator = make_coordinator(tmp_path, transport, rollback_ratio=0.15)
    coordinator.activate_profile(StrategyProfile.default(), baseline_score=100.0)
    coordinator.record_canary_score(70.0)
    coordinator.rollback_if_needed()
    assert coordinator.current_profile() == StrategyProfile.default()


def test_coordinator_due_check_does_not_block_observation(tmp_path):
    coordinator = make_coordinator(tmp_path, FakeTransport([]), interval_ticks=60)
    started = time.monotonic()
    coordinator.observe(fake_turn(tick=1), SimpleNamespace(accepted=True))
    assert time.monotonic() - started < 0.2
```

- [ ] **Step 2: Run focused tests to verify RED**

Run: `python -m pytest -q test_adaptive_strategy.py -k 'cycle or invalid or canary or due'`

Expected: missing coordinator/transport behavior failures.

- [ ] **Step 3: Implement the bounded two-call cycle**

Use a single background executor with at most one active cycle. The evaluator
system prompt must include the skill fingerprint and the rule packet and ask
for JSON keys `summary`, `strengths`, `deficits`, `rule_risks`,
`recommended_changes`, and `confidence`. The designer prompt must include the
same fingerprint, current profile, and evaluator JSON and ask only for
`profile`, `rationale`, `expected_tradeoffs`, and `guardrails_acknowledged`.

Reject Markdown/code fences after extracting one JSON object, unknown top-level
keys, missing required keys, arbitrary strings that look like Python/shell
instructions, invalid profile bounds, or a skill fingerprint mismatch.

`OpenAICompatibleTransport` posts to `<base_url>/chat/completions` with a
finite timeout, never logs headers/body, and raises a redacted `LLMError` for
HTTP/JSON failures. It must accept response content represented either as a
string or a list of text blocks.

`AdaptiveCoordinator.run_cycle()` must:

1. load and fingerprint the current skill packet;
2. aggregate the selected telemetry window;
3. call evaluator, validate its JSON, call designer, validate the candidate;
4. write a timestamped review/candidate/error report atomically;
5. activate the candidate only when `auto_apply` is true and all local checks
   pass; otherwise leave the previous profile unchanged.

Store the previous profile and normalized score in `state.json`. On a later
cycle, if the active profile's normalized score is below the previous score by
`rollback_ratio`, atomically restore the previous profile and record the
rollback reason. `close()` must signal the worker and return without waiting
for a network timeout.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `python -m pytest -q test_adaptive_strategy.py`

Expected: all adaptive tests pass without network access.

- [ ] **Step 5: Commit**

```powershell
git add adaptive_strategy.py test_adaptive_strategy.py
git commit -m "feat: add two-stage adaptive strategy cycle"
```

### Task 4: Feed the profile into the deterministic planner

**Files:**
- Modify: `balanced_tactic.py`
- Modify: `test_balanced_tactic.py`

**Interfaces:**
- Add `TacticMemory.policy: StrategyProfile` with the default profile.
- Keep `choose_actions(turn, memory=None) -> None` source-compatible.
- Update `play(api_key=None, adaptive=None) -> None` to use an optional coordinator without changing output or API-key behavior when adaptive mode is disabled.

- [ ] **Step 1: Write failing planner tests**

```python
def test_profile_worker_target_changes_spawn_preference():
    turn = make_spawn_turn(population=2, workers=1, resources=10)
    memory = TacticMemory(policy=StrategyProfile.default().with_updates(worker_target=3))
    choose_actions(turn, memory)
    assert turn.core.actions == [("SPAWN", UnitType.WORKER)]


def test_profile_carrier_margin_requires_a_safer_retreat():
    turn = make_carrier_threat_turn()
    memory = TacticMemory(policy=StrategyProfile.default().with_updates(carrier_safety_margin=1))
    choose_actions(turn, memory)
    assert turn.core.actions or turn.units[0].actions


def test_play_without_adaptive_coordinator_keeps_one_submission(monkeypatch):
    submissions = []

    class FakeTurn:
        tick = 7
        core = None

        def submit(self):
            submissions.append(self.tick)
            return SimpleNamespace(tick=self.tick, accepted=True)

    class FakeGame:
        def __init__(self, *, api_key):
            assert api_key == "provided-key"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def turns(self):
            yield FakeTurn()

    class DisabledCoordinator:
        def current_profile(self):
            return StrategyProfile.default()

        def observe(self, turn, accepted):
            return None

        def close(self):
            return None

    monkeypatch.setattr("balanced_tactic.ArenaHeroClient", FakeGame)
    play("provided-key", adaptive=DisabledCoordinator())
    assert submissions == [7]
```

- [ ] **Step 2: Run focused tests to verify RED**

Run: `python -m pytest -q test_balanced_tactic.py -k 'profile or adaptive_coordinator'`

Expected: profile constructor/helper failures or unchanged spawn behavior.

- [ ] **Step 3: Add profile-aware hooks without weakening hard rules**

Use `memory.policy` only in these bounded decisions:

- `_desired_spawn_type(turn, excluded_ids=(), population=None, memory=None)` uses `worker_target` and
  `ranger_ratio`, while still checking official `unit_cost()` and capacity.
- `_choose_runner()` adjusts only deterministic type tie-breaking using
  `beacon_priority`; it never abandons a visible Beacon or hidden-state rule.
- `_record_move()`, `_escape_core_cell()`, and carrier preheal use
  `carrier_safety_margin` as an extra visible-hit buffer.
- Ranger/Vanguard speculative predicted-cell attacks are gated by
  `combat_priority`; visible carrier/Core attacks remain ahead of economy.
- Core-cell vacancy uses `spawn_aggression` only as a bounded funding
  threshold; it may not bypass recovery, occupancy, terrain, or capacity.

All defaults must reproduce the pre-profile action choices. Read the profile
once per Turn; never reload it halfway through planning.

In `play()`, instantiate `AdaptiveCoordinator.from_env()` unless an injected
coordinator is supplied, assign `memory.policy = coordinator.current_profile()`
before `choose_actions`, submit exactly once, then call `coordinator.observe()`.
The coordinator's disabled path must be a no-op so existing tests and users
without an LLM key behave exactly as before.

- [ ] **Step 4: Run focused and full tests**

Run: `python -m pytest -q test_balanced_tactic.py -k 'profile or adaptive_coordinator'`

Expected: all profile integration tests pass.

Run: `python -m pytest -q`

Expected: the original 64 tests plus the new adaptive tests pass.

- [ ] **Step 5: Commit**

```powershell
git add balanced_tactic.py test_balanced_tactic.py
git commit -m "feat: connect adaptive profile to deterministic planner"
```

### Task 5: Documentation, configuration, and release verification

**Files:**
- Modify: `README.md`
- Modify: `.gitignore`
- Modify: `test_adaptive_strategy.py`

**Interfaces:**
- Document opt-in adaptive mode, both model roles, the separate LLM key, the
  interval/rollback variables, profile state location, and shadow mode.
- Keep all runtime telemetry under `.codex_tmp/adaptive/` and out of Git.

- [ ] **Step 1: Write documentation assertions**

```python
def test_readme_documents_adaptive_safety_contract():
    text = Path("README.md").read_text(encoding="utf-8")
    for phrase in (
        "ARENA_HERO_ADAPTIVE",
        "ARENA_HERO_LLM_API_KEY",
        "回滚",
        "不会执行 LLM 生成的 Python",
    ):
        assert phrase in text
```

- [ ] **Step 2: Run the documentation test to verify RED**

Run: `python -m pytest -q test_adaptive_strategy.py -k readme`

Expected: failure until README contains the adaptive-operation section.

- [ ] **Step 3: Document safe operation and update ignore rules**

Add a Chinese README section with PowerShell configuration examples:

```powershell
$env:ARENA_HERO_ADAPTIVE="1"
$env:ARENA_HERO_LLM_API_KEY="独立的LLM_API_KEY"
$env:ARENA_HERO_EVALUATOR_MODEL="评估模型名"
$env:ARENA_HERO_DESIGNER_MODEL="重设计模型名"
python .\balanced_tactic.py
```

Explain that the evaluator and designer receive the current local v0.14 skill
packet, only produce JSON, run outside the command window, and roll back on a
normalized-score regression. Add `.codex_tmp/adaptive/` to `.gitignore`.

- [ ] **Step 4: Run all release checks**

Run:

```powershell
python -m pytest -q
python -m compileall -q .
python -m pip check
git diff --check
rg -n -i "ARENA_HERO_API_KEY\s*=|ARENA_HERO_LLM_API_KEY\s*=|Bearer\s+|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}" . -g '!*.pyc' -g '!.git'
```

Expected: all tests pass, compile/pip/diff checks pass, and the secret scan
finds only variable names/documented placeholders—not real credentials.

- [ ] **Step 5: Commit**

```powershell
git add README.md .gitignore test_adaptive_strategy.py
git commit -m "docs: document adaptive Arena Hero strategy loop"
```
