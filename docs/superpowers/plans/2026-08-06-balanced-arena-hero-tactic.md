# Balanced Arena Hero Tactic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone, testable Arena Hero bot that makes deterministic balanced economy, survival, combat, healing, Beacon, and production decisions every Turn.

**Architecture:** Keep all tactical decisions in a pure `choose_actions(turn)` function that reads only the current authoritative Turn and queues one action per object. Keep API-key loading and the synchronous `ArenaHeroClient` loop in the same small module but outside the decision function. Use lightweight fake controllers in unit tests so no credential or live connection is needed.

**Tech Stack:** Python 3.11+, official `arena-hero` SDK `>=0.2.8,<0.3`, pytest, standard library only beyond the SDK.

## Global Constraints

- Use the official PyPI package only: `arena-hero>=0.2.8,<0.3`.
- Treat each `Turn` as a complete authoritative replacement and never reuse controllers across Turns.
- Submit one complete current-Tick plan once per Turn through `turn.submit()`.
- Use only visible current resource cells and visible enemies; never invent fogged entities or resource quantities.
- Core capacity is `max(10, population * 5)`; upkeep is `tier * (tier + 1) // 2` with `tier = population // 20`.
- Core migration is not initiated by the starter policy; while moving, no deposit, heal, repair, or spawn is queued.
- Ranger shots use `ranger.shoot_cell(position)` only after local range/alignment/obstacle checks.
- Never print, store, or commit an API key.
- Follow test-driven development: each behavior gets a failing test before production code.
- Keep each decision pass fast enough for the global 15-second command window.

## File map

- Create `requirements.txt`: the pinned official SDK dependency.
- Create `balanced_tactic.py`: public `choose_actions`, `load_api_key`, and `play`; private deterministic geometry, prioritization, and production helpers.
- Create `test_balanced_tactic.py`: fake Turn/controllers and focused behavior tests.
- Create no framework, database, configuration layer, or custom API/WebSocket implementation.

---

### Task 1: Dependency and test harness

**Files:**
- Create: `requirements.txt`
- Create: `test_balanced_tactic.py`
- Create: `balanced_tactic.py` (only the importable public-function stubs required to make the first test fail for the intended reason)

**Interfaces:**
- Produces `FakeTurn`, `FakeUnit`, `FakeCore`, and `FakeView` fixtures used by later tests.
- Establishes the public signatures `choose_actions(turn) -> None`, `load_api_key() -> str`, and `play(api_key: str | None = None) -> None`.

- [ ] **Step 1: Declare the dependency.**

  Write `requirements.txt` exactly as:

  ```text
  arena-hero>=0.2.8,<0.3
  ```

- [ ] **Step 2: Install the documented SDK.**

  Run:

  ```powershell
  python -m pip install -r requirements.txt
  ```

  Expected: the published `arena-hero` package installs, or the command reports a network/registry failure that must be retried with approved escalation before continuing. Do not substitute a Git checkout or a handwritten SDK.

- [ ] **Step 3: Write the first failing test and reusable fakes.**

  Start `test_balanced_tactic.py` with this minimal harness and lifecycle test:

  ```python
  from __future__ import annotations

  from types import SimpleNamespace
  from uuid import UUID

  from arena_hero import Direction, UnitType

  from balanced_tactic import choose_actions


  class FakeController:
      def __init__(self, *, object_id, position=(0, 0), hp=1, unit_type=None, cargo=0):
          self.id = object_id
          self.position = position
          self.hp = hp
          self.shield = 5
          self.unit_type = unit_type
          self.cargo = cargo
          self.view = SimpleNamespace(
              id=object_id,
              position=position,
              hp=hp,
              shield=5,
              unit_type=unit_type,
              state="NORMAL",
          )
          self.actions = []

      def _record(self, name, *args):
          self.actions.append((name, *args))

      def move(self, direction): self._record("MOVE", direction)
      def harvest(self): self._record("HARVEST")
      def deposit(self): self._record("DEPOSIT")
      def heal(self): self._record("HEAL")
      def sweep(self, direction): self._record("SWEEP", direction)
      def shoot_cell(self, position): self._record("SHOOT", position)
      def pickup_beacon(self): self._record("PICKUP_BEACON")
      def repair_shield(self): self._record("REPAIR_SHIELD")
      def spawn(self, unit_type): self._record("SPAWN", unit_type)


  def make_turn(*, core, units=(), resources=0, upkeep_next_tick=0, resource_cells=(), obstacle_cells=(), enemies=(), beacon=None):
      workers = tuple(u for u in units if u.unit_type is UnitType.WORKER)
      vanguards = tuple(u for u in units if u.unit_type is UnitType.VANGUARD)
      rangers = tuple(u for u in units if u.unit_type is UnitType.RANGER)
      state = SimpleNamespace(
          population=len(units),
          upkeep_next_tick=upkeep_next_tick,
          status="ACTIVE" if core is not None else "RESPAWNING",
      )
      return SimpleNamespace(
          tick=1,
          state=state,
          resources=resources,
          resource_space=max(0, max(10, len(units) * 5) - resources),
          core=core,
          units=tuple(units),
          workers=workers,
          vanguards=vanguards,
          rangers=rangers,
          visible_enemies=tuple(enemies),
          resource_cells=frozenset(resource_cells),
          obstacle_cells=frozenset(obstacle_cells),
          beacon=beacon or SimpleNamespace(position=(0, 0), status=None, carrier_id=None),
          events=(),
      )


  def test_respawning_turn_submits_no_invented_actions():
      turn = make_turn(core=None)

      choose_actions(turn)

      assert turn.core is None
  ```

