# Task 2 report: morning evidence collector and CLI

## Result

Implemented deterministic GitHub PR evidence collection and `janitor publish` / `janitor reviews` CLI dispatch. The collector writes private atomic JSON, Markdown, and final-agent prompt snapshots, preserves original base-SHA documents, retains all review/comment evidence, classifies only exact current-head trusted bot reviews, rereads heads, distinguishes absent/pending/failed checks, and attaches local publication intent/receipt references.

## TDD evidence

- RED: `python3 -m unittest discover -s tests -p 'test_reviews.py'` failed with `ModuleNotFoundError: No module named 'janitor.reviews'` before production code existed.
- RED: the real GitHub empty-check shape regression failed because `pending` with `total_count: 0` was initially classified as pending rather than absent.
- GREEN: collector tests: 11 passed.
- GREEN: PR CLI tests: 5 passed, including collector-lock overlap and limit bounds.
- Regression: full suite passed, 213 tests in 42.533 seconds (the final lock-only test then passed in the focused 5-test CLI run).
- Static checks: Ruff lint passed on all four owned files and `git diff --check` passed.

## Files

- `janitor/reviews.py`
- `janitor/cli.py`
- `tests/test_reviews.py`
- `tests/test_pr_cli.py`

## Self-review

- GitHub list endpoints use the existing bounded `GitHub.pages`; one `GitHub` instance supplies the existing 2700-second total API deadline.
- No collector API mutation or model call exists. Review and repository prose are stored as evidence and never interpreted as instructions.
- Publication status exit handling matches the specified normal/nonzero states, including `state_error` on otherwise published/existing results.
- Publication uses the existing mutating lock and SIGTERM restoration. Collection uses an independent nonblocking lock.
- A controller-run read-only smoke for Janitor PR #4 returned `complete: true`, exact-head `reviewed-pass`, and private snapshot `20260913T083526.183729Z`. It exposed the empty-check shape fixed by the focused regression.

## Concerns

- Missing file patches intentionally make the packet incomplete even though GitHub commonly omits patches for binary or oversized diffs; this is fail-closed per the approved contract.
- The collector reports a bot pass as `reviewed-pass`, never merge approval. The final prompt gives recommendations only and explicitly forbids automatic merge.
