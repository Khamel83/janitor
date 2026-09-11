# Janitor Branch and Worktree Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.
>
> **Execution status (2026-09-11):** Implemented and merged locally into `main` at `780767a`. The merged tree passes `PYTHONPATH=. pytest -q` (159 tests), `ruff check janitor tests`, and `git diff --check`. The feature worktree and branch were removed after verification; the local merge has not been pushed.

**Goal:** Add a deterministic, local-first, report-only branch and worktree review to Janitor, available in nightly sweeps and through 'janitor branches'.

**Architecture:** Add one focused 'janitor.branch_review' module that owns bounded fetch, ref/worktree collection, comparison evidence, classification, stable ordering, and Markdown/JSON-ready report data. Reuse the existing sentinel merge, StateManager, worker, atomic commit guard, and CLI envelope. Integrate the deterministic branch path before the existing LLM fast paths so branch reporting never causes an unnecessary model call or disappears on a quiet repository.

**Tech Stack:** Python 3.10+ standard library, Git CLI subprocesses, existing unittest/pytest suite, existing StateManager, and the existing Janitor worker gateway.

## Global Constraints

- Python runtime remains '>=3.10'; add no third-party dependencies.
- v1 is local-first and makes one bounded fetch of only the primary remote; no GitHub API or agent-transcript access.
- Fetch uses 'GIT_TERMINAL_PROMPT=0', 'GIT_SSH_COMMAND=ssh -o BatchMode=yes -o ConnectTimeout=5', 'git fetch --prune', and finite subprocess timeouts.
- Fetch outcomes are 'fetched', 'fetch_failed', 'no_remote', or 'not_attempted'; failed, missing, and disabled freshness set 'report_stale: true' while 'no_remote' is not a transport error.
- Inventory local branches, remote-tracking branches, and linked worktrees; exclude tags, 'HEAD', and symbolic refs; merge local plus matching 'origin/<name>' into one logical row while retaining local-only and remote-only rows.
- Classify in priority order: 'abandoned_auto_wip', 'active' for a dirty present worktree or tip age '<=7' days, 'aging' for '8..30' days, and 'stale' for '>30' days; use committer timestamps ('%cI'); keep merged as an orthogonal flag.
- Cap changed-path evidence at five paths plus a total count, cap recent subjects and branch-local document evidence, and report 'no_common_ancestor' instead of crashing.
- Never checkout, merge, delete, prune local branches, reset, push, or modify commits; the only normal repository mutation from review is cached remote-tracking refs during fetch.
- Add exactly one '<!-- janitor:begin:branches -->' / '<!-- janitor:end:branches -->' managed block to 'CONTEXT.md'; preserve all bytes outside it.
- Markdown contains every logical branch with attention-first stable ordering; JSON contains full machine data; Markdown has fixed source calendar dates and no changing observation timestamp or relative-time wording.
- A byte-identical deterministic result performs no document write, staging, commit, or 'written' result.
- A branch-only change on a clean checkout of the discovered default branch stages only 'CONTEXT.md' and commits with 'docs(janitor): update branch and worktree inventory for <sha> [skip ci]'; dirty or non-default checkouts are never auto-committed.
- Existing normal sweep activity/hash inputs must not include Janitor-managed branch output; existing Context branch content is masked before the LLM prompt and the deterministic block is merged afterward.
- Linked-worktree '.git' pointer files, worktree-specific gitdirs, common gitdirs, missing paths, and unmounted paths must be handled without crashes or unsafe subprocess probes.
- 'janitor branches' reuses the collector and renderer, skips tidy and model synthesis, supports human output, '--json', and '--no-fetch', and does not write Context or StateManager state.
- Full verification command is 'PYTHONPATH=. pytest -q' from the feature worktree.

---

## File map

Create 'janitor/branch_review.py' for branch fetch, base discovery, ref/worktree collection, comparison evidence, classification, report hashing, and deterministic Markdown rendering.

Modify 'janitor/git_ops.py' for timeout-aware Git execution and linked-worktree-safe metadata/preflight resolution.

Modify 'janitor/hygiene.py' so '.git' pointer files are excluded from non-Git walks.

Modify 'janitor/state.py' for compact branch continuity and 90-day tombstones.

Modify 'janitor/reconciler.py' to orchestrate the branch prepass, mask branch content from model prompts, compare final bytes, and apply branch-aware commit rules.

Modify 'janitor/cli.py' for 'branches' and '--no-fetch' arguments and output.