- [ ] **Step 4: Run the focused test to verify the correct failure.**

  Run:

  ```powershell
  python -m pytest test_balanced_tactic.py::test_respawning_turn_submits_no_invented_actions -q
  ```

  Expected: FAIL because `balanced_tactic` does not yet provide `choose_actions`; it must not fail because of a typo in the fixture or missing SDK import.

- [ ] **Step 5: Add only importable stubs.**

  Create `balanced_tactic.py` with the SDK imports and these temporary signatures:

  ```python
  from __future__ import annotations

  import os
  from getpass import getpass

  from arena_hero import ArenaHeroClient, Direction, UnitType


  def choose_actions(turn) -> None:
      return None


  def load_api_key() -> str:
      return os.environ.get("ARENA_HERO_API_KEY") or getpass("Arena Hero API key: ")


  def play(api_key: str | None = None) -> None:
      raise NotImplementedError
  ```

- [ ] **Step 6: Run the test to verify the lifecycle behavior now passes.**

  Run the same pytest command. Expected: PASS. Commit the harness and dependency:

  ```powershell
  git add requirements.txt balanced_tactic.py test_balanced_tactic.py
  git -c commit.gpgsign=false commit -m "test: scaffold balanced tactic harness"
  ```

---

### Task 2: Turn-safe helpers and visible combat

**Files:**
- Modify: `balanced_tactic.py`
- Test: `test_balanced_tactic.py`

**Interfaces:**
- Consumes the fake controllers from Task 1.
- Produces private helpers `_enum_name`, `_uuid_key`, `_distance`, `_step`, `_aligned_range`, `_line_is_clear`, `_queue_ranger_actions`, and `_queue_vanguard_actions`.

- [ ] **Step 1: Write failing Ranger and Vanguard tests.**

  Add tests with real SDK enums and UUIDs:

  ```python
  def test_ranger_chooses_a_legal_visible_core_cell():
      core = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000010"), position=(5, 5), hp=5)
      ranger = FakeController(
          object_id=UUID("00000000-0000-0000-0000-000000000001"),
          position=(0, 3), hp=2, unit_type=UnitType.RANGER,
      )
      enemy_core = SimpleNamespace(kind="CORE", id=UUID("00000000-0000-0000-0000-000000000020"), position=(0, 0), hp=5)
      turn = make_turn(core=core, units=(ranger,), enemies=(enemy_core,))

      choose_actions(turn)

      assert ranger.actions == [("SHOOT", (0, 0))]


  def test_ranger_does_not_shoot_through_a_visible_obstacle():
      core = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000010"), position=(0, 0), hp=5)
      ranger = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000001"), position=(0, 0), hp=2, unit_type=UnitType.RANGER)
      enemy = SimpleNamespace(kind="UNIT", id=UUID("00000000-0000-0000-0000-000000000020"), position=(0, 3), hp=2)
      turn = make_turn(core=core, units=(ranger,), enemies=(enemy,), obstacle_cells={(0, 1)})

      choose_actions(turn)

      assert ranger.actions == []


  def test_vanguard_sweeps_the_adjacent_cell_with_most_hostiles():
      core = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000010"), position=(0, 0), hp=5)
      vanguard = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000001"), position=(0, 0), hp=4, unit_type=UnitType.VANGUARD)
      enemies = (
          SimpleNamespace(kind="UNIT", id=UUID("00000000-0000-0000-0000-000000000020"), position=(1, 0), hp=2),
          SimpleNamespace(kind="UNIT", id=UUID("00000000-0000-0000-0000-000000000021"), position=(1, 0), hp=1),
      )
      turn = make_turn(core=core, units=(vanguard,), enemies=enemies)

      choose_actions(turn)

      assert vanguard.actions == [("SWEEP", Direction.RIGHT)]
  ```

