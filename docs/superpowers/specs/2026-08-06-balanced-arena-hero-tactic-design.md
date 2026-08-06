# Balanced Arena Hero tactic design

Date: 2026-08-06

## Objective

Create a durable, standalone Arena Hero tactic that makes balanced economic,
defensive, and combat decisions during every command window. The tactic should
be competitive and suitable for unattended continuous play, while remaining
small enough to decide and submit promptly.

The tactic optimizes for robust long-run performance and a high leaderboard
position. It cannot guarantee first place because outcomes also depend on other
players' plans, simultaneous movement conflicts, current world state, network
latency, and server availability.

## Contract and compatibility

- Use Python 3.11 or newer and the official `arena-hero` SDK.
- Pin the SDK to `arena-hero>=0.2.8,<0.3`, matching the bundled Arena Hero API
  v0.1, gameplay v0.13, and SDK v0.2.8 contract.
- Use the synchronous `ArenaHeroClient` and production endpoint
  `https://api.arenahero.io` through the SDK defaults.
- Treat each `Turn` as a complete authoritative replacement. Never retain SDK
  controllers, enemy entities, or resource availability as current truth across
  Turns.
- Queue one complete plan for the current Turn and call `turn.submit()` once.
- Read the API key from `ARENA_HERO_API_KEY`, falling back to `getpass`; never
  print or persist it.

## Program structure

The project will contain three runtime/test files:

- `balanced_tactic.py`: pure tactical helpers, `choose_actions(turn)`, and the
  synchronous connection loop.
- `test_balanced_tactic.py`: behavior-focused tests using lightweight fake Turn
  and controller objects. No credential or network connection is required.
- `requirements.txt`: the compatible official SDK range.

Tactical decisions remain separate from connection setup. This permits fast
offline tests and avoids coupling rule logic to WebSocket or HTTP behavior that
the official SDK already implements.

## Decision pipeline

`choose_actions(turn)` evaluates the current Turn once and queues at most one
action per controlled object. Decisions are deterministic, including target and
direction tie-breaks. The effective priority order is fixed: lifecycle, legal
combat, Worker retreat, Unit healing, Worker deposit/harvest, visible Beacon
pickup, goal-directed movement, then the Core action (recovery, repair, or
production). A lower-priority step never replaces an action already selected
for that object.

1. **Lifecycle**
   - If `turn.core` is absent, queue no invented actions and allow an empty plan
     to be submitted.
   - Use only controllers and state from the current Turn.

2. **Immediate defense and combat**
   - Build indexes from current visible enemies and obstacles.
   - A Ranger shoots a currently visible hostile only when its present cell is
     on a legal unobstructed horizontal, vertical, or exact-diagonal line at
     range 1-3. Prefer a Core, then the lowest-HP hostile, then deterministic ID
     order.
   - A Vanguard sweeps an adjacent cell containing visible hostile entities.
     Prefer a Core cell, then the cell with the most hostiles, then a fixed
     direction order.
   - A Worker threatened by a currently visible enemy favors a legal step toward
     its Core over economy.

3. **Healing and Core recovery**
   - A damaged controlled Unit may heal only when already sharing the stationary
     Core cell. Unit healing consumes that Unit's full action. Combat and
     Worker cargo conversion take precedence for the same object; a cargo-carrying
     Worker deposits rather than trying to heal in that Tick.
   - Use only resources visible at the start of the Turn for the healing budget.
     Queue eligible Unit heals in ascending raw UUID order, one HP budget unit
     at a time, up to the current Core balance; do not assume a future deposit
     or captured loot will fund a decision. This makes competing heals
     deterministic even though the server may resolve a queued heal partially.
   - After that budget is reserved, a damaged Core queues `HEAL` when at least
     one resource remains. If the Core is full HP, queue `REPAIR_SHIELD` when a
     shield point is missing, the current shield cap permits repair, and at
     least one resource remains. Core healing/repair is never queued while the
     Core is migrating.
   - Healing is still subject to same-Tick combat and resource changes; dynamic
     failure is accepted as safe and cost-free.

4. **Worker economy**
   - A Worker carrying cargo deposits when sharing a stationary Core and the Core
     has storage space.
   - An empty Worker standing on a cell present in the current
     `turn.resource_cells` harvests it.
   - Other Workers choose a currently visible resource cell or the Core as a
     destination. Targets are recalculated every Turn; no disappeared or fogged
     resource is treated as available.
   - Multiple empty Workers are assigned to distinct visible resource cells when
     possible to avoid same-cell `RESOURCE_DEPLETED` contention.

