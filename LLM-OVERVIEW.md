# LLM-OVERVIEW — janitor
> Current compressed briefing. Updated 2026-09-08. Agent behavior is defined in `AGENTS.md`. This derived file is not an independent authority.

## What this repo is
Janitor is an autonomous repository caretaker and living-documentation reconciler providing background intelligence and automated maintenance for AI agent (Claude Code) developer sessions. It monitors git repositories across single targets or fleet workspaces, purges ephemeral build/editor cache trash, checkpoints abandoned work-in-progress (WIP) branches without secret leakage, and auto-synthesizes living documentation files (`CONTEXT.md`, `TODO.md`, `LLM-OVERVIEW.md`) using free LLM models ($0).

The system operates under strict safety invariants: preflight git guards skip repos undergoing interactive operations (merge, rebase, bisect, cherry-pick) or holding index locks; atomic stage/commit operations abort if extraneous files are staged; commit trailers (`Janitor-Run: <run_id>`) prevent self-triggering feedback loops; secret exclusions block `.env*` and key files; and SHA-256 input hashing skips unnecessary LLM calls when repository state is unchanged.

## Machine & Host Ownership
No multi-host information is available from the status probe output or AGENTS.md.

## What is actually built
- **`janitor.cli` (CLI & Fleet Discovery)**:
  - Command line parser and runner supporting subcommands: `sweep`, `overview`, `tidy`, and `status`.
  - Discovers child repositories directly under `JANITOR_WORKSPACE` (defaults to `/Volumes/2TB_SSD/GitHub`).
  - Supports `--all` (fleet mode), explicit repo paths, `--dry-run` (prints merged/synthesized previews without writing or committing), and `--json` (structured output).
  - Handles per-repo execution failures gracefully so single-repo errors do not abort fleet runs. Exits 1 on synthesis or execution errors.
- **`janitor.git_ops` (Safe Git Engine)**:
  - Preflight safety guards (`check_preflight_guards`): skips repos mid-operation (merge, rebase, bisect, cherry-pick), locked (`.git/index.lock`), or on detached HEADs.
  - Atomic commit staging (`atomic_stage_and_commit`): stages only designated files, verifies index purity, appends `Janitor-Run: <run_id>` trailers.
  - Deterministic loop prevention (`has_24h_activity`): excludes janitor commits from activity windows; uses absolute ISO-8601 `%cI` timestamps for deterministic SHA-256 input hashing.
- **`janitor.reconciler` (and `janitor.docs` compatibility alias)**:
  - Idempotent sentinel block merging (`merge_sentinel_block`): updates tagged regions (`recent` tag in `CONTEXT.md`, `todo` tag in `TODO.md`).
  - Content hash gating (`_sweep_input_hash`): SHA-256 hashing over porcelain status, recent log, and diff to bypass unchanged repos.
  - Symlink management (`ensure_claude_symlink`): creates relative symlink `CLAUDE.md -> AGENTS.md` if `AGENTS.md` is present.
  - Overview generator (`overview_repo`): synthesizes `LLM-OVERVIEW.md` from `AGENTS.md`, 24h git history, and optional status probe (`scripts/status.py` if `JANITOR_RUN_STATUS_PROBE=1`). Capped at 16,000 total prompt characters. Overview never auto-commits.
  - Central docs mirror (`_mirror_overview`): mirrors `LLM-OVERVIEW.md` to `$JANITOR_DOCS_MIRROR` or `/Volumes/2TB_SSD/GitHub/docs/repos/<repo>.md` when clean.
- **`janitor.hygiene` (Butler Tidy & WIP Checkpointing)**:
  - Ephemeral trash purge (`purge_ephemeral_trash`): cleans `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.DS_Store`, `Thumbs.db`, `.pyc`, `.swp`.
  - Secret path guarding (`_is_secret_path`): pathspec pattern exclusions (`.env*`, `id_rsa*`, `*.pem`, `*.key`, `*credential*`) prevent secret staging across directory depths.
  - WIP staleness check (`is_wip_stale`) & zero-data-loss checkpointing (`checkpoint_abandoned_wip`): commits uncommitted work older than 6 hours to `auto-wip/<timestamp>` branches and resets working tree.
  - WIP branch expiration (`prune_expired_wip_branches`): prunes local `auto-wip/*` branches older than 30 days.
- **`janitor.state` (Persistent State Layer)**:
  - `StateManager` rooted at `~/.local/state/janitor/state.json` (overridden via `JANITOR_STATE_DIR`).
  - Normalizes task text to stable `tk_<hash>` IDs.
  - Tracks per-repo input hashes (`last_hash`), execution history (`last_run`), and WIP branch records (`wip_branches`).
- **`janitor.worker` (Model Gateway Backend)**:
  - Model dispatching via `call_free` and `extract_structured`.
  - Streams prompts over stdin to local Gateway2000 CLI (`g2k-bg` or `g2k`) to prevent system `ARG_MAX` limits.
  - Fallback to `openrouter/free` HTTP API (requires `OPENROUTER_API_KEY`) with 3 retry attempts.
  - Enforces rate limits (1000/day, 20/min) logged in `.janitor/usage.jsonl`.
- **System & Automation Infrastructure**:
  - Systemd timer and service units (`systemd/janitor-overview.{timer,service}`, `systemd/janitor-sweep.{timer,service}`).
  - macOS launchd example configuration plists (`contrib/launchd/com.khamel83.janitor.{overview,sweep}.plist.example`).
  - Mac Mini SSH runner script (`scripts/janitor-runner.sh`).
  - Homelab service definition (`homelab.yaml`).

## Canonical entry points
- **CLI Commands**:
  - `janitor sweep [repos...] [--all] [--dry-run] [--json]`: Regenerates `CONTEXT.md` and `TODO.md` sentinel blocks from recent activity. Auto-commits only on clean main/master.
  - `janitor overview [repos...] [--all] [--dry-run] [--json]`: Synthesizes `LLM-OVERVIEW.md` and mirrors to central docs repo if clean. Never auto-commits.
  - `janitor tidy [repos...] [--all] [--json]`: Executes butler pass (purges cache trash and checkpoints stale WIP).
  - `janitor status [repos...] [--all] [--json]`: Displays git status and last recorded janitor run state.
- **Python Package Entry Points**:
  - `python3 -m janitor.cli <command> [options]`
  - Script executable: `janitor` (installed via `pip install -e .` from `pyproject.toml`).
- **Test Suite**:
  - `python3 -m pytest`: Runs test suite across `tests/test_*.py` (98 unit tests covering CLI, git ops, reconciler, hygiene, state, worker, docs).
- **Environment Controls**:
  - `JANITOR_WORKSPACE`: Root workspace directory for discovery (default: `/Volumes/2TB_SSD/GitHub`).
  - `JANITOR_STATE_DIR`: State JSON directory (default: `~/.local/state/janitor`).
  - `JANITOR_DOCS_MIRROR`: Central repository path for overview mirrors (default: `/Volumes/2TB_SSD/GitHub/docs`).
  - `JANITOR_RUN_STATUS_PROBE`: Set to `1` to run target repo's `scripts/status.py` probe during `overview`.
  - `OPENROUTER_API_KEY`: API key used when fallback to OpenRouter free tier is required.
