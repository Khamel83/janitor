# Janitor — Branch and Worktree Review Design

**Date:** 2026-09-11
**Status:** Final design; implementation plan pending
**Review:** Antigravity, Gemini 3.8 Flash High, independent adversarial review on 2026-09-11. Verdict: `REVISE`; required integration corrections are incorporated below.

## 1. Purpose

Janitor should make repository sprawl visible without taking ownership of branch
decisions. Each nightly handoff should answer:

- Which local branches, remote-tracking branches, and linked worktrees exist?
- Which logical branches are active, aging, stale, merged, or abandoned auto-WIP?
- Which branches need human attention, and what evidence explains their focus?
- How far is each branch from the repository default branch?

This is a review and handoff feature. It is not branch pruning, merge
automation, or a replacement for GitHub.

## 2. Scope and non-goals

### In scope

- A deterministic local branch/worktree collector and renderer.
- One bounded, noninteractive fetch of the primary remote before collection.
- A `branches` sentinel block in `CONTEXT.md`.
- `janitor branches` for on-demand human and JSON output.
- Branch review as an independent part of `janitor sweep [--all]`.
- Lightweight continuity in the existing `StateManager`.
- Safe support for linked, missing, and unmounted worktrees.

### Explicitly out of scope for v1

- Deleting, pruning, merging, rebasing, resetting, checking out, or pushing
  branches.
- GitHub API calls, pull-request state, or server-side branch metadata.
- Reading Codex, Claude Code, Herdr, or other agent transcripts.
- Per-branch `TODO.md` cards.
- Morning reminders or notification delivery. These remain roadmap items.
- A central fleet aggregate. The existing `--all` result stream remains the
  first integration point.

## 3. Safety invariants

1. Branch review never changes commits, local branches, worktree contents, or
   remote servers. The only normal repository mutation is updating cached
   remote-tracking refs during fetch.
2. Preflight failures leave the target repository untouched.
3. Janitor owns only the inside of its sentinel blocks. Human-authored bytes
   outside those blocks remain unchanged.
4. All subprocesses used by the new collector and fetch path are noninteractive
   and have finite timeouts.
5. Untrusted repository text is evidence, not instructions. It is never sent
   to an LLM by the branch collector.
6. A deterministic no-op performs no document write, staging, or commit.

## 4. Local-first freshness and fetch

Before collecting a report, Janitor identifies the primary remote and attempts
one bounded fetch unless `--no-fetch` was supplied. The fetch must use:

```text
GIT_TERMINAL_PROMPT=0
GIT_SSH_COMMAND=ssh -o BatchMode=yes -o ConnectTimeout=5
git fetch --prune <primary-remote>
```

The implementation must enforce a per-fetch timeout and must not depend on a
shell `timeout` utility. Git subprocess helpers should accept an explicit
timeout and handle timeout, missing executable, and non-zero exit as ordinary
report conditions.

The primary remote is the remote used for default-branch discovery, preferring
the configured `origin` when present and otherwise using the available remote
with a cached symbolic default. No other remote is fetched. Cached refs for
other remotes may still be inspected.

Fetch outcomes are explicit:

- `fetched`: the primary remote fetch completed successfully.
- `fetch_failed`: fetch was attempted but failed or timed out.
- `no_remote`: no remote is configured; no fetch is attempted.
- `not_attempted`: `--no-fetch` or a strict dry run suppressed fetch.

For `fetch_failed`, `no_remote`, and `not_attempted`, the collector continues
from cached local refs and sets `report_stale: true`. `no_remote` is not a
transport error and should not be reported as one. A successful fetch sets
`report_stale: false`.

## 5. Default branch and comparison base

The report records the exact comparison base ref and SHA used for each run.
Default-branch discovery is deterministic:

1. Read the cached remote symbolic ref, such as
   `refs/remotes/origin/HEAD`, for the primary remote.
2. If unavailable, use the primary remote’s `main` ref when present.
3. If unavailable, use its `master` ref when present.
4. If no remote ref is available, use the local `main` branch, then local
   `master` branch.
5. If no candidate exists, report `base_unavailable`; still render the branch
   inventory with comparison fields marked unknown.

The discovered branch name, full base ref, and resolved base SHA are carried in
both JSON and the managed Markdown block. The docs commit gate uses the
discovered local default branch name, not a hardcoded `main`/`master` test.

For each branch with a usable base, Janitor records bounded ahead/behind
counts, merged status, and changed-path evidence. Merged is an orthogonal
boolean: `true` means the branch tip is reachable from the base. It never
replaces the primary age/classification value.

