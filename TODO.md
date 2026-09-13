# Janitor completion checklist

Updated 2026-09-12. This human-maintained section is the canonical remaining
work. Keep it outside Janitor's generated sentinel. Read `HANDOFF.md` for
the last run evidence and `WORKER-PROMPT.md` for execution instructions.

## Required to finish

- [ ] Inspect live sweep/overview timers, services, Mac worker processes, source path, and durable state. Temporarily pause relevant timers during diagnosis to prevent overlapping runs; record prior state.
- [ ] Reproduce one real synthesis failure with one representative repository prompt and an explicit wall-clock deadline. Capture sanitized stderr/exit, elapsed time, prompt size, and response-validation result locally. Do not repeat a full-fleet probe or infer the cause from the old background-lane incident.
- [ ] Fix the demonstrated cause with focused regression coverage. Preserve Gateway2000 auto routing. Ensure failures retain useful sanitized durable evidence and timeout/cancellation does not leave the worker's own descendants running. Reuse existing mechanisms; no new orchestration platform.
- [ ] Accept one targeted sweep: valid structured output, correct sentinel preservation and commit/draft behavior, durable outcome, and an unchanged repeat that makes no unnecessary model request. Use an isolated fixture for destructive Butler cases; preserve live pre-existing work.
- [ ] Accept one targeted overview through the same backend: required sections, intended output and mirror, no automatic commit. Do not regenerate all fleet overviews merely to test routing.
- [ ] Run the full offline suite, Ruff, and whitespace checks once after the final relevant changes. Verify the installed runner uses the intended source.
- [ ] Complete one supervised fleet sweep through the real Homelab service. Discover the current target set, account for every target by run ID, explain legitimate skips, and resolve synthesis/execution failures. Save sanitized evidence incrementally. Choose and record a finite wall-clock deadline before starting; stop on a repeated systemic failure rather than retrying the fleet unchanged.
- [ ] Restore the intended Pacific timers after acceptance and verify their effective units, next trigger, and an actual scheduled sweep's durable outcomes. If the scheduled run has not occurred, report that gate as pending rather than claiming unattended acceptance.
- [ ] Refresh HANDOFF/CONTEXT/overview and this checklist with exact evidence, commit scoped changes, push the intended branch, and verify local/remote/runtime revision identity. Preserve unrelated edits; do not blanket-stage them.

## Completion boundary

Finish the existing caretaker: status, safe Butler tidy/checkpointing,
deterministic branch/worktree reports, nightly CONTEXT/TODO reconciliation,
and weekly architectural overview/mirroring. Janitor summarizes repository
evidence; it does not implement projects' TODOs, certify semantic truth of
model text, or automatically resolve branch divergence.

No new reminders, Herdr integration, branch merge/deletion automation,
provider configuration changes, or budget subsystem. Gateway2000 owns model
routing; efficient execution here means bounded probes, quiet-path reuse,
and no repeated failing fleet runs.

## Historical completed implementation

The following generated list is implementation history, not operational acceptance.

<!-- janitor:begin:todo -->
- [x] Design spec and implementation plan (`8058a4b`, `36a13d1`)
- [x] Package scaffolding & preflight guards in `git_ops.py` (`4c205c2`)
- [x] Persistent state layer & task ID manager in `state.py` (`3df61ef`)
- [x] Auto-tidy engine & WIP branch checkpointer in `hygiene.py` (`aef3665`)
- [x] Model worker with stdin payload streaming in `worker.py` (`8d52783`)
- [x] Living documentation reconciler & bootstrap in `reconciler.py` (`b234fd7`)
- [x] CLI interface with fleet discovery & JSON output in `cli.py` (`b77d640`)
- [x] Homelab systemd units & Mac Mini SSH runner script (`a04c03a`)
- [x] Add `.gitignore` and untrack pycache / egg-info (`ec95539`)
- [x] Fix timestamp formatting in `git_ops.py` to keep input hash deterministic (`3295676`)
- [x] Run end-to-end unit test suite verification (90/90 passing)
- [x] Verify local editable package installation (`pip install -e .`)
- [x] Perform live dry-run and real sweep against `janitor` repository
- [x] Perform live overview synthesis against external fleet target (`maya`)
- [x] Adjust systemd timer to 3:00 AM America/Los_Angeles on Homelab
- [x] Archive legacy Mintlify starter files in `docs` repo and populate 80 fleet overviews in `docs/repos/`
- [x] Integrate Butler auto-tidy pre-pass into `sweep_repo` and remove dead `docs.py` code (`0f2bf86`)
- [x] Add deterministic report-only branch/worktree review to CLI and nightly sweep (`780767a`)
- [ ] Add morning/next-login reminders for branch attention (roadmap; v1 remains report-only)
<!-- janitor:end:todo -->
