# Janitor

> Autonomous repository caretaker & living context reconciler for the Homelab fleet.

The existing caretaker passed operational acceptance on 2026-09-12. The new
overnight PR workflow adds documentation publication and a morning evidence
packet. See [TODO.md](TODO.md) for scope and [HANDOFF.md](HANDOFF.md) for current
deployment status and verified receipts.

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

### 4. Documentation PRs and layered review

`janitor publish` creates documentation-only PRs from the repository's published
default-branch evidence. It does not upload your dirty checkout, unpublished
branches, or local branch inventory. Human-maintained text stays outside the
generated sections. Existing open Janitor PRs remain intact for review; another
night does not overwrite reviewer or human changes.

The existing [Homelab PR reviewer](https://github.com/apps/khamel-homelab-pr-reviewer)
reviews the PR through its normal GitHub event flow. Janitor does not change or
duplicate that service. `janitor reviews` collects all open PRs in discovered
repositories owned by the authenticated GitHub user, including code PRs made by
other agents. It saves original context at each PR's base commit, changes,
review comments, current-head checks, and immutable source links.

The last layer is your final agent: compare the original goals with the combined
changes and all review findings, then recommend MERGE or RE-CHECK, including
dependencies and conflicts. Neither Janitor command merges anything. A bot's
COMMENT review with a pass verdict is not GitHub approval or a merge decision.

## Operational Cadence

Janitor uses systemd on Homelab to run the Mac worker. HANDOFF.md records which
units have actually been deployed and verified.

| Pass | Cadence | Trigger | What It Does |
| :--- | :--- | :--- | :--- |
| **Daily Sweep** | **Nightly at 03:00 Pacific** | `janitor-sweep.timer` on Homelab | SSH to Mac mini → runs auto-tidy on stale WIP → reconciles `CONTEXT.md` & `TODO.md` for active repos (quiet fast path). |
| **Publish PRs** | **Nightly at 03:30 Pacific** | `janitor-publish.timer` | Bounded documentation PR publication; existing open PRs are retained. |
| **Weekly Overview** | **Sunday at 04:00 Pacific** | `janitor-overview.timer` on Homelab | SSH to Mac mini → deep codebase inspection → refreshes `LLM-OVERVIEW.md` and mirrors to central docs. |
| **Morning packet** | **Daily at 06:00 Pacific** | `janitor-reviews.timer` | Snapshot original context, all open PRs, review layers, and current-head checks for the final agent. |
| **On-Demand** | **Anytime via CLI** | Manual `janitor` command | Instant health check, immediate tidy, or targeted repo sweep from your terminal. |

---

## Topology

```
┌────────────────────────────────────────────────────────┐
│               HOMELAB (Master Control Plane)           │
│  - systemd user timers:                                │
│      • janitor-sweep.timer (Daily 03:00 Pacific)       │
│      • janitor-overview.timer (Sun 04:00 Pacific)      │
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
│  - Inference: Gateway2000 auto via sourced g2k + stdin │
│    (fallback: OpenRouter free models via HTTPS only    │
│     without the Gateway2000 auto helper)               │
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

# Read-only eligibility preview: no model calls or GitHub mutations
janitor publish --all --dry-run

# Publish at most 20 new documentation PRs; never merge them
janitor publish --all --limit 20

# Larger initial pass, still one proposal per overlapping documentation scope
janitor publish --all --limit 100

# Refresh the local morning packet; no model calls or GitHub mutations
janitor reviews --all
```

Start the final review with [WORKER-PROMPT.md](WORKER-PROMPT.md). The latest
packet is `~/.local/state/janitor/morning/latest.md`; timestamped snapshots retain
the evidence from prior collection runs. Keep these artifacts local: they can
contain private repository context. Missing or stale evidence requires a fresh
check, not an inferred pass. The number of new PRs can be zero. Scheduled runs
default to 20 new proposals; an explicit initial pass can use up to 100, also
the rolling-24-hour ceiling. These are safety bounds, not PR-count targets.
A project can have several independent code PRs in the packet; related updates
to the same generated documentation sections stay one coherent proposal.

### Branch and worktree review

`branches` is report-only. A real `sweep` also runs Butler: it may purge caches,
checkpoint stale work, and restore the original checkout. `sweep --dry-run`
avoids those actions and document/state writes, but can still call the model
and log usage. Janitor summarizes evidence; it does not implement a target
repository's TODOs. Keep human priorities outside generated sentinel blocks.

Mutating CLI runs share a local lock. Gateway2000 calls disable coding tools,
skill/rule discovery, and session saving; Janitor supplies all synthesis inputs
and requests low reasoning for this bounded documentation task.
Each call has a 180-second process-group timeout. Fleet runs stop after three
failed syntheses without an intervening successful synthesis, and stop starting
repositories after 45 minutes for sweeps or three hours for overviews
(`JANITOR_RUN_TIMEOUT` overrides seconds). Quiet repositories do not clear a
provider failure streak. Deferred targets are reported as errors, not success.
Sanitized per-target receipts append to `~/.local/state/janitor/runs.jsonl`
as targets finish; full JSON output remains available at command completion.
Scheduled services use concise human output to avoid journal burst truncation;
the per-target JSONL receipts are the durable machine-readable record. Use
`--json` interactively when the full branch report is needed.

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
