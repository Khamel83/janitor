# Janitor

> Free background intelligence for Claude Code sessions.

Janitor watches your Claude Code sessions passively, records what you do, and runs background analysis using free models ($0). When you start a new session, it injects accumulated context — decisions, patterns, test gaps, code smells — so you pick up where you left off.

No configuration. No API costs. Install once, forget it's there.

## What It Does

**During your session** (PostToolUse hook):
- Records file reads, writes, edits, commits
- Captures decisions, blockers, errors
- Writes to `.janitor/events.jsonl` (append-only, ~30KB/session)

**Every 15 minutes** (system cron, free models):
- Extracts decisions and blockers from recent events
- Detects test gaps (files changed without tests)
- Scans for code smells (oversized files, long functions)
- Monitors config drift (uncommitted config changes)
- Builds dependency graph (who depends on what)
- Enriches commit messages with semantic tags
- Once/day: mines recurring patterns, generates project onboarding summary

**At session start** (SessionStart hook):
- Injects onboarding summary, test gaps, code smells, config drift, patterns, high-impact files
- You see this automatically — no action required

## What You See

```
JANITOR ONBOARDING: # Project Status — 163 events logged. 4 test gaps in janitor module...
JANITOR: Test gaps: 4 gaps: core/janitor/worker.py, core/janitor/jobs.py
JANITOR: Code smells: 2 oversized files, 4 long functions
JANITOR: High-impact files: core/task_schema.py (4 deps), core/router/lane_policy.py (3 deps)
```

## Install