Modify 'README.md' to document the new report-only command and nightly behavior.

Modify 'tests/test_git_ops.py', 'tests/test_hygiene.py', 'tests/test_state.py', 'tests/test_reconciler.py', and 'tests/test_cli.py'; create 'tests/test_branch_review.py'.

Create '.superpowers/sdd/progress.md' as the ignored execution ledger. This file is process state and must not be committed.

## Task 1: Harden Git execution and linked-worktree safety

**Files:**
- Modify: 'janitor/git_ops.py:25-60'
- Modify: 'janitor/hygiene.py:_non_git_walk'
- Test: 'tests/test_git_ops.py'
- Test: 'tests/test_hygiene.py'

**Interfaces:**
- Preserve existing callers of '_sh(cmd, cwd)' while adding optional 'timeout: float = 10.0' and 'env: dict[str, str] | None = None' parameters.
- Add 'resolve_git_metadata(repo_dir: Path) -> tuple[Path, Path] | None', returning absolute worktree gitdir and common gitdir from 'git rev-parse --git-dir' and 'git rev-parse --git-common-dir'.
- Keep 'check_preflight_guards(repo_dir: Path) -> str | None' unchanged for callers, but make it check resolved worktree and common gitdirs.

- [ ] **Step 1: Write failing linked-worktree and timeout tests.**

Add these behaviors to the existing test files:

~~~python
def test_preflight_resolves_git_pointer_and_detects_worktree_lock(self):
    worktree = Path(self.temp_dir.name) / "linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "feature", str(worktree)],
        cwd=self.repo_dir,
        check=True,
    )
    git_dir = Path(subprocess.run(
        ["git", "rev-parse", "--git-dir"], cwd=worktree,
        check=True, capture_output=True, text=True,
    ).stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (worktree / git_dir).resolve()
    (git_dir / "index.lock").write_text("")
    self.assertEqual(check_preflight_guards(worktree), "git_index_locked")

def test_preflight_detects_common_operation_marker_from_linked_worktree(self):
    worktree = Path(self.temp_dir.name) / "linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "feature", str(worktree)],
        cwd=self.repo_dir,
        check=True,
    )
    git_dir = Path(subprocess.run(
        ["git", "rev-parse", "--git-common-dir"], cwd=worktree,
        check=True, capture_output=True, text=True,
    ).stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (worktree / git_dir).resolve()
    (git_dir / "MERGE_HEAD").write_text("0" * 40 + "\n")
    self.assertEqual(check_preflight_guards(worktree), "merge_in_progress")

def test_is_wip_stale_ignores_linked_worktree_git_pointer(self):
    worktree = Path(self.temp_dir.name) / "linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "feature", str(worktree)],
        cwd=self.repo_dir,
        check=True,
    )
    old = time.time() - 48 * 3600
    os.utime(worktree / ".git", (old, old))
    self.assertFalse(is_wip_stale(worktree))
~~~

Add a unit test that patches 'subprocess.run' to raise 'subprocess.TimeoutExpired' for '_sh' and asserts '_sh' returns an empty string instead of raising. Import '_sh' in that test only.

- [ ] **Step 2: Run the focused tests and confirm the new tests fail for the intended missing behavior.**

Run:

~~~bash
PYTHONPATH=. pytest -q tests/test_git_ops.py tests/test_hygiene.py
~~~

Expected: existing tests pass and the new linked-worktree/timeout assertions fail because preflight still inspects only 'repo_dir/.git' and '_non_git_walk' still yields '.git' pointer files.

- [ ] **Step 3: Write minimal timeout-aware Git helpers and metadata resolution.**

Implement the minimal behavior:

~~~python
def _sh(cmd, cwd, timeout=10.0, env=None):
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""

def resolve_git_metadata(repo_dir):
    git_dir = _sh(["git", "rev-parse", "--git-dir"], repo_dir)
    common_dir = _sh(["git", "rev-parse", "--git-common-dir"], repo_dir)
    if not git_dir or not common_dir:
        return None
    def absolute(value):
        path = Path(value)
        return path if path.is_absolute() else (Path(repo_dir) / path).resolve()
    return absolute(git_dir), absolute(common_dir)
~~~

Refactor 'check_preflight_guards' to return 'not_a_git_repo' when resolution fails, then check 'index.lock', merge/cherry-pick, rebase, and bisect markers in both resolved directories. De-duplicate paths before checking. Do not call subprocesses with a missing worktree path.

Update '_non_git_walk' to remove '.git' from both 'dirs' and 'files':