5. **Beacon opportunity**
   - Pick up the Beacon only when its current visible status is `GROUND`, an
     eligible controlled actor is already on its cell, and that actor has not
     taken a higher-priority survival, combat, healing, or economy action.
   - Do not infer a carrier or ground state when Beacon status is outside vision.
   - The starter tactic does not make long-range Beacon pursuit override Core
     defense or resource recovery.

6. **Movement**
   - Movement is a one-step deterministic choice from the four cardinal
     directions.
   - Reject currently visible obstacle cells, signed-int64 overflow, and moves
     that would obviously exceed friendly cell capacity.
   - For a retreat goal, score candidates by (visible-enemy distance descending,
     Core distance ascending, occupancy penalty, fixed direction order). For a
     resource goal, score by (Core/goal distance progress descending, visible
     enemy distance descending, occupancy penalty, fixed direction order). Reject
     a candidate containing a visible hostile or a currently visible obstacle;
     unknown fog cells remain possible but are treated as unverified.
   - Unknown fog cells are not treated as guaranteed clear; a failed move is
     learned only through the next authoritative Turn and events.

7. **Production**
   - Spawn only from a stationary Core with an available cell slot and enough
     resources after planned healing/recovery.
   - Keep population below the first upkeep threshold during the starter phase;
     do not grow into population 20+ unless the Core can sustain the projected
     next-Tick upkeep and retain a safety reserve. The projected upkeep is
     `tier * (tier + 1) // 2` for `tier = (population + 1) // 20` after the
     candidate spawn.
   - Establish a small economic base first, then maintain a mixed force. Prefer
     Workers until there are three, then fill missing combat roles with Rangers
     and Vanguards using a deterministic ratio and affordability checks.
   - Never queue production merely because resources meet the exact unit cost;
     retain a Core survival reserve.

8. **Fallback**
   - If no legal useful action is known, omit the object from the plan rather
     than guessing.
   - The complete plan should be produced in linear or near-linear time over the
     visible objects so it remains comfortably inside the global command window.

## Error handling and runtime behavior

- The connection loop relies on SDK reconnect, validation, safe retry, receipt,
  and Turn-staleness behavior.
- Log only Tick number and accepted status after submission; do not log state
  payloads or credentials.
- Allow `Ctrl-C` to stop cleanly through the client context manager.
- Catch `KeyboardInterrupt` only to close quietly. Terminal authentication,
  policy, protocol, and configuration exceptions are allowed to surface with a
  short non-secret error message; they are not weakened or bypassed.
- A protocol/model mismatch requires upgrading to a compatible official PyPI
  release; the tactic will not patch SDK validation or recreate the client.

## Test design

Tests will be written before implementation and will verify the queued action
intent through fake controllers. Representative cases include:

- absent Core/respawning state queues no actions;
- Worker deposit, harvest, distinct resource assignment, and homeward movement;
- current resource disappearance causes immediate retargeting without memory;
- obstacle-aware and deterministic movement;
- adjacent Vanguard target selection;
- legal Ranger target geometry and obstacle blocking;
- damaged Unit healing at a stationary Core and resource ordering between
  multiple heals;
- damaged Core healing versus shield repair and production priority;
- opportunistic visible Beacon pickup without fog inference;
- production balance, Core-cell capacity, reserve, and upkeep constraints;
- one action per object and no stale controller reuse;
- API-key loading does not expose a secret.

Final verification will run the focused unit tests, the full test suite,
`python -m compileall -q .`, `python -m pip check`, an import/version check for
the official SDK, and a credential-pattern scan. No live Arena Hero connection
will be made unless separately requested and a credential is available.

## Success criteria

- The script runs continuously with the official SDK and submits exactly one
  complete plan per received Turn.
- Decisions are deterministic, current-state-only, legal by the bundled v0.13
  rules, and fast enough for the command window.
- Economy, survival, combat, healing, production, and Beacon opportunities are
  all represented without one category blindly dominating the others.
- Offline tests cover the critical rule boundaries and pass without a network
  connection or API key.
- Source files and logs contain no credentials.
