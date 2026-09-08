# Janitor — Autonomous Repository Caretaker & Living Context Engine

**Date:** 2026-09-07  
**Status:** Hardened & Approved Post-Adversarial Review (OpenCode GLM-5.3-Flash High)  
**Target Fleet:** Repositories under `/Volumes/2TB_SSD/GitHub/*` (including `maya`, `argus`, `janitor`, `baywatch`, and central `docs`)

---

## 1. Executive Summary & Vision

### 1.1 The Operational Reality
Software repositories operated by AI coding agents and human engineers inevitably accumulate operational entropy:
1. **Context Rot:** `CONTEXT.md`, `TODO.md`, and `LLM-OVERVIEW.md` fall out of sync with actual git commits within days, misleading future agent sessions.
2. **Accidentally Dirty Repositories:** Engineers and agents finish a session, get tired, and walk away. Uncommitted changes, scratch files, and cache litter leave working trees dirty for weeks. Traditional tools punish this by throwing errors or refusing to run, stalling automated workflows.
3. **Fragmented Scheduling & Context Bloat:** Previous iterations attempted passive session spying (hooking into Claude Code tool calls and dumping JSONL events), which failed due to path mismatches, fragile tool coupling, and unsolicited context injection that bloated token budgets.

### 1.2 The Solution: The "Butler" Model
Janitor is re-architected as an **opinionated, fault-tolerant repository caretaker**:
* **Scheduled, Evidence-Based Reconciliation:** Operates strictly on git commits, diffs, and live status probes—never passive keystroke hooks.
* **Master–Worker Topology:** Homelab is the Master control plane (owning systemd timers, telemetry, and Baywatch coordination); the Mac mini is the Worker execution node (housing the 2TB SSD with repositories and local Apple Silicon compute).
* **The "Butler" Auto-Tidy Engine:** Safely purges ephemeral cache files and checkpoints abandoned uncommitted work into clean `auto-wip/<date>` branches, restoring the working tree to the pre-existing local HEAD (never blowing away unpushed commits) and leaving actionable handoff notes for incoming agents in `CONTEXT.md`.
* **Zero-Token Fast Path & Self-Stimulation Prevention:** Repositories without external changes in the past 24 hours exit in ~10 milliseconds with 0 tokens. Janitor's own commits carry a distinct git trailer and are excluded from activity checks to prevent self-triggering infinite loops.
* **Sentinel-Preserved Documentation:** Janitor only modifies sentinel-bounded regions (`<!-- janitor:begin -->` ... `<!-- janitor:end -->`), preserving all custom human sections, notes, and architecture diagrams byte-for-byte.
* **Maya Kanban Alignment:** Standardizes `TODO.md` across all repos with stable task IDs and deterministic completion tracking, ingestible by Maya's central Kanban board.

---

## 2. System Architecture & Topology

```
┌────────────────────────────────────────────────────────┐
│               HOMELAB (Master Control Plane)           │
│                                                        │
│  - systemd timers:                                     │
│      • janitor-sweep.timer (Daily 03:00 AM)            │
│      • janitor-overview.timer (Sunday 04:00 AM)        │
│  - Environment & transport hardening:                  │
│      • ssh -o BatchMode=yes -o ConnectTimeout=5        │
│      • TimeoutStopSec=1800s                            │
│  - Ingests structured JSON telemetry & logs            │
│  - Coordinates with Baywatch & Homelab API             │
└───────────────────────────┬────────────────────────────┘
                            │
              SSH over LAN / Tailscale Backhaul
              (ssh macmini "caffeinate -is /usr/local/bin/janitor sweep --all --json")
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│            MAC MINI (Worker & Storage Node)            │
│                                                        │
│  - Storage: /Volumes/2TB_SSD/GitHub/*                  │
│  - Environment wrapper: /etc/janitor/env (pins PATH)   │
│  - State layer: ~/.local/state/janitor/state.json      │
│  - Model Gateway: Gateway2000 (g2k-bg via stdin)       │
│    (fallback: OpenRouter free tier via HTTPS)          │
│  - Local CLI: `janitor`                                │
│                                                        │
│  Components:                                           │
│    • git_ops.py    (Preflight guards, porcelain, atomic)│
│    • hygiene.py    (Cache purge, WIP checkpoint, GC)   │
│    • reconciler.py (Sentinel merge, sweep/overview)    │
│    • worker.py     (Gateway stdin, schema validation)  │
│    • cli.py        (CLI flags & structured JSON)       │
└────────────────────────────────────────────────────────┘
```

