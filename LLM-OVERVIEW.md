# LLM-OVERVIEW — janitor
> Current compressed briefing. Updated 2026-09-13. Existing caretaker accepted; overnight documentation PRs and a layered morning packet are the new authorized scope. Read TODO.md for completion status and HANDOFF.md for deployment evidence. This derived file is not an independent authority.

## What this repo is
Janitor is an autonomous repository caretaker and living-documentation reconciler providing automated maintenance across the Homelab fleet. It monitors git repositories across single targets or fleet workspaces, purges ephemeral build/editor cache trash, checkpoints abandoned work-in-progress (WIP) branches without secret leakage, reviews branch and linked-worktree sprawl, and auto-synthesizes living documentation files (`CONTEXT.md`, `TODO.md`, `LLM-OVERVIEW.md`) using the sourced Gateway2000 `g2k` auto function, with the OpenRouter free-model fallback used only when no Gateway2000 auto helper is available.

The system operates under strict safety invariants: preflight git guards skip repos undergoing interactive operations (merge, rebase, bisect, cherry-pick) or holding index locks; atomic stage/commit operations abort if extraneous files are staged; commit trailers (`Janitor-Run: <run_id>`) prevent self-triggering feedback loops; secret exclusions block `.env*` and key files; branch review never checks out, merges, deletes, resets, or pushes; and SHA-256 input hashing skips unnecessary LLM calls when repository state is unchanged.

## Machine & Host Ownership
- **Homelab Linux Host (`ssh homelab`, Ubuntu 24.04)**:
  - Master control plane and primary scheduler.
  - Hosts user systemd timers: `janitor-sweep.timer` (runs nightly at 03:00 AM America/Los_Angeles) and `janitor-overview.timer` (runs weekly on Sunday at 04:00 AM America/Los_Angeles).
  - Triggers the Mac mini execution worker remotely over private LAN/Tailscale SSH with `BatchMode=yes` and timeout bounds.
- **Mac Mini Host (`macmini`, macOS Darwin arm64)**:
  - Worker execution node and primary repository storage host.
  - Workspace: `/Volumes/2TB_SSD/GitHub/*` (81 targets discovered at 2026-09-12 acceptance; discover live rather than assuming a fixed count).
  - Central docs hub: `/Volumes/2TB_SSD/GitHub/docs/repos/` (mirrored fleet overviews).
  - Local CLI: `janitor` (`/opt/homebrew/bin/janitor`) and runner wrapper `scripts/janitor-runner.sh` (`/Users/macmini/.local/bin/janitor-runner`).
  - Model gateway: sourced Gateway2000 `g2k` auto function (streaming via stdin `-p -`); OpenRouter free models are used only when no Gateway2000 auto helper is available.

## What is actually built
- **`janitor.cli` (CLI & Fleet Discovery)**:
  - Command line parser and runner supporting subcommands: `sweep`, `branches`, `overview`, `tidy`, and `status`.
  - Discovers child repositories directly under `JANITOR_WORKSPACE` (defaults to `/Volumes/2TB_SSD/GitHub`).
  - Supports `--all` (fleet mode), explicit repo paths, `--dry-run` (prints merged/synthesized previews without writing or committing), `--no-fetch` (use cached remote-tracking refs), and `--json` (structured output).
  - Records sanitized per-target receipts incrementally in `runs.jsonl` under the state directory. Mutating runs share a local lock. One failure does not abort the fleet; three failed syntheses without an intervening successful synthesis stop new work. Quiet targets do not reset the failure streak. Exits 1 for failed/deferred targets.
  - Stops starting targets after 45 minutes for sweeps or three hours for overviews; `JANITOR_RUN_TIMEOUT` overrides seconds. SIGTERM unwinds the active owned model process group.
- **`janitor.branch_review` (Deterministic branch/worktree review)**:
  - `collect_branch_report` performs one bounded primary-remote refresh unless fetch is disabled, then inventories local refs, cached remote-tracking refs, and linked worktrees without checking anything out.
  - Groups matching local and remote refs into logical branches, compares each usable tip with the discovered default branch, and classifies branches as active, aging, stale, or abandoned `auto-wip`.
  - Records bounded evidence: ahead/behind/merged state, recent subjects, changed paths, branch-local `CONTEXT.md`/`TODO.md` excerpts, worktree status, and attention flags. Failed inventory queries are marked incomplete instead of being treated as empty.
  - Renders a stable report-only Markdown block for `CONTEXT.md` and a complete JSON report for CLI and future Homelab consumers. A fetch may prune stale remote-tracking refs; local branches are never pruned.
