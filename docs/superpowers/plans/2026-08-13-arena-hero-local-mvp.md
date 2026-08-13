# Arena Hero Local MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved local single-account M0–M3 Arena Hero MVP: rule-correct deterministic planning, a single-owner FastAPI runtime, SQLite history/revisions, and the supplied dark tactical Web console.

**Architecture:** Keep the official synchronous Arena Hero SDK and the deterministic planner on a dedicated runtime thread, then publish immutable post-submit events to SQLite and FastAPI. Extract visibility, risk, movement resolution, and capacity projection into pure `app.strategy` modules while preserving the root CLI imports. Serve a no-build ES-module frontend and the supplied GPL-3.0 asset pack from the same localhost application.

**Tech Stack:** Python `>=3.11,<3.13`, `arena-hero>=0.2.9,<0.3`, FastAPI, Uvicorn, standard-library SQLite WAL, standard-library threads/queues/logging, vanilla HTML/CSS/JavaScript, pytest, HTTPX, Ruff, Bandit, pip-audit, Python Playwright, uv.

## Global Constraints

- Arena Hero gameplay contract is v0.14 and the official SDK floor is 0.2.9.
- Each `Turn` is authoritative; remembered resources are fallible hints, and hidden enemies/carriers are never current facts.
- One account has one AGENT owner; CLI and Web runtime must contend for the same cross-process lock.
- One authoritative Turn is submitted at most once, and `ACCEPTED` is never presented as `RESOLVED`.
- Planner, validation, and `turn.submit()` stay ahead of SQLite, WebSocket, metrics, cleanup, and LLM work.
- API keys, Authorization headers, full UUIDs, usernames, exact raw telemetry, and unredacted prompts never reach public REST, WebSocket, DOM, logs, snapshots, or commits.
- The browser never accepts or edits credentials and never submits Manual game actions.
- Adaptive auto-apply defaults to `0`; candidates use fixed `(start_tick, end_tick]` windows, fingerprints, minimum samples, and revision CAS.
- The server binds to `127.0.0.1`; public authentication, Postgres, Redis, distributed locks, and multi-tenancy are out of scope.
- The supplied `arena-hero-ui-assets/` and its GPL-3.0 license are authoritative UI assets; do not replace them with generated art or Emoji.
- All behavior changes follow RED → GREEN, preserve the existing CLI, and end in a focused commit.
- Use `C:\Users\root\AppData\Local\Programs\Python\Python311\python.exe` to create/sync `.venv`; the current system default Python 3.13 is not a release target.

---

### Task 1: Reproducible Python 3.11 Foundation and Tracked Product Inputs

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `app/__init__.py`
- Create: `tests/test_project_contract.py`
- Create: `.github/workflows/ci.yml`
- Modify: `.gitignore`
- Modify: `requirements.txt`
- Modify: `.env.example`
- Track: `MVP开发指导.md`
- Track: `arena-hero-ui-assets/**`

**Interfaces:**
- Produces: installable package `arena_hero_agent`, `app.__version__`, Python 3.11 `.venv`, reproducible `uv.lock`, and a CI command vocabulary used by every later task.
- Consumes: existing root modules unchanged and the supplied design/assets.

- [ ] **Step 1: Write the failing project-contract test**

```python
from pathlib import Path

import app


def test_supported_python_and_product_assets_are_declared():
    root = Path(__file__).parents[1]
    assert app.__version__ == "1.1.0"
    assert (root / "arena-hero-ui-assets" / "ASSET-MANIFEST.json").is_file()
    assert (root / "MVP开发指导.md").is_file()
    assert "ARENA_HERO_ADAPTIVE_AUTO_APPLY=0" in (
        root / ".env.example"
    ).read_text(encoding="utf-8")
```

- [ ] **Step 2: Verify RED with the target interpreter**

Run:

```powershell
& 'C:\Users\root\AppData\Local\Programs\Python\Python311\python.exe' -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip uv
.\.venv\Scripts\python.exe -m pytest -q tests\test_project_contract.py
```

Expected: collection fails because `app`/the project metadata does not exist and pytest may first need installation through the next sync step.

- [ ] **Step 3: Add the minimal package and tool configuration**

`pyproject.toml` must declare:

```toml
[project]
name = "arena-hero-agent"
version = "1.1.0"
requires-python = ">=3.11,<3.13"
dependencies = [
  "arena-hero>=0.2.9,<0.3",
  "fastapi>=0.116,<1",
  "uvicorn[standard]>=0.35,<1",
]

[dependency-groups]
dev = [
  "bandit>=1.8,<2",
  "httpx>=0.28,<1",
  "pip-audit>=2.9,<3",
  "playwright>=1.58,<2",
  "pytest>=8.4,<9",
  "pytest-asyncio>=1.1,<2",
  "ruff>=0.12,<1",
]

[tool.pytest.ini_options]
testpaths = [".", "tests"]
norecursedirs = [".git", ".venv", ".worktrees", ".codex_tmp", "adaptive", "data"]

[tool.ruff]
target-version = "py311"
line-length = 100
exclude = ["adaptive", "data", ".codex_tmp"]

[tool.bandit]
exclude_dirs = ["tests", ".venv"]
```

`app/__init__.py` exports `__version__ = "1.1.0"`. `.python-version` contains `3.11`. Add `.venv/`, `data/`, `.coverage`, `htmlcov/`, `test-results/`, and `playwright-report/` to `.gitignore`. Keep `.env` and `/adaptive/` ignored. Set adaptive and auto-apply defaults to `0` in `.env.example`.

- [ ] **Step 4: Lock, sync, and verify GREEN**

Run:

