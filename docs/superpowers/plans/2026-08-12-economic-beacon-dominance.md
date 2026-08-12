# Economic Beacon Dominance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current Worker/Beacon live-lock with a deterministic economy-and-scouting state machine, gate remote Beacon missions behind economic readiness and progress leases, grow a staged 30-Unit dominance fleet, and make the repository carry the authoritative Arena Hero skill used by both LLM roles.

**Architecture:** Add a focused `economic_strategy.py` module for bounded resource/scout/runner memory and deterministic assignments, then integrate it at the existing `TacticMemory` and Worker decision seams in `balanced_tactic.py`. Keep live actions deterministic and Turn-authoritative; the background LLM receives a fingerprinted project-local skill packet and can only propose validated profile values. Extend telemetry with aggregate stagnation signals so the evaluator can identify the exact live-lock observed in production.

**Tech Stack:** Python 3.11+, official `arena-hero>=0.2.9,<0.3` SDK, dataclasses, standard-library JSON/hash/path/HTTP, pytest.

## Global Constraints

- Arena Hero gameplay contract is v0.14 and SDK floor is 0.2.9.
- Each Turn is a complete authoritative replacement; remembered resources are fallible hints, never current truth.
- LLM calls remain outside the 15-second Turn path and may never generate executable code or direct actions.
- Project-local `skills/arena-hero` is the preferred LLM rules packet; an incomplete packet safely disables that adaptive cycle.
- Never log, prompt, test-snapshot, commit, or print API keys, model credentials, player identifiers, `.env`, or private operational logs.
- All behavior changes use RED → GREEN tests and one focused commit per task.

---

### Task 1: Bounded Economy Memory and Deterministic Scouting

**Files:**
- Create: `economic_strategy.py`
- Create: `test_economic_strategy.py`

**Interfaces:**
- Produces: `EconomySettings`, `EconomyMemory`, `ResourceProgress`, `ScoutProgress`, `refresh_economy_memory(...)`, `assign_resource_targets(...)`, `scout_target(...)`, `record_worker_progress(...)`, and `detect_two_cell_oscillation(...)`.
- Consumes: only positions, ticks, Worker-like objects with `id`/`position`, visible resource/obstacle sets, and deterministic UUID byte keys supplied by the caller.

- [ ] **Step 1: Write failing memory-expiry, matching, scouting, and oscillation tests**

```python
def test_visible_disappearance_and_ttl_remove_resource_hints(): ...
def test_resource_assignment_is_one_to_one_and_minimum_cost(): ...
def test_workers_without_resources_receive_distinct_scout_targets(): ...
def test_two_cell_oscillation_is_detected_from_four_positions(): ...
def test_stalled_resource_and_scout_targets_advance_after_threshold(): ...
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest -q test_economic_strategy.py --basetemp=.codex_tmp/pytest-economy-red`

Expected: collection fails because `economic_strategy` does not exist.

- [ ] **Step 3: Implement the minimal pure state engine**

```python
@dataclass(frozen=True, slots=True)
class EconomySettings:
    resource_memory_ttl: int = 64
    resource_stall_ticks: int = 6
    resource_cooldown_ticks: int = 8
    scout_stall_ticks: int = 3
    scout_ring_step: int = 10

@dataclass(slots=True)
class EconomyMemory:
    resource_last_seen: dict[Position, int] = field(default_factory=dict)
    resource_intents: dict[bytes, Position] = field(default_factory=dict)
    resource_progress: dict[bytes, ResourceProgress] = field(default_factory=dict)
    resource_cooldowns: dict[tuple[bytes, Position], int] = field(default_factory=dict)
    scout_slots: dict[bytes, int] = field(default_factory=dict)
    scout_stages: dict[bytes, int] = field(default_factory=dict)
    scout_progress: dict[bytes, ScoutProgress] = field(default_factory=dict)
    worker_history: dict[bytes, deque[Position]] = field(default_factory=dict)
    chunk_last_seen: dict[Position, int] = field(default_factory=dict)
```

Use Manhattan/path-aware deterministic costs, eight scout vectors, increasing rings, TTL invalidation, and stable raw-UUID ordering. Cap each history deque at four positions and delete state for dead Workers.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest -q test_economic_strategy.py --basetemp=.codex_tmp/pytest-economy-green`

Expected: all new tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- economic_strategy.py test_economic_strategy.py
git commit -m "feat: add bounded economy and scouting memory"
```