- **`janitor.git_ops` (Safe Git Engine)**:
  - Preflight safety guards (`check_preflight_guards`): resolve linked-worktree and common Git metadata, then skip repos mid-operation (merge, rebase, bisect, cherry-pick), locked (`.git/index.lock`), or on detached HEADs. Git probes are timeout-bounded.
  - Atomic commit staging (`atomic_stage_and_commit`): stages only designated files, verifies index purity, appends `Janitor-Run: <run_id>` trailers.
  - Deterministic loop prevention (`has_24h_activity`): excludes janitor commits from activity windows; uses absolute ISO-8601 `%cI` timestamps for deterministic SHA-256 input hashing.
- **`janitor.reconciler` (Living Document Engine)**:
  - Idempotent sentinel block merging (`merge_sentinel_block`): updates tagged regions (`recent` tag in `CONTEXT.md`, `todo` tag in `TODO.md`).
  - Branch prepass: integrates the deterministic branch report into the `branches` sentinel block before normal quiet-path and model decisions, while masking that block from normal synthesis prompts.
  - Branch-only changes on a clean discovered default-branch checkout may commit only `CONTEXT.md`; dirty or non-default checkouts remain report-only. Generated branch evidence and Janitor commits are excluded from continuity inputs to prevent repeat commits.
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
  - WIP branch expiration helper (`prune_expired_wip_branches`) exists but is not called by the current CLI/sweep. Automatic local branch pruning is outside the completion scope.
- **`janitor.state` (Persistent State Layer)**:
  - `StateManager` rooted at `~/.local/state/janitor/state.json` (overridden via `JANITOR_STATE_DIR`).
  - Normalizes task text to stable `tk_<hash>` IDs.
  - Tracks per-repo input hashes (`last_hash`), execution history (`last_run`), WIP branch records (`wip_branches`), branch continuity, and 90-day missing-branch tombstones.
- **`janitor.worker` (Model Gateway Backend)**:
  - Model dispatching via `call_free` and `extract_structured`.
  - Streams prompts over stdin to sourced Gateway2000 auto. Disables coding tools, skills/rules, LSP, title generation, and session persistence; supplies a synthesis-only system prompt with low reasoning. The 180-second timeout kills the owned shell/client process group.
  - Falls back to the `openrouter/free` HTTP API only when no Gateway2000 auto helper is available (requires `OPENROUTER_API_KEY`) with 3 retry attempts.
  - Tracks request counts in repository `.janitor/usage.jsonl`, or a created state-directory `usage.jsonl` when the SSH worker starts outside a repository. The latter fixes the verified fleet logging failure.
- **System & Automation Infrastructure**:
  - Systemd timer and service units (`systemd/janitor-sweep.timer`, `systemd/janitor-sweep.service`, `systemd/janitor-overview.timer`, `systemd/janitor-overview.service`).
  - Execution runner wrapper (`scripts/janitor-runner.sh`).

## Canonical entry points
- `janitor publish [repo ...] [--all] [--dry-run] [--limit 20]`: Propose documentation-only PRs from published evidence; never publish local WIP or merge anything. Existing Janitor PRs remain intact for review.
- `janitor reviews [repo ...] [--all]`: Collect all open owned-repository PRs into private local morning artifacts. Original context, changes, findings, and current-head checks remain separate layers. No model call or GitHub mutation.
- `~/.local/state/janitor/morning/latest.md`: Latest packet for the user's final agent; use WORKER-PROMPT.md to assess cumulative changes and recommend MERGE / RE-CHECK. A bot pass is not merge approval.
- `janitor status --all`: Git status and last-run records across discovered fleet repositories; this is not downstream acceptance by itself.
- `janitor sweep [--all]`: Reconcile living documentation (`CONTEXT.md`, `TODO.md`) with auto-tidy pre-pass.
- `janitor branches [repo ...] [--all] [--json] [--no-fetch]`: Review local/remote-tracking branches and linked worktrees without branch actions.
- `janitor tidy [--all]`: Purge cache droppings and checkpoint abandoned WIP to `auto-wip/` branches.
- `janitor overview [--all]`: Synthesize deep architectural map and mirror to `/Volumes/2TB_SSD/GitHub/docs/repos/`.
- `PYTHONPATH=. pytest -q`: Full offline unit test suite (166 passed in the 2026-09-12 recovery session).
- `systemctl --user list-timers | grep janitor`: Inspect active Homelab timers (on `ssh homelab`).