```powershell
uv lock --python 'C:\Users\root\AppData\Local\Programs\Python\Python311\python.exe'
uv sync --python 'C:\Users\root\AppData\Local\Programs\Python\Python311\python.exe' --group dev
.\.venv\Scripts\python.exe -m pytest -q tests\test_project_contract.py
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.codex_tmp\pytest-foundation
```

Expected: project contract passes and all 172 pre-existing tests remain green.

- [ ] **Step 5: Add CI using the same commands**

The workflow runs Python 3.11 and 3.12, `uv sync --group dev`, compileall, Ruff, Bandit, pytest, pip check, and a secret/path guard that confirms `.env`, `adaptive/`, and `data/` are untracked.

- [ ] **Step 6: Commit**

```powershell
git add -- pyproject.toml uv.lock .python-version app/__init__.py tests/test_project_contract.py .github/workflows/ci.yml .gitignore requirements.txt .env.example 'MVP开发指导.md' arena-hero-ui-assets
git commit -m "build: establish local MVP foundation"
```

### Task 2: Authoritative Visibility and Visible Attack Geometry

**Files:**
- Create: `app/strategy/__init__.py`
- Create: `app/strategy/models.py`
- Create: `app/strategy/visibility.py`
- Create: `app/strategy/risk.py`
- Create: `tests/unit/strategy/test_visibility.py`
- Create: `tests/unit/strategy/test_risk.py`

**Interfaces:**
- Produces: `Position`, `EntityKind`, `EntitySnapshot`, `CellRisk`, `VisibilityMap`, `supercover_cells()`, `compute_visible_cells()`, `build_visible_risk_map()`, and `risk_at()`.
- Consumes: only immutable snapshots; no SDK controllers or network objects.

- [ ] **Step 1: Write failing supercover and radius tests**

```python
def test_each_friendly_kind_uses_its_v014_manhattan_radius(): ...
def test_obstacle_cell_is_visible_but_the_cell_behind_it_is_not(): ...
def test_corner_supercover_checks_both_touched_cells(): ...
def test_union_contains_visibility_from_every_living_friendly(): ...
```

- [ ] **Step 2: Write failing risk-geometry tests**

```python
def test_vanguard_threatens_only_four_adjacent_cells(): ...
def test_ranger_threatens_row_column_and_exact_diagonal_at_range_three(): ...
def test_ranger_does_not_threaten_a_two_by_one_offset(): ...
def test_obstacle_stops_ranger_risk_beyond_it(): ...
def test_risk_map_contains_attack_count_damage_and_stable_attacker_ids(): ...
```

- [ ] **Step 3: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\strategy\test_visibility.py tests\unit\strategy\test_risk.py
```

Expected: imports fail because `app.strategy.visibility` and `app.strategy.risk` do not exist.

- [ ] **Step 4: Implement immutable strategy snapshots**

```python
class EntityKind(StrEnum):
    CORE = "CORE"
    WORKER = "WORKER"
    VANGUARD = "VANGUARD"
    RANGER = "RANGER"


@dataclass(frozen=True, slots=True)
class EntitySnapshot:
    entity_id: bytes
    kind: EntityKind
    position: Position
    hp: int
    shield: int = 0
    controlled: bool = True


@dataclass(frozen=True, slots=True)
class CellRisk:
    visible_attack_count: int = 0
    expected_damage: int = 0
    attackers: tuple[bytes, ...] = ()
```

Validate coordinates and non-negative HP/shield. Use radii Core 5, Worker 3, Vanguard 4, Ranger 5. Implement integer supercover so a line through a grid corner checks both adjacent touched cells. Risk uses only current visible enemies and obstacle-blocked v0.14 geometry.

- [ ] **Step 5: Verify GREEN and root regression**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\strategy\test_visibility.py tests\unit\strategy\test_risk.py
.\.venv\Scripts\python.exe -m pytest -q test_defense_strategy.py test_balanced_tactic.py --basetemp=.codex_tmp\pytest-visibility-risk
```

Expected: all focused and existing tactic tests pass.

- [ ] **Step 6: Commit**

```powershell
git add -- app/strategy tests/unit/strategy
git commit -m "feat: add authoritative visibility and risk maps"
```

### Task 3: Deterministic Global Movement Resolution

**Files:**
- Create: `app/strategy/movement.py`
- Create: `tests/unit/strategy/test_movement.py`
- Modify: `app/strategy/models.py`

**Interfaces:**
- Produces: `MoveCandidate`, `MoveIntent`, `MovementDependency`, `RejectedMove`, `MovementResolution`, and `resolve_movement()`.
- Consumes: current occupancy, obstacles, current visible enemy cells, per-entity ordered candidates, and capacity two.

- [ ] **Step 1: Write failing dependency and conflict tests**

```python
def test_cannot_enter_a_friendly_waiting_occupants_cell(): ...
def test_dependency_chain_succeeds_when_every_occupant_leaves(): ...
def test_dependency_chain_falls_back_when_the_tail_cannot_leave(): ...
def test_same_destination_uses_priority_then_raw_uuid(): ...
def test_incomplete_cycle_rejects_all_dependants(): ...
def test_legal_four_cell_cycle_is_accepted_atomically(): ...
def test_enemy_occupied_and_obstacle_cells_are_rejected(): ...
```

- [ ] **Step 2: Write failing risk-order tests**

```python
def test_zero_risk_candidate_beats_a_closer_attacked_candidate(): ...
def test_when_all_cells_are_risky_the_lowest_damage_candidate_wins(): ...
def test_lethal_candidate_is_rejected_without_explicit_sacrifice_reason(): ...
```

