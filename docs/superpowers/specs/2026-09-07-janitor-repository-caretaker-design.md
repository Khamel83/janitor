# Janitor — Autonomous Repository Caretaker & Living Context Engine

**Date:** 2026-09-07  
**Status:** Approved by User  
**Target Repositories:** All repositories under `/Volumes/2TB_SSD/GitHub/*` (including `maya`, `argus`, `janitor`, `baywatch`, and central `docs`)

---

## 1. Executive Summary & Vision

### 1.1 The Problem
Software repositories operated by AI coding agents and human engineers inevitably accumulate entropy:
1. **Context Rot:** `CONTEXT.md`, `TODO.md`, and `LLM-OVERVIEW.md` fall out of sync with actual git commits within days, misleading future agent sessions.
2. **Accidentally Dirty Repositories:** Engineers and agents finish a session, get tired, and walk away. Uncommitted changes, scratch files, and cache litter leave working trees dirty for weeks. Traditional tools punish this by throwing errors or refusing to run, stalling automated workflows.
3. **Fragmented Scheduling & Context Bloat:** Previous iterations attempted passive session spying (hooking into Claude Code tool calls and dumping JSONL events), which failed due to path mismatches, fragile tool coupling, and unsolicited context injection that bloated token budgets.

### 1.2 The Solution
Janitor is re-architected as an **opinionated repository caretaker ("The Butler")**:
* **Scheduled, Evidence-Based Reconciliation:** Operates on git commits, diffs, and live status probes—never passive keystroke hooks.
* **Master–Worker Topology:** Homelab is the Master control plane (owning systemd timers, telemetry, and Baywatch coordination); the Mac mini is the Worker execution node (housing the 2TB SSD with repositories and local Apple Silicon compute).
* **The "Butler" Auto-Tidy Engine:** Safely purges ephemeral cache files and checkpoints abandoned uncommitted work into clean `auto-wip/<date>` branches, restoring `main` to a clean state and leaving actionable handoff notes for incoming agents in `CONTEXT.md`.
* **Zero-Token Fast Path:** Repositories without changes in the past 24 hours exit in ~10 milliseconds with 0 tokens and 0 API calls.
* **Maya Kanban Alignment:** Standardizes `TODO.md` across all repos into a machine-readable schema ingestible by Maya's central Kanban board.

---

## 2. System Architecture & Topology

```
┌────────────────────────────────────────────────────────┐
│               HOMELAB (Master Control Plane)           │
│                                                        │
│  - systemd timers:                                     │
│      • janitor-sweep.timer (Daily 03:00 AM)            │
│      • janitor-overview.timer (Sunday 04:00 AM)        │
│  - Central log aggregation & health monitoring         │
│  - Coordinates with Baywatch & Homelab API             │
└───────────────────────────┬────────────────────────────┘
                            │
              SSH over LAN / Tailscale Backhaul
              (ssh macmini "janitor sweep --all --json")
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│            MAC MINI (Worker & Storage Node)            │
│                                                        │
│  - Storage: /Volumes/2TB_SSD/GitHub/*                  │
│  - Model Gateway: Gateway2000 (g2k-bg / g2k)           │
│    (fallback: OpenRouter free tier via HTTPS)          │
│  - Local CLI: `janitor`                                │
│                                                        │
│  Components:                                           │
│    • git_ops.py       (Git status, porcelain, atomic)  │
│    • hygiene.py       (Cache purge, WIP checkpointing) │
│    • reconciler.py    (Sweep & Overview synthesis)     │
│    • worker.py        (Model gateway interface)        │
│    • cli.py           (Argparse & JSON output)         │
└────────────────────────────────────────────────────────┘
```

---

## 3. Core Capabilities & Workflows

### 3.1 Nightly Sweep (`janitor sweep [--all] [repos...]`)
* **Schedule:** Triggered nightly at 03:00 AM by Homelab.
* **Zero-Token Fast Path:** For each repository:
  1. Inspects `git status --porcelain` and `git log --since=24.hours --oneline`.
  2. If the working tree is clean and 0 commits occurred in the last 24 hours: skips immediately (<10ms runtime, 0 LLM tokens).