If the branch and base have no common ancestor, comparison fields are unknown
with evidence status `no_common_ancestor`; collection continues.

## 6. Logical branch inventory

The collector enumerates:

- local branch refs;
- remote-tracking branch refs; and
- entries from `git worktree list --porcelain`.

It excludes tags, `HEAD`, and symbolic refs. A local branch and a matching
`origin/<name>` ref form one logical row. Local-only and remote-only branches
remain visible. The logical row contains all known local/remote refs and all
attached worktree records rather than duplicating the same branch in the
Markdown report.

Each row includes full machine-readable data for:

- logical branch name and source refs;
- local and remote presence;
- tip SHA, committer timestamp, and recent commit subjects;
- ahead/behind counts and merged flag when comparable;
- changed paths, capped at five representative paths plus a total count;
- bounded branch-local `CONTEXT.md` and `TODO.md` evidence when safely
  available;
- attached worktree paths and status;
- classification, attention flags, and evidence status.

Changed paths must be collected without requiring a checkout. A large diff is
represented by a count and at most five paths. The collector never emits an
unbounded path list. Branch-local document excerpts are bounded, clearly
marked as repository evidence, and sanitized for the output format.

## 7. Classification and attention

Use the tip commit committer timestamp (`%cI`) and the observation clock. The
primary classification is deterministic and mutually exclusive in this order:

1. `abandoned_auto_wip` for any branch whose logical name starts with
   `auto-wip/`;
2. `active` when a present attached worktree is dirty, or the tip is no more
   than seven days old;
3. `aging` when the tip is eight through thirty days old;
4. `stale` when the tip is more than thirty days old.

Missing or unmounted worktrees do not count as dirty. Their branch falls back
to tip age and carries a `missing_worktree` attention flag.

Attention flags are additive and include:

- `dirty_worktree`;
- `stale_unmerged_ahead`;
- `unattached_local_branch`;
- `abandoned_auto_wip`;
- `missing_worktree`;
- `stale_fetch_data`; and
- `comparison_unknown` when the base or merge-base is unavailable.

Ordinary feature branches remain in the report even when they have no attention
flag. Focus is evidence-based only: recent commit subjects, changed paths, and
bounded branch-local documents may support an `inferred` focus; otherwise the
focus is `unknown`. No model call is made for this feature in v1.

## 8. Deterministic rendering and sentinel ownership

Janitor adds one independent block to `CONTEXT.md`:

```markdown
<!-- janitor:begin:branches -->
...
<!-- janitor:end:branches -->
```

The existing sentinel merge helper is reused and extended only as needed. If
the block is absent, it is appended. If it exists, only its contents are
replaced. Malformed or duplicate markers are handled without deleting text
outside a recognized block.

Markdown contains every logical branch in a compact, attention-first table or
row format. It includes the base ref/SHA and enough evidence to make the JSON
report useful to a person. JSON and state retain the complete machine data.

The renderer is deterministic. Its sort key is:

```text
(attention_rank, classification_rank, -tip_committer_timestamp, logical_name)
```

Ties are resolved by logical name and then stable source-ref order. The
classification rank is `abandoned_auto_wip`, `active`, `aging`, `stale`.
Markdown uses source commit dates and fixed calendar dates only; it does not
include a changing observation timestamp or relative-time wording. JSON and
state carry the precise observation timestamp.

The final rendered block is compared with the existing branch block before
writing. If it is byte-identical, no file is written, nothing is staged, and no
commit is attempted.

When the normal LLM sweep runs, the existing `branches` block is stripped or
replaced with a one-line placeholder before `SWEEP_PROMPT` is formatted. The
LLM sees the human-authored Context content without deterministic branch-table
bulk or branch evidence contamination. The branch block is merged afterward by
Janitor and is never generated by the model.

## 9. Sweep orchestration

`janitor sweep [--all]` runs the branch review in the following order:

1. Run preflight guards, including linked-worktree-safe gitdir resolution.
2. Run the existing bounded tidy pass when not in dry-run mode.
3. Attempt the bounded primary-remote fetch, unless disabled.
4. Collect the complete branch/worktree inventory in memory.
5. Render and compare the deterministic branch block.
6. Evaluate the existing activity and input-hash gates without including
   branch output in their inputs.
7. If the normal gate is quiet, finalize the branch report without an LLM.
8. If normal synthesis is needed, call the existing worker for the normal
   `recent` and `todo` blocks using sanitized Context input, then merge the
   independent branch block.