- [ ] **Step 3: Verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests\unit\strategy\test_movement.py`

Expected: import failure for `app.strategy.movement`.

- [ ] **Step 4: Implement the pure resolver**

```python
@dataclass(frozen=True, slots=True)
class MoveCandidate:
    destination: Position
    direction: str
    risk: CellRisk
    goal_distance: int
    reason_code: str
    lethal: bool = False


@dataclass(frozen=True, slots=True)
class MoveIntent:
    entity_id: bytes
    origin: Position
    priority: int
    candidates: tuple[MoveCandidate, ...]


def resolve_movement(
    intents: Sequence[MoveIntent],
    *,
    occupancy: Mapping[Position, tuple[bytes, ...]],
    owner_by_entity: Mapping[bytes, bytes],
    obstacles: AbstractSet[Position],
    capacity: int = 2,
) -> MovementResolution:
    ...
```

Rank candidates lexicographically by lethal, attack count, damage, dependency penalty, stagnation/oscillation flags, goal distance, fixed direction order. Build an origin→destination dependency graph, resolve chains from empty destinations, accept complete legal cycles atomically, and retry rejected intents with their next candidate before returning WAIT/rejection.

- [ ] **Step 5: Verify GREEN and determinism**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\strategy\test_movement.py
.\.venv\Scripts\python.exe -m pytest -q tests\unit\strategy --basetemp=.codex_tmp\pytest-movement
```

Expected: every resolution is stable under shuffled mapping insertion order.

- [ ] **Step 6: Commit**

```powershell
git add -- app/strategy/models.py app/strategy/movement.py tests/unit/strategy/test_movement.py
git commit -m "feat: resolve global movement dependencies"
```

### Task 4: Capacity Projection, Resource Invalidation, and Planner Result Adapter

**Files:**
- Create: `app/strategy/projection.py`
- Create: `app/strategy/planner.py`
- Create: `app/strategy/planner_adapter.py`
- Create: `tests/unit/strategy/test_projection.py`
- Create: `tests/integration/test_planner_adapter.py`
- Modify: `economic_strategy.py`
- Modify: `balanced_tactic.py`
- Modify: `adaptive_strategy.py`
- Modify: `strategy_policy.py`
- Modify: `test_economic_strategy.py`
- Modify: `test_balanced_tactic.py`
- Modify: `test_adaptive_strategy.py`

**Interfaces:**
- Produces: `CapacityProjection`, `DecisionAction`, `DecisionExplanation`, `PlannerDiagnostics`, `PlannerResult`, `compute_capacity_projection()`, `plan_turn()`, and `apply_planner_result()`.
- Consumes: Task 2 visibility/risk and Task 3 movement resolver; root `TacticMemory`, `choose_actions()`, and SDK Turn controls remain compatible.

- [ ] **Step 1: Write failing capacity and overflow tests**

```python
def test_visible_doomed_unit_lowers_projected_capacity(): ...
def test_safe_planned_move_prevents_false_death_projection(): ...
def test_deposit_is_deferred_when_post_combat_capacity_would_overflow(): ...
def test_overflow_destroyed_has_an_explicit_negative_score_weight(): ...
```

- [ ] **Step 2: Write failing resource-visibility tests**

```python
def test_resource_visible_by_core_but_missing_is_removed_immediately(): ...
def test_resource_behind_supercover_obstacle_remains_a_fallible_hint(): ...
def test_resource_depleted_event_clears_intent_before_reassignment(): ...
```

- [ ] **Step 3: Write failing planner-result tests**

```python
def test_same_turn_memory_and_profile_produce_identical_result(): ...
def test_result_contains_public_action_reason_and_risk_delta(): ...
def test_validation_failure_degrades_one_entity_to_wait_without_second_submit(): ...
def test_watch_defenders_remain_in_their_vanguard_and_ranger_rings(): ...
def test_completing_core_move_uses_legal_projected_destination_for_defense(): ...
```

- [ ] **Step 4: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\strategy\test_projection.py tests\integration\test_planner_adapter.py test_economic_strategy.py -k "visible or projection or overflow or planner_result or defender"
```

Expected: new types are missing and old resource invalidation/deposit behavior fails.

- [ ] **Step 5: Implement projection and update memory**

```python
@dataclass(frozen=True, slots=True)
class CapacityProjection:
    current_population: int
    projected_population_floor: int
    current_capacity: int
    projected_capacity: int
    projected_overflow: int
    visibly_doomed_unit_ids: tuple[bytes, ...]


def refresh_economy_memory(
    memory: EconomyMemory,
    *,
    tick: int,
    workers: Sequence[object],
    visible_resources: Iterable[Position],
    visible_cells: AbstractSet[Position],
    settings: EconomySettings,
) -> None:
    ...
```

Remove the old distance-one `friendly_positions` visibility shortcut. Compute doomed Units from their projected destination and current visible risk; exclude successful safe moves. Before DEPOSIT, compare the server's all-or-fit action with projected post-combat capacity and choose cargo preservation when the loss is avoidable.

- [ ] **Step 6: Implement the adapter without a second strategy implementation**

```python
@dataclass(frozen=True, slots=True)
class PlannerResult:
    tick: int
    plan: object
    explanation: DecisionExplanation
    diagnostics: PlannerDiagnostics


def plan_turn(turn, memory: TacticMemory, profile: StrategyProfile) -> PlannerResult:
    memory.policy = profile
    choose_actions(turn, memory)
    return build_result_from_queued_plan(turn, memory)
```

Instrument accepted move candidates and action reasons at the existing action-recording seams; do not infer explanations from raw action names after the fact when a reason is known. Keep `choose_actions()` for compatibility and make CLI call the adapter before one `turn.submit()`.

- [ ] **Step 7: Verify GREEN and all legacy tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\strategy tests\integration\test_planner_adapter.py test_economic_strategy.py test_balanced_tactic.py test_adaptive_strategy.py --basetemp=.codex_tmp\pytest-planner-m0
```