~~~python
if ".git" in dirs:
    dirs.remove(".git")
files = [name for name in files if name != ".git"]
~~~

- [ ] **Step 4: Run the focused tests and the complete baseline suite.**

Run:

~~~bash
PYTHONPATH=. pytest -q tests/test_git_ops.py tests/test_hygiene.py
PYTHONPATH=. pytest -q
~~~

Expected: both commands exit 0; no existing behavior regresses.

- [ ] **Step 5: Commit the task.**

~~~bash
git add janitor/git_ops.py janitor/hygiene.py tests/test_git_ops.py tests/test_hygiene.py
git commit -m "fix: harden git metadata and worktree guards"
~~~

## Task 2: Implement branch collection, comparison, and classification

**Files:**
- Create: 'janitor/branch_review.py'
- Create: 'tests/test_branch_review.py'

**Interfaces:**
- Define 'collect_branch_report(repo_dir: Path, *, now: datetime | None = None, fetch: bool = True) -> dict'.
- Define 'render_branch_block(report: dict) -> str'.
- Define 'branch_report_hash(report: dict) -> str'.
- The report dictionary must contain 'repo', 'observed_at', 'fetch', 'report_stale', 'base', 'branches', 'attention_flags', and 'report_hash'.
- Each branch row must contain 'name', 'local_ref', 'remote_refs', 'refs', 'tip', 'worktrees', 'classification', 'attention_flags', 'focus', and 'evidence'.

- [ ] **Step 1: Write failing collector tests covering the report contract.**

Create Git fixtures with deterministic commit dates and these tests:

~~~python
def test_collects_local_origin_and_remote_only_refs_into_logical_rows(self):
    # Create local feature, refs/remotes/origin/feature, and
    # refs/remotes/upstream/remote-only without checking them out.
    report = collect_branch_report(repo, now=FIXED_NOW, fetch=False)
    rows = {row["name"]: row for row in report["branches"]}
    self.assertEqual(set(rows), {"main", "feature", "remote-only"})
    self.assertEqual(rows["feature"]["local_ref"]["name"], "refs/heads/feature")
    self.assertEqual([r["name"] for r in rows["feature"]["remote_refs"]], ["origin/feature"])

def test_excludes_symbolic_remote_head_and_tags(self):
    report = collect_branch_report(repo, now=FIXED_NOW, fetch=False)
    names = {row["name"] for row in report["branches"]}
    self.assertNotIn("HEAD", names)
    self.assertNotIn("release-tag", names)

def test_classification_uses_committer_age_dirty_worktree_and_auto_wip_priority(self):
    rows = {row["name"]: row for row in collect_branch_report(
        repo, now=FIXED_NOW, fetch=False
    )["branches"]}
    self.assertEqual(rows["auto-wip/old"]["classification"], "abandoned_auto_wip")
    self.assertEqual(rows["fresh"]["classification"], "active")
    self.assertEqual(rows["eight-days"]["classification"], "aging")
    self.assertEqual(rows["old"]["classification"], "stale")
    self.assertIn("dirty_worktree", rows["dirty"]["attention_flags"])

def test_records_base_ref_sha_comparison_and_bounded_changed_paths(self):
    report = collect_branch_report(repo, now=FIXED_NOW, fetch=False)
    self.assertEqual(report["base"]["branch"], "main")
    row = next(item for item in report["branches"] if item["name"] == "feature")
    comparison = row["tip"]["comparison"]
    self.assertEqual(comparison["ahead"], 1)
    self.assertEqual(comparison["behind"], 0)
    self.assertFalse(comparison["merged"])
    self.assertLessEqual(len(comparison["changed_paths"]), 5)
    self.assertIn("changed_path_count", comparison)

def test_no_common_ancestor_is_reported_without_raising(self):
    report = collect_branch_report(repo, now=FIXED_NOW, fetch=False)
    row = next(item for item in report["branches"] if item["name"] == "unrelated")
    self.assertEqual(row["tip"]["comparison"]["status"], "no_common_ancestor")

def test_missing_worktree_is_reported_without_status_probe(self):
    report = collect_branch_report(repo, now=FIXED_NOW, fetch=False)
    row = next(item for item in report["branches"] if item["name"] == "feature")
    self.assertEqual(row["worktrees"][0]["status"], "missing")
    self.assertIn("missing_worktree", row["attention_flags"])
~~~