### Task 2: Integrate Economy, Exploration, and Anti-Oscillation

**Files:**
- Modify: `balanced_tactic.py:199-310,445-570,1626-1959,2403-2520`
- Modify: `test_balanced_tactic.py:11-23,252-365,897-918,1628-1651`

**Interfaces:**
- Consumes Task 1 `EconomyMemory` and pure assignment/scouting helpers.
- Produces an `economy` field on `TacticMemory`, refreshed once per Turn before actions, and Worker actions ordered as survival → carrier recovery → deposit/return → harvest → resource route → scout route.

- [ ] **Step 1: Add failing planner regressions for the observed live-lock**

```python
def test_two_empty_workers_with_no_visible_resources_explore_distinctly(): ...
def test_worker_does_not_return_to_core_when_no_resource_is_visible(): ...
def test_visible_resource_targets_are_unique_across_workers(): ...
def test_worker_releases_stalled_resource_target_and_changes_route(): ...
def test_two_cell_worker_oscillation_changes_scout_stage(): ...
def test_cargo_worker_still_returns_and_deposits_before_scouting(): ...
```

Update the obsolete `test_core_cell_worker_does_not_roam_without_spawn_pressure` expectation: a healthy empty Worker must scout when the economy has no visible target.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest -q test_balanced_tactic.py -k "explore or oscillation or resource_targets or cargo_worker_still" --basetemp=.codex_tmp/pytest-planner-economy-red`

Expected: current planner waits at Core, shares targets, or repeats the old route.

- [ ] **Step 3: Wire Task 1 into `TacticMemory.observe` and Worker planning**

```python
@dataclass
class TacticMemory:
    ...
    economy: EconomyMemory = field(default_factory=EconomyMemory)

def choose_actions(turn, memory=None):
    memory.observe(turn)
    refresh_economy_memory(
        memory.economy,
        tick=turn.tick,
        workers=turn.workers,
        visible_resources=turn.resource_cells,
        friendly_positions=(turn.core.position, *(unit.position for unit in turn.units)),
        settings=_economy_settings(memory.policy),
    )
```

Compute resource assignments once before the Worker loop. Replace `goal = core_position` for an empty Worker without a resource with its distinct scout target. Record planned post-move positions only after `_record_move` succeeds; stalled/oscillating routes advance their scout stage before choosing the next target.

- [ ] **Step 4: Verify focused and existing planner tests**

Run: `python -m pytest -q test_economic_strategy.py test_balanced_tactic.py --basetemp=.codex_tmp/pytest-planner-economy-green`

Expected: new regressions and all existing planner tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- balanced_tactic.py economic_strategy.py test_balanced_tactic.py test_economic_strategy.py
git commit -m "fix: replace idle worker live-lock with exploration"
```

### Task 3: Bounded Beacon Missions and Staged Dominance Production

**Files:**
- Modify: `economic_strategy.py`
- Modify: `strategy_policy.py`
- Modify: `balanced_tactic.py:754-824,1041-1081,1271-1625,2223-2402`
- Modify: `test_strategy_policy.py`
- Modify: `test_balanced_tactic.py`
- Modify: `test_economic_strategy.py`

**Interfaces:**
- Produces `RunnerLease` in `EconomyMemory`, `update_runner_lease(...)`, and expanded validated profile fields: `worker_target`, `bootstrap_worker_target`, `near_beacon_radius`, `runner_stall_ticks`, `resource_memory_ttl`, `resource_stall_ticks`, `scout_ring_step`.
- Preserves `StrategyProfile.from_mapping`, `to_mapping`, `with_updates`, direct-constructor validation, and JSON compatibility.

- [ ] **Step 1: Add failing profile, runner, and production tests**