* **Reconciliation:** If commits or status diffs exist:
  1. Summarizes commits and diff stats (`HEAD~1..HEAD`).
  2. Generates updated `CONTEXT.md` (Active Focus, Recent Accomplishments, Watch Items).
  3. Reconciles `TODO.md` (retains pending items, marks completed tasks based on verified commits).
  4. If working tree is clean on `main`: stages only `CONTEXT.md` and `TODO.md`, verifies staged changes via `git diff --cached --name-only`, and commits with `docs(janitor): sweep CONTEXT.md and TODO.md [skip ci]`.

### 3.2 Weekly Architecture Overview (`janitor overview [--all] [repos...]`)
* **Schedule:** Triggered weekly on Sunday at 04:00 AM by Homelab.
* **Inputs:** Target repo's `AGENTS.md` (canonical vocabulary & invariants), live status probe output (`scripts/*status.py` if present, e.g. `maya-status.py`), recent commit log (last 20 commits), and current `LLM-OVERVIEW.md`.
* **Output:** Re-synthesizes a dense (120–180 line) `LLM-OVERVIEW.md` adhering to mandatory sections:
  1. `# LLM-OVERVIEW — <repo-name>`
  2. `## What this repo is`
  3. `## Machine & Host Ownership` (Mac mini vs Homelab vs Standby)
  4. `## What is actually built`
  5. `## Current live boundary & Ports`
  6. `## Canonical entry points`
* **Central Mirroring:** Automatically mirrors the overview to `/Volumes/2TB_SSD/GitHub/docs/repos/<repo-name>.md` to maintain a single-pane architectural map in the central docs repository.
* **Symlink Maintenance:** Ensures `CLAUDE.md` cleanly symlinks to `AGENTS.md` if `AGENTS.md` exists and `CLAUDE.md` is not an independent file.

### 3.3 The "Butler" Auto-Tidy Engine (`janitor tidy [--all] [repos...]`)
Runs automatically during sweeps or on-demand to resolve accidental repo dirtiness:

#### A. Ephemeral Trash Purge
* Scans for and removes non-source clutter:
  - OS clutter: `.DS_Store`, `Thumbs.db`
  - Python caches: `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`
  - Scratch/swap files: `*.swp`, `*~`, `*.tmp`
* Automatically ensures standard ignore patterns are added to `.gitignore` if missing.

#### B. Stale Abandoned WIP Checkpointing
When uncommitted modifications exist and have had no write activity for >6 hours (indicating a developer/agent session was abandoned):
1. **Safety Checkpoint:**
   - If on `main` / `master`: Creates an isolated branch `auto-wip/YYYYMMDD-HHMM` and commits all uncommitted changes with a descriptive commit message generated from the diff.
   - If on a feature/topic branch: Commits the changes directly as `wip: auto-checkpoint uncommitted work from YYYY-MM-DD`.
2. **Restore Clean State:**
   - Resets `main` to `origin/main` (or latest clean commit), leaving the working tree 100% clean for the next agent.
3. **Agent Handoff Instructions:**
   - Writes an explicit entry in `CONTEXT.md` under `## Janitor Handoff & Recent Interventions`:
     ```markdown
     ## Janitor Handoff & Recent Interventions
     - **2026-09-08 03:00:** Auto-checkpointed 3 uncommitted files on `main` to branch `auto-wip/20260908-0300` (edits to `worker.py`, `cli.py`). Restored `main` to clean state.
       - *Context for Next Agent:* Work was paused mid-session. Inspect `auto-wip/20260908-0300` if resuming this feature, or delete branch if abandoned.
     ```

---

## 4. Maya Kanban & `TODO.md` Standard Contract

All repositories maintained by Janitor enforce a machine-parseable `TODO.md` format designed for direct ingestion by Maya's central task tracking and Kanban board:

```markdown
# TODO — <repo-name>
> Maintained by Janitor. Synced with git evidence.

## Active / In Progress
- [ ] Task description <!-- id: <short-id> priority: high -->
  - Supporting context or technical criteria

## Backlog / Planned
- [ ] Future improvement or planned refactor <!-- id: <short-id> priority: medium -->

## Completed
- [x] Implemented doc-sweep CLI <!-- completed: 2026-09-08 sha: 40c2522 -->
```

Maya parses these markdown checklists directly from disk without needing API sync layers.

---

## 5. Security, Guardrails & Error Handling

