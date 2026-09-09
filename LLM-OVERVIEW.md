# LLM-OVERVIEW — janitor
> Current compressed briefing. Updated 2026-09-08. Agent behavior is defined in `AGENTS.md`. This derived file is not an independent authority.

## What this repo is
Janitor is an autonomous repository caretaker and living-documentation reconciler providing automated maintenance across the Homelab fleet. It monitors git repositories across single targets or fleet workspaces, purges ephemeral build/editor cache trash, checkpoints abandoned work-in-progress (WIP) branches without secret leakage, and auto-synthesizes living documentation files (`CONTEXT.md`, `TODO.md`, `LLM-OVERVIEW.md`) using Gateway2000 (`g2k-bg`) with fallback to free models ($0).

The system operates under strict safety invariants: preflight git guards skip repos undergoing interactive operations (merge, rebase, bisect, cherry-pick) or holding index locks; atomic stage/commit operations abort if extraneous files are staged; commit trailers (`Janitor-Run: <run_id>`) prevent self-triggering feedback loops; secret exclusions block `.env*` and key files; and SHA-256 input hashing skips unnecessary LLM calls when repository state is unchanged.

## Machine & Host Ownership
- **Homelab Linux Host (`ssh homelab`, Ubuntu 24.04)**:
  - Master control plane and primary scheduler.
  - Hosts user systemd timers: `janitor-sweep.timer` (runs nightly at 03:00 AM America/Los_Angeles) and `janitor-overview.timer` (runs weekly on Sunday at 04:00 AM America/Los_Angeles).
  - Triggers the Mac mini execution worker remotely over private LAN/Tailscale SSH with `BatchMode=yes` and timeout bounds.
- **Mac Mini Host (`macmini`, macOS Darwin arm64)**:
  - Worker execution node and primary repository storage host.
  - Workspace: `/Volumes/2TB_SSD/GitHub/*` (80 git repositories).
  - Central docs hub: `/Volumes/2TB_SSD/GitHub/docs/repos/` (mirrored fleet overviews).
  - Local CLI: `janitor` (`/opt/homebrew/bin/janitor`) and runner wrapper `scripts/janitor-runner.sh` (`/Users/macmini/.local/bin/janitor-runner`).
  - Model gateway: Gateway2000 (`/Users/macmini/.local/bin/g2k-bg` streaming via stdin `-p -`).

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
- **`janitor.reconciler` (Living Document Engine)**:
  - Idempotent sentinel block merging (`merge_sentinel_block`): updates tagged regions (`recent` tag in `CONTEXT.md`, `todo` tag in `TODO.md`).
  - First-run bootstrap: automatically appends sentinel blocks to existing human-authored docs without clobbering text.
  - Integrated Butler auto-tidy pre-pass in `sweep_repo`: automatically purges cache droppings and checkpoints stale abandoned work (>6h) before running the sweep.
  - Content hash gating (`_sweep_input_hash`): SHA-256 hashing over porcelain status, recent log, and diff to bypass unchanged repos.
  - Symlink management (`ensure_claude_symlink`): creates relative symlink `CLAUDE.md -> AGENTS.md` if `AGENTS.md` is present.
  - Overview generator (`overview_repo`): synthesizes `LLM-OVERVIEW.md` from code inspection, directory trees, configs, `AGENTS.md`, and git history. Capped at 32,000 total prompt characters. Overview never auto-commits.
  - Central docs mirror (`_mirror_overview`): mirrors `LLM-OVERVIEW.md` to `/Volumes/2TB_SSD/GitHub/docs/repos/<repo>.md`.
- **`janitor.hygiene` (Butler Tidy & WIP Checkpointing)**:
  - Ephemeral trash purge (`purge_ephemeral_trash`): cleans `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.DS_Store`, `Thumbs.db`, `.pyc`, `.swp`.
  - Secret path guarding: pathspec pattern exclusions (`.env*`, `id_rsa*`, `*.pem`, `*.key`, `*credential*`) prevent secret staging across directory depths.
  - WIP staleness check (`is_wip_stale`) & zero-data-loss checkpointing (`checkpoint_abandoned_wip`): commits uncommitted work older than 6 hours to `auto-wip/<timestamp>` branches and resets working tree to pre-existing local HEAD (never origin/main).
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
  - Systemd timer and service units (`systemd/janitor-sweep.timer`, `systemd/janitor-sweep.service`, `systemd/janitor-overview.timer`, `systemd/janitor-overview.service`).
  - Execution runner wrapper (`scripts/janitor-runner.sh`).

## Canonical entry points
- `janitor status --all`: Instant health check across all 80 fleet repositories.
- `janitor sweep [--all]`: Reconcile living documentation (`CONTEXT.md`, `TODO.md`) with auto-tidy pre-pass.
- `janitor tidy [--all]`: Purge cache droppings and checkpoint abandoned WIP to `auto-wip/` branches.
- `janitor overview [--all]`: Synthesize deep architectural map and mirror to `/Volumes/2TB_SSD/GitHub/docs/repos/`.
- `python3 -m unittest discover -s tests -v`: Full offline unit test suite (90 tests).
- `systemctl --user list-timers | grep janitor`: Inspect active Homelab timers (on `ssh homelab`).
