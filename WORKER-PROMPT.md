# Fresh completion session

Select the Luna worker with max reasoning effort in the session controls.
Paste the following prompt from the Janitor repository. This file specifies
the work; it does not change the selected model or effort.

```text
Complete Janitor's existing implementation and operational acceptance.
Workspace: /Volumes/2TB_SSD/GitHub/janitor (may resolve to /Users/macmini/github/janitor).

Read TODO.md, HANDOFF.md, CONTEXT.md, and README.md first, then inspect the
relevant code and applicable repository guidance. TODO.md's human-maintained
completion section is the canonical checklist. The auto-only design/plan
under docs/superpowers is supporting history, not a request to redo completed
implementation. Do not reopen settled scope or ask me to restate the handoff.

You are authorized to diagnose and implement the smallest necessary fixes,
run focused/offline tests and bounded live Gateway2000 auto probes, temporarily
pause Janitor timers to avoid overlapping runs, deploy to the existing runner,
perform targeted sweep/overview acceptance and then one supervised fleet
sweep, restore accepted scheduling, and commit/push scoped Janitor changes.
Preserve all pre-existing work and verify the current branch/remote before
publication. Do not blanket-stage the dirty tree. Do not push other fleet
repositories. Do not change Gateway2000 providers, credentials, global
launchers, or routing policy. Do not add a budget system or switch to g2k-bg.

First inspect live timers/services, worker processes, installed source, and
durable run state. The handoff's scheduler state is historical. Avoid overlapping
model calls. One real failing prompt must produce useful sanitized evidence
before another fleet run. Record a finite deadline for each live test; no
unbounded retries or repeated unchanged failing fleet runs. Gateway2000 owns
provider recovery. Fix only demonstrated causes; no broad refactoring.

A real sweep runs Butler and may checkpoint stale WIP and restore its checkout.
Inspect target worktrees first. Protect dirty work, including this repository's
existing CONTEXT.md branch-table and tests/test_git_ops.py edits. Use isolated
fixtures to test destructive paths. Do not enable local branch pruning or do
unrelated cleanup. Scope includes status, existing Butler, report-only branch
review, nightly CONTEXT/TODO reconciliation, and weekly overview/mirroring.
Reminders, Herdr, automatic branch resolution, and implementing other repos'
TODOs are out of scope.

Follow TODO.md through one targeted sweep and unchanged repeat, one targeted
overview, offline checks, deployed source verification, and a supervised fleet
run with every discovered target accounted for by run ID. Verify output content,
commit/draft behavior, durable state, bounded cancellation, and no leftover
owned child processes. Retain sanitized evidence locally, with paths in the
handoff. Never commit private prompts, raw provider output, or secrets.

After supervised acceptance restore the intended Pacific timers and verify an
actual scheduled sweep. Use the available waiting/monitoring mechanism if
remaining active; do not busy-poll or manufacture a scheduled-success claim.
If that run has not happened, keep its checklist item open and state the exact
next trigger and verification needed. A genuine external blocker must include
the failing boundary, evidence, and smallest required action; do not stop just
because a safe authorized implementation or verification step remains.

Update the completion checklist and current docs as evidence changes. Finish
with a short report of what changed, tests, source/remote/runtime revisions,
targeted and fleet outcomes, scheduler acceptance, and any explicit remaining
gate. Do not claim fully operational from a health check or tiny model probe.
```