---

## 3. Core Capabilities & Workflows

### 3.1 Nightly Sweep (`janitor sweep [--all] [repos...]`)
* **Schedule:** Triggered nightly at 03:00 AM by Homelab.
* **Preflight Safety Gate (§5.1):** Immediately skips repos with locks or active surgery (rebase, merge, cherry-pick, detached HEAD).
* **Zero-Token Fast Path & Loop Prevention:**
  1. Inspects `git status --porcelain` and commits in the last 24h:
     ```bash
     git log --since=24.hours --invert-grep --grep="^Janitor-Run:" --oneline
     ```
  2. Commits generated by Janitor itself carry the git trailer `Janitor-Run: <run-id>` and are excluded from activity counting.
  3. If working tree is clean and 0 external commits occurred in 24 hours: skips immediately (<10ms runtime, 0 LLM tokens).
* **Sentinel-Preserved Reconciliation & First-Run Bootstrap:**
  1. Summarizes verified commits and diff stats (`HEAD~1..HEAD`).
  2. **First-Run Bootstrap:** If `CONTEXT.md` exists but lacks `<!-- janitor:begin -->` sentinels, Janitor preserves all pre-existing text at the top and appends the standard sentinel blocks below. If `CONTEXT.md` does not exist, it creates a clean template with sentinels.
  3. Updates `CONTEXT.md` strictly within `<!-- janitor:begin:focus -->` and `<!-- janitor:begin:recent -->` sentinels.
  4. Updates `TODO.md` (checking off items only with verified commit evidence or explicit human checks).
  5. Stages only `CONTEXT.md` and `TODO.md`, verifies `git diff --cached --name-only`, and commits:
     ```bash
     git commit -m "docs(janitor): sweep CONTEXT.md and TODO.md [skip ci]

     Janitor-Run: 20260907-0300"
     ```

### 3.2 Weekly Architecture Overview (`janitor overview [--all] [repos...]`)
* **Schedule:** Triggered weekly on Sunday at 04:00 AM by Homelab.
* **Inputs:** Target repo's `AGENTS.md` (canonical vocabulary & invariants), live status probe output (`scripts/*status.py` if present, with 15s timeout), recent commit log (last 20 commits), and current `LLM-OVERVIEW.md`.
* **Output:** Synthesizes a dense (120–180 line) `LLM-OVERVIEW.md` strictly enforcing required sections:
  1. `# LLM-OVERVIEW — <repo-name>`
  2. `## What this repo is`
  3. `## Machine & Host Ownership` (Mac mini vs Homelab vs Standby)
  4. `## What is actually built`
  5. `## Current live boundary & Ports`
  6. `## Canonical entry points`
* **Central Mirroring:** Mirrors overview to `/Volumes/2TB_SSD/GitHub/docs/repos/<repo-name>.md`. Mirror writes check for locks/dirty status in the central docs repo to avoid recursion loops.
* **Relative Symlink Maintenance:** Ensures `CLAUDE.md` is a relative symlink to `AGENTS.md` (`ln -sf AGENTS.md CLAUDE.md`) if `AGENTS.md` exists and `CLAUDE.md` is not an independent file.

### 3.3 The "Butler" Auto-Tidy Engine (`janitor tidy [--all] [repos...]`)
Runs automatically before sweeps or on-demand to resolve accidental repo dirtiness:

#### A. Ephemeral Trash Purge
* Safely removes non-source clutter:
  - OS clutter: `.DS_Store`, `Thumbs.db`
  - Python caches: `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`
  - Scratch/swap files: `*.swp`, `*~`, `*.tmp`
* Ensures missing ignore rules are appended to `.gitignore`.
* **Zero-Blast Rule:** Never executes `git clean -fdx` (which would destroy local `.env`, venvs, or untracked credentials).

#### B. Abandoned WIP Checkpointing (Zero-Data-Loss Invariant)
When uncommitted modifications exist and have had no write activity for >6 hours:
1. **Liveness Check:** Verifies no active process has open file handles on the repository (`lsof +D <repo>` or active IDE/agent session markers). If processes are active, the repo is marked `active_session` and skipped.
2. **Exact Checkpoint Sequence:**
   ```bash
   BRANCH=$(git symbolic-ref --short HEAD)       # e.g., main
   BASE_SHA=$(git rev-parse HEAD)                # pre-existing local commit
   WIP_BRANCH="auto-wip/$(date +%Y%m%d-%H%M)"

   # 1. Create and switch to isolated checkpoint branch
   git checkout -b "$WIP_BRANCH"

   # 2. Stage non-secret changes (strict secret denylist)
   git add -A -- ':!.env*' ':!*.pem' ':!id_rsa*' ':!*.key' ':!*credential*'

   # 3. Commit checkpoint
   git commit -m "wip(janitor): auto-checkpoint uncommitted work left on $(date)

   Janitor-Run: $(date +%Y%m%d-%H%M)"
   WIP_SHA=$(git rev-parse HEAD)

   # 4. Switch back to original branch
   git checkout "$BRANCH"

   # 5. Restore working tree to PRE-EXISTING LOCAL HEAD (NEVER origin/main)
   git reset --hard "$BASE_SHA"
   ```
   * **Why this is bulletproof:** `BASE_SHA` includes all pre-existing unpushed human commits. The only state discarded is the dirty working tree that is now safely committed at `WIP_SHA`. Auto-wip branches are strictly local and **never pushed**.
3. **Agent Handoff Instructions in `CONTEXT.md`:**
   Janitor updates the sentinel block in `CONTEXT.md`:
   ```markdown
   <!-- janitor:begin:handoff -->
   ## Janitor Handoff & Recent Interventions
   - **2026-09-08 03:00:** Auto-checkpointed uncommitted changes on `main` to branch `auto-wip/20260908-0300` (WIP SHA: `a1b2c3d`). Restored `main` to clean state at `e0c295c`.
     - *Instructions for Next Agent:* A previous session ended mid-work. Run `git diff main..auto-wip/20260908-0300` to inspect. Cherry-pick or merge if resuming, or delete branch if superseded.
   <!-- janitor:end:handoff -->
   ```
4. **Auto-WIP Branch Retention & GC:**
   - Janitor tracks created auto-wip branches in `~/.local/state/janitor/state.json`.
   - Branches older than 30 days are automatically archived to `~/.local/state/janitor/bundles/<repo>-wip-archive.bundle` and pruned from the local git repository to prevent branch sprawl.

---

## 4. Maya Kanban & `TODO.md` Contract

To prevent formatting churn and Kanban card duplication:
1. **Schema Versioning & Sentinel Boundaries:**
   ```markdown
   <!-- janitor:begin:todo schema:1 -->
   # TODO — <repo-name>
   > Maintained by Janitor. Synced with verified git evidence.

   ## Active / In Progress
   - [ ] Task description <!-- id: tk_8f1a priority: high -->
     - Supporting details (notes at depth 1 are not cards)

   ## Backlog / Planned
   - [ ] Future refactor <!-- id: tk_9c2b priority: medium -->

   ## Completed
   - [x] Implement doc-sweep CLI <!-- id: tk_1a3d completed: 2026-09-08 sha: 40c2522 -->
   <!-- janitor:end:todo -->
   ```