Expected: new M0 regressions and all existing behavior tests pass.

- [ ] **Step 8: Commit**

```powershell
git add -- app/strategy economic_strategy.py balanced_tactic.py adaptive_strategy.py strategy_policy.py tests test_economic_strategy.py test_balanced_tactic.py test_adaptive_strategy.py
git commit -m "fix: enforce safe deterministic planner outcomes"
```

### Task 5: SQLite WAL, Immutable Strategy Revisions, and Legacy Import

**Files:**
- Create: `app/storage/__init__.py`
- Create: `app/storage/database.py`
- Create: `app/storage/migrations.py`
- Create: `app/storage/models.py`
- Create: `app/storage/runtime_store.py`
- Create: `app/storage/strategy_repository.py`
- Create: `app/storage/adaptive_repository.py`
- Create: `app/storage/metrics_repository.py`
- Create: `app/storage/retention.py`
- Create: `app/adaptive/__init__.py`
- Create: `app/adaptive/legacy_import.py`
- Create: `tests/unit/storage/test_database.py`
- Create: `tests/unit/storage/test_strategy_repository.py`
- Create: `tests/integration/test_legacy_import.py`

**Interfaces:**
- Produces: `Database`, `RuntimeStore`, `StrategyRepository`, `AdaptiveRepository`, `MetricsRepository`, `RetentionService`, `LegacyImporter`, `ServiceEvent`, `StrategyRevision`, and `RevisionConflict`.
- Consumes: JSON-serializable public/raw planner records and `StrategyProfile.from_mapping()`.

- [ ] **Step 1: Write failing migration and atomicity tests**

```python
def test_open_enables_wal_foreign_keys_and_busy_timeout(): ...
def test_migrations_are_idempotent(): ...
def test_turn_batch_and_service_events_commit_atomically(): ...
def test_events_after_returns_monotonic_seq_and_limit(): ...
```

- [ ] **Step 2: Write failing revision tests**

```python
def test_profile_update_creates_pending_revision_instead_of_mutating_active(): ...
def test_expected_revision_conflict_preserves_newer_revision(): ...
def test_pending_revision_activates_only_at_a_turn_boundary(): ...
def test_rollback_creates_a_new_revision_with_rollback_source(): ...
```

- [ ] **Step 3: Write failing legacy-import tests**

```python
def test_valid_legacy_active_profile_is_imported_once(): ...
def test_reimport_is_idempotent_by_source_hash(): ...
def test_invalid_or_oversized_legacy_file_warns_without_mutation(): ...
def test_import_never_deletes_or_renames_legacy_files(): ...
```

- [ ] **Step 4: Verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests\unit\storage tests\integration\test_legacy_import.py`

Expected: storage modules do not exist.

- [ ] **Step 5: Implement SQLite transactions and migrations**

Open one connection per operation/thread with `check_same_thread=False` avoided unless ownership is explicit. Set WAL, FK, 5000 ms busy timeout, and NORMAL synchronous. Create the approved tables and indexes in ordered migrations. `RuntimeStore.save_turn_batch()` inserts snapshot/plan/resolution/service events inside `BEGIN IMMEDIATE` and returns committed events only after COMMIT.

- [ ] **Step 6: Implement immutable revisions and fixed windows**

```python
def create_revision(
    self,
    *,
    expected_revision: int,
    profile: StrategyProfile,
    source: str,
    reason: str,
) -> StrategyRevision:
    ...


def close_window(
    self,
    *,
    start_tick: int,
    end_tick: int,
    base_revision: int,
    fingerprint: str,
) -> AdaptiveWindow:
    ...
```

Use `(start_tick, end_tick]`, persist cursor and failure status, and require minimum samples before an applicable candidate. Default auto-apply remains false.

- [ ] **Step 7: Verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\storage tests\integration\test_legacy_import.py
```

Expected: migrations, transactions, CAS, windows, and import are deterministic and idempotent.

- [ ] **Step 8: Commit**

```powershell
git add -- app/storage app/adaptive tests/unit/storage tests/integration/test_legacy_import.py
git commit -m "feat: persist runtime and strategy revisions"
```

### Task 6: Single-Owner Agent Runtime and Windows Account Lock

**Files:**
- Create: `app/runtime/__init__.py`
- Create: `app/runtime/models.py`
- Create: `app/runtime/client.py`
- Create: `app/runtime/account_lock.py`
- Create: `app/runtime/event_queue.py`
- Create: `app/runtime/serialization.py`
- Create: `app/runtime/agent_runtime.py`
- Create: `app/runtime/runtime_manager.py`
- Create: `tests/fixtures/fake_game.py`
- Create: `tests/unit/runtime/test_account_lock.py`
- Create: `tests/unit/runtime/test_state_machine.py`
- Create: `tests/integration/test_runtime_flow.py`
- Modify: `balanced_tactic.py`

**Interfaces:**
- Produces: `RuntimeStatus`, `RuntimeSnapshot`, `GameClient`/`GameClientFactory` protocols, `AccountLock`, `RuntimeEventQueue`, `AgentRuntime`, and `RuntimeManager`.
- Consumes: `plan_turn()`, repositories, adaptive observer, `.env` key loader, and official SDK factory.

- [ ] **Step 1: Write failing account-lock tests**

```python
def test_two_lock_objects_cannot_hold_the_same_account_hash(): ...
def test_releasing_lock_allows_takeover(): ...
def test_lock_metadata_contains_runtime_and_pid_but_not_api_key(): ...
def test_cli_and_runtime_use_the_same_lock_derivation(): ...
```