```python
def test_profile_accepts_only_bounded_dominance_parameters(): ...
def test_unknown_remote_beacon_does_not_lease_a_worker_before_bootstrap(): ...
def test_near_visible_ground_beacon_allows_opportunistic_runner(): ...
def test_runner_harvests_current_resource_before_resuming_beacon_route(): ...
def test_loaded_runner_returns_home_before_beacon_route(): ...
def test_runner_lease_releases_after_no_progress_or_two_cell_cycle(): ...
def test_visible_enemy_carrier_is_intercepted_by_combat_unit_not_worker(): ...
def test_staged_production_builds_6w_then_1v1r_then_12w_then_3v4r_then_23w(): ...
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest -q test_strategy_policy.py test_economic_strategy.py test_balanced_tactic.py -k "dominance or bootstrap or runner or staged_production" --basetemp=.codex_tmp/pytest-beacon-stage-red`

Expected: old permanent runner selection and old 2-Worker target violate the assertions.

- [ ] **Step 3: Implement bounded runner leases**

```python
@dataclass(slots=True)
class RunnerLease:
    unit_id: bytes
    target: Position
    best_distance: int
    stalled_ticks: int = 0

def update_runner_lease(..., status_visible: bool, economic_ready: bool,
                        near_ground_exception: bool, stall_limit: int) -> bytes | None:
    ...
```

Unknown Beacon status may bias one Scout only after bootstrap; it must not create a permanent Worker runner. A currently visible ground Beacon within `near_beacon_radius` may use the nearest eligible Unit. Visible enemy carriers are combat interception targets. Release on pickup/drop/owner change, death, cargo, economic regression, target change without progress, lease stall, or A-B-A-B history.

- [ ] **Step 4: Implement staged production**

Set profile defaults to `worker_target=23`, `bootstrap_worker_target=6`, near radius 12, runner stall 6, resource TTL 64, resource stall 6, scout ring step 10. `_desired_spawn_type` follows the exact stage ordering in the design while filtering candidates by current Core capacity and dynamic `unit_cost()`.

- [ ] **Step 5: Verify GREEN**

Run: `python -m pytest -q test_strategy_policy.py test_economic_strategy.py test_balanced_tactic.py --basetemp=.codex_tmp/pytest-beacon-stage-green`

Expected: all profile, economy, Beacon, combat, healing and dynamic-spawn tests pass.

- [ ] **Step 6: Commit**

```powershell
git add -- economic_strategy.py strategy_policy.py balanced_tactic.py test_strategy_policy.py test_economic_strategy.py test_balanced_tactic.py
git commit -m "feat: add bounded beacon missions and staged growth"
```

### Task 4: Vendor and Prefer the Project Arena Hero Skill

**Files:**
- Create: `skills/arena-hero/SKILL.md`
- Create: `skills/arena-hero/LICENSE`
- Create: `skills/arena-hero/references/game-rules.md`
- Create: `skills/arena-hero/references/reference-numbers.md`
- Create: `skills/arena-hero/references/reference-glossary.md`
- Create: `skills/arena-hero/references/tactic-authoring.md`
- Create: `skills/arena-hero/references/sdk-quickstart.md`
- Create: `skills/arena-hero/references/sdk-reference.md`
- Create: `skills/arena-hero/references/reference-source-and-version.md`
- Create: `skills/arena-hero/references/api-resolution-results.md`
- Modify: `adaptive_strategy.py:29-37,467-505,928-987`
- Modify: `test_adaptive_strategy.py:15-35,165-205,249-330`

**Interfaces:**
- Produces `_PROJECT_SKILL_ROOT = Path(__file__).resolve().parent / "skills" / "arena-hero"` and `SkillBundle.load(root=None)` precedence of project root → legacy user roots.
- The fixed packet contains exactly the files listed in `_SKILL_FILES`; evaluator and designer responses require the same fingerprint.

- [ ] **Step 1: Add failing project-local precedence and two-role prompt tests**

```python
def test_default_skill_bundle_loads_project_packet(): ...
def test_project_skill_packet_contains_v014_rules_and_sdk_source_version(): ...
def test_project_packet_wins_over_legacy_user_skill(monkeypatch, tmp_path): ...
def test_both_llm_roles_receive_the_same_project_skill_fingerprint(tmp_path): ...
def test_incomplete_project_packet_does_not_silently_mix_legacy_files(tmp_path): ...
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest -q test_adaptive_strategy.py -k "project_skill or project_packet or both_llm_roles" --basetemp=.codex_tmp/pytest-skill-red`

Expected: default loader points only at user installation roots.

- [ ] **Step 3: Mechanically copy the reviewed v0.14 packet and update loader precedence**