2. **Stable ID Lifecycle:**
   - IDs (`tk_<hash>`) are minted once when a task first appears in `TODO.md` and stored in `~/.local/state/janitor/state.json`.
   - Formatting changes or reordering never alters an existing task ID.
3. **Deterministic Completion Rule:**
   - Janitor only marks a task `[x]` if:
     a) A commit message contains `task:tk_<id>` or `fixes #<id>`, OR
     b) A human manually toggled `[x]`.
   - If an LLM infers completion from diffs without explicit trailer evidence, it marks the task `[?] Proposed Done: awaiting confirmation`, never unilaterally closing cards on Maya's board.

---

## 5. Preflight Guards, Transport & Security

### 5.1 Git Preflight Guards
Before any repository operation, Janitor checks for active git operations:
```python
def check_preflight_guards(repo_dir: Path) -> Optional[str]:
    dot_git = repo_dir / ".git"
    if (dot_git / "index.lock").exists():
        return "git_index_locked"
    if (dot_git / "MERGE_HEAD").exists() or (dot_git / "CHERRY_PICK_HEAD").exists():
        return "merge_in_progress"
    if (dot_git / "rebase-merge").exists() or (dot_git / "rebase-apply").exists():
        return "rebase_in_progress"
    if (dot_git / "BISECT_LOG").exists():
        return "bisect_in_progress"
    if not is_symbolic_ref_head(repo_dir):
        return "detached_head"
    return None
```
If any guard triggers, Janitor logs a structured warning and leaves the repository untouched.

### 5.2 SSH Transport & Execution Hardening
* **Non-Interactive Shell Environment:**
  - Mac mini environment wrapper `/usr/local/bin/janitor-runner` explicitly sources `/etc/janitor/env` (mode 0600) pinning `PATH=/opt/homebrew/bin:/Users/macmini/.local/bin:/usr/bin:/bin` and `OPENCODE_GO_API_KEY`.
* **Homelab SSH Command Specification:**
  ```bash
  ssh -o BatchMode=yes \
      -o ConnectTimeout=10 \
      -o ServerAliveInterval=15 \
      -o ServerAliveCountMax=3 \
      macmini "caffeinate -is /usr/local/bin/janitor sweep --all --json"
  ```
* **Exit Code & Output Contract:**
  * `stdout`: Pure JSON telemetry (`{"schema_version": 1, "repos": [...], "status": "ok"}`).
  * `stderr`: Diagnostic and operational logs.
  * Exit codes: `0` = All repos succeeded or fast-pathed; `1` = Partial repo failure (details in JSON); `2` = Transport/environment fatal error.

### 5.3 Model Gateway Hardening (Gateway2000 & Fallback)
* **ARG_MAX Defense (Stdin Transport):**
  Prompts are streamed directly over `stdin` to `g2k-bg -p -` rather than passed as a command-line argument, eliminating OS `E2BIG` argument limits on large diffs.
* **Payload Budgeting:**
  Hard prompt limit of 32KB. Diffs exceeding 16KB automatically fall back to `git diff --stat` plus commit log messages.
* **Deterministic JSON Extraction & Schema Validation:**
  Responses are stripped of markdown fences, parsed with `json.loads`, and validated against a JSON Schema. One repair retry is permitted; on persistent failure, the operation returns `synthesis_failed` without touching disk.
* **Per-Call Timeout & Circuit Breaker:**
  - Gateway calls have a strict 180s timeout.
  - Circuit Breaker: If 3 consecutive repositories fail model inference, Janitor trips the circuit breaker, logs `model_gateway_offline`, and skips model calls for remaining repos.

---

## 6. Codebase Decomposition & Implementation

### 6.1 Legacy Purge
* Remove: `janitor/recorder.py`, `janitor/jobs.py`, `hooks/`, `scripts/cron.sh`, `setup.sh`.
* Close GitHub Issue #2 as resolved/superseded by caretaker architecture.

