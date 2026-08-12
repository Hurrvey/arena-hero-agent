# Adaptive Root Directory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the repository-root `adaptive/` directory the deterministic location for all adaptive runtime state and safely migrate the existing state.

**Architecture:** Resolve default and relative configured state paths against the directory containing `adaptive_strategy.py`; retain absolute-path overrides. Update local configuration, Git ignore rules, and documentation, then move the verified legacy directory without overwriting any destination.

**Tech Stack:** Python 3.11+, pathlib, PowerShell, pytest, Git.

## Global Constraints

- Do not read, print, stage, or commit `.env` secrets.
- Do not overwrite an existing `adaptive/` directory.
- Do not delete `.codex_tmp`; only move its verified `adaptive` child.
- Keep adaptive failures isolated from the deterministic Turn loop.

---

### Task 1: Project-root State Path

**Files:**
- Modify: `adaptive_strategy.py`
- Modify: `test_adaptive_strategy.py`

**Interfaces:**
- Produces `_PROJECT_ROOT` and `_adaptive_state_dir(value: str | None) -> Path`.
- `AdaptiveCoordinator.from_env()` consumes the helper and passes its result to the existing constructor.

- [ ] **Step 1: Write a failing test**

Add a test that changes the process current directory, enables the coordinator,
omits `ARENA_HERO_ADAPTIVE_STATE_DIR`, and expects `coordinator.state_dir` to
equal `Path(adaptive_strategy.__file__).resolve().parent / "adaptive"`.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest -q test_adaptive_strategy.py -k project_adaptive`

Expected: the current `.codex_tmp/adaptive` relative default does not equal the
project-root `adaptive` path.

- [ ] **Step 3: Implement minimal path resolution**

Resolve an empty/default value as `_PROJECT_ROOT / "adaptive"`. Resolve a
relative configured value against `_PROJECT_ROOT`; preserve absolute values.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest -q test_adaptive_strategy.py -k "project_adaptive or env_factory"`

Expected: the new test and all environment-factory tests pass.

### Task 2: Configuration, Migration, and Documentation

**Files:**
- Modify: `.gitignore`
- Modify: `.env` (ignored local file only)
- Modify: `.env.example` (preserve its untracked status)
- Modify: `README.md`

**Interfaces:**
- Consumes the path contract from Task 1.
- Produces a local `adaptive/` runtime directory ignored by Git.

- [ ] **Step 1: Update configuration and documentation**

Replace `.codex_tmp/adaptive` with `adaptive`, add `/adaptive/` to `.gitignore`,
and document project-root resolution and safe migration.

- [ ] **Step 2: Move existing runtime state safely**

Resolve and verify both paths under `D:/arena-hero`, require the old directory
to exist and the destination not to exist, then use PowerShell `Move-Item` with
literal paths. Compare file counts before and after.

- [ ] **Step 3: Verify the release**

Run the full pytest suite, compile all source modules, run `pip check`, run
`git diff --check`, confirm `adaptive/` is ignored, and confirm no tracked
credential-shaped values are present.