9. Write only changed managed files and apply the commit rules below.
10. Record branch continuity and run status in `StateManager`.

The branch report is not suppressed by a zero-token or unchanged normal-input
fast path. Conversely, a branch report with no semantic change does not force a
model call. If normal synthesis fails but the deterministic branch block has
changed, Janitor persists the branch block alone and reports both the durable
branch result and the synthesis failure. If neither output changes, synthesis
failure leaves repository files untouched.

`janitor branches` uses the same collector and renderer, skips the tidy/model
path, and is read-only except for its allowed fetch. It supports human output,
`--json`, and `--no-fetch`. A strict dry run does not fetch.

## 10. Linked worktree and preflight handling

Preflight must not assume `.git` is a directory. Resolve both:

- `git rev-parse --git-dir` for worktree-specific metadata; and
- `git rev-parse --git-common-dir` for shared repository metadata.

Resolve relative paths against the target repository and check locks and
merge/rebase/cherry-pick/bisect markers in the applicable worktree and common
git directories. A failed resolution returns `not_a_git_repo` or a structured
guard result without probing further.

For every worktree from the porcelain listing, verify `Path(path).is_dir()`
before invoking any subprocess with that path as `cwd`. A missing or unmounted
path is recorded as such and never causes a fleet run to abort. Hygiene walks
must ignore `.git` both when it appears as a directory and when it appears as
the linked-worktree pointer file.

## 11. State and JSON contracts

The existing `StateManager` remains the persistence boundary. Each repository
record gains a compact branch-review section containing:

- `last_report_hash`;
- `last_semantic_change_date`;
- per logical branch: `first_seen`, `last_seen`, last tip SHA, classification,
  and present/missing state; and
- tombstones for absent branches retained for 90 days.

State stores continuity, not full nightly snapshots. State writes are separate
from repository commits and may occur even when the report block is unchanged.

The JSON result uses the existing top-level CLI envelope and includes:

- `schema_version` and `run_id`;
- repository and observation metadata;
- fetch status and `report_stale`;
- exact base ref and SHA or `base_unavailable`;
- the full logical branch rows; and
- write/commit status for sweep callers.

Human output is compact and attention-first. JSON is the stable machine
interface for later Homelab aggregation and reminder work.

## 12. Document write and commit rules

All writes compare final bytes first. The write set is the subset of
`CONTEXT.md` and `TODO.md` whose managed output actually changed.

- A branch-only semantic change on a clean checkout of the discovered default
  local branch stages only `CONTEXT.md` and uses:

  ```text
  docs(janitor): update branch and worktree inventory for <sha> [skip ci]
  ```

- A branch change combined with normal sweep changes shares the normal sweep
  commit and stages only the changed authorized docs files.
- Dirty or non-default checkouts may receive managed-block updates in place,
  but Janitor never auto-commits them.
- No semantic change means no write, stage, commit, or `written` result.
- The existing atomic staging guard remains the final defense: only the
  authorized file set may be staged, and every Janitor commit carries its
  `Janitor-Run:` trailer.

## 13. Acceptance tests

The implementation plan must cover tests for:

- local plus matching remote, local-only, remote-only, multiple remotes, and
  symbolic-ref exclusion;
- default-branch discovery for `main`, `master`, and a nonstandard branch;
- fetched, failed, no-remote, disabled, and timed-out fetches with no prompt;
- active/aging/stale boundaries, dirty worktrees, auto-WIP precedence, merged
  as an orthogonal flag, and attention ordering;
- ahead/behind evidence, five-path caps, large diffs, and no common ancestor;
- present, linked, duplicate, missing, and unmounted worktrees;
- `.git` pointer files and operation markers in preflight and hygiene walks;
- sentinel bootstrap, outside-byte preservation, prompt branch-block masking,
  deterministic ordering, byte-identical no-op behavior, and repeated runs;
- quiet branch-only updates, exact branch-only commit file/message, shared
  normal commits, dirty/non-default no-commit behavior, and preflight no-touch;
- StateManager continuity and 90-day tombstone expiry; and
- human/JSON output plus fleet error isolation.

## 14. Deferred roadmap

After v1 proves reliable local inventory and handoff quality, revisit:

- morning or next-login reminders backed by durable branch attention state;
- a central fleet report;
- GitHub PR/branch metadata;
- explicit user-approved cleanup workflows; and
- agent-specific handoff integrations.

No deferred item changes the v1 report-only contract.
