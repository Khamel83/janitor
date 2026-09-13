# Janitor handoff

Updated: 2026-09-12 17:26 America/Los_Angeles.

## Read this first

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

The current local branch is `main` at `ca861d8`, 23 commits ahead of
`origin/main`. It has not been pushed. Preserve these pre-existing unstaged
edits; they are not part of the auto-only change:

- `CONTEXT.md` — existing generated branch-review update.
- `tests/test_git_ops.py` — existing import-order edit.

Do not reset, clean, or discard them.
