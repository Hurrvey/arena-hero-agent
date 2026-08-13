# Persistent Exploration and Contact Response Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build account-scoped persistent exploration, a three-state tactical fog map, frontier-based non-oscillating Worker scouting, and legal evacuation/interception behavior for visible enemies away from the Core.

**Architecture:** Keep v0.14 visibility geometry in the existing pure strategy layer, represent unbounded exploration as sparse `32 x 32` bitmask chunks, and persist only changed chunks under a SHA-256 account scope. A narrow runtime exploration coordinator lazily loads a bounded working set before planning and persists its delta only after `turn.submit()`; the browser reads bounded viewport windows, while frontier and contact planners consume immutable in-memory projections without importing SQLite or FastAPI.

**Tech Stack:** Python `>=3.11,<3.13`, `arena-hero>=0.2.9,<0.3`, FastAPI, standard-library SQLite WAL, standard-library dataclasses/heapq/hashlib, vanilla ES modules and Canvas 2D, pytest, Node test runner, Python Playwright.

## Global Constraints

- Arena Hero gameplay contract is v0.14 and the official SDK floor is 0.2.9.
- Each `Turn` is authoritative; hidden enemies, old Beacon carriers, and old resource cells are never current facts.
- Visibility uses the existing radii Core 5, Worker 3, Vanguard 4, Ranger 5, Manhattan distance, and supercover obstacle blocking.
- Exploration is isolated by `SHA-256(API Key)`; the API key and account scope never reach REST, WebSocket, DOM, logs, adaptive prompts, or commits.
- `explored` and permanent obstacle bits may persist; enemies, Beacon carrier state, and resource existence may not persist in exploration storage.
- One exploration chunk is exactly `32 x 32` cells and each bitmask is exactly 128 bytes.
- One viewport request is at most `96 x 96` cells and cannot choose an account scope.
- Runtime planning uses only a bounded loaded working set; SQLite busy/corrupt exploration data degrades to current visibility and never blocks plan submission.
- Planner decisions remain deterministic Python. LLM evaluation remains post-submit, bounded, redacted, and unable to emit Tick actions or coordinates.
- Core `APPROACH`, `ATTACK`, and `LETHAL` defense outrank remote contact pursuit; when Core is safe and a Vanguard exists, one Vanguard remains in the 1-2 cell defense ring.
- Investigation after contact loss lasts at most 3 Tick, permits movement only, and never uses a stale enemy UUID for a precise attack.
- Every behavior change follows RED -> GREEN, preserves the CLI and Web runtime, and ends in a focused commit.
- Never read, print, diff, or commit `.env`, `adaptive/`, or `data/` during implementation or verification.

---

### Task 1: Sparse Exploration Domain Model

**Files:**
- Create: `app/strategy/exploration.py`
- Create: `tests/unit/strategy/test_exploration.py`
- Modify: `app/strategy/__init__.py`

**Interfaces:**
- Consumes: `Position` and `validate_position` from `app.strategy.models`.
- Produces: `CHUNK_SIZE`, `MASK_BYTES`, `ChunkKey`, `ExplorationChunk`, `ExplorationDelta`, `ExplorationWindow`, `chunk_key()`, `bit_index()`, and `ExplorationMap`.
- `ExplorationMap.observe()` is monotonic and returns only chunks changed by that observation.
- `ExplorationMap.window()` is the common source for REST windows and frontier candidate generation.

- [ ] **Step 1: Write failing chunk-codec and monotonic observation tests**

Create `tests/unit/strategy/test_exploration.py` with these concrete cases:

```python
from app.strategy.exploration import (
    MASK_BYTES,
    ChunkKey,
    ExplorationChunk,
    ExplorationMap,
    bit_index,
    chunk_key,
)


def test_negative_coordinates_have_stable_chunk_and_bit_indexes() -> None:
    assert chunk_key((-1, -1)) == ChunkKey(-1, -1)
    assert bit_index((-1, -1)) == 1023
    assert chunk_key((-32, -32)) == ChunkKey(-1, -1)
    assert bit_index((-32, -32)) == 0
    assert chunk_key((-33, 0)) == ChunkKey(-2, 0)
    assert bit_index((-33, 0)) == 31


def test_observation_is_monotonic_and_returns_only_changed_chunks() -> None:
    exploration = ExplorationMap()
    first = exploration.observe(
        visible_cells=frozenset({(-1, -1), (0, 0), (1, 0)}),
        visible_obstacles=frozenset({(1, 0)}),
        tick=10,
    )
    second = exploration.observe(
        visible_cells=frozenset({(0, 0), (1, 0)}),
        visible_obstacles=frozenset({(1, 0)}),
        tick=11,
    )

    assert {item.key for item in first.chunks} == {ChunkKey(-1, -1), ChunkKey(0, 0)}
    assert second.chunks == ()
    assert exploration.is_explored((-1, -1))
    assert exploration.is_explored((0, 0))
    assert exploration.is_known_obstacle((1, 0))


def test_loaded_chunks_merge_by_or_without_erasing_newer_bits() -> None:
    exploration = ExplorationMap()
    exploration.observe(
        visible_cells=frozenset({(1, 1)}),
        visible_obstacles=frozenset(),
        tick=12,
    )
    old_mask = bytearray(MASK_BYTES)
    old_mask[0] = 1
    exploration.merge_loaded(
        (
            ExplorationChunk(
                key=ChunkKey(0, 0),
                explored_mask=bytes(old_mask),
                obstacle_mask=bytes(MASK_BYTES),
                last_seen_tick=5,
                revision=2,
            ),
        ),
        account_revision=2,
    )

    assert exploration.is_explored((0, 0))
    assert exploration.is_explored((1, 1))


def test_window_contains_only_requested_explored_and_obstacle_cells() -> None:
    exploration = ExplorationMap()
    exploration.observe(
        visible_cells=frozenset({(-1, 0), (0, 0), (3, 3), (50, 50)}),
        visible_obstacles=frozenset({(3, 3)}),
        tick=20,
    )

    window = exploration.window(min_x=-1, min_y=0, max_x=3, max_y=3)

    assert window.explored_cells == ((-1, 0), (0, 0), (3, 3))
    assert window.known_obstacle_cells == ((3, 3),)
```

- [ ] **Step 2: Run the new test to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\strategy\test_exploration.py
```

Expected: collection fails with `ModuleNotFoundError: No module named 'app.strategy.exploration'`.

- [ ] **Step 3: Implement the immutable chunk values and mutable bounded map**

Create `app/strategy/exploration.py`. Use exactly these public values and validation rules:

```python
from __future__ import annotations

from dataclasses import dataclass

from .models import Position, validate_position

CHUNK_SIZE = 32
MASK_BYTES = CHUNK_SIZE * CHUNK_SIZE // 8


@dataclass(frozen=True, order=True, slots=True)
class ChunkKey:
    x: int
    y: int

    def __post_init__(self) -> None:
        if any(not isinstance(value, int) or isinstance(value, bool) for value in (self.x, self.y)):
            raise ValueError("chunk coordinates must be integers")


@dataclass(frozen=True, slots=True)
class ExplorationChunk:
    key: ChunkKey
    explored_mask: bytes
    obstacle_mask: bytes
    last_seen_tick: int
    revision: int

    def __post_init__(self) -> None:
        if len(self.explored_mask) != MASK_BYTES or len(self.obstacle_mask) != MASK_BYTES:
            raise ValueError("exploration masks must contain exactly 128 bytes")
        if self.last_seen_tick < 0 or self.revision < 0:
            raise ValueError("tick and revision must be non-negative")
        if any(obstacle & ~explored for obstacle, explored in zip(self.obstacle_mask, self.explored_mask, strict=True)):
            raise ValueError("known obstacles must also be explored")


@dataclass(frozen=True, slots=True)
class ExplorationDelta:
    tick: int
    chunks: tuple[ExplorationChunk, ...]
    touched_keys: tuple[ChunkKey, ...] = ()


@dataclass(frozen=True, slots=True)
class ExplorationWindow:
    revision: int
    explored_cells: tuple[Position, ...]
    known_obstacle_cells: tuple[Position, ...]