- [ ] **Step 2: Write failing state-machine tests**

```python
def test_start_pause_resume_stop_are_idempotent(): ...
def test_invalid_transition_returns_domain_conflict(): ...
def test_pause_observes_turns_without_planning_or_submitting(): ...
def test_resume_waits_for_a_new_authoritative_turn(): ...
def test_authentication_error_is_redacted_and_enters_error(): ...
```

- [ ] **Step 3: Write failing runtime-flow tests**

```python
def test_duplicate_turn_submits_at_most_once(): ...
def test_agent_and_manual_received_are_both_persisted_with_source(): ...
def test_submit_precedes_slow_persistence_and_adaptive_observation(): ...
def test_stop_waits_for_current_submit_then_closes_and_releases_lock(): ...
def test_full_low_priority_queue_does_not_block_submit(): ...
```

- [ ] **Step 4: Verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests\unit\runtime tests\integration\test_runtime_flow.py`

Expected: runtime modules do not exist.

- [ ] **Step 5: Implement lock and state machine**

On Windows, hold an exclusive byte-range lock with `msvcrt.locking()` on a stable file under `data/locks/`; include a cross-platform `fcntl` implementation for CI. Derive the name from `sha256(api_key)` but never persist the key. Keep the file descriptor open for lock lifetime.

`AgentRuntime` owns one thread, one client, one memory object, processed Tick set, atomic stop/pause events, and a bounded post-submit queue. Only legal state transitions mutate status and append `runtime.status`.

- [ ] **Step 6: Implement the critical Turn order**

```python
def _handle_turn(self, turn) -> None:
    if turn.tick in self._submitted_ticks:
        self._record_duplicate(turn)
        return
    self._record_observation_in_memory(turn)
    if self._pause_requested.is_set():
        self._enqueue_snapshot_only(turn)
        return
    result = self._planner(turn, self._memory, self._profile())
    accepted = turn.submit()
    self._submitted_ticks.add(turn.tick)
    self._queue.put_critical(build_post_submit_batch(turn, result, accepted))
    self._observe_adaptive_after_submit(turn, accepted, result)
```

Handle Tick and Received separately. Retry transient SDK reconnects through official behavior; Authentication/Protocol errors are terminal and redacted. Never retry planning/submission for the same authoritative Tick.

- [ ] **Step 7: Make CLI share the lock**

Acquire `AccountLock` before constructing `ArenaHeroClient` in `play()`, release in `finally`, and preserve `Ctrl+C` and redacted terminal errors.

- [ ] **Step 8: Verify GREEN and legacy CLI tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\runtime tests\integration\test_runtime_flow.py test_balanced_tactic.py --basetemp=.codex_tmp\pytest-runtime
```

Expected: lifecycle, single-submit, receipts, queue isolation, lock, and existing planner tests pass.

- [ ] **Step 9: Commit**

```powershell
git add -- app/runtime tests/fixtures tests/unit/runtime tests/integration/test_runtime_flow.py balanced_tactic.py
git commit -m "feat: add single-owner agent runtime"
```

### Task 7: FastAPI REST, Error Contract, WebSocket Replay, and Static Shell

**Files:**
- Create: `app/config.py`
- Create: `app/errors.py`
- Create: `app/main.py`
- Create: `app/api/__init__.py`
- Create: `app/api/dependencies.py`
- Create: `app/api/agent.py`
- Create: `app/api/state.py`
- Create: `app/api/strategy.py`
- Create: `app/api/adaptive.py`
- Create: `app/api/metrics.py`
- Create: `app/api/websocket.py`
- Create: `app/observability/__init__.py`
- Create: `app/observability/redaction.py`
- Create: `app/observability/logging.py`
- Create: `frontend/index.html`
- Create: `tests/contract/test_openapi_contract.py`
- Create: `tests/contract/test_public_redaction.py`
- Create: `tests/integration/test_api.py`
- Create: `tests/integration/test_websocket.py`

**Interfaces:**
- Produces: `create_app(settings=None, services=None) -> FastAPI`, `/api/v1/*`, `/ws/v1/live`, static SPA fallbacks, request IDs, unified errors, CSP, and replay.
- Consumes: RuntimeManager and repositories from Tasks 5–6.

- [ ] **Step 1: Write failing REST and error tests**

```python
def test_health_ready_without_running_agent(client): ...
def test_state_current_returns_404_when_unavailable(client): ...
def test_start_without_key_returns_redacted_configuration_error(client): ...
def test_lifecycle_endpoints_are_idempotent(client): ...
def test_strategy_conflict_is_409_and_preserves_newer_revision(client): ...
def test_all_errors_have_code_message_request_id_and_details(client): ...
```

- [ ] **Step 2: Write failing WebSocket tests**

```python
def test_websocket_hello_contains_current_max_seq(client): ...
def test_only_committed_events_are_broadcast(client): ...
def test_events_after_replays_monotonic_events_without_duplicates(client): ...
def test_old_after_seq_returns_event_gap(client): ...
def test_slow_client_queue_is_disconnected_without_blocking_runtime(client): ...
```

- [ ] **Step 3: Write failing secret/OpenAPI tests**

```python
def test_openapi_has_no_key_authorization_or_raw_uuid_fields(client): ...
def test_public_state_and_events_replace_ids_with_session_short_ids(client): ...
def test_security_headers_and_same_origin_csp_are_present(client): ...
```

