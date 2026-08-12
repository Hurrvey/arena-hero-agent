# Adaptive Root Directory Design

## Goal

Move all adaptive runtime state from `.codex_tmp/adaptive/` to `adaptive/` at
the repository root, while preserving existing telemetry, reports, and profile
state without exposing credentials or overwriting a newer destination.

## Path Contract

- The built-in default is `D:/arena-hero/adaptive`, derived from the directory
  containing `adaptive_strategy.py`, not from the process working directory.
- `ARENA_HERO_ADAPTIVE_STATE_DIR=adaptive` resolves relative to that same
  project root. An explicitly configured absolute path remains absolute.
- `.env` and the local `.env.example` use `adaptive` as the portable value.
- Git ignores the complete `adaptive/` directory because it contains private
  telemetry, model reports, and adaptive profile state.

## Migration

The existing `.codex_tmp/adaptive` directory is moved to `adaptive` only after
both absolute paths are verified to be direct descendants of the repository
root and the destination is confirmed absent. If the destination already
exists, no merge or overwrite is attempted. The remaining `.codex_tmp`
directory is left intact because it also contains disposable test output.

## Compatibility and Failure Handling

- Existing explicit absolute state-directory settings continue to work.
- A missing legacy directory is not an error.
- Adaptive loading remains fail-open and never blocks the deterministic game
  loop.
- No key or `.env` value is printed, copied into documentation, or committed.

## Verification

Tests must prove that an enabled coordinator with no explicit state directory
uses the project-root `adaptive` directory even after changing the process
working directory. Existing explicit-directory tests must remain green. After
migration, the old directory must be absent, the new directory must contain the
same file count, and the complete test, compile, dependency, and diff checks
must pass.