- [ ] **Step 2: Run the three tests and confirm they fail for missing behavior.**

  Run:

  ```powershell
  python -m pytest test_balanced_tactic.py -k "ranger or vanguard" -q
  ```

  Expected: FAIL because the stub does not queue combat actions.

- [ ] **Step 3: Implement geometry and combat selection.**

  Add these exact behaviors to `balanced_tactic.py`:

  ```python
  def _enum_name(value) -> str:
      return getattr(value, "value", value).upper()


  def _uuid_key(identifier) -> bytes:
      return getattr(identifier, "bytes", str(identifier).encode("ascii"))


  def _distance(a, b) -> int:
      return abs(a[0] - b[0]) + abs(a[1] - b[1])


  def _aligned_range(origin, target) -> int | None:
      dx = abs(target[0] - origin[0])
      dy = abs(target[1] - origin[1])
      if dx == 0 and 1 <= dy <= 3:
          return dy
      if dy == 0 and 1 <= dx <= 3:
          return dx
      if dx == dy and 1 <= dx <= 3:
          return dx
      return None


  def _line_is_clear(origin, target, obstacles) -> bool:
      distance = _aligned_range(origin, target)
      if distance is None:
          return False
      dx = 0 if target[0] == origin[0] else (1 if target[0] > origin[0] else -1)
      dy = 0 if target[1] == origin[1] else (1 if target[1] > origin[1] else -1)
      return all((origin[0] + dx * i, origin[1] + dy * i) not in obstacles for i in range(1, distance))
  ```

  In `_queue_ranger_actions`, sort legal visible targets by `(kind != "CORE", hp, _uuid_key(id))`, call `ranger.shoot_cell(target.position)`, and mark the Ranger as acted. In `_queue_vanguard_actions`, group adjacent visible enemies by target cell and sort cells by `(contains_core ? 0 : 1, -hostile_count, direction_order, x, y)`, then call `vanguard.sweep(direction)`.

- [ ] **Step 4: Run all focused combat tests and commit.**

  Run:

  ```powershell
  python -m pytest test_balanced_tactic.py -k "ranger or vanguard" -q
  ```

  Expected: all selected tests PASS. Commit:

  ```powershell
  git add balanced_tactic.py test_balanced_tactic.py
  git -c commit.gpgsign=false commit -m "feat: add visible combat targeting"
  ```

---

### Task 3: Worker economy and obstacle-aware movement

**Files:**
- Modify: `balanced_tactic.py`
- Test: `test_balanced_tactic.py`

**Interfaces:**
- Consumes `_distance`, `_step`, current Turn collections, and the `acted`/`planned_from_core` sets from combat selection.
- Produces `_candidate_steps`, `_move_to_goal`, `_queue_worker_actions`, and deterministic direction ordering `(UP, RIGHT, DOWN, LEFT)`.

- [ ] **Step 1: Write failing Worker tests.**

  Add:

  ```python
  def test_worker_harvests_visible_resource_on_current_cell():
      core = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000010"), position=(0, 0), hp=5)
      worker = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000001"), position=(1, 0), hp=2, unit_type=UnitType.WORKER)
      turn = make_turn(core=core, units=(worker,), resources=5, resource_cells={(1, 0)})

      choose_actions(turn)

      assert worker.actions == [("HARVEST",)]


  def test_worker_deposits_cargo_only_at_stationary_core_with_space():
      core = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000010"), position=(0, 0), hp=5)
      worker = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000001"), position=(0, 0), hp=2, unit_type=UnitType.WORKER, cargo=2)
      turn = make_turn(core=core, units=(worker,), resources=5)

      choose_actions(turn)

      assert worker.actions == [("DEPOSIT",)]


  def test_worker_moves_around_visible_obstacle_toward_resource():
      core = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000010"), position=(0, 0), hp=5)
      worker = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000001"), position=(0, 0), hp=2, unit_type=UnitType.WORKER)
      turn = make_turn(core=core, units=(worker,), resources=5, resource_cells={(2, 0)}, obstacle_cells={(1, 0)})

      choose_actions(turn)

      assert worker.actions == [("MOVE", Direction.UP)]
  ```

