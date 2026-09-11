# Janitor

> Autonomous repository caretaker & living context reconciler for the Homelab fleet.

Janitor is an opinionated groundskeeper for your software repositories. It runs on a master–worker architecture between Homelab and your Mac mini, ensuring your repositories stay clean, documented, and reconciled against ground truth—without human friction or unsolicited prompt pollution.

---

## What It Does

### 1. The "Butler" Auto-Tidy Engine (`janitor tidy`)
When engineers or AI agents finish a session, walk away, and leave repos accidentally dirty:
- **Trash Purge:** Safely sweeps away ephemeral caches (`__pycache__`, `.pytest_cache`, `.mypy_cache`, `.DS_Store`, swap files).
- **Abandoned WIP Checkpoint:** If changes have been untouched for >6 hours:
  - Checkpoints all uncommitted work into a safe, local-only branch `auto-wip/<date>`.
  - **Zero Data Loss:** Restores `main` strictly to your pre-existing local commit (`git rev-parse HEAD`), **never `origin/main`** (unpushed commits are 100% preserved).
  - **Secret Denylist:** Strictly refuses to stage or commit `.env*`, `*.pem`, `*.key`, or credentials.
  - Leaves an actionable handoff note in `CONTEXT.md` under `## Janitor Handoff & Recent Interventions` so the next agent knows where work paused.

### 2. Living Context Reconciler (`janitor sweep`)
- Reconciles `CONTEXT.md` (Active Focus, Verified Accomplishments, Watch Items) and `TODO.md` from actual git commits and diffs over the last 24 hours.
- **Sentinel Preservation:** Writes strictly inside `<!-- janitor:begin -->` ... `<!-- janitor:end -->` blocks, preserving all custom human notes, diagrams, and instructions byte-for-byte.
- **First-Run Bootstrap:** Automatically initializes sentinel blocks in repositories that don't have them yet without breaking existing content.
- **Atomic Commit Gate:** Only auto-commits if on clean `main`/`master`, staging strictly `CONTEXT.md` and `TODO.md` with a `Janitor-Run: <run-id>` git trailer. Leaves drafts if dirty.
- **Zero-Token Fast Path:** Unchanged repos exit in ~0.1 seconds with **0 tokens and 0 API calls**.

### 3. Deep Architectural Overview (`janitor overview`)
- Re-synthesizes high-density, ground-truth architectural maps (`LLM-OVERVIEW.md`) covering:
  - `## What this repo is`
  - `## Machine & Host Ownership` (Mac mini vs Homelab Docker vs Pi)
  - `## What is actually built` (source code headers, routes, models, tables, configs)
  - `## Canonical entry points`
- **Central Mirroring:** Automatically mirrors overviews to `/Volumes/2TB_SSD/GitHub/docs/repos/<repo_name>.md`.

---

## Operational Cadence

Janitor runs on a dual-cadence master–worker schedule orchestrated by systemd on Homelab:

| Pass | Cadence | Trigger | What It Does |
| :--- | :--- | :--- | :--- |
| **Daily Sweep** | **Nightly at 03:00 UTC** | `janitor-sweep.timer` on Homelab | SSH to Mac mini → runs auto-tidy on stale WIP → reconciles `CONTEXT.md` & `TODO.md` for active repos (fast-paths quiet repos in 0.1s). |
| **Weekly Overview** | **Sunday at 04:00 UTC** | `janitor-overview.timer` on Homelab | SSH to Mac mini → deep codebase inspection → refreshes `LLM-OVERVIEW.md` and mirrors to central docs. |
| **On-Demand** | **Anytime via CLI** | Manual `janitor` command | Instant health check, immediate tidy, or targeted repo sweep from your terminal. |

---

## Topology

```
┌────────────────────────────────────────────────────────┐
│               HOMELAB (Master Control Plane)           │
│  - systemd user timers:                                │
│      • janitor-sweep.timer (Daily 03:00 UTC)           │
│      • janitor-overview.timer (Sun 04:00 UTC)          │
│  - Triggers Mac mini over private LAN / Tailscale SSH  │
└───────────────────────────┬────────────────────────────┘
                            │
            ssh macmini "janitor-runner <command>"
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│             MAC MINI (Worker & Storage Node)           │
│  - Repositories: /Volumes/2TB_SSD/GitHub/*             │
│  - Central Docs Hub: /Volumes/2TB_SSD/GitHub/docs/repos│
│  - Inference: Gateway2000 (g2k-bg via stdin streaming) │
│    (fallback: OpenRouter free models via HTTPS)        │
│  - State & Telemetry: ~/.local/state/janitor/state.json│
└────────────────────────────────────────────────────────┘
```

---

## CLI Usage

```bash
# Check status across all 80 repos on the workstation (~2 seconds)
janitor status --all

# Sweep the current repo (or quiet fast-path if unchanged)
janitor sweep

# Sweep specific repos
janitor sweep /Volumes/2TB_SSD/GitHub/maya /Volumes/2TB_SSD/GitHub/argus

# Sweep all repos across the entire fleet
janitor sweep --all

# Preview sweep without modifying files
janitor sweep --dry-run

# Review branches and linked worktrees without branch actions
janitor branches [repo ...] [--all] [--json] [--no-fetch]
janitor branches /Volumes/2TB_SSD/GitHub/maya --no-fetch

# Sweep while using cached remote-tracking refs
janitor sweep --no-fetch

# Tidy ephemeral trash and checkpoint abandoned work
janitor tidy
janitor tidy --all

# Regenerate deep architectural overview and mirror to central docs
janitor overview /Volumes/2TB_SSD/GitHub/maya
janitor overview --all

# Machine-readable JSON output (used by Homelab & Baywatch)
janitor sweep --all --json
```

### Branch and worktree review

`janitor branches` inventories local branch refs, cached remote-tracking refs,
and linked worktrees. Matching local and remote refs appear as one logical
branch, while local-only and remote-only refs remain visible. The report uses a
single bounded fetch of the primary remote unless `--no-fetch` is supplied.
Failed, disabled, or unavailable refreshes continue from cached refs and mark
the freshness as stale.

This command is report-only: it does not check out, merge, rebase, reset,
delete, or push branches. A refresh may prune stale remote-tracking refs, but
Janitor never prunes local branches. It does not write `CONTEXT.md`,
`TODO.md`, or Janitor state. The human output includes the deterministic branch
review block; `--json` keeps the complete `branch_review` and rendered
`markdown` in the existing JSON envelope. Nightly `janitor sweep` uses the
same collector and deterministic block, and `sweep --no-fetch` passes the
fetch suppression through to that review.

---

## Testing & Verification

Janitor includes an extensive offline test suite covering preflight guards, stage verification, zero-loss checkpointing, sentinel merging, and gateway fallback:

```bash
python3 -m unittest discover -s tests -v
```

---

## License

MIT
