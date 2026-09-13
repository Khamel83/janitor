# Janitor operational handoff

Updated 2026-09-13. Overnight publication is live: the initial pass opened 42 documentation PRs (including the canary). No PR was merged.

## New authorized workflow

Original context -> Janitor documentation PRs -> existing Homelab PR reviewer ->
private morning evidence packet -> user's final agent recommends MERGE / RE-CHECK.
No PR is automatically merged. Existing code PRs are included in the packet;
unpublished WIP is not automatically published. The reviewer service is unchanged.

The implementation plan is `docs/superpowers/plans/2026-09-13-overnight-prs.md`.
New units are installed, validated, enabled and active: publication next runs September 13 at 03:30 Pacific and collection at 06:00 Pacific. The first full collector was started through the installed Homelab service at 01:49 Pacific.
Use WORKER-PROMPT.md for the final agent, and TODO.md for remaining delivery gates.

## Overnight receipts and morning handoff

- Initial fleet: 66 unique GitHub repositories; 41 published, one existing canary PR, 17 rejected `invalid_synthesis` outputs, seven ineligible repositories. The initial service exited 1 because those 17 proposals failed validation; no invalid PRs were published. This is partial publication success, not an all-green fleet run. Scheduled bounded publication can retry failed targets while preserving existing proposals.
- Canary: https://github.com/Khamel83/janitor/pull/4, head `b901808a5d762e70416fbff10871843ba5e8ae86`. Existing `khamel-homelab-pr-reviewer[bot]` review `5190231788` returned pass for that exact head. This COMMENTED review is not approval or merge authority. A repeat created no PR and made no model call.
- Runtime checkout was fast-forwarded locally to tested implementation `158a044`; the installed SSH runner exposes `publish` and `reviews`. Remote main is intentionally unchanged pending the implementation PR. Do not mistake local-main-ahead for missing deployment or push it blindly.
- Publication receipts: `/Users/macmini/.local/state/janitor/publication-receipts.jsonl`. Original publication evidence: sibling `publication-intents/`.
- Morning entry point: `/Users/macmini/.local/state/janitor/morning/latest.md`; JSON pointer: sibling `latest.json`. Use the timestamped report and FINAL-REVIEW-PROMPT linked there. Check timestamp and completeness; an earlier canary-only packet is not fleet acceptance.
- The 06:00 job collects evidence only. It does not run a final reasoning agent. In the morning, start your agent with WORKER-PROMPT.md to assess all PRs together and recommend MERGE / RE-CHECK. Nothing merges automatically.

The caretaker evidence below is historical acceptance of the earlier sweep, not the result of the new publication batch.

## Current state

- Final timer-driven sweep: `run_1789261279`, 18:01:19–18:06:01 Pacific (4m42s).
- All 81 discovered targets have matching durable receipts and state records: 47 quiet, 27 unchanged_hash, seven written, zero failures, zero missing/extra targets.
- Homelab service exited 0. Five successful model calls were logged during this final pass; unchanged targets reused their hashes. Written results are drafts under the existing safety gate, not automatic commits.
- Both timers are active and enabled; user lingering is enabled. Next sweep: September 13 at 03:00 Pacific. Next overview: September 13 at 04:00 Pacific, then Sundays.
- The actual sweep timer triggered acceptance through a temporary near-term runtime override. That override was removed; effective calendars are back to their normal Pacific schedules. This verifies timer-to-service execution now, not an assertion that tomorrow's run has already happened.
- No Janitor-owned OMP client remained after completion.

## What was fixed

The main failure was local: the SSH worker starts outside a repository, but usage logging returned an uncreated relative `.janitor/usage.jsonl`. Logging after a successful model response raised FileNotFoundError, incorrectly making the sweep synthesis_failed. The fallback now creates and uses JANITOR_STATE_DIR or ~/.local/state/janitor.

Janitor now uses the sourced Gateway2000 auto helper with a synthesis-only system prompt, low reasoning, no coding tools, no skill/rule discovery, no LSP/title generation, and no saved client session. Three later calls returned no printable completion; the failed targets were recovered, and the final full timer pass passed with these settings. Gateway2000 provider configuration, credentials, and global launchers were not changed.

Additional protections: 180-second owned-process-group timeout cleanup, a mutating-run lock, incremental sanitized receipts, a stop after three failed syntheses without an intervening successful synthesis, and finite run deadlines. Scheduled services print concise output rather than flooding the journal with full branch JSON.

Relevant published implementation commits:
- `902fa8e`: lifecycle bounds, synthesis isolation, durable receipts.
- `77b34fc`: non-repository usage-log path fix.
- `d846788`: concise scheduled journal output.
- `763e9d8`: low reasoning for bounded synthesis.

## Verification

- Final source: 166 offline tests passed; Ruff and git diff --check passed. Deployed systemd units passed systemd-analyze --user verify.
- Targeted argus-ops sweep wrote drafts; its unchanged repeat made zero model calls and changed no documents.
- Exact SSH/non-repository path passed for MacMiniM4: `run_1789260450`.
- Recovered maya-live via SSH with final settings: `run_1789261216`, written in 4.008s.
- Targeted overview with final settings: `run_1789261239`, successful in 7.426s.
- Earlier overview/mirror acceptance `run_1789260233` produced matching SHA-256 copies: `cd84e6edf089cfe059e19b2fd9ca972cbc59733cd3192e36173c003264b4ed02`.
- The later overview correctly skipped central mirroring because the docs repository had dirty CONTEXT/TODO files. Mirror writes remain conditional on the existing clean-docs guard; overview never auto-commits. Do not force-clean or force-commit that repository to bypass the guard.

## Evidence and recheck

Durable local records:
- `~/.local/state/janitor/state.json`: latest per-target state.
- `~/.local/state/janitor/runs.jsonl`: per-target status, run ID, elapsed time, sanitized failure category/exit/digest.
- `~/.local/state/janitor/usage.jsonl`: request-count log for the non-repository SSH worker.
- `.janitor/state.before-acceptance.json` and `.janitor/preexisting-before-fleet.patch`: ignored local preservation snapshots.

```bash
ssh homelab 'systemctl --user show janitor-sweep.timer janitor-overview.timer -p Id -p ActiveState -p NextElapseUSecRealtime'
ssh homelab 'systemctl --user show janitor-sweep.service -p ExecMainStatus -p ExecMainStartTimestamp -p ExecMainExitTimestamp'
jq -c 'select(.run_id=="run_1789261279")' ~/.local/state/janitor/runs.jsonl
janitor status --all --json
```

The original tests/test_git_ops.py import-order edit was verified (16 focused tests and Ruff) and published in bb702bd during the final push cleanup. The generated branch table was refreshed by the accepted sweep; its original pre-session diff remains in the preservation snapshot.

TODO.md is the canonical scope/status list. Historical implementation plans are completed records, not a queue to replay. Janitor maintains repository documentation and branch reports; it does not implement target projects' TODOs.