- [ ] **Step 4: Verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests\contract tests\integration\test_api.py tests\integration\test_websocket.py`

Expected: `app.main` and routes do not exist.

- [ ] **Step 5: Implement app factory and typed domain errors**

Load configuration lazily; Web app creation must not require a key. Register request-ID middleware, JSON error handlers, security headers, lifespan startup/migration/retention, and graceful manager shutdown. Map domain conflicts to 409, missing current state to 404, invalid input to 422, and terminal upstream failures to redacted 503 responses.

- [ ] **Step 6: Implement APIs and committed-event broadcaster**

All JSON is camelCase. `start` reads local `.env`, never a request credential. Strategy PUT replaces the whole profile with `expectedRevision`. WebSocket has a per-client bounded asyncio queue; database commit publishes to a thread-safe bridge, and slow clients are closed. REST is authoritative recovery.

- [ ] **Step 7: Add static SPA fallback safely**

Serve `/assets/app`, `/assets/arena-hero`, and only the approved route fallbacks. Prevent path traversal, use correct MIME types, and return `index.html` for `/`, `/strategy`, `/adaptive`, `/history`, `/settings` only.

- [ ] **Step 8: Verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\contract tests\integration\test_api.py tests\integration\test_websocket.py
```

Expected: health, lifecycle, CAS, replay, EVENT_GAP, redaction, headers, and SPA contract pass.

- [ ] **Step 9: Commit**

```powershell
git add -- app/api app/observability app/config.py app/errors.py app/main.py frontend/index.html tests/contract tests/integration/test_api.py tests/integration/test_websocket.py
git commit -m "feat: expose local runtime API and live events"
```

### Task 8: Tactical Overview, Canvas Map, Plan, Events, and Unit Table

**Files:**
- Create: `frontend/css/tokens.css`
- Create: `frontend/css/base.css`
- Create: `frontend/css/layout.css`
- Create: `frontend/css/components.css`
- Create: `frontend/css/map.css`
- Create: `frontend/css/responsive.css`
- Create: `frontend/js/app.js`
- Create: `frontend/js/router.js`
- Create: `frontend/js/api-client.js`
- Create: `frontend/js/live-connection.js`
- Create: `frontend/js/app-store.js`
- Create: `frontend/js/formatters.js`
- Create: `frontend/js/components/runtime-header.js`
- Create: `frontend/js/components/metric-card.js`
- Create: `frontend/js/components/plan-status.js`
- Create: `frontend/js/components/event-list.js`
- Create: `frontend/js/components/unit-table.js`
- Create: `frontend/js/components/entity-detail.js`
- Create: `frontend/js/components/empty-state.js`
- Create: `frontend/js/map/map-camera.js`
- Create: `frontend/js/map/map-assets.js`
- Create: `frontend/js/map/map-layers.js`
- Create: `frontend/js/map/map-hit-test.js`
- Create: `frontend/js/map/map-accessibility.js`
- Create: `frontend/js/map/tactical-map.js`
- Create: `frontend/js/views/overview.js`
- Create: `frontend/tests/app-store.test.mjs`
- Create: `frontend/tests/live-connection.test.mjs`
- Create: `frontend/tests/map-camera.test.mjs`
- Create: `tests/e2e/test_dashboard.py`
- Create: `tests/e2e/test_runtime_controls.py`
- Modify: `frontend/index.html`

**Interfaces:**
- Produces: no-build dashboard, one `AppStore`, ordered live reducer, relative-coordinate map camera, layered tactical canvas, accessible text equivalents, and runtime controls.
- Consumes: Task 7 REST/WS and supplied assets.

- [ ] **Step 1: Write browser-module RED tests**

Use a Playwright fixture page to import ES modules and assert:

```javascript
test("duplicate seq is ignored and a gap requests replay", async () => { /* ... */ });
test("EVENT_GAP replaces state from authoritative REST", async () => { /* ... */ });
test("camera preserves int64 world coordinates as relative deltas", async () => { /* ... */ });
test("UNKNOWN beacon never renders a guessed carrier", async () => { /* ... */ });
```

- [ ] **Step 2: Write failing dashboard E2E tests**

```python
def test_dashboard_renders_runtime_metrics_plan_events_and_units(page, live_server): ...
def test_pause_and_stop_wait_for_server_confirmation(page, live_server): ...
def test_accepted_and_resolved_have_distinct_labels_and_icons(page, live_server): ...
def test_enemy_removed_from_new_snapshot_disappears_from_canvas_and_details(page, live_server): ...
def test_disconnected_snapshot_is_visibly_stale(page, live_server): ...
```

- [ ] **Step 3: Verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests\e2e\test_dashboard.py tests\e2e\test_runtime_controls.py`

Expected: controls/components/map are absent.

- [ ] **Step 4: Implement the app shell and store**

Use the supplied token values and logo lockup. `AppStore` owns runtime/state/plan/metrics/strategy/adaptive/events/connection/selection/preferences. Apply REST replacements and monotonic WS events; pause on gaps and replay before applying later events. Use `textContent`, no external scripts/fonts/CDNs, and only last seq/non-sensitive preferences in localStorage.

- [ ] **Step 5: Implement the Canvas map**

Preload supplied PNGs, use SVG sprite in DOM, draw layers in the approved order with `devicePixelRatio`, relative coordinates, fog/current/history distinctions, current-only enemies, UNKNOWN Beacon marker, plans/rays/sweeps/risk counts/dependencies, and geometric fallbacks. Add pointer/touch/keyboard pan/zoom/Home and matching unit/plan/detail text.

- [ ] **Step 6: Implement responsive overview and states**

Match the supplied 1672×941 reference at wide desktop, then reflow at 1024/768/390. Implement STOPPED/STARTING/RUNNING/PAUSED/RECONNECTING/ERROR/STOPPING, loading/empty/stale states, 40px controls, focus-visible, aria-live polite, and reduced motion.

- [ ] **Step 7: Verify GREEN and screenshots**

Run:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest -q tests\e2e\test_dashboard.py tests\e2e\test_runtime_controls.py
```