Copy the reviewed files byte-for-byte from `C:\Users\root\.codex\skills\arena-hero-skill`, including its Apache license. Do not copy tests, workflow, executable scripts, API keys, caches, or local logs. Load one complete root only; never assemble a packet from multiple roots.

- [ ] **Step 4: Verify skill integrity and adaptive tests**

Run: `python -m pytest -q test_adaptive_strategy.py --basetemp=.codex_tmp/pytest-skill-green`

Expected: fingerprint/integrity, malformed UTF-8, evaluator, designer, transport and coordinator tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- adaptive_strategy.py test_adaptive_strategy.py skills/arena-hero
git commit -m "feat: vendor Arena Hero skill for adaptive models"
```

### Task 5: Stagnation Telemetry, Scorecard, Documentation, and Release Verification

**Files:**
- Modify: `adaptive_strategy.py:350-465,509-590`
- Modify: `strategy_policy.py:102-125`
- Modify: `balanced_tactic.py:2531-2575`
- Modify: `test_adaptive_strategy.py`
- Modify: `test_strategy_policy.py`
- Modify: `README.md`

**Interfaces:**
- Produces redacted record fields `visible_resource_count`, `worker_modes`, and score metrics `zero_resource_ticks`, `idle_worker_ticks`, `route_stalls`, `oscillation_ticks`, `runner_progress_ticks`.
- Extends `internal_score` with deterministic penalties without including identifiers or coordinates in model summaries.

- [ ] **Step 1: Add failing telemetry and scoring tests**

```python
def test_scorecard_penalizes_zero_resource_worker_oscillation(): ...
def test_scorecard_rewards_harvest_deposit_and_runner_progress(): ...
def test_telemetry_contains_counts_and_modes_without_targets_or_identifiers(): ...
def test_adaptive_prompt_exposes_stagnation_score_not_raw_private_data(): ...
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest -q test_strategy_policy.py test_adaptive_strategy.py -k "stagnation or oscillation or worker_modes" --basetemp=.codex_tmp/pytest-telemetry-red`

Expected: scorecard lacks the new metrics.

- [ ] **Step 3: Implement bounded aggregate telemetry and scoring**

Count Worker modes and current visible resources at observation time. Detect oscillation from consecutive redacted records using per-record unit positions only inside `Scorecard.from_records`, then discard identifiers from `to_mapping()`. Add penalties for route stalls, oscillation, idle Workers and zero-resource Tick duration; preserve finite/non-negative input validation.

- [ ] **Step 4: Update README**

Document the new phase order, exploration behavior, Beacon lease gates, 23/3/4 mature composition, project-local skill source/fingerprint, adaptive failure reports, and restart requirement after deployment. Remove statements that claim every unknown Beacon immediately receives a Worker runner.

- [ ] **Step 5: Run complete verification**

Run:

```powershell
python -m pytest -q --basetemp=.codex_tmp/pytest-economic-beacon-release
python -m compileall -q balanced_tactic.py economic_strategy.py adaptive_strategy.py strategy_policy.py
python -m pip check
git diff --check
git status --short
```

Expected: all tests pass; compilation and dependency checks succeed; diff check is clean; only intended source, tests, docs and vendored skill files are changed. Search tracked files outside `.env`, `.codex_tmp`, `.git` and operational logs for credential-shaped strings before committing.

- [ ] **Step 6: Commit**

```powershell
git add -- adaptive_strategy.py strategy_policy.py balanced_tactic.py test_adaptive_strategy.py test_strategy_policy.py README.md
git commit -m "feat: score and document economic beacon stagnation"
```

## Plan Self-Review

- Spec coverage: economy memory, one-to-one matching, exploration, stalls, oscillation, Beacon gating, opportunistic harvest, staged production, project-local skill, two-role fingerprints, telemetry, canary-safe profiles, documentation and release checks are each assigned to a task.
- Placeholder scan: no TBD/TODO/future-only implementation steps remain.
- Type consistency: `EconomyMemory` originates in Task 1, is embedded by Task 2, gains `RunnerLease` in Task 3, and is observed only through aggregate fields in Task 5. `StrategyProfile` mapping compatibility is updated in Task 3 before the adaptive designer consumes it in Tasks 4–5.