- [ ] **Step 2: Run the Worker tests and confirm they fail.**

  Run:

  ```powershell
  python -m pytest test_balanced_tactic.py -k "worker" -q
  ```

  Expected: FAIL because no Worker action policy exists yet.

- [ ] **Step 3: Implement current-state Worker assignment.**

  Add the following concrete rules:

  ```python
  DIRECTIONS = (Direction.UP, Direction.RIGHT, Direction.DOWN, Direction.LEFT)
  DIRECTION_DELTAS = {direction: direction.delta for direction in DIRECTIONS}


  def _step(position, direction):
      dx, dy = DIRECTION_DELTAS[direction]
      candidate = (position[0] + dx, position[1] + dy)
      if not (-2**63 <= candidate[0] <= 2**63 - 1 and -2**63 <= candidate[1] <= 2**63 - 1):
          return None
      return candidate


  def _candidate_steps(unit, turn, occupied, enemies):
      enemy_cells = {enemy.position for enemy in turn.visible_enemies}
      for index, direction in enumerate(DIRECTIONS):
          destination = _step(unit.position, direction)
          if destination is None or destination in turn.obstacle_cells or destination in enemy_cells:
              continue
          other_count = sum(position == destination for object_id, position in occupied if object_id != unit.id)
          if other_count >= 2:
              continue
          yield index, direction, destination, other_count
  ```

  Sort Workers by raw UUID bytes before assigning goals. Assign visible resource cells in sorted `(distance, x, y)` order, skipping cells already assigned to another Worker. A cargo Worker targets the Core; an empty Worker on a current resource cell harvests; a Worker within Manhattan distance 2 of any visible enemy retreats toward the Core. For a retreating Worker, choose the candidate with greatest distance from the nearest visible enemy first, then greatest progress toward the Core, then lowest occupancy count, then the fixed direction index. For a non-threatened Worker, choose greatest progress toward its resource/Core goal first, then greatest distance from visible enemies, then lowest occupancy count, then the fixed direction index. Do not persist a resource target after the Turn ends.

- [ ] **Step 4: Run focused Worker tests and commit.**

  Run:

  ```powershell
  python -m pytest test_balanced_tactic.py -k "worker" -q
  ```

  Expected: all selected tests PASS. Commit:

  ```powershell
  git add balanced_tactic.py test_balanced_tactic.py
  git -c commit.gpgsign=false commit -m "feat: add Worker economy and movement"
  ```

---

### Task 4: Healing, Beacon handling, and Core production

**Files:**
- Modify: `balanced_tactic.py`
- Test: `test_balanced_tactic.py`

**Interfaces:**
- Consumes combat/economy action tracking and current `turn.resources`, `turn.resource_space`, `turn.beacon`, and `turn.state.population`.
- Produces `_is_stationary_core`, `_beacon_is_ground`, `_queue_unit_heals`, `_desired_spawn_type`, `_upkeep_for`, and `_queue_core_action`.

- [ ] **Step 1: Write failing recovery and production tests.**

  Add:

  ```python
  def test_damaged_unit_at_stationary_core_heals_before_idle_movement():
      core = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000010"), position=(0, 0), hp=5)
      ranger = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000001"), position=(0, 0), hp=1, unit_type=UnitType.RANGER)
      turn = make_turn(core=core, units=(ranger,), resources=2)

      choose_actions(turn)

      assert ranger.actions == [("HEAL",)]


  def test_core_repairs_shield_before_spawning_when_hp_is_full():
      core = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000010"), position=(0, 0), hp=5)
      core.shield = 4
      core.view.shield = 4
      worker = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000001"), position=(1, 0), hp=2, unit_type=UnitType.WORKER)
      turn = make_turn(core=core, units=(worker,), resources=6)

      choose_actions(turn)

      assert core.actions == [("REPAIR_SHIELD",)]


  def test_core_spawns_worker_only_with_reserve_and_cell_room():
      core = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000010"), position=(0, 0), hp=5)
      turn = make_turn(core=core, units=(), resources=10)

      choose_actions(turn)

      assert core.actions == [("SPAWN", UnitType.WORKER)]


  def test_ground_beacon_is_picked_up_only_when_already_visible_and_idle():
      core = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000010"), position=(0, 0), hp=5)
      worker = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000001"), position=(1, 0), hp=2, unit_type=UnitType.WORKER)
      beacon = SimpleNamespace(position=(1, 0), status="GROUND", carrier_id=None)
      turn = make_turn(core=core, units=(worker,), resources=5, beacon=beacon)

      choose_actions(turn)

      assert worker.actions == [("PICKUP_BEACON",)]
  ```

