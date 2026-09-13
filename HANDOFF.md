# Janitor handoff

Updated: 2026-09-12 recovery session (America/Los_Angeles).

## Current recovery result

The earlier fleet failure is now reproduced and identified: the SSH runner
starts outside a repository, and `_usage_log_path()` returned the uncreated
relative `.janitor/usage.jsonl`. Logging after a successful model completion
raised FileNotFoundError and converted it to synthesis_failed. This is a
Janitor filesystem-path defect, not evidence of Gateway2000 capacity failure.

Fixed the fallback to create/use JANITOR_STATE_DIR (or ~/.local/state/janitor).
A real SSH runner sweep from /private/tmp then passed for MacMiniM4 in 4.825s,
run_1789260450, with written output and no raw error. A regression test covers
logging outside a repository.

Additional accepted protections: synthesis-only auto client, process-group
timeout cleanup, mutating-run lock, per-target sanitized runs.jsonl receipts,
and bounded fleet failure/deadline handling. Source protection commit 902fa8e
and regroup documentation b21e116 were pushed. The logging fix and final fleet
acceptance are being completed; consult TODO.md before claiming completion.

Targeted argus-ops sweep run_1789260157 wrote drafts; unchanged repeat made
zero model calls and preserved documents. Overview run_1789260233 wrote and
mirrored successfully; both copies hashed
cd84e6edf089cfe059e19b2fd9ca972cbc59733cd3192e36173c003264b4ed02.
Its repository HEAD stayed 232bd42 (overview did not commit).

Accelerated timer acceptance run_1789260375 reproduced the logging bug and
stopped after three synthesis failures, with four quiet and 73 deferred/error
outcomes. The runtime-only timer override was removed; both recurring timers
are paused until the corrected acceptance run. Sanitized receipts are in
~/.local/state/janitor/runs.jsonl. Pre-fleet state and original dirty-file
patch are saved locally under .janitor/ (ignored, not committed).

## Historical interrupted-run snapshot (superseded by current result)

Regroup addendum, 2026-09-12: `TODO.md` is now the canonical ordered completion
checklist. Use `WORKER-PROMPT.md` to start the implementation/acceptance run.
This handoff retains historical evidence; scheduler and process claims below
describe the interrupted run's aftermath and must be checked live.

Scope is settled: complete the existing caretaker, including one targeted
overview acceptance. No new product features or Gateway2000 budget work.
The worker should progress through diagnosis, the smallest supported fix,
verification, deployment, supervised fleet acceptance, and publication.
Temporarily pause timers during recovery, then restore after acceptance.
An actual scheduled sweep is a separate final gate.

Janitor is paused after an interrupted fleet verification run. The
Gateway2000 auto-only source change is implemented, locally tested, and
installed on the path used by the Mac mini worker. It is **not yet accepted
end to end**: the real fleet run was slow, produced synthesis failures, and
was stopped before all repositories completed.

Do not start another full-fleet run until the model-call failure and timeout
behavior have been investigated with one bounded repository test.

## What was changed

- Janitor model calls now invoke the sourced Gateway2000 `g2k` auto function:
  `zsh -lc 'source "$HOME/.config/gateway2000/gateway2000.zsh" && g2k -p -'`.
- Full prompts are streamed through stdin. Janitor never selects or invokes
  `g2k-bg`.
- The direct OpenRouter free-model path remains only for environments without
  the Gateway2000 auto helper.
- Gateway2000 configuration, credentials, and global launchers were not
  changed.
- The repaired Homelab user service transport is deployed. It SSHes to the
  Mac mini and uses the shared Janitor source through
  `/Users/macmini/.local/bin/janitor-runner`.

Relevant local commits:

- `468527d` — repair the Janitor user-service transport.
- `137b62b` — auto-only routing design.
- `079c16e` — auto-only routing implementation plan.
- `79c45ba` — auto-lane worker tests.
- `8c0d8ad` — route worker calls through Gateway2000 auto.
- `ca861d8` — align active documentation.

The approved design and implementation plan are:

- [Auto-only design](docs/superpowers/specs/2026-09-12-janitor-gateway-auto-only-design.md)
- [Auto-only implementation plan](docs/superpowers/plans/2026-09-12-janitor-gateway-auto-only.md)

## Verification already completed

- Offline suite: `161 passed`.
- Ruff: passed.
- `git diff --check`: passed.
- A bounded live probe through the current Janitor source returned
  `JANITOR_AUTO_ONLY_PROBE`.

The bounded probe proves that the auto command can work. It does not prove
that the full Janitor prompts, response format, or fleet cadence work.

## Interrupted fleet run

Run ID: `run_1789257955`  
Started: 2026-09-12 17:05:55 PDT  
Stopped: 2026-09-12 17:22:54 PDT

Persistent state recorded 45 repository outcomes:

| Outcome | Count |
| --- | ---: |
| quiet | 20 |
| synthesis_failed | 25 |
| committed | 0 |
| written | 0 |
| error | 0 |

No repository `CONTEXT.md`, `TODO.md`, or other tracked files were written
by this run, and no repository commit was created. Janitor state and branch
observations did advance for the repositories reached before the stop.

The remote systemd service is stopped and shows exit status 255 because the
manual stop terminated its SSH wrapper. The orphaned Mac worker and its
Gateway2000 child were then explicitly terminated and are no longer running.

## Scheduler state

- `janitor-sweep.timer`: active.
- Next scheduled sweep: 2026-09-13 03:00 PDT.
- `janitor-sweep.service`: currently failed/stopped because the verification
  run was manually interrupted.

The timer was intentionally left enabled. Before the next scheduled time,
either confirm that an unattended retry is wanted or pause the timer:

```bash
ssh homelab 'systemctl --user stop janitor-sweep.timer'
```

Re-enable it later with:

```bash
ssh homelab 'systemctl --user start janitor-sweep.timer'
```

## Open investigation

The full run established that the auto-only path was selected, but not why
most real synthesis calls failed. The persistent per-repository state stores
only `synthesis_failed`, `run_id`, and a timestamp; it does not preserve the
raw Gateway2000 stderr. The simple live probe succeeded, so do not attribute
the new failures to the earlier `g2k-bg` `provider_pool_exhausted` incident
without fresh evidence.

Next session:

1. Inspect the actual Gateway2000 auto stderr/exit behavior for one bounded
   Janitor prompt.
2. Decide whether the issue is prompt size, response shape, provider
   capacity, or timeout behavior.
3. Improve durable failure evidence if needed.
4. Run one targeted repository acceptance test.
5. Only then decide whether to rerun the fleet and whether to keep the timer
   enabled.

## Working-tree preservation

At regroup inspection, local `main` was `b7f0599`, 24 commits ahead of the
cached `origin/main`, including this handoff's original commit. Re-read Git
state rather than treating these numbers as current. Preserve these pre-existing unstaged
edits; they are not part of the auto-only change:

- `CONTEXT.md` — existing generated branch-review update.
- `tests/test_git_ops.py` — existing import-order edit.

Do not reset, clean, or discard them.

The regroup documentation edits intentionally update the recent section of
`CONTEXT.md` while preserving its existing branch block byte-for-byte. The
worker must distinguish these authorized documentation edits from the
pre-existing branch-table and test edits when staging.

## Operational boundaries for acceptance

- `branches` is report-only; fetch can prune remote-tracking refs, never local branches.
- A real `sweep` includes Butler cleanup/checkpointing before synthesis. It is not a read-only diagnostic. Inspect target state first and protect pre-existing work. Use dry-run or isolated fixtures for initial diagnosis; dry-run may still invoke the model and write usage telemetry.
- `overview` writes the architectural overview and central mirror, but does not auto-commit.
- Ordinary sweep synthesis consumes recent git evidence and current CONTEXT/TODO, not this handoff as an independent input. Keep durable operator priorities outside generated blocks.
- Failure text currently exists in returned results but is not retained by `record_run`; preserve sanitized actionable evidence, not raw private prompts or secrets.
- A simple auto reply, passing tests, a service exit code, and durable repository outcomes prove different things. Do not substitute one for another.
