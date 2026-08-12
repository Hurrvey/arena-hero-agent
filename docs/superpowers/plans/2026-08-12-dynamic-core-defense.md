# Dynamic Core Defense Implementation Plan

> **Execution note:** implement in the current approved main workspace with TDD and a verification checkpoint after every task.

**Goal:** Add a deterministic, four-level Core defense system that protects survival without abandoning Beacon control or long-run economy, and expose bounded defense telemetry to the existing two-stage adaptive LLM cycle.

**Architecture:** Put geometry-only threat assessment and defender selection in a small `defense_strategy.py` module. Keep SDK action construction in `balanced_tactic.py`. Extend the existing bounded `StrategyProfile`, telemetry, scorecard, and prompt record schemas instead of allowing the LLM to issue live actions.

**Tech Stack:** Python 3.12, Arena Hero Python SDK 0.2.9, pytest, stdlib dataclasses/enums.

---

## Task 1: Pure threat model and defender roster

**Files:**

- Create: `defense_strategy.py`
- Create: `test_defense_strategy.py`

1. Write failing tests for `CLEAR`, `WATCH`, one-step `APPROACH`, current `ATTACK`, and combined visible `LETHAL` states.
2. Write failing tests for obstacle-aware Ranger lines and deterministic defender selection that excludes the Beacon carrier.
3. Implement `ThreatLevel`, immutable `DefenseAssessment`, attack geometry, one-step approach detection, and defender roster selection.
4. Run `python -m pytest -q test_defense_strategy.py` and commit the green slice.

## Task 2: Bounded policy controls and TacticMemory integration

**Files:**

- Modify: `strategy_policy.py`
- Modify: `test_strategy_policy.py`
- Modify: `balanced_tactic.py`
- Modify: `test_balanced_tactic.py`

1. Add failing validation/default/round-trip tests for the five defense profile fields.
2. Add failing planner tests showing that each Turn refreshes a current-state-only defense assessment and stable defender roster.
3. Implement bounded fields and memory state without persisting enemy positions through fog.
4. Run focused policy and planner tests; commit the green slice.

## Task 3: Combat recall and target priority

**Files:**

- Modify: `balanced_tactic.py`
- Modify: `test_balanced_tactic.py`

1. Add failing legal-Turn tests showing a Ranger/Vanguard that can attack a lethal Core attacker does so before an enemy Core.
2. Add failing tests showing an out-of-ring selected defender returns toward Core during `CLEAR/WATCH`, and all non-carrier combat units recall during `APPROACH`.
3. Integrate the assessment into `_combat_target_rank`, Vanguard sweep ranking, and `_combat_goal` while preserving Beacon carrier escape/preheal behavior.
4. Run all combat and Beacon-carrier regressions; commit the green slice.

## Task 4: Worker evacuation and wartime production

**Files:**

- Modify: `balanced_tactic.py`
- Modify: `test_balanced_tactic.py`

1. Add failing tests for a threatened near-Core Worker choosing a safe flank instead of moving into Core.
2. Add a blocked evacuation test proving no illegal move is queued and existing cargo fallback remains legal.
3. Add failing tests showing `APPROACH+` pauses Worker production and fills missing Vanguard/Ranger defense targets under dynamic prices/capacity.
4. Implement the minimum planner changes and run economy/Beacon/production regressions; commit the green slice.

## Task 5: Defense telemetry and adaptive scoring

**Files:**

- Modify: `balanced_tactic.py`
- Modify: `adaptive_strategy.py`
- Modify: `strategy_policy.py`
- Modify: `test_adaptive_strategy.py`
- Modify: `test_strategy_policy.py`

1. Add failing telemetry tests for bounded defense mappings, `CORE_DAMAGED` accounting, record serialization, and prompt visibility.
2. Add failing Scorecard aggregation/internal-score tests for Core damage, lethal exposure, defender coverage, and Worker evacuations.
3. Implement defensive diagnostics and scoring with nonnegative finite validation and bounded prompt records.
4. Prove malformed or injected telemetry cannot alter the deterministic planner or bypass profile validation.
5. Run all adaptive tests and commit the green slice.

## Task 6: Documentation and release verification

**Files:**

- Modify: `README.md`
- Modify: `.env.example` only if a new runtime setting is actually required

1. Document the four levels, target order, wartime production, Worker evacuation, and the fact that LLM evaluation is post-submit only.
2. Update the test count only after final collection.
3. Run:
   - `python -m pytest -q --basetemp=adaptive/pytest-defense-release`
   - `python -m compileall -q balanced_tactic.py defense_strategy.py strategy_policy.py adaptive_strategy.py`
   - `python -m pip check`
   - `git diff --check`
4. Review the complete diff against the design, commit, and report exact verification evidence.