- [ ] **Step 2: Run the recovery tests and confirm they fail.**

  Run:

  ```powershell
  python -m pytest test_balanced_tactic.py -k "heal or shield or spawn or beacon" -q
  ```

  Expected: FAIL because recovery, Beacon, and Core policies are not implemented.

- [ ] **Step 3: Implement deterministic recovery and production.**

  Use these exact formulas and priorities:

  ```python
  CORE_RESERVE = 5
  CORE_MAX_HP = 5
  UNIT_MAX_HP = {"WORKER": 2, "VANGUARD": 4, "RANGER": 2}


  def _upkeep_for(population: int) -> int:
      tier = population // 20
      return tier * (tier + 1) // 2


  def _desired_spawn_type(turn):
      workers = sum(_enum_name(unit.unit_type) == "WORKER" for unit in turn.units)
      rangers = sum(_enum_name(unit.unit_type) == "RANGER" for unit in turn.units)
      vanguards = sum(_enum_name(unit.unit_type) == "VANGUARD" for unit in turn.units)
      if workers < 3:
          return UnitType.WORKER
      if rangers == 0:
          return UnitType.RANGER
      if vanguards == 0:
          return UnitType.VANGUARD
      return UnitType.RANGER if rangers <= vanguards else UnitType.VANGUARD
  ```

  Set the initial local budget to `max(0, turn.resources - turn.state.upkeep_next_tick)`. Derive each Unit's missing HP from `UNIT_MAX_HP[_enum_name(unit.unit_type)] - unit.hp` and use `CORE_MAX_HP - core.hp` for the Core. Queue eligible non-cargo damaged Units at the stationary Core in raw UUID order, decrementing that budget by `min(missing_hp, budget)` per queued Unit. Then queue Core `HEAL` if HP is below `CORE_MAX_HP` and budget remains; otherwise queue `REPAIR_SHIELD` if shield is below its current cap and budget remains. The current shield cap is 10 only when the current Turn proves that this player's Core or Unit carries the Beacon; otherwise it is 5. If neither recovery action is selected, compute the candidate spawn cost (Worker 5, Vanguard 10, Ranger 12), projected upkeep after the spawn, and require `max(0, turn.resources - turn.state.upkeep_next_tick) >= cost + projected_upkeep + CORE_RESERVE`. Require a stationary Core and post-movement Core-cell occupancy (including the Core itself) below 2; account for units already queued to move away and do not count a Worker merely moving toward the Core unless its destination is the Core cell. Never queue Core actions while `core.view.state` is `MOVING`.

  For Beacon pickup, inspect only `turn.beacon.status`; accept `GROUND` and reject absent/unknown status. Choose an unacted controlled object already on `turn.beacon.position`, preferring a stationary Core only if no Core recovery action is needed, then the lowest UUID idle Unit. Do not move toward an unseen Beacon.

- [ ] **Step 4: Run recovery tests and commit.**

  Run:

  ```powershell
  python -m pytest test_balanced_tactic.py -k "heal or shield or spawn or beacon" -q
  ```

  Expected: all selected tests PASS. Commit:

  ```powershell
  git add balanced_tactic.py test_balanced_tactic.py
  git -c commit.gpgsign=false commit -m "feat: add healing Beacon and production policy"
  ```

---

### Task 5: Compose the complete policy and connection loop

**Files:**
- Modify: `balanced_tactic.py`
- Test: `test_balanced_tactic.py`

**Interfaces:**
- Consumes all private helpers from Tasks 2–4.
- Produces the final public `choose_actions(turn)`, `load_api_key()`, and `play(api_key=None)` behavior.