def chunk_key(position: Position) -> ChunkKey:
    validate_position(position)
    return ChunkKey(position[0] // CHUNK_SIZE, position[1] // CHUNK_SIZE)


def bit_index(position: Position) -> int:
    key = chunk_key(position)
    local_x = position[0] - key.x * CHUNK_SIZE
    local_y = position[1] - key.y * CHUNK_SIZE
    return local_y * CHUNK_SIZE + local_x
```

Implement `ExplorationMap` with an internal `dict[ChunkKey, ExplorationChunk]`, an `account_revision` property, and these exact methods:

```python
class ExplorationMap:
    def merge_loaded(
        self,
        chunks: tuple[ExplorationChunk, ...],
        *,
        account_revision: int,
    ) -> None: ...

    def observe(
        self,
        *,
        visible_cells: frozenset[Position],
        visible_obstacles: frozenset[Position],
        tick: int,
    ) -> ExplorationDelta: ...

    def is_explored(self, position: Position) -> bool: ...
    def is_known_obstacle(self, position: Position) -> bool: ...
    def loaded_keys(self) -> frozenset[ChunkKey]: ...
    def known_obstacle_cells(self) -> tuple[Position, ...]: ...
    def last_seen_tick(self, position: Position) -> int: ...
    def evict_except(self, keep: frozenset[ChunkKey], *, max_chunks: int) -> None: ...
    def window(self, *, min_x: int, min_y: int, max_x: int, max_y: int) -> ExplorationWindow: ...
```

The implementation must manipulate masks with `byte_index, offset = divmod(bit_index(position), 8)`, set bits with OR, merge loaded masks with byte-wise OR, sort chunks by `ChunkKey`, and sort returned cells by `(x, y)`. `observe()` returns a chunk in `delta.chunks` only when at least one explored or obstacle bit changed. It also returns every currently visible chunk key in `delta.touched_keys`, so the repository can advance chunk-level `last_seen_tick` without incrementing the account revision. A repeated observation therefore has `chunks == ()` but nonempty deterministic `touched_keys`.

Export the new public names from `app/strategy/__init__.py`.

- [ ] **Step 4: Verify GREEN and existing visibility regression**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\strategy\test_exploration.py tests\unit\strategy\test_visibility.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit the pure exploration model**

```powershell
git add -- app/strategy/exploration.py app/strategy/__init__.py tests/unit/strategy/test_exploration.py
git commit -m "feat: add sparse exploration map"
```

### Task 2: Account-Scoped SQLite Exploration Repository

**Files:**
- Create: `app/storage/exploration_repository.py`
- Create: `tests/unit/storage/test_exploration_repository.py`
- Modify: `app/storage/migrations.py`
- Modify: `app/storage/__init__.py`
- Modify: `app/runtime/account_lock.py`
- Modify: `app/runtime/service_factory.py`
- Modify: `tests/unit/storage/test_database.py`
- Modify: `tests/unit/runtime/test_account_lock.py`

**Interfaces:**
- Consumes: Task 1 `ChunkKey`, `ExplorationChunk`, `ExplorationDelta`, and `ExplorationWindow`.
- Produces: `account_scope_from_api_key(api_key: str) -> str` and `ExplorationRepository` methods `load_chunks()`, `merge_delta()`, `window()`, and `revision()`.
- `RuntimeServicesFactory.build()` creates `runtime_sessions.account_hash` from the same scope used by the cross-process account lock.

- [ ] **Step 1: Write failing migration, isolation, idempotency, and secrecy tests**

Add a migration assertion to `tests/unit/storage/test_database.py`:

```python
def test_exploration_migration_is_present_and_idempotent(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    database.initialize()

    with database.connect() as connection:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        chunk_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='exploration_chunks'"
        ).fetchone()[0]

    assert versions == [(1,), (2,), (3,), (4,)]
    assert "length(explored_mask) = 128" in chunk_sql
    assert "length(obstacle_mask) = 128" in chunk_sql
```

Create `tests/unit/storage/test_exploration_repository.py`:

```python
from app.storage import Database, ExplorationRepository
from app.strategy.exploration import ChunkKey, ExplorationChunk, ExplorationDelta, MASK_BYTES


def chunk(*, explored: int, obstacle: int = 0, tick: int = 7) -> ExplorationChunk:
    explored_mask = bytearray(MASK_BYTES)
    obstacle_mask = bytearray(MASK_BYTES)
    explored_mask[0] = explored
    obstacle_mask[0] = obstacle
    return ExplorationChunk(ChunkKey(0, 0), bytes(explored_mask), bytes(obstacle_mask), tick, 0)


def test_merge_is_idempotent_and_revisions_only_change_for_new_bits(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    repository = ExplorationRepository(database)

    first = repository.merge_delta("account-a", ExplorationDelta(7, (chunk(explored=1),)))
    repeated = repository.merge_delta("account-a", ExplorationDelta(8, (chunk(explored=1),)))
    expanded = repository.merge_delta("account-a", ExplorationDelta(9, (chunk(explored=3),)))

    assert (first, repeated, expanded) == (1, 1, 2)
    loaded_revision, loaded = repository.load_chunks("account-a", (ChunkKey(0, 0),))
    assert loaded_revision == 2
    assert loaded[0].explored_mask[0] == 3
    assert loaded[0].revision == 2


def test_accounts_are_isolated_and_window_is_bounded_to_the_requested_scope(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    repository = ExplorationRepository(database)
    repository.merge_delta("account-a", ExplorationDelta(1, (chunk(explored=1),)))
    repository.merge_delta("account-b", ExplorationDelta(1, (chunk(explored=2),)))

    account_a = repository.window("account-a", min_x=0, min_y=0, max_x=1, max_y=0)
    account_b = repository.window("account-b", min_x=0, min_y=0, max_x=1, max_y=0)

    assert account_a.explored_cells == ((0, 0),)
    assert account_b.explored_cells == ((1, 0),)


def test_corrupt_mask_is_rejected_by_sqlite_check(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    repository = ExplorationRepository(database)

    with database.connect() as connection:
        try:
            connection.execute(
                "INSERT INTO exploration_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("account", 0, 0, b"short", bytes(MASK_BYTES), 1, 1, "now"),
            )
            connection.commit()
        except Exception:
            connection.rollback()
        else:
            raise AssertionError("invalid masks must not commit")

    assert repository.load_chunks("account", (ChunkKey(0, 0),))[1] == ()
```

Add to `tests/unit/runtime/test_account_lock.py`:

```python
from app.runtime.account_lock import account_scope_from_api_key


def test_account_scope_is_stable_nonsecret_sha256() -> None:
    scope = account_scope_from_api_key("private-key")
    assert len(scope) == 64
    assert scope == account_scope_from_api_key("private-key")
    assert scope != account_scope_from_api_key("other-key")
    assert "private-key" not in scope
```

- [ ] **Step 2: Run focused tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\storage\test_database.py tests\unit\storage\test_exploration_repository.py tests\unit\runtime\test_account_lock.py
```

Expected: imports fail for `ExplorationRepository`/`account_scope_from_api_key`, and the migration version assertion fails.

- [ ] **Step 3: Add migration 4 and the repository transaction**

Append migration version `4` in `app/storage/migrations.py` with the two tables and exact constraints from the approved design. Add an index on `exploration_chunks(account_scope, revision)`.

Implement `app/storage/exploration_repository.py` with this public shape:

```python
class ExplorationRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def revision(self, account_scope: str) -> int: ...

    def load_chunks(
        self,
        account_scope: str,
        keys: tuple[ChunkKey, ...],
        *,
        busy_timeout_ms: int = 0,
    ) -> tuple[int, tuple[ExplorationChunk, ...]]: ...

    def merge_delta(self, account_scope: str, delta: ExplorationDelta) -> int: ...

    def window(
        self,
        account_scope: str,
        *,
        min_x: int,
        min_y: int,
        max_x: int,
        max_y: int,
    ) -> ExplorationWindow: ...
```

`merge_delta()` must execute `BEGIN IMMEDIATE`, load every addressed row, byte-wise OR old and incoming masks, validate `obstacle_mask & ~explored_mask == 0`, increment `exploration_accounts.revision` once only when any mask changed, upsert all changed chunks with the same revision, advance `last_seen_tick` with `MAX(old, delta.tick)` for every existing or changed `delta.touched_keys` row, and commit. A touch-only delta updates the chunk timestamp but does not rewrite masks or increment the account revision. Replaying the same Tick is idempotent.

`load_chunks()` must cap input at 64 unique sorted keys, issue `PRAGMA busy_timeout = <busy_timeout_ms>`, construct a parameterized `(chunk_x = ? AND chunk_y = ?)` predicate, skip malformed rows by catching `ValueError` around `ExplorationChunk`, and never interpolate an account scope into SQL.

`window()` must reject reversed coordinates or an area above 9216 with `ValueError`, derive intersecting chunk keys, load them, reconstruct only cells inside inclusive bounds, and return sorted tuples.

Export `ExplorationRepository` from `app/storage/__init__.py`.

- [ ] **Step 4: Unify account hashing and remove the `configured` session scope**

In `app/runtime/account_lock.py`, add:

```python
def account_scope_from_api_key(api_key: str) -> str:
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("api_key must be a non-empty string")
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()
```

Change `AccountLock.from_api_key()` to call this helper. In `RuntimeServicesFactory.build()`, replace `create_session(account_hash="configured")` with:

```python
account_scope = account_scope_from_api_key(api_key)
session = self.runtime_store.create_session(account_hash=account_scope)
```

Store the scope in a private factory field and expose a read-only `account_scope` property for server-internal API dependency resolution; never include it in a returned mapping.

- [ ] **Step 5: Verify repository GREEN and no secret disclosure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\storage\test_database.py tests\unit\storage\test_exploration_repository.py tests\unit\runtime\test_account_lock.py
.\.venv\Scripts\python.exe -m pytest -q tests\contract\test_public_redaction.py tests\contract\test_openapi_contract.py
```

Expected: all focused tests pass and the OpenAPI/redaction contract still contains no API key or account scope field.

- [ ] **Step 6: Commit account-scoped exploration storage**

```powershell
git add -- app/storage/migrations.py app/storage/exploration_repository.py app/storage/__init__.py app/runtime/account_lock.py app/runtime/service_factory.py tests/unit/storage/test_database.py tests/unit/storage/test_exploration_repository.py tests/unit/runtime/test_account_lock.py
git commit -m "feat: persist account scoped exploration"
```

### Task 3: Runtime Exploration Observation and Post-Submit Persistence

**Files:**
- Create: `app/runtime/exploration.py`
- Create: `tests/unit/runtime/test_exploration_runtime.py`
- Modify: `balanced_tactic.py`
- Modify: `app/runtime/models.py`
- Modify: `app/runtime/agent_runtime.py`
- Modify: `app/runtime/service_factory.py`
- Modify: `app/strategy/models.py`
- Modify: `app/main.py`
- Modify: `app/api/dependencies.py`
- Modify: `app/strategy/planner.py`
- Modify: `app/strategy/planner_adapter.py`
- Modify: `tests/integration/test_runtime_flow.py`
- Modify: `tests/unit/strategy/test_models.py`
- Modify: `tests/unit/runtime/test_serialization.py`

**Interfaces:**
- Consumes: Tasks 1-2 exploration map/repository and existing `compute_visible_cells()`.
- Produces: `ExplorationObservation`, `ExplorationRuntime.observe_turn()`, `ExplorationRuntime.persist()`, and `RuntimeBatch.exploration`.
- Guarantees: current visibility is observed for running and paused Turns; only the post-submit persistence path writes exploration deltas.

- [ ] **Step 1: Write failing observation, bounded lazy-load, and fail-open tests**

Create `tests/unit/runtime/test_exploration_runtime.py` around a repository fake and `SimpleNamespace` Turn:

```python
from types import SimpleNamespace
from uuid import UUID

from app.runtime.exploration import ExplorationRuntime
from app.strategy.exploration import ChunkKey, ExplorationMap


class Repository:
    def __init__(self) -> None:
        self.loads: list[tuple[ChunkKey, ...]] = []
        self.saved = []

    def load_chunks(self, account_scope, keys, *, busy_timeout_ms=0):
        assert account_scope == "scope"
        assert busy_timeout_ms == 0
        self.loads.append(keys)
        return 0, ()

    def merge_delta(self, account_scope, delta):
        self.saved.append((account_scope, delta))
        return 1


def turn() -> SimpleNamespace:
    core = SimpleNamespace(id=UUID(int=1), position=(0, 0), hp=5, shield=5)
    worker = SimpleNamespace(
        id=UUID(int=2), position=(3, 0), hp=2, shield=0, unit_type="WORKER"
    )
    return SimpleNamespace(
        tick=10,
        core=core,
        units=(worker,),
        obstacle_cells=frozenset({(1, 0)}),
    )


def test_observe_loads_a_bounded_working_set_and_marks_current_visibility() -> None:
    repository = Repository()
    runtime = ExplorationRuntime(repository, "scope", max_loaded_chunks=64)
    memory = SimpleNamespace(exploration=ExplorationMap(), current_visible_cells=frozenset())

    observation = runtime.observe_turn(turn(), memory)

    assert repository.loads
    assert len(repository.loads[0]) <= 64
    assert (0, 0) in observation.current_cells
    assert (1, 0) in observation.current_cells
    assert (2, 0) not in observation.current_cells
    assert memory.current_visible_cells == observation.current_cells
    assert observation.delta.chunks


def test_persist_happens_only_when_explicitly_called_after_observation() -> None:
    repository = Repository()
    runtime = ExplorationRuntime(repository, "scope")
    memory = SimpleNamespace(exploration=ExplorationMap(), current_visible_cells=frozenset())
    observation = runtime.observe_turn(turn(), memory)
    assert repository.saved == []

    revision = runtime.persist(observation)

    assert revision == 1
    assert repository.saved == [("scope", observation.delta)]


def test_busy_repository_degrades_to_current_visibility() -> None:
    class BusyRepository(Repository):
        def load_chunks(self, account_scope, keys, *, busy_timeout_ms=0):
            raise OSError("busy")

    runtime = ExplorationRuntime(BusyRepository(), "scope")
    memory = SimpleNamespace(exploration=ExplorationMap(), current_visible_cells=frozenset())

    observation = runtime.observe_turn(turn(), memory)

    assert (0, 0) in observation.current_cells
    assert observation.loaded_history is False
```

Add an integration test to `tests/integration/test_runtime_flow.py` using the existing fake Turn/client to assert call order:

```python
def test_exploration_observes_before_plan_and_persists_after_submit() -> None:
    calls = []
    exploration = SimpleNamespace(
        observe_turn=lambda turn, memory: calls.append("observe") or SimpleNamespace(
            current_cells=frozenset({(0, 0)}), delta=None, base_revision=0, loaded_history=True
        ),
        persist=lambda observation: calls.append("persist") or 1,
    )
    planner = lambda turn, memory, profile: calls.append("plan") or SimpleNamespace(plan={})
    turn = FakeTurn(8, calls=calls)
    runtime = make_runtime(turn, planner=planner, exploration=exploration)

    runtime.handle_event(turn)

    assert calls == ["observe", "plan", "submit", "persist"]
```

Use the actual helper names already present in that test file when inserting the case; the required assertion and call order must remain exact.

- [ ] **Step 2: Run the runtime tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\runtime\test_exploration_runtime.py tests\integration\test_runtime_flow.py
```

Expected: collection fails for `app.runtime.exploration`, then constructor/signature assertions fail until the runtime hook exists.

- [ ] **Step 3: Implement the bounded exploration runtime adapter**

Create `app/runtime/exploration.py` with:

```python
@dataclass(frozen=True, slots=True)
class ExplorationObservation:
    tick: int
    current_cells: frozenset[Position]
    delta: ExplorationDelta
    base_revision: int
    loaded_history: bool


class ExplorationRuntime:
    def __init__(
        self,
        repository: ExplorationRepository,
        account_scope: str,
        *,
        max_loaded_chunks: int = 256,
        load_radius: int = 48,
    ) -> None: ...

    def observe_turn(self, turn: object, memory: object) -> ExplorationObservation: ...
    def persist(self, observation: ExplorationObservation) -> int: ...
```

`observe_turn()` must:

1. Project the living controlled Core and Units into `EntitySnapshot` values using the existing `balanced_tactic._unit_snapshot()` semantics, but move the generic projection into `app.strategy.models.entity_snapshot_from_view()` first so `app.runtime.exploration` does not import the root tactic module. Cover Core, Worker, Vanguard, Ranger, unknown Unit type, dead objects, and non-UUID byte ordering in `tests/unit/strategy/test_models.py`.
2. Derive at most 64 chunk keys intersecting `load_radius` around those entities, excluding `memory.exploration.loaded_keys()`.
3. Call `load_chunks(..., busy_timeout_ms=0)` inside `try/except (sqlite3.Error, OSError, ValueError)` and merge valid rows.
4. Compute current cells with `compute_visible_cells()` and current plus loaded permanent obstacles.
5. Call `memory.exploration.observe()` with current visible obstacles only.
6. Update `memory.current_visible_cells` and `memory.exploration_observed_tick`; after eviction, replace `memory.known_obstacles` with the current Turn obstacles plus `memory.exploration.known_obstacle_cells()` so the live route set stays bounded instead of retaining every historical chunk forever.
7. Evict remote loaded chunks to `max_loaded_chunks`, always retaining chunks touched by controlled entities/current visibility.

`persist()` catches `sqlite3.Error`/`OSError`/`ValueError`, logs only `"exploration persistence degraded"`, and returns `observation.base_revision` on failure. It never logs the account scope or mask bytes.

- [ ] **Step 4: Wire observation through `TacticMemory`, `RuntimeBatch`, and `AgentRuntime`**

Add to `TacticMemory`:

```python
exploration: ExplorationMap = field(default_factory=ExplorationMap)
current_visible_cells: frozenset[tuple[int, int]] = field(default_factory=frozenset)
exploration_observed_tick: int | None = None
exploration_diagnostics: dict[str, object] = field(default_factory=dict)
```

Add to `RuntimeBatch`:

```python
exploration: ExplorationObservation | None = None
```

Give `AgentRuntime.__init__` an optional `exploration` dependency. At the start of `_handle_turn()` after dedupe checks but before the pause branch, call `observation = self._exploration.observe_turn(turn, self._memory)` when configured. Include the observation in both `SNAPSHOT_ONLY` and `TURN_SUBMITTED` batches. Because `_persistence(batch)` already runs after `turn.submit()` for an active Turn, `RuntimeServicesFactory.persist()` must call `self._exploration_runtime.persist(batch.exploration)` there and use the returned revision in the public state. For a paused `SNAPSHOT_ONLY` Turn, persist the delta only after the authoritative snapshot has committed. Thus no exploration write occurs before an active plan submission or before a paused snapshot commit.

When `choose_actions()` is invoked by the standalone CLI without a runtime coordinator, compute and observe the current visibility only if `memory.exploration_observed_tick != turn.tick`; this preserves CLI behavior and keeps repeated observation idempotent.

Instantiate `ExplorationRepository` in `app/main.py`, add it to `Services`, pass it through `build_runtime_manager()`, and create one `ExplorationRuntime(repository, account_scope)` in `RuntimeServicesFactory.build()`.

- [ ] **Step 5: Publish current visibility and diagnostics without historical objects**

Extend `PlannerDiagnostics` with:

```python
exploration: Mapping[str, object] = field(default_factory=dict)
contact: Mapping[str, object] = field(default_factory=dict)
```

Have `plan_turn()` copy only aggregate `memory.exploration_diagnostics` into `diagnostics.exploration`. In `RuntimeServicesFactory.persist()`, add this public state projection after `serialize_turn()`:

```python
if batch.exploration is not None:
    revision = self._exploration_runtime.persist(batch.exploration)
    public_state["visibility"] = {
        "tick": int(batch.exploration.tick),
        "currentCells": [list(cell) for cell in sorted(batch.exploration.current_cells)],
        "explorationRevision": int(revision),
    }
```

Do not put exploration masks, account scope, historical enemies, or remembered resources into `public_state`.

- [ ] **Step 6: Verify runtime GREEN, pause observation, and public redaction**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\runtime\test_exploration_runtime.py tests\integration\test_runtime_flow.py tests\unit\runtime\test_serialization.py
.\.venv\Scripts\python.exe -m pytest -q test_balanced_tactic.py tests\integration\test_planner_adapter.py --basetemp=.codex_tmp\pytest-exploration-runtime
```

Expected: observation/submit ordering passes, paused snapshots include current visibility, and all existing tactic/planner tests remain green.

- [ ] **Step 7: Commit the runtime bridge**

```powershell
git add -- app/runtime/exploration.py app/runtime/models.py app/runtime/agent_runtime.py app/runtime/service_factory.py app/strategy/models.py app/main.py app/api/dependencies.py app/strategy/planner.py app/strategy/planner_adapter.py balanced_tactic.py tests/unit/runtime/test_exploration_runtime.py tests/unit/strategy/test_models.py tests/integration/test_runtime_flow.py tests/unit/runtime/test_serialization.py
git commit -m "feat: observe exploration in the runtime"
```

### Task 4: Bounded Exploration Viewport API

**Files:**
- Create: `app/api/exploration.py`
- Create: `tests/integration/test_exploration_api.py`
- Modify: `app/api/__init__.py`
- Modify: `app/main.py`
- Modify: `app/runtime/service_factory.py`
- Modify: `tests/contract/test_openapi_contract.py`

**Interfaces:**
- Consumes: `Services.exploration`, the current factory `account_scope`, current `session_id`, and Task 2 `ExplorationRepository.window()`.
- Produces: `GET /api/v1/exploration?minX=&minY=&maxX=&maxY=` with an account-hidden bounded JSON window and ETag support.
- Guarantees: no request parameter or response field can choose or reveal an account scope.

- [ ] **Step 1: Write failing API boundary, isolation, and cache tests**

Create `tests/integration/test_exploration_api.py`:

```python
import hashlib

from fastapi.testclient import TestClient

from app.main import create_app
from app.strategy.exploration import ExplorationDelta

from tests.integration.test_api import settings
from tests.unit.storage.test_exploration_repository import chunk


def activate_scope(client: TestClient, scope: str) -> None:
    services = client.app.state.services
    session = services.runtime_store.create_session(account_hash=scope)
    services.session_id = session.session_id
    services.runtime_factory._session_id = session.session_id
    services.runtime_factory._account_scope = scope


def test_exploration_endpoint_returns_only_current_account_window(tmp_path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        services = client.app.state.services
        services.exploration.merge_delta("account-a", ExplorationDelta(1, (chunk(explored=1),)))
        services.exploration.merge_delta("account-b", ExplorationDelta(1, (chunk(explored=2),)))
        activate_scope(client, "account-a")

        response = client.get("/api/v1/exploration?minX=0&minY=0&maxX=1&maxY=0")

    assert response.status_code == 200
    assert response.json() == {
        "revision": 1,
        "bounds": {"minX": 0, "minY": 0, "maxX": 1, "maxY": 0},
        "exploredCells": [[0, 0]],
        "knownObstacleCells": [],
    }
    assert "account" not in response.text.lower()


def test_exploration_endpoint_rejects_missing_session_and_oversized_window(tmp_path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        missing = client.get("/api/v1/exploration?minX=0&minY=0&maxX=1&maxY=1")
        activate_scope(client, "account-a")
        oversized = client.get("/api/v1/exploration?minX=0&minY=0&maxX=96&maxY=95")

    assert missing.status_code == 404
    assert missing.json()["code"] == "EXPLORATION_NOT_AVAILABLE"
    assert oversized.status_code == 422
    assert oversized.json()["code"] == "EXPLORATION_WINDOW_INVALID"


def test_etag_varies_by_revision_and_normalized_window(tmp_path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        services = client.app.state.services
        services.exploration.merge_delta("account-a", ExplorationDelta(1, (chunk(explored=1),)))
        activate_scope(client, "account-a")
        first = client.get("/api/v1/exploration?minX=0&minY=0&maxX=1&maxY=0")
        cached = client.get(
            "/api/v1/exploration?minX=0&minY=0&maxX=1&maxY=0",
            headers={"If-None-Match": first.headers["etag"]},
        )
        other_window = client.get("/api/v1/exploration?minX=0&minY=0&maxX=2&maxY=0")

    assert cached.status_code == 304
    assert first.headers["etag"] != other_window.headers["etag"]
    expected_etag = '"' + hashlib.sha256(b"1:0:0:1:0").hexdigest() + '"'
    assert first.headers["etag"] == expected_etag
```

Extend `tests/contract/test_openapi_contract.py`:

```python
def test_exploration_contract_has_bounds_but_no_account_selector() -> None:
    with TestClient(create_app()) as client:
        schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/exploration"]["get"]
    names = {parameter["name"] for parameter in operation["parameters"]}
    assert names == {"minX", "minY", "maxX", "maxY"}
    assert "account" not in str(operation).lower()
```

- [ ] **Step 2: Run the API tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\integration\test_exploration_api.py tests\contract\test_openapi_contract.py
```

Expected: `/api/v1/exploration` is missing and the OpenAPI path assertion fails.

- [ ] **Step 3: Implement strict bounds, ETag, and current-scope resolution**

Create `app/api/exploration.py`:

```python
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Query, Request, Response

from app.errors import AppError

router = APIRouter(prefix="/api/v1", tags=["exploration"])


@router.get("/exploration")
def exploration_window(
    request: Request,
    min_x: int = Query(alias="minX"),
    min_y: int = Query(alias="minY"),
    max_x: int = Query(alias="maxX"),
    max_y: int = Query(alias="maxY"),
):
    services = request.app.state.services
    factory = services.runtime_factory
    scope = getattr(factory, "account_scope", None)
    if not services.session_id or not scope:
        raise AppError(
            "EXPLORATION_NOT_AVAILABLE",
            "Exploration is unavailable before the first runtime session",
            404,
        )
    width = max_x - min_x + 1
    height = max_y - min_y + 1
    if width < 1 or height < 1 or width * height > 9216 or width > 96 or height > 96:
        raise AppError(
            "EXPLORATION_WINDOW_INVALID",
            "Exploration bounds must describe at most a 96 by 96 window",
            422,
        )
    window = services.exploration.window(
        scope, min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y
    )
    token = hashlib.sha256(
        f"{window.revision}:{min_x}:{min_y}:{max_x}:{max_y}".encode("ascii")
    ).hexdigest()
    etag = f'"{token}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(
        media_type="application/json",
        headers={"ETag": etag, "Cache-Control": "private, no-cache"},
        content=json.dumps(
            {
                "revision": window.revision,
                "bounds": {"minX": min_x, "minY": min_y, "maxX": max_x, "maxY": max_y},
                "exploredCells": [list(cell) for cell in window.explored_cells],
                "knownObstacleCells": [list(cell) for cell in window.known_obstacle_cells],
            },
            separators=(",", ":"),
        ),
    )
```

Include the missing `import json`. Register `exploration.router` before static/SPA fallback in `app/main.py`. Instantiate `ExplorationRepository(database)` as `Services.exploration`, and when a runtime is built update both `services.session_id` and the factory's private account scope through the existing runtime-start hook. Do not accept an injected scope from HTTP.

- [ ] **Step 4: Verify GREEN, OpenAPI privacy, and 304 behavior**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\integration\test_exploration_api.py tests\contract\test_openapi_contract.py tests\contract\test_public_redaction.py
```

Expected: all tests pass; 304 has no JSON body and no public account field exists.

- [ ] **Step 5: Commit the bounded API**

```powershell
git add -- app/api/exploration.py app/api/__init__.py app/main.py app/runtime/service_factory.py tests/integration/test_exploration_api.py tests/contract/test_openapi_contract.py
git commit -m "feat: expose bounded exploration windows"
```

### Task 5: Three-State Canvas Fog and Viewport Cache

**Files:**
- Create: `frontend/js/map/exploration-cache.js`
- Create: `frontend/tests/exploration-cache.test.mjs`
- Create: `frontend/tests/map-fog.test.mjs`
- Modify: `frontend/js/api-client.js`
- Modify: `frontend/js/app-store.js`
- Modify: `frontend/js/app.js`
- Modify: `frontend/js/map/map-camera.js`
- Modify: `frontend/js/map/tactical-map.js`
- Modify: `frontend/js/map/map-layers.js`
- Modify: `frontend/js/map/map-accessibility.js`
- Modify: `frontend/js/views/overview.js`
- Modify: `frontend/css/map.css`
- Modify: `tests/e2e/test_dashboard.py`

**Interfaces:**
- Consumes: `/state/current.visibility.currentCells`, `.explorationRevision`, Task 4 viewport JSON, and camera state.
- Produces: `ExplorationCache`, `MapCamera.worldBounds()`, fog-layer commands, a three-state legend, and an accessible visibility summary.
- The cache is keyed by runtime ID plus normalized bounds plus ETag; runtime generation changes clear all old cells.

- [ ] **Step 1: Write failing cache, bounds, and fog classification tests**

Create `frontend/tests/exploration-cache.test.mjs`:

```javascript
import assert from "node:assert/strict";
import test from "node:test";

import { ExplorationCache } from "../js/map/exploration-cache.js";

test("cache isolates runtime generations and exact windows", () => {
  const cache = new ExplorationCache();
  cache.replace("runtime-a", { minX: 0, minY: 0, maxX: 2, maxY: 2 }, {
    revision: 4,
    exploredCells: [[0, 0], [1, 0]],
    knownObstacleCells: [[1, 0]],
  }, '"etag-a"');

  assert.equal(cache.classify("runtime-a", [0, 0], []), "EXPLORED");
  assert.equal(cache.classify("runtime-a", [9, 9], []), "UNKNOWN");
  assert.equal(cache.classify("runtime-b", [0, 0], []), "UNKNOWN");
  cache.reset("runtime-b");
  assert.equal(cache.classify("runtime-a", [0, 0], []), "UNKNOWN");
});

test("current visibility always wins over explored and unknown", () => {
  const cache = new ExplorationCache();
  cache.reset("runtime-a");
  assert.equal(cache.classify("runtime-a", [8, -2], [[8, -2]]), "VISIBLE");
});
```

Extend `frontend/tests/map-camera.test.mjs`:

```javascript
test("camera converts the canvas viewport to inclusive bounded world coordinates", () => {
  const camera = new MapCamera([100n, -50n]);
  const bounds = camera.worldBounds({ width: 300, height: 180, cell: 30, padding: 2 });
  assert.deepEqual(bounds, { minX: 93, minY: -55, maxX: 107, maxY: -45 });
});
```

Create `frontend/tests/map-fog.test.mjs` around an exported pure `visibleMapCells()` and `classifyFogCell()` from `map-layers.js`:

```javascript
import assert from "node:assert/strict";
import test from "node:test";

import { classifyFogCell, visibleMapCells } from "../js/map/map-layers.js";

test("fog has current visible, explored dark, and unknown opaque states", () => {
  const current = visibleMapCells({ visibility: { currentCells: [[0, 0]] } });
  const explored = new Set(["1,0"]);
  assert.equal(classifyFogCell([0, 0], current, explored), "VISIBLE");
  assert.equal(classifyFogCell([1, 0], current, explored), "EXPLORED");
  assert.equal(classifyFogCell([2, 0], current, explored), "UNKNOWN");
});
```

- [ ] **Step 2: Run Node tests to verify RED**

Run:

```powershell
node --test frontend/tests/exploration-cache.test.mjs frontend/tests/map-camera.test.mjs frontend/tests/map-fog.test.mjs
```

Expected: imports/functions are missing.

- [ ] **Step 3: Implement camera bounds and exact-window cache**

Add to `MapCamera`:

```javascript
worldBounds({ width, height, cell, padding = 2 }) {
  const halfX = Math.ceil(width / (2 * cell * this.zoom)) + padding;
  const halfY = Math.ceil(height / (2 * cell * this.zoom)) + padding;
  const centerX = this.origin[0] - BigInt(Math.round(this.offset[0]));
  const centerY = this.origin[1] - BigInt(Math.round(this.offset[1]));
  return {
    minX: Number(centerX - BigInt(halfX)), minY: Number(centerY - BigInt(halfY)),
    maxX: Number(centerX + BigInt(halfX)), maxY: Number(centerY + BigInt(halfY)),
  };
}
```

Before converting to `Number`, assert each value is within `Number.MIN_SAFE_INTEGER` and `Number.MAX_SAFE_INTEGER`; otherwise throw `RangeError("viewport is outside safe HTTP coordinate bounds")` and leave current visibility rendering intact.

Implement `ExplorationCache` with `reset(runtimeId)`, `entry(runtimeId, bounds)`, `etag(runtimeId, bounds)`, `replace(runtimeId, bounds, payload, etag)`, `exploredSet(runtimeId, bounds)`, `obstacleSet(runtimeId, bounds)`, and `classify(runtimeId, position, currentCells)`. A normalized bounds key is `${minX}:${minY}:${maxX}:${maxY}`. `reset()` deletes every entry when the runtime ID changes.

Add to `ApiClient`:

```javascript
async exploration(bounds, etag = null) {
  const query = new URLSearchParams({
    minX: String(bounds.minX), minY: String(bounds.minY),
    maxX: String(bounds.maxX), maxY: String(bounds.maxY),
  });
  const headers = etag ? { "If-None-Match": etag } : {};
  const response = await fetch(`${this.base}/exploration?${query}`, { headers });
  if (response.status === 304) return { notModified: true, etag };
  const payload = await response.json();
  if (!response.ok) throw new ApiError(response.status, payload);
  return { payload, etag: response.headers.get("etag") };
}
```

- [ ] **Step 4: Draw three fog states before terrain and current objects**

Export these helpers from `map-layers.js`:

```javascript
export function positionKey(position) { return `${position[0]},${position[1]}`; }
export function visibleMapCells(state) {
  return new Set((state?.visibility?.currentCells || []).map(positionKey));
}
export function classifyFogCell(position, current, explored) {
  const key = positionKey(position);
  if (current.has(key)) return "VISIBLE";
  if (explored.has(key)) return "EXPLORED";
  return "UNKNOWN";
}
```

Change `drawTacticalLayers()` to receive `bounds`, `exploredCells`, and `knownObstacleCells`. Iterate only the inclusive viewport (maximum 9216 cells): unknown cells use `rgba(1, 5, 10, .94)`, explored cells use `rgba(4, 13, 24, .54)`, current visible cells have no dark fill. Draw a one-pixel `rgba(85, 205, 252, .45)` frontier edge where an explored cell borders unknown. Draw `knownObstacleCells` at 45% alpha before current terrain; draw current obstacle/resource/enemy layers only from current state and never synthesize historical enemies/resources.

Update `map-accessibility.js` to say:

```javascript
description.textContent = `当前可见 ${currentCount} 格，已探索暗区 ${exploredDarkCount} 格；已探索不代表当前安全。`;
```

Update the overview legend with `当前可见`, `已探索`, and `未探索` swatches in addition to the entity legend. Add matching CSS without replacing supplied Arena Hero assets.

- [ ] **Step 5: Fetch viewport data on home, pan, zoom, and exploration revision changes**

Give `TacticalMap` constructor an async `loadExploration(bounds, etag)` callback and store a `requestNonce`. After render computes bounds, debounce 100 ms; request only when bounds/runtime/revision differs from the last successful key. Apply a result only when its nonce is current and its runtime ID still matches. On failure keep current visibility and unknown fog, and do not promote stale cells.

In `app.js`, own one `ExplorationCache`, reset it when `snapshot.runtime.runtimeId` changes, and pass this callback:

```javascript
async (bounds) => {
  const runtimeId = store.snapshot.runtime?.runtimeId || "";
  const etag = explorationCache.etag(runtimeId, bounds);
  const result = await api.exploration(bounds, etag);
  if (!result.notModified) {
    explorationCache.replace(runtimeId, bounds, result.payload, result.etag);
  }
  return explorationCache.entry(runtimeId, bounds);
}
```

`AppStore.replaceFromRest()` already deep-clones the full state; add a regression proving `visibility` survives normalization unchanged and that no explored cells are derived from old state snapshots.

- [ ] **Step 6: Add a browser assertion for legend and API window**

Extend `install_api_mocks()` in `tests/e2e/test_dashboard.py` to include:

```python
state["visibility"] = {
    "tick": 1234,
    "currentCells": [[12, 8], [13, 8]],
    "explorationRevision": 4,
}
```

Return an exploration payload for URLs starting `/api/v1/exploration?`, then add:

```python
def test_dashboard_labels_current_explored_and_unknown_fog(page, live_server_url) -> None:
    install_api_mocks(page)
    page.goto(live_server_url + "/")
    expect(page.get_by_text("当前可见", exact=True)).to_be_visible()
    expect(page.get_by_text("已探索", exact=True)).to_be_visible()
    expect(page.get_by_text("未探索", exact=True)).to_be_visible()
    expect(page.locator("#map-description")).to_contain_text("已探索不代表当前安全")
```

- [ ] **Step 7: Verify frontend GREEN and visual regression**

Run:

```powershell
$nodeTests = Get-ChildItem -LiteralPath 'frontend/tests' -Filter '*.test.mjs' | Sort-Object FullName | ForEach-Object FullName
node --test $nodeTests
.\.venv\Scripts\python.exe -m pytest -q tests\e2e\test_dashboard.py tests\e2e\test_visual_layout.py --basetemp=.codex_tmp\pytest-fog-ui
```

Expected: all Node and browser tests pass; Playwright screenshots show current cells bright, explored cells dark blue-gray, and unknown cells nearly opaque.

- [ ] **Step 8: Commit the three-state map**

```powershell
git add -- frontend/js/api-client.js frontend/js/app-store.js frontend/js/app.js frontend/js/map frontend/js/views/overview.js frontend/css/map.css frontend/tests tests/e2e/test_dashboard.py
git commit -m "feat: render persistent three state fog"
```

### Task 6: Pure Frontier Leasing and Anti-Oscillation Routing

**Files:**
- Create: `app/strategy/frontier.py`
- Create: `tests/unit/strategy/test_frontier.py`
- Modify: `app/strategy/__init__.py`

**Interfaces:**
- Consumes: `ExplorationMap`, `EntitySnapshot`, `CellRisk`, permanent obstacles, current occupancy, and current Tick.
- Produces: `FrontierSettings`, `FrontierLease`, `FrontierMemory`, `FrontierAssignment`, `frontier_cells()`, `assign_frontiers()`, `record_scout_observation()`, and `next_frontier_step()`.
- The module does not import SDK controllers, SQLite, FastAPI, `balanced_tactic`, or `economic_strategy`.

- [ ] **Step 1: Write failing frontier, lease, distribution, and taboo-edge tests**

Create `tests/unit/strategy/test_frontier.py`:

```python
from app.strategy.exploration import ExplorationMap
from app.strategy.frontier import (
    FrontierMemory,
    FrontierSettings,
    ScoutSnapshot,
    assign_frontiers,
    frontier_cells,
    next_frontier_step,
    record_scout_observation,
)
from app.strategy.models import CellRisk


def explored_rectangle(min_x: int, min_y: int, max_x: int, max_y: int) -> ExplorationMap:
    exploration = ExplorationMap()
    cells = frozenset(
        (x, y)
        for x in range(min_x, max_x + 1)
        for y in range(min_y, max_y + 1)
    )
    exploration.observe(visible_cells=cells, visible_obstacles=frozenset(), tick=1)
    return exploration


def test_frontier_is_explored_passable_and_cardinally_adjacent_to_unknown() -> None:
    exploration = explored_rectangle(0, 0, 2, 2)
    cells = frontier_cells(
        exploration,
        min_x=0, min_y=0, max_x=2, max_y=2,
        obstacles=frozenset({(1, 0)}),
        limit=64,
    )
    assert (1, 1) not in cells
    assert (1, 0) not in cells
    assert (0, 0) in cells
    assert (2, 2) in cells


def test_two_workers_receive_distinct_low_overlap_frontiers_deterministically() -> None:
    exploration = explored_rectangle(-2, -2, 2, 2)
    workers = (
        ScoutSnapshot(b"a", (0, 0)),
        ScoutSnapshot(b"b", (0, 1)),
    )
    memory = FrontierMemory()
    assignments = assign_frontiers(
        memory,
        workers,
        exploration=exploration,
        risk_map={},
        obstacles=frozenset(),
        occupied=frozenset(),
        tick=5,
        settings=FrontierSettings(),
    )
    repeated = assign_frontiers(
        memory,
        tuple(reversed(workers)),
        exploration=exploration,
        risk_map={},
        obstacles=frozenset(),
        occupied=frozenset(),
        tick=5,
        settings=FrontierSettings(),
    )
    assert assignments == repeated
    assert len({item.target for item in assignments.values()}) == 2


def test_existing_lease_stays_until_completed_invalid_or_stalled() -> None:
    exploration = explored_rectangle(0, 0, 4, 4)
    worker = ScoutSnapshot(b"a", (2, 2))
    memory = FrontierMemory()
    first = assign_frontiers(
        memory, (worker,), exploration=exploration, risk_map={},
        obstacles=frozenset(), occupied=frozenset(), tick=10,
        settings=FrontierSettings(),
    )[b"a"]
    second = assign_frontiers(
        memory, (ScoutSnapshot(b"a", (2, 3)),), exploration=exploration, risk_map={},
        obstacles=frozenset(), occupied=frozenset(), tick=11,
        settings=FrontierSettings(),
    )[b"a"]
    assert second.target == first.target
    assert second.reason_code == "SCOUT_FRONTIER"


def test_a_b_a_marks_the_reverse_edge_taboo_and_reassigns() -> None:
    memory = FrontierMemory()
    settings = FrontierSettings(edge_cooldown_ticks=4)
    for tick, position in enumerate(((0, 0), (1, 0), (0, 0)), start=1):
        record_scout_observation(
            memory, b"a", position, explored_count=tick,
            tick=tick, settings=settings,
        )
    assert memory.taboo_edges[(b"a", (0, 0), (1, 0))] == 7
    assert memory.leases.get(b"a") is None
    assert memory.oscillation_detections == 1


def test_a_b_c_b_and_no_coverage_progress_also_release_the_lease() -> None:
    memory = FrontierMemory()
    settings = FrontierSettings(stall_ticks=3)
    memory.ensure_lease(b"a", target=(9, 9), distance=10, explored_count=4, tick=1)
    for tick, position in enumerate(((0, 0), (1, 0), (2, 0), (1, 0)), start=2):
        record_scout_observation(
            memory, b"a", position, explored_count=4,
            tick=tick, settings=settings,
        )
    assert b"a" not in memory.leases
    assert memory.oscillation_detections == 1


def test_route_avoids_taboo_risk_obstacle_occupancy_and_can_return_wait() -> None:
    memory = FrontierMemory()
    memory.taboo_edges[(b"a", (0, 0), (1, 0))] = 9
    step = next_frontier_step(
        ScoutSnapshot(b"a", (0, 0)),
        target=(2, 0),
        memory=memory,
        risk_map={(0, -1): CellRisk(1, 1, (b"enemy",))},
        obstacles=frozenset({(0, 1)}),
        occupied=frozenset({(-1, 0)}),
        reserved=frozenset(),
        tick=5,
        max_expansions=64,
    )
    assert step is None
    assert memory.oscillation_prevented_moves == 1
```

- [ ] **Step 2: Run the pure frontier tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\strategy\test_frontier.py
```

Expected: collection fails because `app.strategy.frontier` does not exist.

- [ ] **Step 3: Implement the public frontier values and bounded candidate generation**

Create `app/strategy/frontier.py` with these public dataclasses:

```python
@dataclass(frozen=True, slots=True)
class FrontierSettings:
    search_radius: int = 48
    candidate_limit: int = 256
    route_expansions: int = 512
    lease_stall_ticks: int = 3
    edge_cooldown_ticks: int = 4


@dataclass(frozen=True, slots=True)
class ScoutSnapshot:
    entity_id: bytes
    position: Position


@dataclass(slots=True)
class FrontierLease:
    target: Position
    best_distance: int
    best_explored_count: int
    stalled_ticks: int
    created_tick: int
    failed_until: int = 0


@dataclass(frozen=True, slots=True)
class FrontierAssignment:
    unit_id: bytes
    target: Position
    expected_gain: int
    path_cost: int
    reason_code: str


@dataclass(slots=True)
class FrontierMemory:
    leases: dict[bytes, FrontierLease] = field(default_factory=dict)
    histories: dict[bytes, deque[Position]] = field(default_factory=dict)
    taboo_edges: dict[tuple[bytes, Position, Position], int] = field(default_factory=dict)
    failed_targets: dict[tuple[bytes, Position], int] = field(default_factory=dict)
    oscillation_detections: int = 0
    oscillation_prevented_moves: int = 0
    frontier_progress_ticks: int = 0
    scout_wait_ticks: int = 0
    observed_ticks: dict[bytes, int] = field(default_factory=dict)
```

Validate positive limits and non-empty byte IDs. Histories use `deque(maxlen=8)`.

`frontier_cells()` scans only the requested inclusive bounds and stops after `limit`. A cell is a frontier when it is explored, not a known/current obstacle, and one cardinal neighbor is not explored. Sort candidates by `(x, y)` before truncation.

For each candidate, estimate:

- `gain`: count currently unknown cells inside the scout's v0.14 Worker Manhattan radius 3;
- `age_bonus`: `min(20, max(0, tick - exploration.last_seen_tick(candidate)))`, with never-observed frontiers treated as 20;
- `path_cost`: bounded A* distance through non-obstacle, non-occupied cells;
- `overlap`: unknown cells in that radius already claimed by earlier UUID-ordered assignments;
- `risk`: sum of visible attack counts at the target and first route step;
- `reverse_penalty`: 1 when the first step is a still-active taboo edge.

Rank highest utility first with this deterministic tuple:

```python
rank = (
    -(5 * gain + age_bonus - path_cost - 3 * overlap - 8 * risk - 20 * reverse_penalty),
    path_cost,
    candidate[0],
    candidate[1],
)
```

Keep an existing lease when its target is still a frontier, its failure cooldown is over, and a bounded route exists. Assignment reason is `SCOUT_FRONTIER` for a kept/new normal lease and `SCOUT_REASSIGNED` for a lease selected after a recorded failure.

- [ ] **Step 4: Implement observation patterns and bounded A* next step**

`record_scout_observation()` must:

1. Prune expired taboo edges and failed targets.
2. Ignore a duplicate call when `memory.observed_ticks[unit_id] == tick`; otherwise store the Tick and append the position once to the worker's length-8 history.
3. Detect `history[-3] == history[-1] != history[-2]` (`A-B-A`) or `history[-4] == history[-2]` with `history[-3] != history[-1]` (`A-B-C-B`).
4. On a detected cycle, add `(unit_id, current_position, previous_position)` with expiry `tick + edge_cooldown_ticks`, remove the lease, cool its target to the same expiry, and increment `oscillation_detections`.
5. Otherwise compare Manhattan distance and total explored count to lease bests; increment progress when either improves, else increment stalls and release/cool the lease at `lease_stall_ticks`.

`next_frontier_step()` uses cardinal order `UP, RIGHT, DOWN, LEFT`, bounded A*, and rejects obstacles, occupied/reserved cells, any cell with `risk.expected_damage >= 1`, and still-active taboo edges. Return `None` when no path is proven within `max_expansions`; increment `oscillation_prevented_moves` when a taboo edge was the only geometric step and `scout_wait_ticks` when no step is returned.

Export all public names from `app/strategy/__init__.py`.

- [ ] **Step 5: Verify GREEN and deterministic repeatability**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\strategy\test_frontier.py
.\.venv\Scripts\python.exe -m pytest -q tests\unit\strategy --basetemp=.codex_tmp\pytest-frontier-domain
```

Expected: all pure strategy tests pass.

- [ ] **Step 6: Commit the frontier domain**

```powershell
git add -- app/strategy/frontier.py app/strategy/__init__.py tests/unit/strategy/test_frontier.py
git commit -m "feat: add frontier leases and anti oscillation routing"
```

### Task 7: Replace Radial Worker Roaming with Frontier Actions

**Files:**
- Modify: `balanced_tactic.py`
- Modify: `economic_strategy.py`
- Modify: `app/strategy/planner_adapter.py`
- Modify: `test_economic_strategy.py`
- Modify: `test_balanced_tactic.py`
- Modify: `tests/integration/test_planner_adapter.py`
- Modify: `tests/integration/test_long_run.py`

**Interfaces:**
- Consumes: Task 6 frontier planner and `TacticMemory.exploration/current_visible_cells` from Task 3.
- Produces: Worker `SCOUT_FRONTIER`, `SCOUT_REASSIGNED`, and `SCOUT_WAIT_NO_SAFE_FRONTIER` actions/explanations plus aggregate exploration diagnostics.
- Removes fixed radial `scout_targets()` from the live Worker fallback; keeps it only as a deprecated compatibility helper until all root tests no longer import it.

- [ ] **Step 1: Add failing live tactic regressions for progress, distribution, and WAIT**

Append to `test_balanced_tactic.py` using the existing `FakeController`/`make_turn` fixtures:

```python
def explore_square(memory: TacticMemory, radius: int = 4) -> None:
    memory.exploration.observe(
        visible_cells=frozenset(
            (x, y)
            for x in range(-radius, radius + 1)
            for y in range(-radius, radius + 1)
        ),
        visible_obstacles=frozenset(),
        tick=1,
    )


def test_idle_workers_move_to_distinct_real_frontiers_not_radial_fallbacks() -> None:
    core = FakeController(object_id=UUID(int=100), position=(0, 0), hp=5, shield=5)
    first = FakeController(object_id=UUID(int=1), position=(0, 1), hp=2, unit_type=UnitType.WORKER)
    second = FakeController(object_id=UUID(int=2), position=(1, 0), hp=2, unit_type=UnitType.WORKER)
    turn = make_turn(core=core, units=(first, second), resources=0)
    memory = TacticMemory()
    explore_square(memory)
    memory.exploration_observed_tick = turn.tick
    memory.current_visible_cells = frozenset({core.position, first.position, second.position})

    choose_actions(turn, memory)

    moves = [unit.actions[-1] for unit in (first, second)]
    assert all(action[0] == "MOVE" for action in moves)
    assert moves[0][1] != moves[1][1]


def test_worker_does_not_repeat_a_b_a_after_oscillation_is_observed() -> None:
    memory = TacticMemory()
    explore_square(memory)
    worker_id = UUID(int=1)
    positions = ((0, 0), (1, 0), (0, 0))
    for tick, position in enumerate(positions, start=10):
        core = FakeController(object_id=UUID(int=100), position=(0, 2), hp=5, shield=5)
        worker = FakeController(object_id=worker_id, position=position, hp=2, unit_type=UnitType.WORKER)
        turn = make_turn(core=core, units=(worker,), resources=0)
        turn.tick = tick
        memory.exploration_observed_tick = tick
        memory.current_visible_cells = frozenset({position, core.position})
        choose_actions(turn, memory)

    assert worker.actions == [] or worker.actions[-1] != ("MOVE", Direction.RIGHT)
    assert memory.exploration_diagnostics["oscillation_detections"] >= 1


def test_worker_waits_when_every_frontier_route_is_blocked_or_attacked() -> None:
    core = FakeController(object_id=UUID(int=100), position=(0, 0), hp=5, shield=5)
    worker = FakeController(object_id=UUID(int=1), position=(0, 1), hp=2, unit_type=UnitType.WORKER)
    enemy = SimpleNamespace(
        id=UUID(int=200), kind="UNIT", unit_type=UnitType.RANGER,
        position=(0, 4), hp=2,
    )
    turn = make_turn(
        core=core, units=(worker,), enemies=(enemy,), resources=0,
        obstacle_cells={(1, 1), (-1, 1), (0, 0)},
    )
    memory = TacticMemory()
    explore_square(memory)
    memory.exploration_observed_tick = turn.tick
    memory.current_visible_cells = frozenset({worker.position})

    choose_actions(turn, memory)

    assert worker.actions == []
    assert memory.planned_reason_codes[worker.id] == "SCOUT_WAIT_NO_SAFE_FRONTIER"
```

Add to `tests/integration/test_long_run.py` a 20-Tick synthetic sequence that applies successful Worker positions from the prior plan, then assert no suffix contains four alternating `A, B, A, B` positions and that every move explanation is `SCOUT_FRONTIER`/`SCOUT_REASSIGNED` or a higher-priority economy/Beacon/defense reason.

- [ ] **Step 2: Run the focused tactic tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q test_balanced_tactic.py -k "frontier or oscillation or every_frontier"
.\.venv\Scripts\python.exe -m pytest -q tests\integration\test_long_run.py
```

Expected: fixed radial behavior repeats or does not expose frontier reason codes.

- [ ] **Step 3: Add frontier memory and per-Tick reason storage to `TacticMemory`**

Add:

```python
frontier: FrontierMemory = field(default_factory=FrontierMemory)
planned_reason_codes: dict[object, str] = field(default_factory=dict)
planned_reason_targets: dict[object, tuple[int, int]] = field(default_factory=dict)
```

At the beginning of `TacticMemory.observe()`, clear only the per-Tick reason dictionaries. Do not clear frontier leases, history, taboo edges, or exploration.

Add a small helper in `balanced_tactic.py`:

```python
def _record_reason(
    memory: TacticMemory,
    identifier: object,
    reason: str,
    target: tuple[int, int] | None = None,
) -> None:
    memory.planned_reason_codes[identifier] = reason
    if target is not None:
        memory.planned_reason_targets[identifier] = target
```

- [ ] **Step 4: Replace live radial assignment in `_queue_worker_actions()`**

Keep resource assignment, Beacon carrier/runner, cargo, harvest, defense evacuation, and Core-slot behavior in their current priority order. Replace only `previous_scout_targets`/`worker_scout_targets` live fallback with:

1. Build `ScoutSnapshot`s from the eligible `scouting_workers`.
2. Build a `risk_map` from current friendly/enemy snapshots with `build_visible_risk_map()`.
3. Call `record_scout_observation()` before assignment using the bounded loaded-map explored count maintained by `ExplorationMap`; do not scan persisted history outside the loaded working set.
4. Call `assign_frontiers()` with known permanent obstacles, current occupancy, current Tick, and `FrontierSettings(search_radius=max(16, memory.policy.scout_ring_step * 4))`.
5. For a Worker with an assignment, call `next_frontier_step()` and queue the returned cardinal direction directly after rechecking `_candidate_steps()` legality and same-Tick reservation.
6. When no step is proven, queue no SDK action and call `_record_reason(memory, worker.id, "SCOUT_WAIT_NO_SAFE_FRONTIER")`.
7. When a step is queued, call `_record_reason()` with the assignment reason and target.

Do not route a Worker to Core merely because no frontier exists. Do not let frontier fallback override resource/cargo/Beacon/evacuation/Core-slot branches.

Leave `economic_strategy.scout_targets()` callable for compatibility, but add a docstring stating it is not used by the live planner and remove `advance_stalled_targets()` handling of live scout assignments. Retain resource stall handling.

- [ ] **Step 5: Make the planner explanation use explicit reasons**

Change `_build_explanation(turn, plan)` to `_build_explanation(turn, plan, memory)`. For every planned unit/core action, choose:

```python
reason = memory.planned_reason_codes.get(identifier, _reason_for(action_type))
target = memory.planned_reason_targets.get(identifier, _target(action, origin))
```

After planned actions, append `DecisionAction(entity_id=_raw_id(identifier), action_type="WAIT", reason_code=reason, risk_before=0, risk_after=0, target=memory.planned_reason_targets.get(identifier))` for each reason entry whose identifier has no action and whose reason is `SCOUT_WAIT_NO_SAFE_FRONTIER`; this is explanation-only and does not consume an SDK action slot.

Populate `memory.exploration_diagnostics` with integer aggregates only:

```python
{
    "newly_explored_cells": newly_explored_cells,
    "visible_cells": len(memory.current_visible_cells),
    "frontier_assignments": len(assignments),
    "frontier_progress_ticks": memory.frontier.frontier_progress_ticks,
    "oscillation_detections": memory.frontier.oscillation_detections,
    "oscillation_prevented_moves": memory.frontier.oscillation_prevented_moves,
    "scout_wait_ticks": memory.frontier.scout_wait_ticks,
}
```

- [ ] **Step 6: Verify anti-oscillation GREEN and economic regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q test_economic_strategy.py test_balanced_tactic.py tests\integration\test_planner_adapter.py tests\integration\test_long_run.py --basetemp=.codex_tmp\pytest-frontier-live
```

Expected: the 20-Tick regression has no repeating two-cell loop and all existing economy/Beacon tests pass.

- [ ] **Step 7: Commit the live frontier behavior**

```powershell
git add -- balanced_tactic.py economic_strategy.py app/strategy/planner_adapter.py test_economic_strategy.py test_balanced_tactic.py tests/integration/test_planner_adapter.py tests/integration/test_long_run.py
git commit -m "fix: replace oscillating worker roaming with frontiers"
```

### Task 8: Pure Visible Contact Assessment and Response Selection

**Files:**
- Create: `app/strategy/contact.py`
- Create: `tests/unit/strategy/test_contact.py`
- Modify: `app/strategy/__init__.py`

**Interfaces:**
- Consumes: current controlled `EntitySnapshot`s, current visible enemy `EntitySnapshot`s, current permanent obstacles, `CellRisk`, Core defense level, and current Tick.
- Produces: `ContactLevel`, `ContactAssessment`, `ContactMemory`, `ContactResponse`, `assess_contact()`, `choose_worker_evasion()`, `select_responder()`, `ranger_intercept_goal()`, `vanguard_intercept_goal()`, and `update_investigation()`.
- No function treats investigation memory as a visible enemy or produces an attack target ID.

- [ ] **Step 1: Write failing current-contact geometry and role tests**

Create `tests/unit/strategy/test_contact.py`:

```python
from app.strategy.contact import (
    ContactLevel,
    ContactMemory,
    assess_contact,
    choose_worker_evasion,
    ranger_intercept_goal,
    select_responder,
    update_investigation,
)
from app.strategy.models import EntityKind, EntitySnapshot


def entity(identifier: bytes, kind: EntityKind, position, *, hp=2, controlled=True):
    return EntitySnapshot(identifier, kind, position, hp=hp, controlled=controlled)


def test_remote_visible_enemy_is_spotted_without_inflating_core_defense() -> None:
    core = entity(b"core", EntityKind.CORE, (0, 0), hp=5)
    worker = entity(b"worker", EntityKind.WORKER, (10, 0))
    enemy = entity(b"enemy", EntityKind.RANGER, (15, 0), controlled=False)

    assessment = assess_contact(
        core=core, friendlies=(worker,), visible_enemies=(enemy,),
        obstacles=frozenset(), protected_friendly_ids=frozenset({b"worker"}),
    )

    assert assessment.level is ContactLevel.SPOTTED
    assert assessment.threatened_friendly_ids == frozenset()


def test_enemy_that_can_hit_or_step_to_hit_a_worker_is_threatening() -> None:
    core = entity(b"core", EntityKind.CORE, (0, 0), hp=5)
    worker = entity(b"worker", EntityKind.WORKER, (10, 0))
    enemy = entity(b"enemy", EntityKind.RANGER, (14, 0), controlled=False)

    assessment = assess_contact(
        core=core, friendlies=(worker,), visible_enemies=(enemy,),
        obstacles=frozenset(), protected_friendly_ids=frozenset({b"worker"}),
    )

    assert assessment.level is ContactLevel.THREATENING
    assert assessment.threatened_friendly_ids == frozenset({b"worker"})
    assert assessment.threatening_enemy_ids == frozenset({b"enemy"})


def test_current_legal_combat_attack_is_engaged() -> None:
    core = entity(b"core", EntityKind.CORE, (0, 0), hp=5)
    ranger = entity(b"ranger", EntityKind.RANGER, (10, 0))
    enemy = entity(b"enemy", EntityKind.RANGER, (13, 0), controlled=False)
    assessment = assess_contact(
        core=core, friendlies=(ranger,), visible_enemies=(enemy,),
        obstacles=frozenset(), protected_friendly_ids=frozenset(),
    )
    assert assessment.level is ContactLevel.ENGAGED


def test_worker_evasion_prefers_fewer_attacks_then_more_enemy_distance() -> None:
    worker = entity(b"worker", EntityKind.WORKER, (10, 0))
    enemy = entity(b"enemy", EntityKind.RANGER, (13, 0), controlled=False)
    destination = choose_worker_evasion(
        worker,
        visible_enemies=(enemy,),
        obstacles=frozenset(),
        occupied=frozenset(),
        reserved=frozenset(),
        core_position=(0, 0),
    )
    assert destination == (9, 0)


def test_ranger_is_responder_and_one_vanguard_remains_guard() -> None:
    ranger = entity(b"ranger", EntityKind.RANGER, (0, 3))
    guard = entity(b"guard", EntityKind.VANGUARD, (1, 0), hp=4)
    enemy = entity(b"enemy", EntityKind.RANGER, (12, 0), controlled=False)
    responder = select_responder(
        (guard, ranger),
        enemy=enemy,
        contact_level=ContactLevel.THREATENING,
        core_position=(0, 0),
        defender_ids=frozenset({b"guard"}),
        core_defense_level="CLEAR",
        obstacles=frozenset(),
    )
    assert responder.entity_id == b"ranger"


def test_ranger_intercept_goal_creates_a_legal_clear_shot_cell() -> None:
    ranger = entity(b"ranger", EntityKind.RANGER, (0, 0))
    enemy = entity(b"enemy", EntityKind.RANGER, (8, 0), controlled=False)
    goal = ranger_intercept_goal(
        ranger, enemy,
        obstacles=frozenset(), occupied=frozenset(), reserved=frozenset(),
        search_radius=8,
    )
    assert goal is not None
    assert max(abs(goal[0] - enemy.position[0]), abs(goal[1] - enemy.position[1])) <= 3


def test_contact_loss_creates_only_a_three_tick_movement_investigation() -> None:
    memory = ContactMemory()
    update_investigation(
        memory, tick=20, visible_threat=entity(
            b"enemy", EntityKind.RANGER, (8, 0), controlled=False
        ), responder_id=b"ranger",
    )
    assert update_investigation(memory, tick=21, visible_threat=None, responder_id=b"ranger") == (8, 0)
    assert update_investigation(memory, tick=23, visible_threat=None, responder_id=b"ranger") == (8, 0)
    assert update_investigation(memory, tick=24, visible_threat=None, responder_id=b"ranger") is None
    assert memory.enemy_id is None
```

- [ ] **Step 2: Run contact tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\strategy\test_contact.py
```

Expected: collection fails because `app.strategy.contact` does not exist.

- [ ] **Step 3: Implement current-only assessment and legal evasion**

Create `app/strategy/contact.py` with:

```python
class ContactLevel(IntEnum):
    NONE = 0
    SPOTTED = 1
    THREATENING = 2
    ENGAGED = 3


@dataclass(frozen=True, slots=True)
class ContactAssessment:
    level: ContactLevel
    visible_enemy_ids: frozenset[bytes]
    threatening_enemy_ids: frozenset[bytes]
    threatened_friendly_ids: frozenset[bytes]
    currently_engaged_enemy_ids: frozenset[bytes]

    @classmethod
    def none(cls) -> "ContactAssessment":
        return cls(ContactLevel.NONE, frozenset(), frozenset(), frozenset(), frozenset())


@dataclass(slots=True)
class ContactMemory:
    responder_id: bytes | None = None
    last_seen_position: Position | None = None
    expires_tick: int = 0
    enemy_id: bytes | None = None


@dataclass(frozen=True, slots=True)
class ContactResponse:
    level: ContactLevel
    responder_id: bytes | None
    target_position: Position | None
    threatened_worker_ids: frozenset[bytes]
    reason_code: str | None
```

`assess_contact()` must use only current visible living enemy Rangers/Vanguards. Its caller passes `protected_friendly_ids`, computed from the current Beacon carrier, cargo Workers, Workers standing on current resources, and Workers with a current resource-route intent. A current legal attack on a protected object is `THREATENING`; the conservative one-step attack helper from `defense_strategy` also qualifies. If a friendly combat unit can currently legally attack any visible enemy, classify `ENGAGED`. Presence without either condition is `SPOTTED`.

`choose_worker_evasion()` evaluates legal cardinal cells excluding obstacles, visible enemy occupied cells, friendly capacity conflicts, reserved cells, and the Core cell. Rank by `(visible_attack_count, -nearest_enemy_distance, distance_from_core_penalty, direction_order)` and return `None` when every candidate has at least as many visible attacks as the origin.

- [ ] **Step 4: Implement responder floor, intercept cells, and bounded investigation**

`select_responder()` accepts `contact_level` and returns `None` for `NONE`, or for Core `APPROACH`, `ATTACK`, or `LETHAL`. For `SPOTTED`, it may select only an otherwise-idle non-defender Ranger; it never dispatches a Vanguard. For `THREATENING`/`ENGAGED`, prefer Rangers ordered by bounded route distance, negative HP, raw ID. Use a Vanguard only when at least two non-carrier Vanguards exist or it is not in `defender_ids`; never select the sole Vanguard defender.

`ranger_intercept_goal()` searches at most radius 8 around the enemy for an empty, non-obstacle cell with a legal v0.14 Ranger line to the enemy, then ranks by bounded path cost from the Ranger, current visible risk, and coordinates. `vanguard_intercept_goal()` searches cardinal cells between enemy and threatened asset, choosing a non-riskier bounded route. Neither helper submits an action or returns an enemy ID.

`update_investigation()` sets `last_seen_position`, `responder_id`, `enemy_id`, and `expires_tick=tick+3` while a threat is visible. On the first hidden Tick, set `enemy_id=None` before returning the position. Return `None` after expiry or when the last-seen cell is currently confirmed visible and empty (accept an optional `current_visible_cells` argument). This ensures investigation memory cannot become current enemy truth.

Export the public values from `app/strategy/__init__.py`.

- [ ] **Step 5: Verify GREEN and defense geometry regression**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\strategy\test_contact.py test_defense_strategy.py tests\unit\strategy\test_risk.py
```

Expected: all tests pass and the independent Core-defense model remains unchanged.

- [ ] **Step 6: Commit the pure contact domain**

```powershell
git add -- app/strategy/contact.py app/strategy/__init__.py tests/unit/strategy/test_contact.py
git commit -m "feat: assess visible frontier contacts"
```

### Task 9: Live Worker Evasion, Mobile Interception, and Investigation

**Files:**
- Modify: `balanced_tactic.py`
- Modify: `app/strategy/planner_adapter.py`
- Modify: `test_balanced_tactic.py`
- Modify: `tests/integration/test_planner_adapter.py`
- Modify: `tests/integration/test_long_run.py`

**Interfaces:**
- Consumes: Task 8 contact assessment/response and current Core defense/defender roster.
- Produces: legal `CONTACT_EVADE`, `CONTACT_INTERCEPT`, `CONTACT_ATTACK`, `CONTACT_INVESTIGATE`, and `DEFENSE_HOLD` behavior plus bounded diagnostics.
- Contact movement never outranks current legal high-value attacks, Beacon carrier survival, or Core `APPROACH+` recall.

- [ ] **Step 1: Add a failing regression for the observed E12-style geometry**

Append to `test_balanced_tactic.py`:

```python
def test_remote_enemy_threatening_workers_triggers_evasion_and_ranger_interception() -> None:
    core = FakeController(object_id=UUID(int=100), position=(0, 0), hp=5, shield=5)
    worker = FakeController(object_id=UUID(int=1), position=(-7, -9), hp=2, unit_type=UnitType.WORKER)
    ranger = FakeController(object_id=UUID(int=2), position=(2, 0), hp=2, unit_type=UnitType.RANGER)
    guard = FakeController(object_id=UUID(int=3), position=(1, 0), hp=4, unit_type=UnitType.VANGUARD)
    enemy = SimpleNamespace(
        id=UUID(int=200), kind="UNIT", unit_type=UnitType.RANGER,
        position=(-10, -9), hp=2,
    )
    turn = make_turn(core=core, units=(worker, ranger, guard), enemies=(enemy,), resources=0)
    memory = TacticMemory()

    choose_actions(turn, memory)

    assert worker.actions and worker.actions[-1][0] == "MOVE"
    assert ranger.actions and ranger.actions[-1][0] == "MOVE"
    assert guard.actions == []
    assert memory.defense.level.name == "CLEAR"
    assert memory.contact_assessment.level.name == "THREATENING"
    assert memory.planned_reason_codes[worker.id] == "CONTACT_EVADE"
    assert memory.planned_reason_codes[ranger.id] == "CONTACT_INTERCEPT"
    assert memory.planned_reason_codes[guard.id] == "DEFENSE_HOLD"
```

Add five complete cases beside it, using the same real helper signatures:

- `test_approach_core_recall_overrides_remote_contact_interception`: place the Core at `(0, 0)`, an enemy Vanguard at `(2, 0)`, a remote threatening Ranger at `(12, 0)`, and friendly Ranger/Vanguard outside their exact defense rings. Assert both friendly combat units move toward their defense-ring goals, `contact_response is None`, and neither reason is `CONTACT_INTERCEPT`.
- `test_visible_enemy_in_legal_range_is_attacked_before_intercept_move`: place a friendly Ranger at `(0, 0)` and the selected visible threat at `(3, 0)` with no obstacle. Assert the queued action is `SHOOT`, the reason is `CONTACT_ATTACK`, and no move is queued.
- `test_hidden_contact_uses_three_tick_move_only_investigation_then_expires`: reuse one `TacticMemory` across visible Tick 20 and hidden Ticks 21-24. Move the test controller to each successful planned destination before constructing the next Turn. Assert only `MOVE`/no-action on hidden Ticks 21-23, no `SHOOT`/`SWEEP`, and no investigation action on Tick 24.
- `test_hidden_contact_never_queues_precision_shoot_at_old_uuid`: after Tick 20, make the old position hidden and place no enemy in the current Turn. Assert the old UUID never appears in a recorded `SHOOT` tuple and `memory.contact.enemy_id is None`.
- `test_no_legal_evasion_or_intercept_records_controlled_wait_reason`: surround the threatened Worker with obstacles/current occupancy and make every Ranger intercept cell unsafe. Assert no illegal SDK action is queued and the explanation contains `CONTACT_WAIT_NO_SAFE_RESPONSE`.

- [ ] **Step 2: Run contact live tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q test_balanced_tactic.py -k "contact or interception or investigation"
```

Expected: current tactic leaves the remote Ranger/Vanguard idle and has no contact reason codes.

- [ ] **Step 3: Add current contact state to `TacticMemory` and refresh it once per Turn**

Add:

```python
contact: ContactMemory = field(default_factory=ContactMemory)
contact_assessment: ContactAssessment = field(default_factory=ContactAssessment.none)
contact_response: ContactResponse | None = None
contact_diagnostics: dict[str, object] = field(default_factory=dict)
```

Use the `ContactAssessment.none()` classmethod defined in Task 8. Immediately after `_refresh_defense_state()` and after current visibility/economy observation, project current friendlies/enemies and call `assess_contact()`. Build `protected_friendly_ids` from the current Beacon carrier, current Workers with cargo, Workers currently on a resource cell, and Workers with a current resource intent; do not include stale resource targets outside the loaded exploration/routing working set.

Select one current visible contact enemy by this deterministic order:

1. can hit our Beacon carrier now;
2. can hit a cargo Worker now;
3. can hit any Worker now;
4. one-step threat;
5. lowest effective HP;
6. raw UUID.

For `THREATENING`/`ENGAGED`, use the full order above. If the level is only `SPOTTED`, select the visible enemy closest to any eligible scouting/resource Worker, then enemy effective HP and raw UUID; dispatch only an otherwise-idle non-defender Ranger to a bounded monitoring/intercept cell. Select a responder and update the investigation lease. Clear the entire investigation on Core `APPROACH+`, responder death, or a visible-empty last-seen cell.

- [ ] **Step 4: Queue direct attacks, Worker evasion, and interception in existing priority order**

Do not move the existing direct Ranger/Vanguard legal attack blocks below movement. When one of those blocks attacks the selected contact, record `CONTACT_ATTACK` but preserve higher reasons for Core attacker, Beacon carrier, or carrier threat.

In `_queue_worker_actions()`, after Beacon carrier survival but before ordinary cargo/resource/frontier movement, call `choose_worker_evasion()` only for IDs in `contact_assessment.threatened_friendly_ids`. Queue the chosen destination through the existing reservation/occupancy machinery and record `CONTACT_EVADE`. If no safer cell exists, continue into cargo deposit/harvest when legal; otherwise record a WAIT explanation without inventing a move.

In `_combat_goal()` after urgent Core defense and visible enemy-carrier handling, but before enemy Core/runner scouting goals:

- If the unit is the selected responder and the threat is currently visible, return a Ranger/Vanguard intercept goal with reason `CONTACT_INTERCEPT`.
- If it is the responder and only a valid investigation lease exists, return `last_seen_position` with reason `CONTACT_INVESTIGATE`.
- If it is a selected defender holding its exact ring and not the responder, preserve no movement and record `DEFENSE_HOLD`.

Change `_combat_goal()` to return a small internal `Goal(position, retreat, reason_code)` dataclass instead of a tuple, and update all call sites in this task. This prevents reason/target metadata from being inferred later.

- [ ] **Step 5: Expose contact diagnostics and dual threat status**

Populate aggregate diagnostics only:

```python
memory.contact_diagnostics = {
    "level": memory.contact_assessment.level.name,
    "visible_enemy_count": len(memory.contact_assessment.visible_enemy_ids),
    "threatened_workers": threatened_workers,
    "evading_workers": evading_workers,
    "responding_combat_units": int(memory.contact_response is not None),
    "contact_attack_actions": contact_attacks,
    "contact_investigation_ticks": investigation_ticks,
}
```

Keep the plan diagnostics shape consistent with Task 7:

```python
diagnostics = PlannerDiagnostics(
    economy=MappingProxyType(dict(memory.economy_diagnostics)),
    defense=MappingProxyType({
        "level": _enum_name(memory.defense.level),
        "incoming_damage": int(memory.defense.incoming_damage),
    }),
    exploration=MappingProxyType(dict(memory.exploration_diagnostics)),
    contact=MappingProxyType(dict(memory.contact_diagnostics)),
)
```

Copy this into `PlannerDiagnostics.contact`. In `RuntimeServicesFactory.persist()`, add:

```python
public_state["contact"] = {
    "level": str(batch.result.diagnostics.contact.get("level", "NONE")),
    "visibleEnemyCount": int(batch.result.diagnostics.contact.get("visible_enemy_count", 0)),
    "respondingUnitCount": int(batch.result.diagnostics.contact.get("responding_combat_units", 0)),
}
```

The response contains no enemy IDs or last-seen coordinates.

- [ ] **Step 6: Verify live response GREEN and all combat/defense regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q test_balanced_tactic.py test_defense_strategy.py tests\integration\test_planner_adapter.py tests\integration\test_long_run.py --basetemp=.codex_tmp\pytest-contact-live
```

Expected: E12-style regression produces Worker evasion plus Ranger intercept, Vanguard holds, Core remains `CLEAR`, and existing Beacon/Core-defense combat priorities remain green.

- [ ] **Step 7: Commit live contact response**

```powershell
git add -- balanced_tactic.py app/strategy/planner_adapter.py test_balanced_tactic.py tests/integration/test_planner_adapter.py tests/integration/test_long_run.py
git commit -m "feat: respond to visible frontier threats"
```

### Task 10: Operator Contact Status and Bounded Adaptive Diagnostics

**Files:**
- Modify: `app/strategy/planner.py`
- Modify: `app/runtime/service_factory.py`
- Modify: `adaptive_strategy.py`
- Modify: `app/adaptive/projection.py`
- Modify: `frontend/js/views/overview.js`
- Modify: `frontend/tests/dashboard-presentation.test.mjs`
- Modify: `test_adaptive_strategy.py`
- Modify: `tests/unit/adaptive/test_projection.py`
- Modify: `tests/integration/test_dashboard_data_api.py`

`tests/integration/test_dashboard_data_api.py` must import `json`, `MappingProxyType`, `RuntimeBatch`, `DecisionExplanation`, `PlannerDiagnostics`, and `PlannerResult` for the persisted public-state fixture added below.

**Interfaces:**
- Completes the `PlannerDiagnostics.exploration`/`.contact` fields introduced in Task 3 and fills both mappings from tactic memory.
- Public dashboard state exposes only `contact.level`, `contact.visibleEnemyCount`, and `contact.respondingUnitCount`.
- Adaptive records expose only aggregate exploration/contact counters and enum labels; they never expose coordinates, UUIDs, API-key hashes, account scopes, chunk masks, leases, or last-seen positions.
- The scalar `Scorecard.internal_score` remains unchanged in this task; the evaluator/designer receive the new bounded context without changing rollback arithmetic.

- [ ] **Step 1: Write RED tests for the dual threat card and public state projection**

Import `overviewMetrics` from `../js/views/overview.js`, then append to `frontend/tests/dashboard-presentation.test.mjs`:

```javascript
test("overview distinguishes Core defense from frontier contact", () => {
  const html = overviewMetrics({
    tick: 42,
    defenseLevel: "CLEAR",
    contact: { level: "THREATENING", visibleEnemyCount: 1, respondingUnitCount: 1 },
  });

  assert.match(html, /Core CLEAR/);
  assert.match(html, /接敌 THREATENING/);
  assert.match(html, /1 敌军 · 1 响应/);
});
```

Add and export a pure `overviewMetrics(state)` helper that returns only the metrics-grid HTML, then have the existing `renderOverview()` embed that helper. Do not add a DOM emulator dependency.

Append to `tests/integration/test_dashboard_data_api.py` a persisted `RuntimeBatch("TURN_SUBMITTED", ...)` fixture whose `PlannerResult.diagnostics` contains:

```python
PlannerDiagnostics(
    exploration={"newly_explored_cells": 4, "visible_cells": 57},
    contact={"level": "THREATENING", "visible_enemy_count": 2,
             "responding_combat_units": 1, "enemy_ids": ["secret"]},
)
```

Assert `/api/v1/state/current` contains only:

```python
assert body["contact"] == {
    "level": "THREATENING",
    "visibleEnemyCount": 2,
    "respondingUnitCount": 1,
}
assert "enemy_ids" not in json.dumps(body)
```

- [ ] **Step 2: Write RED tests for the adaptive allowlists**

Append to `test_adaptive_strategy.py`:

```python
def test_turn_telemetry_whitelists_exploration_and_contact_aggregates() -> None:
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
```

Append to `tests/unit/adaptive/test_projection.py` a `project_record()` case with the same mappings. Assert all seven exploration counters, all six contact counters, and `THREATENING` survive; assert `account_scope`, coordinates, IDs, booleans, negative values, `math.inf`, and a 65-character contact label do not survive.

- [ ] **Step 3: Run presentation and telemetry tests to verify RED**

Run:

```powershell
$nodeTests = @(
  'frontend/tests/dashboard-presentation.test.mjs'
)
node --test $nodeTests
.\.venv\Scripts\python.exe -m pytest -q test_adaptive_strategy.py tests\unit\adaptive\test_projection.py tests\integration\test_dashboard_data_api.py --basetemp=.codex_tmp\pytest-observability-red
```

Expected: the UI helper is missing, the public state lacks contact output, and adaptive telemetry/projection drop the new mappings.

- [ ] **Step 4: Extend diagnostics without widening any trust boundary**

Confirm `PlannerDiagnostics` has the Task 3 shape below; if Task 3 added only `exploration`, add the missing `contact` field now:

```python
@dataclass(frozen=True, slots=True)
class PlannerDiagnostics:
    economy: Mapping[str, object] = field(default_factory=dict)
    defense: Mapping[str, object] = field(default_factory=dict)
    exploration: Mapping[str, object] = field(default_factory=dict)
    contact: Mapping[str, object] = field(default_factory=dict)
    rejected_moves: tuple[Mapping[str, object], ...] = ()
```

In `RuntimeServicesFactory.persist()`, copy the three public contact values exactly as defined above. Treat an unknown/malformed level as `NONE` and counts as zero; never pass the diagnostic mapping through wholesale.

In `RuntimeServicesFactory.observe_adaptive()`, preserve the existing flat economy/defense keys and add nested copies:

```python
diagnostics = {
    **dict(result.diagnostics.economy),
    "defense_level": result.diagnostics.defense.get("level", "CLEAR"),
    "incoming_core_damage": result.diagnostics.defense.get("incoming_damage", 0),
    "exploration": dict(result.diagnostics.exploration),
    "contact": dict(result.diagnostics.contact),
}
```

This local mapping may contain more information, but both adaptive implementations must still pass it through the allowlists below before persistence or prompting.

- [ ] **Step 5: Add explicit exploration/contact allowlists to both adaptive paths**

In `adaptive_strategy.py`, add `_exploration_mapping()` and `_contact_mapping()` beside `_economy_mapping()` and `_defense_mapping()`. Accept only these exact keys:

```python
_EXPLORATION_COUNT_FIELDS = frozenset({
    "newly_explored_cells", "visible_cells",
    "frontier_assignments", "frontier_progress_ticks",
    "oscillation_detections", "oscillation_prevented_moves", "scout_wait_ticks",
})
_CONTACT_COUNT_FIELDS = frozenset({
    "visible_enemy_count", "threatened_workers", "evading_workers",
    "responding_combat_units", "contact_attack_actions",
    "contact_investigation_ticks",
})
_CONTACT_LEVELS = frozenset({"NONE", "SPOTTED", "THREATENING", "ENGAGED"})
```

Use `_nonnegative_int()` for every counter and `_safe_label()` for `contact.level`. Call the two helpers from both `TurnTelemetry.from_turn()` and `_prompt_record()`.

In `app/adaptive/projection.py`, add equivalent local allowlists and project `record.exploration`/`record.contact` into new bounded mappings. Do not add coordinates, identifiers, chunk keys, masks, account scope, or route strings to `_METRIC_NAMES`. Keep `bounded_records()`'s existing record/character caps.

- [ ] **Step 6: Render Core defense and frontier contact independently**

In `frontend/js/views/overview.js`, derive:

```javascript
const coreThreat = state.defenseLevel || state.threat || "CLEAR";
const contact = state.contact || {};
const contactLevel = contact.level || "NONE";
const danger = coreThreat !== "CLEAR" || ["THREATENING", "ENGAGED"].includes(contactLevel);
```

Render the existing fifth metric card with value `Core ${coreThreat}`, unit text `接敌 ${contactLevel} · ${visibleEnemyCount} 敌军 · ${respondingUnitCount} 响应`, and danger color only when `danger` is true. Escape every string value before interpolation. This keeps Core safety and frontier contact separate while preserving the five-card responsive layout.

- [ ] **Step 7: Verify GREEN, prompt bounds, and the overview layout**

Run:

```powershell
$nodeTests = @(
  'frontend/tests/dashboard-presentation.test.mjs',
  'frontend/tests/app-store.test.mjs'
)
node --test $nodeTests
.\.venv\Scripts\python.exe -m pytest -q test_adaptive_strategy.py tests\unit\adaptive\test_projection.py tests\integration\test_dashboard_data_api.py tests\e2e\test_visual_layout.py --basetemp=.codex_tmp\pytest-observability
```

Expected: all tests pass, the fifth card fits at 1440/1024/768/390 widths, and serialized adaptive payloads contain aggregate counts/levels only.

- [ ] **Step 8: Commit operator/adaptive observability**

```powershell
git add -- app/strategy/planner.py app/runtime/service_factory.py adaptive_strategy.py app/adaptive/projection.py frontend/js/views/overview.js frontend/tests/dashboard-presentation.test.mjs test_adaptive_strategy.py tests/unit/adaptive/test_projection.py tests/integration/test_dashboard_data_api.py
git commit -m "feat: expose bounded exploration and contact status"
```

### Task 11: Documentation, Visual Acceptance, and Release Gate

**Files:**
- Modify: `README.md`
- Modify only if implementation commands changed: `docs/superpowers/specs/2026-08-13-persistent-exploration-contact-response-design.md`

**Interfaces:**
- Documents the account-scoped persistence boundary, the three fog states, the Worker frontier lifecycle, the contact/defense priority order, the exploration REST endpoint, and the adaptive telemetry boundary.
- Performs no migration of `.env`, `adaptive/`, or `data/` and never prints their contents.

- [ ] **Step 1: Update the operator and strategy documentation**

Replace README claims about “八方向递增环分散侦察” with the shipped behavior:

```markdown
- **持久探索与真实边界侦察**：当前可见区域保持明亮；曾经探索但当前不可见的区域保留为深色历史地形；从未探索区域保持近乎不透明。探索进度按 Arena 账号隔离并跨重启保存，只保存探索/永久障碍位，不保存敌人、资源或 Beacon 归属。
- **抗振荡 Worker 路由**：空载 Worker 领取真正连接未知区域的 frontier 租约；两格往返、短周期重复和失败边触发 tabu/cooldown，无法推进时明确 WAIT，而不是制造假进度。
- **接敌与 Core 防御分层**：Core `APPROACH+` 永远优先回防；Core 安全时，受威胁 Worker 先撤离，Ranger 优先拦截，至少一名 Vanguard 留守，敌人消失后只移动调查 3 Tick，绝不按旧 UUID 盲射。
```

Add the endpoint contract:

```markdown
`GET /api/v1/exploration?minX=&minY=&maxX=&maxY=` 返回至多 96×96 的 explored/currentVisible/obstacle 位图和单调 revision；账号作用域由服务端当前运行配置决定，调用方不能指定。
```

Document the dual dashboard status (`Core defense` versus `frontier contact`) and state that adaptive LLMs receive only aggregate exploration/contact counts and levels, never route coordinates or object/account identifiers.

- [ ] **Step 2: Run the complete Python release suite from a workspace temp root**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.codex_tmp\pytest-release
```

Expected: zero failures. Do not substitute only the focused suites from earlier tasks.

- [ ] **Step 3: Run every frontend Node test with PowerShell-safe file expansion**

Run:

```powershell
$nodeTests = Get-ChildItem -LiteralPath 'frontend/tests' -Filter '*.test.mjs' | Sort-Object FullName | ForEach-Object FullName
node --test $nodeTests
```

Expected: zero failures, including viewport-cache, fog-state, dashboard-presentation, map-camera, app-store, and live-connection coverage.

- [ ] **Step 4: Run compile, dependency, whitespace, and tracked-secret gates**

Run:

```powershell
.\.venv\Scripts\python.exe -m compileall -q balanced_tactic.py economic_strategy.py defense_strategy.py adaptive_strategy.py app tests
.\.venv\Scripts\python.exe -m pip check
git diff --check
$forbidden = git ls-files -- '.env' 'adaptive/**' 'data/**'
if ($forbidden) { throw "private runtime files are tracked" }
```

Expected: compile and dependency checks exit zero, `git diff --check` is empty, and `$forbidden` is empty. Do not run `git diff` on `.env`, `adaptive/`, or `data/`.

- [ ] **Step 5: Perform final visual acceptance at desktop and mobile widths**

Open the screenshots created by `tests/e2e/test_visual_layout.py`:

- `test-results/dashboard/overview-1440.png`
- `test-results/dashboard/overview-390.png`

Confirm all five conditions before release:

1. current visible cells are materially brighter than explored cells;
2. unknown cells are materially darker than explored cells;
3. obstacle/resource/unit sprites remain legible over their permitted fog state;
4. the legend names `当前可见`, `已探索`, and `未知` without horizontal overflow;
5. the dual Core/contact threat card remains readable at both widths.

If any condition fails, add or tighten an automated contrast/layout assertion before adjusting CSS and rerun Steps 2-5.

- [ ] **Step 6: Self-audit the implementation against the approved design**

Use the design file as a checklist and record the evidence in the final handoff:

```powershell
rg -n "SHA-256|32 x 32|96 x 96|APPROACH|3 Tick|account scope|unknown" docs/superpowers/specs/2026-08-13-persistent-exploration-contact-response-design.md
rg -n "enemy_id|last_seen|account_scope|api_key|position|coordinates" adaptive_strategy.py app/adaptive/projection.py app/api/exploration.py
```

Inspect every second command match. Identifiers/positions may exist in deterministic local planner or private storage code, but must not be admitted by the public exploration serializer or either adaptive allowlist. Verify runtime ordering from a focused integration test: load exploration -> observe authoritative Turn -> plan -> submit -> persist delta -> publish state.

- [ ] **Step 7: Commit documentation and any release-only test corrections**

```powershell
git add -- README.md docs/superpowers/specs/2026-08-13-persistent-exploration-contact-response-design.md
git commit -m "docs: explain persistent exploration and contact response"
```

If Step 5 or Step 6 required code/test corrections, commit each correction separately with its focused regression before this documentation commit; do not fold unrelated fixes into the docs commit.