Capture 1440, 1024, 768, and 390 screenshots to ignored `test-results/` and inspect against `arena-hero-ui-assets/reference/dashboard-ui-concept.png`.

- [ ] **Step 8: Commit**

```powershell
git add -- frontend tests/e2e/test_dashboard.py tests/e2e/test_runtime_controls.py
git commit -m "feat: build tactical operations dashboard"
```

### Task 9: Strategy, Adaptive, History, and Settings Views

**Files:**
- Create: `frontend/js/components/profile-diff.js`
- Create: `frontend/js/views/strategy.js`
- Create: `frontend/js/views/adaptive.js`
- Create: `frontend/js/views/history.js`
- Create: `frontend/js/views/settings.js`
- Create: `frontend/tests/profile-diff.test.mjs`
- Create: `tests/e2e/test_strategy_conflict.py`
- Create: `tests/e2e/test_adaptive_history.py`
- Modify: `frontend/js/router.js`
- Modify: `frontend/js/app.js`
- Modify: `frontend/css/components.css`
- Modify: `frontend/css/responsive.css`

**Interfaces:**
- Produces: full-profile revision editor, conflict merge, candidate review/apply/reject/rollback, fixed-window reports, real Tick charts, and non-sensitive settings.
- Consumes: Task 7 strategy/adaptive/metrics APIs and Task 8 AppStore/components.

- [ ] **Step 1: Write failing profile diff and conflict tests**

```javascript
test("diff emits only changed bounded fields with old and new values", () => { /* ... */ });
test("three-way merge preserves the user draft after a server revision change", () => { /* ... */ });
```

```python
def test_revision_conflict_keeps_draft_and_shows_server_user_merge(page, live_server): ...
def test_saved_profile_waits_for_turn_boundary_activation(page, live_server): ...
```

- [ ] **Step 2: Write failing adaptive/history tests**

```python
def test_candidate_page_shows_window_samples_score_per_tick_fingerprint_and_diff(page, live_server): ...
def test_invalid_or_stale_candidate_has_disabled_apply_with_reason(page, live_server): ...
def test_history_uses_discrete_tick_axis_and_event_markers(page, live_server): ...
def test_settings_never_render_keys_or_edit_base_url(page, live_server): ...
```

- [ ] **Step 3: Verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests\e2e\test_strategy_conflict.py tests\e2e\test_adaptive_history.py`

Expected: routes are shell-only or missing required content.

- [ ] **Step 4: Implement strategy and adaptive views**

Group fields by economy/Beacon/defense/combat/recovery-production/adaptive, render server schema/ranges, send complete profile + expected revision, preserve draft on 409, and show pending activation. Candidate actions require expected revision; invalid, stale, undersampled, changed-fingerprint, pending-manual, or LETHAL candidates are disabled with text reasons.

- [ ] **Step 5: Implement history and settings views**

Draw discrete Tick series using Canvas/SVG only, mark overflow/Core damage/Beacon loss, and link Tick selections to public state/plan/results. Settings expose only retention/log level/map/UI preferences and read-only provider host/model; never keys, Base URL editing, shell, skill mutation, or raw database download.

- [ ] **Step 6: Verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\e2e\test_strategy_conflict.py tests\e2e\test_adaptive_history.py
```

Expected: CAS/merge, candidate safety, history, and settings redaction pass at desktop and mobile.

- [ ] **Step 7: Commit**

```powershell
git add -- frontend tests/e2e/test_strategy_conflict.py tests/e2e/test_adaptive_history.py
git commit -m "feat: add strategy adaptive and history consoles"
```

### Task 10: Adaptive Coordinator Persistence and Transport Hardening

**Files:**
- Create: `app/adaptive/models.py`
- Create: `app/adaptive/scoring.py`
- Create: `app/adaptive/projection.py`
- Create: `app/adaptive/transport.py`
- Create: `app/adaptive/coordinator.py`
- Create: `tests/unit/adaptive/test_scoring.py`
- Create: `tests/unit/adaptive/test_projection.py`
- Create: `tests/unit/adaptive/test_transport.py`
- Create: `tests/integration/test_adaptive_cycle.py`
- Modify: `adaptive_strategy.py`
- Modify: `test_adaptive_strategy.py`

**Interfaces:**
- Produces: SQLite-backed `AdaptiveCoordinator`, exact `Scorecard`, bounded `LLMProjection`, strict `OpenAICompatibleTransport`, candidate state machine, and root compatibility exports.
- Consumes: Task 5 repositories, project skill bundle/fingerprint, and existing bounded `StrategyProfile`.

- [ ] **Step 1: Write failing fixed-window and score tests**

```python
def test_restart_resumes_after_persisted_cursor_without_tick_zero_replay(): ...
def test_window_is_start_exclusive_end_inclusive_and_non_overlapping(): ...
def test_score_per_tick_is_reported_with_sample_count(): ...
def test_overflow_has_explicit_negative_weight_and_exact_ties_have_no_epsilon(): ...
def test_negative_baseline_rollback_threshold_is_symmetric(): ...
```

- [ ] **Step 2: Write failing projection/transport tests**

```python
def test_projection_removes_names_ids_coordinates_routes_plans_and_prompts(): ...
def test_telemetry_is_bounded_and_delimited_as_untrusted_data(): ...
def test_parser_requires_one_complete_json_object_without_prefix_or_suffix(): ...
def test_missing_fingerprint_is_rejected_in_both_roles(): ...
def test_response_body_limit_and_malformed_choices_raise_redacted_llm_error(): ...
def test_disallowed_private_or_metadata_base_url_fails_closed(): ...
```

