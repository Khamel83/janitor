# Janitor status and scope

Updated 2026-09-13. Existing caretaker is accepted and scheduled. Overnight PR publication and layered morning review are now authorized additions. The first complete morning packet is verified through the installed Homelab service. See [HANDOFF.md](HANDOFF.md) for deployment evidence. This human-maintained status is outside the generated sentinel.

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

The caretaker acceptance above is complete. Do not restart those historical plans when working on the new scope below.

## Overnight PR workflow

Use a larger initial pass, then incremental changes. PRs are concrete reviewable
units, not a count target: keep related updates together and include multiple
independent project PRs in the final combined review. Preserve settled reasoning
and evidence instead of making the next agent rediscover it.

- [x] Publish bounded documentation-only PRs from published default-branch evidence, without uploading local WIP or overwriting existing PR branches.
- [x] Collect original context, all open PR changes, existing bot reviews, and checks into a private morning packet.
- [x] Preserve original goals through every layer; provide a final-agent prompt for cross-PR MERGE / RE-CHECK recommendations, never automatic merging.
- [x] Pass offline tests and independent review; verify a real PR receives a matching-head review from the existing App.
- [x] Deploy and verify publication at 03:30 and morning collection at 06:00 Pacific; run the initial bounded fleet pass and record actual results.

Initial result: 42 documentation PRs open, 17 invalid synthesis outputs rejected, seven ineligible repositories. These failures remain visible for bounded retries; do not repeat completed implementation work. The complete morning packet covers 66 repositories and 47 open PRs with 47/47 complete evidence records. The final combined reasoning review is a morning agent task, not an automatic merge or a scheduled model run.

Execution plan: [overnight PRs](docs/superpowers/plans/2026-09-13-overnight-prs.md). Checkboxes above record delivery, not just source-code claims.

## Capability boundary

Janitor provides status, Butler cache cleanup and stale-WIP checkpointing, deterministic branch/worktree reports, nightly CONTEXT/TODO reconciliation, and weekly architectural overview/mirroring.

A real sweep includes Butler and can checkpoint stale work and restore its checkout. Branch review itself is report-only. Documentation commits obey the existing clean-default-branch gate; otherwise results remain drafts. Overview never auto-commits, and central mirroring can defer while the docs repository is dirty.

Janitor summarizes repository evidence and can publish its documentation proposals. It does not implement projects' TODOs, certify the semantic truth of model prose, resolve branch divergence, or automatically merge/delete branches. Existing code PRs are included in the morning review, but unpublished code branches are not auto-published. Gateway2000 owns provider selection. No budget subsystem is part of this work.

## Deferred roadmap (not a completion blocker)

- Notifications and next-login reminders remain deferred; the authorized morning deliverable is a local evidence packet, not a new messaging service.
- Herdr and unrelated integrations remain out of scope.

<!-- janitor:begin:todo -->
- [x] Existing caretaker implementation and branch/worktree review.
- [x] Gateway2000 auto routing and SSH usage-log recovery.
- [x] Targeted and fleet operational acceptance; schedules restored.
<!-- janitor:end:todo -->