Also test fetch outcomes with mocked 'subprocess.run': successful fetch, non-zero fetch, timeout, no remote, and 'fetch=False'. Assert the exact environment contains 'GIT_TERMINAL_PROMPT=0' and the required 'GIT_SSH_COMMAND', and assert no fetch runs for 'fetch=False'.

- [ ] **Step 2: Run the new tests and confirm they fail because the module and collector do not exist.**

Run:

~~~bash
PYTHONPATH=. pytest -q tests/test_branch_review.py
~~~

Expected: collection fails with the missing 'janitor.branch_review' import. Fix only test-fixture typos if needed; do not implement production code before observing the intended failure.

- [ ] **Step 3: Implement bounded Git querying and fetch.**

Add these constants and internal boundaries:

~~~python
FETCH_TIMEOUT_SECONDS = 15.0
GIT_QUERY_TIMEOUT_SECONDS = 10.0
MAX_CHANGED_PATHS = 5
MAX_RECENT_SUBJECTS = 5
MAX_DOC_EVIDENCE_CHARS = 1000
ACTIVE_DAYS = 7
AGING_DAYS = 30
~~~

Use one internal '_run_git(repo_dir, args, *, cwd=None, timeout=GIT_QUERY_TIMEOUT_SECONDS, env=None)' that returns a small result object with 'returncode', 'stdout', 'stderr', and 'timed_out'. It must catch 'TimeoutExpired' and 'OSError'.

Implement these internal boundaries with the stated return types:

- '_fetch_primary_remote(repo_dir: Path, *, enabled: bool) -> dict'
- '_discover_base(repo_dir: Path, primary_remote: str | None) -> dict'
- '_collect_refs(repo_dir: Path) -> list[dict]'
- '_collect_worktrees(repo_dir: Path) -> list[dict]'
- '_compare_ref(repo_dir: Path, base_ref: str | None, ref: str) -> dict'
- '_classify(row: dict, now_epoch: int) -> tuple[str, list[str]]'

Use 'git for-each-ref' over 'refs/heads' and 'refs/remotes' with NUL-separated fields including '%(refname)', '%(objectname)', '%(committerdate:unix)', '%(committerdate:iso-strict)', '%(subject)', and '%(symref)'. Skip symbolic refs and remote 'HEAD'.

Parse 'git worktree list --porcelain'; before running status for an entry, require 'Path(path).is_dir()'. Missing entries get 'status: missing' and no subprocess with that path as 'cwd'. Present entries use 'git status --porcelain --untracked-files=normal'; a query failure is 'status: probe_error' and does not count as dirty.

Choose a row’s canonical tip as local ref first, then the primary remote ref, then the lexicographically first remaining remote ref. Keep every ref’s SHA/date/comparison in 'refs'. For comparisons, use 'git rev-list --left-right --count <base>...<ref>' ('behind' is left, 'ahead' is right), 'git merge-base --is-ancestor <ref> <base>' for merged, and 'git diff --name-only -z --no-renames <base>...<ref>' for changed paths. On failed merge-base, return 'status: no_common_ancestor'; never emit an unbounded path list.

Build recent subjects with 'git log -n 5 --format=%s <ref>'. Read 'CONTEXT.md' and 'TODO.md' through 'git show <ref>:<file>' without checkout, truncate to 'MAX_DOC_EVIDENCE_CHARS', and mark the content as repository evidence. Do not pass this data to the worker.

Implement classification with 'now_epoch - committer_timestamp', dirty-present-worktree priority, and 'auto-wip/' priority. Add row flags for dirty, stale-unmerged-ahead, unattached local, abandoned auto-WIP, missing worktree, and comparison unknown. Add 'stale_fetch_data' at report level when freshness is limited.

- [ ] **Step 4: Implement deterministic report assembly and hash.**

Group refs by logical name, attach worktrees by 'refs/heads/<name>', compute focus as a bounded deterministic summary of recent subjects/paths or 'unknown', and sort rows by:

~~~python
(attention_rank, classification_rank, -tip_committer_timestamp, logical_name)
~~~

Use 'classification_rank = {"abandoned_auto_wip": 0, "active": 1, "aging": 2, "stale": 3}' and a stable boolean-derived attention rank. 'branch_report_hash' must hash canonical JSON with 'observed_at' and 'report_hash' removed, 'sort_keys=True', and compact separators.

- [ ] **Step 5: Implement and test the deterministic Markdown renderer.**

Render the exact block shape:

~~~markdown
<!-- janitor:begin:branches -->
## Branch and Worktree Review
Base: refs/remotes/origin/main @ <full-sha>
Freshness: current