- [ ] **Step 3: Write failing candidate lifecycle tests**

```python
def test_low_sample_candidate_cannot_apply(): ...
def test_changed_revision_or_fingerprint_marks_candidate_stale(): ...
def test_manual_pending_revision_blocks_auto_apply(): ...
def test_lethal_runtime_state_blocks_profile_activation(): ...
def test_rollback_creates_new_revision_and_audit_event(): ...
```

- [ ] **Step 4: Verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests\unit\adaptive tests\integration\test_adaptive_cycle.py`

Expected: new SQLite-backed adaptive modules do not exist.

- [ ] **Step 5: Implement bounded projection and strict transport**

Use only aggregate numeric/bucket/reason-code fields. Validate non-negative finite metrics. Parse with `json.JSONDecoder().raw_decode(text.strip())` and require `end == len(source)`. Require fingerprints. Resolve/validate provider URLs, deny unsafe address classes and redirects, and allow localhost HTTP only behind a dedicated development setting. Keep verbosity/reasoning behavior compatible.

- [ ] **Step 6: Implement persisted coordinator and compatibility layer**

Coordinator seals windows transactionally, runs evaluator/designer off the submit path, stores reports/candidates, validates against current revision/fingerprint/minimum samples, defaults manual, and applies only at Turn boundaries. Root `adaptive_strategy.py` re-exports compatible names and retains dotenv/load-key behavior.

- [ ] **Step 7: Verify GREEN and old adaptive tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\adaptive tests\integration\test_adaptive_cycle.py test_adaptive_strategy.py --basetemp=.codex_tmp\pytest-adaptive-sqlite
```

Expected: new persistence/security tests and old transport/profile integrations pass.

- [ ] **Step 8: Commit**

```powershell
git add -- app/adaptive adaptive_strategy.py tests/unit/adaptive tests/integration/test_adaptive_cycle.py test_adaptive_strategy.py
git commit -m "feat: persist and harden adaptive cycles"
```

### Task 11: Documentation, Long-Run Verification, and Release Gate

**Files:**
- Create: `tests/integration/test_long_run.py`
- Create: `tests/contract/test_event_schema.py`
- Create: `tests/contract/test_static_assets.py`
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `MVP开发指导.md`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: documented Windows setup/start/migration/operation, current test count without stale fixed wording, 10,000-Turn fake soak, event/static contracts, and release evidence.
- Consumes: every earlier task.

- [ ] **Step 1: Write failing long-run and static contracts**

```python
def test_ten_thousand_fake_turns_keep_one_submit_per_tick_monotonic_seq_and_bounded_queues(): ...
def test_every_event_type_matches_schema_version_one_envelope(): ...
def test_all_manifest_assets_are_served_with_expected_mime_type(): ...
def test_public_artifacts_contain_no_secret_or_full_uuid_patterns(): ...
```

- [ ] **Step 2: Verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests\integration\test_long_run.py tests\contract\test_event_schema.py tests\contract\test_static_assets.py`

Expected: missing long-run fixture/schema/static assertions fail.

- [ ] **Step 3: Finish documentation and defaults**

README must cover:

```powershell
uv sync --python 3.11 --group dev
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Document CLI compatibility, shared lock conflict, Web no-key startup, `.env` secrecy, adaptive auto-apply `0`, old `adaptive/` idempotent import/no deletion, database/retention, pause semantics, accepted vs resolved, map fact/hint/fog distinctions, and troubleshooting. Do not promise first place.

- [ ] **Step 4: Verify the complete release gate**

Run:

```powershell
.\.venv\Scripts\python.exe -m compileall -q app tests
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\bandit.exe -q -r app
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.codex_tmp\pytest-release
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\pip-audit.exe
git diff --check
git status --short
```

Expected: all commands pass; only explicitly intended tracked changes remain; `.env`, `adaptive/`, `data/`, DB/WAL, logs, Playwright results, and credentials are absent from Git.

- [ ] **Step 5: Run browser and Windows lock smoke tests**

Start the app without a key using an isolated missing dotenv and confirm health/UI work. Run two test processes against the same fake account hash and confirm the second receives the lock conflict. Run Playwright at 1440/1024/768/390 and inspect screenshots. Do not connect the real Arena Hero account unless the user explicitly requests it.

- [ ] **Step 6: Commit**

```powershell
git add -- README.md .env.example 'MVP开发指导.md' .github/workflows/ci.yml tests/integration/test_long_run.py tests/contract/test_event_schema.py tests/contract/test_static_assets.py
git commit -m "docs: complete local MVP release workflow"
```

## Plan Self-Review

- Spec coverage: Tasks 2–4 cover all M0 correctness requirements; Tasks 5–7 cover storage/runtime/API/WS; Tasks 8–9 cover all five UI routes and supplied assets; Task 10 covers the required local adaptive hardening; Task 11 covers migration docs, soak, security, browser, and Windows release gates.
- Scope: no Postgres, Redis, public authentication, remote multi-tenancy, Prometheus, or browser key editing is introduced.
- Interface consistency: `PlannerResult` flows Task 4 → Runtime Task 6 → persistence/API Tasks 5/7 → UI Task 8. `StrategyRevision` and fixed windows flow Task 5 → API Task 7 → views Task 9 → coordinator Task 10.
- Completeness scan: every task names RED tests, implementation seams, verification commands, and a focused commit; no unfinished markers remain.
- Safety: the plan never reads or prints the real `.env`; live Arena Hero connectivity remains outside automated verification.