### 6.2 Target Directory Structure
```
janitor/
├── pyproject.toml              # Console script entry point: `janitor = janitor.cli:main`
├── README.md                   # Architecture and CLI operations
├── homelab.yaml                # Homelab service contract
├── janitor/
│   ├── __init__.py             # Package version
│   ├── cli.py                  # CLI commands: sweep, overview, tidy, status
│   ├── git_ops.py              # Preflight guards, porcelain parsing, safe commit
│   ├── hygiene.py              # Cache purge, WIP checkpointing, branch GC
│   ├── reconciler.py           # Sentinel-based doc updating & hash gating
│   ├── state.py                # Persistent state manager (~/.local/state/janitor)
│   └── worker.py               # Gateway2000 stdin client & OpenRouter fallback
├── tests/
│   ├── test_git_ops.py         # Preflight guards, stage verification, dirty checks
│   ├── test_hygiene.py         # Trash purge & WIP checkpoint/restore sequence
│   ├── test_reconciler.py      # Sentinel parsing, hash gating, fast-path check
│   ├── test_state.py           # Task ID stability & run telemetry tracking
│   └── test_worker.py          # Stdin payload streaming & mock fallback
└── systemd/                    # Deployed to Homelab Master
    ├── janitor-sweep.service
    ├── janitor-sweep.timer
    ├── janitor-overview.service
    └── janitor-overview.timer
```

---

## 7. Acceptance Criteria & Verification

1. **Self-Stimulation Immunity Test:**
   * Run `janitor sweep` on a test repository.
   * Immediately run `janitor sweep` a second time.
   * Verify: Second run finishes in <20ms, emits `status: quiet`, and executes 0 model calls.
2. **Zero-Data-Loss WIP Checkpoint Test:**
   * Create unpushed commits on `main`.
   * Make uncommitted edits to 2 tracked files and create 1 untracked file.
   * Execute `janitor tidy`.
   * Verify: `main` is clean at its original unpushed commit SHA; dirty files are preserved on `auto-wip/<date>`; `CONTEXT.md` contains the handoff note; zero files are deleted.
3. **Adversarial Preflight Tests:**
   * Create `.git/index.lock`, `MERGE_HEAD`, and detached HEAD states across test repos.
   * Run `janitor sweep`.
   * Verify: Repos are cleanly skipped with structured log reasons; no git errors or crashes.
4. **ARG_MAX Resilience Test:**
   * Generate a 500KB synthetic diff in a repository.
   * Verify: `worker.py` streams payload via stdin or drops to diffstat without `OSError: [Errno 7]`.
5. **Full Unit Test Suite:**
   * `python3 -m unittest discover -s tests` runs 100% offline with zero external network or API dependencies.

---

## 8. Staged Rollout Plan

To ensure Janitor runs completely unattended for weeks without surprises:
1. **Phase 1: Implementation & Offline Tests:** Build `git_ops.py`, `hygiene.py`, `reconciler.py`, `worker.py`, `state.py`, and `cli.py` with 100% offline unit tests.
2. **Phase 2: Local Dogfooding on `janitor` Repo:** Run `janitor sweep` and `janitor tidy` on this repository. Verify clean commits, draft handling, and zero-token second run.
3. **Phase 3: Targeted Verification on `maya`:** Run `janitor sweep` and `janitor overview` on `maya`. Verify `scripts/maya-status.py` probe execution, sentinel bootstrapping, and central docs mirroring.
4. **Phase 4: Fleet Dry-Run:** Run `janitor sweep --all --dry-run` across all 30 repos on `/Volumes/2TB_SSD/GitHub/*`. Verify quiet repos exit in <20ms and active repos generate expected diffs.
5. **Phase 5: Homelab Master Deployment:** Install `systemd` timers on Homelab with SSH transport wrapper and start the unattended schedule.