| Branch | Class | Sources | Merged | Ahead/behind | Worktree | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| main | active | local, origin/main | yes | +0/-0 | clean | subject: initial; paths: README.md |
<!-- janitor:end:branches -->
~~~

The renderer must include every row, use fixed source commit dates, escape Markdown table delimiters, cap evidence strings, and render '?' for unknown comparisons. Include a report-level stale/no-remote reason without adding an observation timestamp.

Add tests for attention-first ordering, stable tie sorting by name, escaping '|' and newlines, no branches, base unavailable, repeated render byte equality, and hash equality when only 'observed_at' changes.

- [ ] **Step 6: Run focused and complete tests.**

~~~bash
PYTHONPATH=. pytest -q tests/test_branch_review.py
PYTHONPATH=. pytest -q
~~~

Expected: exit 0 with all collector/render tests and all prior tests passing.

- [ ] **Step 7: Commit the task.**

~~~bash
git add janitor/branch_review.py tests/test_branch_review.py
git commit -m "feat: collect and render branch review reports"
~~~

## Task 3: Add StateManager continuity and branch sentinel helpers

**Files:**
- Modify: 'janitor/state.py:31-150'
- Modify: 'janitor/reconciler.py:181-226'
- Test: 'tests/test_state.py'
- Test: 'tests/test_reconciler.py'

**Interfaces:**
- Add 'StateManager.get_branch_review(repo_name: str) -> dict | None'.
- Add 'StateManager.record_branch_observation(repo_name: str, rows: list[dict], report_hash: str, observed_at: int) -> None'.
- Add 'extract_sentinel_block(existing_text: str, tag: str) -> str' and 'remove_sentinel_block(existing_text: str, tag: str) -> str' beside 'merge_sentinel_block'.
- 'record_branch_observation' stores only 'last_report_hash', 'last_semantic_change_date', and per-branch continuity ('first_seen', 'last_seen', 'last_sha', 'classification', 'present', 'missing_since'); remove tombstones missing for more than 90 days.

- [ ] **Step 1: Write failing state and sentinel tests.**

Add tests for first observation, stable 'first_seen', updated 'last_seen'/SHA/classification, missing branch tombstones, 90-day expiry using an injected timestamp, and reload persistence. Add sentinel tests:

~~~python
def test_extract_and_remove_sentinel_block_preserve_outside_bytes(self):
    text = "before\n<!-- janitor:begin:branches -->\nold\n<!-- janitor:end:branches -->\nafter\n"
    self.assertEqual(extract_sentinel_block(text, "branches"), "old")
    self.assertEqual(remove_sentinel_block(text, "branches"), "before\nafter\n")

def test_missing_or_malformed_sentinel_is_left_unchanged_by_remove(self):
    text = "human <!-- janitor:begin:branches --> no end"
    self.assertEqual(remove_sentinel_block(text, "branches"), text)
~~~

- [ ] **Step 2: Run focused tests and observe intended failures.**

~~~bash
PYTHONPATH=. pytest -q tests/test_state.py tests/test_reconciler.py
~~~

Expected: only the new API assertions fail; all existing tests continue to pass.

- [ ] **Step 3: Implement compact state observation and sentinel extraction/removal.**

Use one 'branch_review' object under the existing repository record. Preserve all existing state keys and WIP behavior. Use UTC calendar date from 'observed_at' for 'last_semantic_change_date'; update it only when 'report_hash' changes. Remove expired missing entries before saving.

Implement extraction with the same escaped marker regex used by 'merge_sentinel_block'; return only the first recognized block’s inner content, and make removal a no-op if there is no complete block.

- [ ] **Step 4: Run focused and complete tests.**

~~~bash
PYTHONPATH=. pytest -q tests/test_state.py tests/test_reconciler.py
PYTHONPATH=. pytest -q
~~~

Expected: exit 0.

- [ ] **Step 5: Commit the task.**

~~~bash
git add janitor/state.py janitor/reconciler.py tests/test_state.py tests/test_reconciler.py
git commit -m "feat: persist branch review continuity"
~~~

## Task 4: Integrate the deterministic branch path into sweep

**Files:**
- Modify: 'janitor/reconciler.py:sweep_repo'
- Test: 'tests/test_reconciler.py'

