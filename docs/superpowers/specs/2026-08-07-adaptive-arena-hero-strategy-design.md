# Adaptive Arena Hero Strategy Design

## Goal

Add a safe, periodic learning loop that evaluates the tactic against the
latest bundled Arena Hero v0.14 rules, asks a second LLM to propose a better
Beacon-and-economy policy, and applies only a bounded, validated policy profile
with automatic rollback when the canary period underperforms.

The feature must improve the balance between the three independent lifetime
leaderboards (Beacon ticks, damage, and Core-destruction participation) without
claiming that any tactic can guarantee first place.

## Non-goals

- Do not let an LLM submit Arena Hero actions directly.
- Do not let an LLM write or execute arbitrary Python, shell commands, or Git
  operations.
- Do not use hidden Beacon, enemy, resource, UUID, or map information.
- Do not perform an LLM request inside the 15-second command-window path.
- Do not replace the official `arena-hero` SDK or its connection/retry logic.
- Do not require a live Arena Hero connection to test the feature.

## Architecture

The existing deterministic planner remains the authority for every Turn. A
new `StrategyProfile` contains a small, bounded set of knobs that are read by
the planner. The profile preserves hard Beacon and economy floors, so a model
cannot turn the tactic into a pure combat or pure harvesting policy.

`AdaptiveCoordinator` runs as a daemon worker after a plan is submitted. It
serializes the complete current Turn and the previous Tick's resolution events
to a local JSONL telemetry store. When the configured tick/time interval is
reached, it performs two separate chat-completion calls:

1. An evaluator LLM receives a versioned packet from the current local
   `$arena-hero` skill plus a deterministic scorecard and returns a structured
   review of Beacon control, resource throughput, combat, losses, failures, and
   rule risks.
2. A designer LLM receives the same rules packet, the current profile, and the
   evaluator review. It returns only a JSON `StrategyProfile` candidate and a
   short rationale.

The coordinator validates the candidate locally, writes a versioned cycle
report, and atomically swaps the profile at a Turn boundary. A later cycle
compares normalized internal objective scores. If the candidate loses more than
the configured rollback ratio, the previous profile is restored. LLM failures,
timeouts, malformed JSON, stale skill files, or validation errors leave the
current profile unchanged.

## Skill and rule packet

At each cycle the coordinator reads `SKILL.md`, `game-rules.md`,
`reference-numbers.md`, `reference-glossary.md`, `tactic-authoring.md`, and
`reference-source-and-version.md` from the local skill root. It computes a
SHA-256 fingerprint and includes the fingerprint and relevant text in both
prompts. The packet states that telemetry is untrusted data, not instructions.
If the required files are missing, no LLM call is made.

## Telemetry and scorecard

Telemetry is a redacted JSON object per Turn. It includes only the current
authoritative state, controlled-object summaries, currently visible enemies,
the public Beacon fields when visible, the queued plan, acceptance metadata,
and `turn.events`. API keys and authorization headers are never serialized.

The deterministic scorecard counts, when observable:

- Beacon pickup/loss events and Beacon-carried ticks;
- natural and Beacon-bonus harvest resources, deposits, and Core captures;
- successful shots, sweep damage, Core-destruction participation, and spawns;
- Unit/Core losses, failed actions, overflow, and recovery;
- ticks observed and normalized internal score.

The internal score is explicitly a tuning signal, not an official aggregate
leaderboard. Beacon ticks and Core survival receive hard positive/negative
weights; harvest/deposit/capture throughput and combat are separate fields so
the evaluator cannot hide an economic regression behind damage.

## Bounded strategy profile

The profile schema is versioned and rejects unknown fields and out-of-range
values. Defaults reproduce the current tactic. The first version exposes:

- `beacon_priority`: `[0.75, 1.50]`;
- `economy_priority`: `[0.75, 1.50]`;
- `combat_priority`: `[0.50, 1.25]`;
- `worker_target`: integer `[2, 3]`;
- `ranger_ratio`: `[1.0, 3.0]` relative to Vanguard count;
- `carrier_safety_margin`: integer `[0, 1]` extra visible incoming hit buffer;
- `spawn_aggression`: `[0.0, 1.0]` bounded willingness to vacate a Core cell.

The validator additionally requires Beacon and economy priorities to remain at
least `0.75`, keeps `worker_target >= 2`, and never changes the official
prices, capacity, action legality, or fog-of-war behavior.

## Configuration and operation

Adaptive mode is opt-in and disabled when no LLM key is configured. The
following environment variables control it:

- `ARENA_HERO_ADAPTIVE=1` enables the coordinator;
- `ARENA_HERO_LLM_API_KEY` supplies the separate LLM credential;
- `ARENA_HERO_LLM_BASE_URL` defaults to an OpenAI-compatible `/v1` endpoint;
- `ARENA_HERO_EVALUATOR_MODEL` and `ARENA_HERO_DESIGNER_MODEL` select the two
  roles;
- `ARENA_HERO_ADAPTIVE_INTERVAL_TICKS` defaults to `60`;
- `ARENA_HERO_ADAPTIVE_MIN_SECONDS` defaults to `900`;
- `ARENA_HERO_ADAPTIVE_AUTO_APPLY` defaults to `1` after local validation;
- `ARENA_HERO_ADAPTIVE_ROLLBACK_RATIO` defaults to `0.15`;
- `ARENA_HERO_ADAPTIVE_STATE_DIR` defaults to the project-root `adaptive`
  directory; relative overrides are also resolved from the project root.

The LLM adapter uses the standard library HTTP client and an
OpenAI-compatible `chat/completions` endpoint, so no provider-specific SDK or
new network protocol is embedded in the tactic. The two role models may be the
same model, but are configured independently.

## Error handling and safety

- A coordinator call has a finite timeout and one background worker; cycles do
  not overlap.
- HTTP errors, missing credentials, non-JSON responses, schema failures, and
  skill-bundle errors are recorded without exposing secrets.
- Profile writes use a temporary file and `os.replace`.
- A profile is read once per Turn and never changes during planning.
- A candidate is never arbitrary source code and cannot directly call SDK
  controls.
- The main loop remains responsive if the LLM endpoint is offline.
- `Ctrl+C` closes the coordinator without waiting on a live request.

## Testing

Tests will cover deterministic scorecard aggregation, event de-duplication,
profile bounds and Beacon/economy floors, prompt skill fingerprinting, JSON
parsing, timeout/failure fallback, two-role sequencing, atomic profile
activation, rollback, and the existing planner's default behavior. All tests
run without an Arena Hero API key or a live game.
