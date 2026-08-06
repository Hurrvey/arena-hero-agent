# Arena Hero README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the placeholder README with a Chinese-first, copyable guide for installing, securely configuring, running, monitoring, testing, and contributing to the balanced Arena Hero tactic.

**Architecture:** Keep the documentation in one root `README.md`; organize it in dependency order so a new user can run the bot before reading the strategy details. Derive every command and behavior statement from the existing `balanced_tactic.py`, `requirements.txt`, tests, and repository state; do not change runtime code.

**Tech Stack:** Markdown, Python 3.11+, `arena-hero>=0.2.8,<0.3`, synchronous `ArenaHeroClient`, PowerShell, pytest, Git/GitHub CLI.

## Global Constraints

- Write the prose in Simplified Chinese; keep commands, filenames, Python identifiers, and SDK names in their original English form.
- Use the exact dependency command `python -m pip install -r requirements.txt`.
- Use the exact entry-point command `python .\balanced_tactic.py`.
- Explain that `ARENA_HERO_API_KEY` is read from the process environment or a hidden `getpass` prompt; never show or request a real secret.
- State that the tactic aims for balanced competitive play and cannot guarantee first place.
- Describe only current visible state, legal actions, and behavior covered by the implementation/tests.
- Do not modify `balanced_tactic.py`, `requirements.txt`, tests, or SDK behavior.
- Use relative repository links for local files and the existing Arena Hero/GitHub URLs for external destinations.

---

## File Map

- Modify: `README.md` — the complete user-facing project guide.
- Read for facts: `balanced_tactic.py`, `requirements.txt`, `test_balanced_tactic.py`, `.gitignore`.
- Read for design terminology: `docs/superpowers/specs/2026-08-06-balanced-arena-hero-tactic-design.md`.
- No new source or test files are required; the existing 22-test suite is the regression check for a documentation-only change.

## Task 1: Write the Chinese-first quick-start README

**Files:**

- Modify: `README.md`

**Interfaces:**

- Consumes: the `play()` entry point and `load_api_key()` behavior in `balanced_tactic.py`, the dependency pin in `requirements.txt`, and the verified test command in `test_balanced_tactic.py`.
- Produces: a reader-facing guide whose first executable path is `cd D:\arena-hero`, `python -m pip install -r requirements.txt`, and `python .\balanced_tactic.py`.

- [ ] **Step 1: Confirm source facts before drafting.**

  Run:

  ```powershell
  rg -n "def play|def load_api_key|ArenaHeroClient|ARENA_HERO_API_KEY|turn.submit|Arena Hero API key" balanced_tactic.py
  Get-Content requirements.txt
  rg -n "^def test_" test_balanced_tactic.py
  ```

  Expected facts: the script reads `ARENA_HERO_API_KEY` or prompts invisibly, creates a synchronous `ArenaHeroClient`, submits once per Turn, and the dependency is `arena-hero>=0.2.8,<0.3`.

- [ ] **Step 2: Replace the placeholder with the complete README sections.**

  Use these headings in this order:

  ```markdown
  # Arena Hero Agent
  ## 项目简介
  ## 主要特性
  ## 环境要求
  ## 安装
  ## 配置 API key
  ## 启动战术
  ## 观察、停止与手动操作
  ## 战术逻辑摘要
  ## 本地测试
  ## 上传到 GitHub
  ## 常见问题
  ## 安全说明
  ## 项目文件
  ## 许可证
  ```

  The content must include the following concrete details:

  - `balanced_tactic.py` is a standalone deterministic starter tactic, not a guarantee of rank.
  - Installation uses `cd D:\arena-hero` and `python -m pip install -r requirements.txt`.
  - The default run command is `python .\balanced_tactic.py`; the terminal must stay open, and `Ctrl+C` stops the loop.
  - The script first reads `ARENA_HERO_API_KEY`; when absent, it displays `Arena Hero API key:` and hides typed characters. Tell readers not to paste a key into chat or commit it.
  - The monitoring URL is `https://app.arenahero.io/arena`; readers should use the same account as the key. Explain that a manual action for the same Tick can override the Agent action.
  - Combat policy: Rangers shoot only visible, aligned, in-range, unobstructed hostile cells; Vanguards sweep adjacent visible hostiles and prioritize a Core cell.
  - Economy policy: Workers harvest only currently visible resources, deposit at a stationary Core, avoid visible obstacles/enemies, retreat when threatened, and use deterministic UUID ordering for contention.
  - Recovery and production policy: damaged Units heal at a stationary Core when the local budget permits; the Core heals/repairs before conservative production; production reserves resources for upkeep and does not start Core migration.
  - Beacon policy: pick up a Beacon only when its visible status is `GROUND` and a controlled object is already on its cell; do not chase an unseen Beacon.
  - Testing uses `python -m pytest -q`; also list `python -m pip check` and `git diff --check` as optional verification.
  - GitHub instructions target `https://github.com/Hurrvey/arena-hero-agent` and distinguish the first push from later `git add`, `git commit`, and `git push` updates. Include the safe merge command for a remote initialized with its own README: `git pull --no-rebase --no-edit --allow-unrelated-histories origin main`.
  - Link local files with relative links: `balanced_tactic.py`, `requirements.txt`, `test_balanced_tactic.py`, and `LICENSE`.

- [ ] **Step 3: Review the rendered Markdown structure.**

  Run:

  ```powershell
  rg -n "^#|^##|ARENA_HERO_API_KEY|balanced_tactic.py|pytest|arena-hero-agent" README.md
  git diff -- README.md
  ```

  Expected result: headings are in the dependency order above, all copyable commands use PowerShell syntax, and no real credential or unresolved placeholder appears.

## Task 2: Verify and commit the documentation

**Files:**

- Test: existing `test_balanced_tactic.py` (regression only; no test source changes).
- Modify: `README.md` if verification finds a factual or formatting issue.

**Interfaces:**

- Consumes: the completed README and current Python environment.
- Produces: a clean, committed documentation change with the existing tactic tests still passing.

- [ ] **Step 1: Run the existing test suite.**

  Run:

  ```powershell
  python -m pytest -q
  ```

  Expected: all existing tests pass (the current baseline is 22 passing tests).

- [ ] **Step 2: Verify dependency metadata and whitespace.**

  Run:

  ```powershell
  python -m pip check
  git diff --check
  ```

  Expected: `No broken requirements found.` and no `git diff --check` output.

- [ ] **Step 3: Check the final worktree and commit README.**

  Run:

  ```powershell
  git status --short
  git diff --stat
  git add README.md
  git -c commit.gpgsign=false commit -m "docs: improve Arena Hero README"
  git status --short --branch
  ```

  Expected: only the intended README commit is created; the working tree is clean. The commit can then be uploaded with `git push` when the user is ready.