**Requires:** Claude Code, Python 3.10+, an [OpenRouter API key](https://openrouter.ai/keys) (free tier works).

```bash
git clone https://github.com/Khamel83/janitor.git ~/github/janitor
cd ~/github/janitor
./setup.sh
```

That's it. The setup script:
1. Installs the `janitor` Python package
2. Adds Claude Code hooks (record, context, session-end)
3. Installs a cron job (every 15 minutes)

## How It Works

```
┌─────────────────┐     ┌──────────────┐     ┌──────────────┐
│ Claude Code      │────>│ .janitor/    │────>│ cron.sh     │
│ (your session)  │     │ events.jsonl │     │ (every 15m) │
└─────────────────┘     └──────────────┘     └──────┬───────┘
                                                          │
                              ┌─────────────────────┐
                              │ openrouter/free ($0)  │
                              ├─────────────────────┤
                              │ 12 background jobs  │
                              ├─────────────────────┤
                              │ decisions          │
                              │ test gaps           │
                              │ code smells         │
                              │ config drift        │
                              │ dependency map      │
                              │ commit enrichment  │
                              │ pattern mining      │
                              │ onboarding summary │
                              └─────────────────────┘
                                                          │
                              ┌─────────────────────┐
                              │ .janitor/           │
                              │ test-gaps.json      │
                              │ code-smells.json    │
                              │ dep-graph.json      │
                              │ onboarding.md      │
                              │ patterns.json       │
                              └─────────────────────┘
```

## Rate Budget

- **Pure-compute jobs** (test gaps, code smells, config drift, dependency map): unlimited, no API calls
- **LLM jobs**: 8-19 calls/day, well under the 1000/day free tier limit
- **Storage**: ~35MB/year per project

## Architecture

```
janitor/
  janitor/
    recorder.py      # Event recording (append-only JSONL + SQLite)
    worker.py        # OpenRouter free model caller (rate-limited, cached)
    jobs.py          # 12 background jobs
    __init__.py      # Package exports
  hooks/
    record.sh        # PostToolUse: records tool calls
    context.sh       # SessionStart: injects accumulated context
    session-end.sh   # SessionEnd: marks session end
  scripts/
    cron.sh          # System cron entry point
  setup.sh           # One-command install
```

## Jobs

| Job | LLM? | Frequency | What it finds |
|-----|------|----------|---------------|
| Test Gap Detector | No | Every 15m | Source files changed without test files |
| Code Smell Scanner | No | Every 15m | Oversized files (>500 lines), long functions (>100 lines) |
| Config Drift Monitor | No | Every 15m | Uncommitted changes in config/ |
| Dependency Impact | No | Every 15m | Import graph, ranks files by downstream impact |
| Stale File Detection | No | Daily | Files untouched in 30+ days |
| Turn Summarizer | 1 call | Every 15m | Decisions, blockers, discoveries from events |
| Commit Enricher | 1/new commit | Per commit | Semantic tags and summaries for commits |
| Pattern Miner | 1 call | Daily | Recurring files, errors, decisions across sessions |
| Onboarding Summary | 1 call | Daily | "State of the project" summary from all data |
| Session Digest | 1 call | Session end | Full structured summary for handoff |
| File Change Analysis | 1 call | After commits | Categorizes changes, identifies hotspots |
| Memory Hygiene | 1 call | Daily | Overlapping memory files to merge |

## On-Demand: Doc Sweeps

Besides the always-on hook/cron jobs above, `janitor` also ships a CLI for the two
jobs you're most likely to want to trigger directly — regenerating high-level docs
from git activity rather than session events:

```bash
janitor sweep [repo ...]      # regenerate CONTEXT.md / TODO.md from recent commits + working tree state
janitor overview [repo ...]   # regenerate LLM-OVERVIEW.md from AGENTS.md + commit history
```

Both default to the current directory, take `--dry-run` to print instead of write,
and use the same soft rate budget as every other job. `sweep` never commits
into a dirty working tree — it writes `CONTEXT.draft.md`/`TODO.draft.md` instead, and
only auto-commits on a clean `main`/`master` where the only diff is the two doc files
themselves.

`overview` also:
- Runs `scripts/status.py` in the target repo, if present, and feeds its output into
  the LLM-OVERVIEW.md prompt as a live status probe (no such script → no probe section).
- Mirrors the generated overview to `$JANITOR_DOCS_MIRROR/repos/<repo-name>.md` if that
  env var is set, in addition to (never instead of) the repo's own `LLM-OVERVIEW.md`.

Run these from cron/launchd (e.g. `janitor sweep` nightly, `janitor overview` weekly —
see `contrib/launchd/` for macOS templates) or by hand.

## Model Backend

Every job that calls a model — sweep, overview, turn summarizer, commit enricher,
pattern miner, onboarding summary, memory hygiene — goes through `janitor.worker`,
which:

1. Uses `g2k-bg`/`g2k` (Gateway2000) if either is on `PATH`.
2. Otherwise falls back to a direct `openrouter/free` HTTP call — set
   `OPENROUTER_API_KEY` for this path.

Usage is tracked locally in `.janitor/usage.jsonl` against a soft 1000/day,
20/minute budget regardless of which backend actually served the request.

## Configuration

Optional environment variables:

```bash
export OPENROUTER_API_KEY=sk-or-...  # Required for LLM jobs. Get one free at openrouter.ai/keys
```

Without the API key, pure-compute jobs (test gaps, code smells, config drift, dependency impact) still work. LLM jobs will fail gracefully.

## Storage

Per project, in `.janitor/`:

| File | Purpose |
|------|---------|
| `events.jsonl` | Raw session events (append-only, source of truth) |
| `intelligence.db` | SQLite index for queries (rebuilt on demand) |
| `usage.jsonl` | API usage log for rate limiting |
| `test-gaps.json` | Latest test gap detection results |
| `code-smells.json` | Latest code smell scan results |
| `config-drift.json` | Latest config drift results |
| `dep-graph.json` | Latest dependency graph |
| `commit-enrichments.json` | Enriched commit messages (by hash) |
| `patterns.json` | Latest pattern mining results |
| `onboarding-summary.md` | Latest onboarding summary |

## Uninstall

```bash
pip uninstall janitor
crontab -l | grep -v janitor | crontab -
# Remove hook entries from ~/.claude/settings.json
```

## License

MIT
