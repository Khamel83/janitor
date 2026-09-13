# Janitor status and scope

Updated 2026-09-12. Existing caretaker is accepted and scheduled. See [HANDOFF.md](HANDOFF.md) for evidence and recheck commands. This human-maintained status is outside the generated sentinel.

## Completed acceptance

- [x] Diagnose the real SSH/non-repository failure and fix usage-log directory creation.
- [x] Keep Gateway2000 auto; constrain calls to bounded synthesis with low reasoning and no coding tools.
- [x] Verify process-group timeout cleanup, mutating-run exclusion, durable failure receipts, and bounded fleet failure handling.
- [x] Accept a real sweep, correct draft behavior, and a zero-model-call unchanged repeat.
- [x] Accept overview generation and conditional central mirroring; preserve dirty-docs and no-auto-commit gates.
- [x] Pass 166 offline tests, Ruff, whitespace checks, and deployed systemd unit validation.
- [x] Accept the real timer → Homelab service → SSH worker → fleet flow: run_1789261279, 81 targets, zero failures, service exit 0.
- [x] Restore enabled daily 03:00 and Sunday 04:00 America/Los_Angeles schedules; verify next triggers and user lingering.
- [x] Publish scoped source and reconciled operator documentation; retain pre-existing work.

No required implementation remains for this scope. Future runtime failures are maintenance work, not evidence that the historical implementation plans should be restarted.

## Capability boundary

Janitor provides status, Butler cache cleanup and stale-WIP checkpointing, deterministic branch/worktree reports, nightly CONTEXT/TODO reconciliation, and weekly architectural overview/mirroring.

A real sweep includes Butler and can checkpoint stale work and restore its checkout. Branch review itself is report-only. Documentation commits obey the existing clean-default-branch gate; otherwise results remain drafts. Overview never auto-commits, and central mirroring can defer while the docs repository is dirty.

Janitor summarizes repository evidence. It does not implement projects' TODOs, certify the semantic truth of model prose, resolve branch divergence, or automatically merge/delete branches. Gateway2000 owns provider selection. No budget subsystem is part of this work.

## Deferred roadmap (not a completion blocker)

- [ ] Morning/next-login branch-attention reminders, only if separately requested.
- Herdr and unrelated integrations remain out of scope.

<!-- janitor:begin:todo -->
- [x] Existing caretaker implementation and branch/worktree review.
- [x] Gateway2000 auto routing and SSH usage-log recovery.
- [x] Targeted and fleet operational acceptance; schedules restored.
<!-- janitor:end:todo -->