**Interfaces:**
- Extend 'sweep_repo(repo_dir, state_mgr, run_id, dry_run=False, no_fetch=False) -> dict' without breaking existing positional callers.
- Import 'BRANCH_SENTINEL_TAG', 'collect_branch_report', 'render_branch_block', and 'branch_report_hash' from 'janitor.branch_review'.
- Keep existing 'quiet', 'unchanged_hash', 'dry_run', 'written', 'committed', and 'synthesis_failed' statuses compatible; add nested branch report metadata rather than replacing existing keys.

- [ ] **Step 1: Write failing integration tests before changing sweep code.**

Define a local test helper with a stable report shape so the reconciler tests
do not depend on GitHub or on the collector implementation:

~~~python
def fixture_branch_report(changed):
    return {
        "repo": "repo",
        "observed_at": "2026-09-11T00:00:00+00:00",
        "fetch": {"status": "not_attempted", "remote": "origin"},
        "report_stale": True,
        "base": {"branch": "main", "local_branch": "main", "ref": "refs/heads/main", "sha": "base"},
        "branches": [],
        "attention_flags": [],
        "report_hash": "changed" if changed else "same",
    }
~~~

Patch the renderer in each test that needs a changed or unchanged block. Add
tests that prove:

~~~python
@patch("janitor.reconciler.extract_structured")
@patch("janitor.reconciler.render_branch_block", return_value="<!-- janitor:begin:branches -->\nnew\n<!-- janitor:end:branches -->")
@patch("janitor.reconciler.collect_branch_report")
def test_quiet_normal_repo_still_writes_changed_branch_block_without_model(
    self, collect, render, extract
):
    collect.return_value = fixture_branch_report(changed=True)
    result = sweep_repo(repo, self.sm, "run_branch", no_fetch=True)
    self.assertIn(result["status"], {"written", "committed"})
    extract.assert_not_called()
    self.assertIn("<!-- janitor:begin:branches -->", (repo / "CONTEXT.md").read_text())

@patch("janitor.reconciler.extract_structured")
@patch("janitor.reconciler.render_branch_block", return_value="<!-- janitor:begin:branches -->\nold\n<!-- janitor:end:branches -->")
@patch("janitor.reconciler.collect_branch_report")
def test_unchanged_branch_block_and_normal_hash_do_not_write_or_call_model(
    self, collect, render, extract
):
    collect.return_value = fixture_branch_report(changed=False)
    (repo / "CONTEXT.md").write_text(
        "<!-- janitor:begin:branches -->\nold\n<!-- janitor:end:branches -->\n"
    )
    result = sweep_repo(repo, self.sm, "run_noop", no_fetch=True)
    self.assertEqual(result["status"], "quiet")
    extract.assert_not_called()

@patch("janitor.reconciler.extract_structured")
@patch("janitor.reconciler.render_branch_block", return_value="<!-- janitor:begin:branches -->\nnew\n<!-- janitor:end:branches -->")
@patch("janitor.reconciler.collect_branch_report")
def test_existing_branch_block_is_masked_from_normal_sweep_prompt(
    self, collect, render, extract
):
    collect.return_value = fixture_branch_report(changed=False)
    (repo / "CONTEXT.md").write_text(
        "human\n<!-- janitor:begin:branches -->\nsecret branch table\n"
        "<!-- janitor:end:branches -->\n"
    )
    (repo / "dirty.txt").write_text("work\n")
    extract.return_value = dict(SWEEP_RESPONSE)
    sweep_repo(repo, self.sm, "run_mask", no_fetch=True)
    prompt = extract.call_args.args[0]
    self.assertNotIn("secret branch table", prompt)

def test_branch_only_commit_uses_dynamic_default_and_context_only(self):
    # Fixture discovers refs/remotes/origin/trunk as base and checks out trunk.
    result = sweep_repo(repo, self.sm, "run_trunk", no_fetch=True)
    self.assertEqual(result["status"], "committed")
    names = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    self.assertEqual(names, ["CONTEXT.md"])
    self.assertIn("docs(janitor): update branch and worktree inventory for", _git(repo, "log", "-1", "--format=%B"))

@patch("janitor.reconciler.extract_structured", side_effect=RuntimeError("gateway down"))
@patch("janitor.reconciler.render_branch_block", return_value="<!-- janitor:begin:branches -->\nnew\n<!-- janitor:end:branches -->")
@patch("janitor.reconciler.collect_branch_report")
def test_branch_report_is_persisted_when_normal_synthesis_fails(self, collect, render, extract):
    collect.return_value = fixture_branch_report(changed=True)
    result = sweep_repo(repo, self.sm, "run_model_failure", no_fetch=True)
    self.assertEqual(result["status"], "synthesis_failed")
    self.assertEqual(result["branch_status"], "committed")