1. **TOCTOU & Staged File Isolation:**
   - Auto-commits only occur when `git diff --cached --name-only` confirms that *strictly* the authorized markdown files are staged.
   - If any other staged changes appear, Janitor immediately executes `git reset` and aborts the commit.
2. **Prompt Injection Boundary:**
   - All external repository data (commit messages, diffs, untracked filenames, status probes) is enclosed between strict delimiter tokens:
     ```
     >>> REPO CONTENT
     ...
     <<< END REPO CONTENT
     ```
   - System prompts explicitly direct the model to treat all text within these markers as untrusted data, ignoring any embedded instructions or role redefinitions.
3. **Dual Model Backend & Graceful Degradation:**
   - Primary: `g2k-bg` (Gateway2000 CLI on Mac mini) adhering to `-p` flag conventions.
   - Fallback: Direct HTTP POST to OpenRouter free models when Gateway2000 CLI is absent (e.g. CI or non-gateway environments).
   - If model generation fails, Janitor logs `synthesis_failed`, leaves existing files untouched, and continues processing remaining repositories.
4. **Fault Isolation Across the Fleet:**
   - Failure in repository $N$ (e.g., git lock file, disk error, probe timeout) is captured in JSON telemetry and does not prevent processing repositories $N+1 \dots M$.

---

## 6. Codebase Decomposition & Cleanup Plan

### 6.1 Legacy Files to Remove
* `janitor/recorder.py` (obsolete event-logging database)
* `janitor/jobs.py` (obsolete 12 background jobs)
* `hooks/record.sh`, `hooks/context.sh`, `hooks/session-end.sh` (obsolete Claude Code hooks)
* `scripts/cron.sh` (obsolete 15-minute cron runner)
* `setup.sh` (obsolete shell installer)
* Close GitHub Issue #2 as obsolete / superseded by caretaker architecture.

### 6.2 Target Directory Structure
```
janitor/
├── pyproject.toml              # Console script entry point: `janitor = janitor.cli:main`
├── README.md                   # Operator guide and architecture reference
├── homelab.yaml                # Homelab project contract
├── janitor/
│   ├── __init__.py             # Version and exports
│   ├── cli.py                  # CLI entrypoint (sweep, overview, tidy, status)
│   ├── reconciler.py           # Core prompt generation & doc update logic
│   ├── git_ops.py              # Git discovery, status check, porcelain parsing, safe commit
│   ├── hygiene.py              # Cache cleanup, stale WIP auto-checkpointing, handoff logging
│   └── worker.py               # Gateway2000 (g2k-bg) & OpenRouter model client
├── tests/
│   ├── test_git_ops.py         # Unit tests for porcelain parsing and stage guards
│   ├── test_hygiene.py         # Unit tests for cache purging & WIP branch creation
│   ├── test_reconciler.py      # Unit tests for sweep and overview document synthesis
│   └── test_worker.py          # Unit tests for gateway execution and fallback
└── systemd/                    # Deployed to Homelab Master
    ├── janitor-sweep.service
    ├── janitor-sweep.timer
    ├── janitor-overview.service
    └── janitor-overview.timer
```

---

## 7. Operational Verification & Acceptance Criteria

1. **CLI Execution on Mac mini:**
   * `janitor sweep --dry-run` against `/Volumes/2TB_SSD/GitHub/janitor` succeeds without errors.
   * `janitor overview --dry-run` against `/Volumes/2TB_SSD/GitHub/maya` executes live status probe and produces valid markdown.
   * `janitor tidy --dry-run` correctly identifies junk files and stale WIP without unintended deletions.
2. **Fast-Path Verification:**
   * Running `janitor sweep` twice in succession on an unchanged repo completes the second run in <50ms with 0 tokens spent.
3. **WIP Safety Verification:**
   * Simulating an abandoned dirty working tree on `main` causes `janitor tidy` to create `auto-wip/<date>`, commit the changes, restore `main` to clean, and write handoff notes into `CONTEXT.md`.
4. **Remote Homelab Invocation:**
   * `ssh macmini "janitor sweep --all --json"` returns structured JSON summary of all fleet repositories.
5. **Full Test Suite:**
   * `python3 -m unittest discover -s tests` passes 100% without external API or network dependencies.