- [ ] **Step 1: Write failing composition and credential tests.**

  Add:

  ```python
  def test_priority_does_not_replace_worker_deposit_with_retreat():
      core = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000010"), position=(0, 0), hp=5)
      worker = FakeController(object_id=UUID("00000000-0000-0000-0000-000000000001"), position=(0, 0), hp=2, unit_type=UnitType.WORKER, cargo=1)
      enemy = SimpleNamespace(kind="UNIT", id=UUID("00000000-0000-0000-0000-000000000020"), position=(1, 0), hp=2)
      turn = make_turn(core=core, units=(worker,), resources=5, enemies=(enemy,))

      choose_actions(turn)

      assert worker.actions == [("DEPOSIT",)]


  def test_load_api_key_prefers_environment_without_printing(monkeypatch):
      monkeypatch.setenv("ARENA_HERO_API_KEY", "secret-test-key")

      from balanced_tactic import load_api_key

      assert load_api_key() == "secret-test-key"


  def test_load_api_key_prompts_when_environment_is_empty(monkeypatch):
      monkeypatch.delenv("ARENA_HERO_API_KEY", raising=False)
      monkeypatch.setattr("balanced_tactic.getpass", lambda prompt: "prompted-key")

      from balanced_tactic import load_api_key

      assert load_api_key() == "prompted-key"
  ```

- [ ] **Step 2: Run the composition tests and confirm the missing behavior.**

  Run:

  ```powershell
  python -m pytest test_balanced_tactic.py -k "priority_does_not_replace or load_api_key" -q
  ```

  Expected: the priority test fails until the complete composition order is wired; the credential tests fail until `load_api_key` is wired into the final module.

- [ ] **Step 3: Compose priorities and implement the SDK loop.**

  Implement `choose_actions` in this order: return for `turn.core is None`; initialize `acted` and `planned_from_core`; queue Ranger/Vanguard combat; queue Worker retreat; queue eligible Unit heals; queue Worker deposit/harvest; queue Beacon pickup for an idle co-located actor; queue movement for remaining Workers and idle combat Units; finally queue one Core recovery/production action. Every queue helper checks `unit.id not in acted` before calling a controller method and adds the ID immediately afterward.

  Replace the temporary loop with:

  ```python
  def play(api_key: str | None = None) -> None:
      key = api_key or load_api_key()
      try:
          with ArenaHeroClient(api_key=key) as game:
              for turn in game.turns():
                  choose_actions(turn)
                  accepted = turn.submit()
                  print(f"tick={accepted.tick} accepted={accepted.accepted}")
      except KeyboardInterrupt:
          return
      except Exception as exc:
          # Do not include exception text because a transport implementation may
          # echo request details; expose only the non-secret exception class.
          raise SystemExit(f"Arena Hero stopped: {type(exc).__name__}") from exc


  if __name__ == "__main__":
      play()
  ```

- [ ] **Step 4: Run the complete offline suite and commit.**

  Run:

  ```powershell
  python -m pytest -q
  ```

  Expected: all tests PASS with no live connection. Commit:

  ```powershell
  git add balanced_tactic.py test_balanced_tactic.py
  git -c commit.gpgsign=false commit -m "feat: compose balanced Arena Hero bot"
  ```

---

### Task 6: Verification and handoff

**Files:**
- Modify: none unless a verification failure requires a focused fix.

**Interfaces:**
- Verifies the final public script and dependency contract; no live credential is required.

- [ ] **Step 1: Run syntax and import checks.**

  ```powershell
  python -m compileall -q .
  python -c "import arena_hero, balanced_tactic; print(arena_hero.__version__)"
  ```

  Expected: both commands succeed; the printed SDK version is in the `0.2.x` compatible range.

- [ ] **Step 2: Check dependency consistency.**

  ```powershell
  python -m pip check
  ```

  Expected: `No broken requirements found.`

- [ ] **Step 3: Search for credential leakage.**

  ```powershell
  rg -n -i "ARENA_HERO_API_KEY|authorization|bearer|secret|api[-_ ]?key" --glob "!docs/superpowers/**" .
  ```

  Expected: only the environment-variable name, safe test fixture strings, and code that passes the runtime key to the official SDK; no real credential or logged Authorization header.

- [ ] **Step 4: Run the final suite and inspect the diff.**

  ```powershell
  python -m pytest -q
  git status --short
  git log --oneline --decorate -6
  ```

  Expected: all tests pass, the worktree is clean, and the history contains the focused commits from Tasks 1–5.

- [ ] **Step 5: Report the handoff.**

  Report the selected tactic-script mode, created files, installed SDK version, validation commands/results, and that no live session was started unless the user separately supplies a credential and requests live play. State plainly that the tactic is designed to compete for a high rank but cannot guarantee first place.