~~~

Add coverage for branch output sharing a normal commit, dirty/non-default no-commit, dry-run no fetch/no writes, preflight no-touch, exact byte no-op, and the existing normal hash gate remaining independent of branch output.

- [ ] **Step 2: Run the new integration tests and confirm they fail for the old early-return/commit behavior.**

~~~bash
PYTHONPATH=. pytest -q tests/test_reconciler.py
~~~

Expected: new tests fail because 'sweep_repo' currently returns before branch collection, sends the full Context to the model, and hardcodes 'main'/'master'.

- [ ] **Step 3: Implement the orchestration in the approved order.**

Use this control flow:

~~~python
guard = check_preflight_guards(repo_dir)
if guard:
    return skipped_result
if not dry_run:
    purge_ephemeral_trash(repo_dir)
    if is_wip_stale(repo_dir):
        checkpoint_abandoned_wip(repo_dir, state_mgr, run_id)
branch_report = collect_branch_report(repo_dir, now=None, fetch=not (dry_run or no_fetch))
curr_context = read_context()
existing_branch_block = extract_sentinel_block(curr_context, BRANCH_SENTINEL_TAG)
new_branch_block = render_branch_block(branch_report)
branch_changed = existing_branch_block != inner_content(new_branch_block)
has_activity, recent_log, recent_diff = has_24h_activity(repo_dir)
normal_hash = _sweep_input_hash(status["porcelain"], recent_log, recent_diff)
normal_needed = (status["is_dirty"] or has_activity) and state_mgr.get_last_input_hash(repo_dir.name) != normal_hash
~~~

Do not return from the normal quiet/hash gate until the deterministic branch block has been finalized. If 'normal_needed' is false, retain existing normal blocks and merge only 'branches'. If true, pass 'remove_sentinel_block(curr_context, "branches")' to 'SWEEP_PROMPT', then merge the model’s 'recent'/'todo' blocks and the deterministic branch block.

Before any write, compare final Context/TODO bytes to current bytes and write only changed files. If the normal model fails, persist a changed deterministic branch block alone and return 'status: synthesis_failed' plus 'branch_status: written' or 'committed'; if the branch block is unchanged, leave files untouched.

Use 'branch_report["base"]["local_branch"]' for the commit gate. A branch-only clean/default commit calls:

~~~python
atomic_stage_and_commit(
    repo_dir,
    ["CONTEXT.md"],
    f"docs(janitor): update branch and worktree inventory for {status['sha']} [skip ci]",
    run_id,
)
~~~

For a combined normal/branch update, stage only changed files from 'SWEEP_FILES' and use the existing sweep message. For dirty or non-default status, write managed blocks but never commit. Record the branch observation after collection using 'StateManager', and record the run status after the write/commit decision.

- [ ] **Step 4: Run targeted integration tests and the complete suite.**

~~~bash
PYTHONPATH=. pytest -q tests/test_reconciler.py
PYTHONPATH=. pytest -q
~~~

Expected: exit 0; normal existing sweep tests and all new branch fast-path/commit tests pass.

- [ ] **Step 5: Commit the task.**

~~~bash
git add janitor/reconciler.py tests/test_reconciler.py
git commit -m "feat: integrate branch review into sweeps"
~~~

## Task 5: Add the CLI command, flags, documentation, and final tests

**Files:**
- Modify: 'janitor/cli.py'
- Modify: 'README.md'
- Test: 'tests/test_cli.py'

**Interfaces:**
- Add 'janitor branches [repo ...] [--all] [--json] [--no-fetch]'.
- Add '--no-fetch' to 'sweep'.
- Add '_run_branches(repo: Path, no_fetch: bool) -> dict' that calls 'collect_branch_report(repo, fetch=not no_fetch)' and returns 'status: ok', 'branch_review', and rendered 'markdown'; it must not call tidy, model, or StateManager mutation.
- Pass 'no_fetch' through 'main()' to 'sweep_repo(repo, state_mgr, run_id, dry_run=args.dry_run, no_fetch=args.no_fetch)'.
- Human 'branches' output prints a compact status line followed by the rendered branch block; JSON uses the existing top-level envelope and full report.

- [ ] **Step 1: Write failing CLI tests.**

Add tests for parser/dispatch and output:

~~~python
def test_branches_json_uses_collector_and_no_fetch(self):
    repo = self.fake_repo("demo")
    expected = {
        "repo": "demo", "status": "ok",
        "branch_review": {"branches": [{"name": "main"}], "report_stale": True},
        "markdown": "BRANCHES",
    }
    with patch("janitor.cli.collect_branch_report") as collect, patch(
        "janitor.cli.render_branch_block", return_value="BRANCHES"
    ) as render:
        collect.return_value = expected["branch_review"]
        code, out = self.run_cli(["branches", "--json", "--no-fetch", str(repo)])
    self.assertEqual(code, 0)
    result = json.loads(out)["results"][0]
    self.assertEqual(result["status"], "ok")
    self.assertEqual(result["markdown"], "BRANCHES")
    collect.assert_called_once_with(repo.resolve(), fetch=False)
    render.assert_called_once_with(expected["branch_review"])

def test_sweep_no_fetch_is_forwarded(self):
    repo = self.fake_repo("demo")
    with patch("janitor.cli.sweep_repo", return_value={"repo": "demo", "status": "quiet"}) as sweep:
        self.assertEqual(self.run_cli(["sweep", "--no-fetch", str(repo)])[0], 0)
    self.assertEqual(sweep.call_args.kwargs["no_fetch"], True)

def test_branches_human_output_contains_all_rows(self):
    repo = self.fake_repo("demo")
    with patch("janitor.cli.collect_branch_report", return_value={"branches": [{"name": "main"}], "report_stale": False}), patch(
        "janitor.cli.render_branch_block", return_value="| main |"
    ):
        code, out = self.run_cli(["branches", str(repo)])
    self.assertEqual(code, 0)
    self.assertIn("[ok] demo", out)
    self.assertIn("| main |", out)
~~~

Update existing sweep mock assertions to accept the added 'no_fetch=False' keyword while preserving existing behavior.

- [ ] **Step 2: Run CLI tests and observe intended failures.**

~~~bash
PYTHONPATH=. pytest -q tests/test_cli.py
~~~

Expected: new command/flag tests fail because the parser and dispatch do not yet define them.

- [ ] **Step 3: Implement CLI dispatch and output.**

Add the parser entries using the same target arguments as 'sweep'. In 'main()' dispatch 'args.command == "branches"' before the existing commands. Keep 'FAILING_STATUSES' unchanged unless a collector exception is converted to the existing 'error' result. Add a branch preview path in '_emit' only for the 'branches' result’s 'markdown' field.

- [ ] **Step 4: Document the user-facing behavior.**

Update 'README.md' with:

~~~text
janitor branches [--all] [--json] [--no-fetch]
~~~

State that the command inventories local/remote-tracking refs and linked worktrees, fetches only the primary remote unless disabled, reports stale freshness when it cannot refresh refs, never performs branch actions, and feeds the same deterministic block used by nightly 'sweep'.

- [ ] **Step 5: Run CLI, full suite, and a real local smoke test.**

~~~bash
PYTHONPATH=. pytest -q tests/test_cli.py
PYTHONPATH=. pytest -q
PYTHONPATH=. python -m janitor.cli branches --no-fetch --json .
~~~

Expected: all tests exit 0; the smoke test emits one valid JSON envelope with 'schema_version', 'run_id', a branch review, exact base information or 'base_unavailable', and all logical branch rows. It must not modify 'CONTEXT.md', 'TODO.md', or the working tree.

- [ ] **Step 6: Commit the task.**

~~~bash
git add janitor/cli.py README.md tests/test_cli.py
git commit -m "feat: expose branch review CLI"
~~~

## Final review and verification

- [ ] Read this plan against 'docs/superpowers/specs/2026-09-11-janitor-branch-worktree-review-design.md' and confirm every design section has a task: safety/fetch (Tasks 1–2), inventory/comparison/classification (Task 2), sentinel/prompt/state (Tasks 3–4), orchestration/commit gate (Task 4), CLI/output/docs (Task 5), and acceptance tests across all tasks.
- [ ] Confirm every implementation step has concrete paths, signatures, commands, expected results, and no placeholder instructions; references to the repository TODO.md are requirements, not placeholders.
- [ ] Run 'PYTHONPATH=. pytest -q' and read the complete result.
- [ ] Run 'git diff --check' and 'git status --short --branch'; only intended committed changes may remain.
- [ ] Create a review package from the branch merge-base through 'HEAD' and dispatch the final whole-branch code reviewer before offering integration options.
- [ ] Keep the implementation worktree and feature branch until the user chooses merge, PR push, or keep-as-is; never force-push or discard work without explicit confirmation.
