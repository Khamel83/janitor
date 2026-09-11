<!-- janitor:begin:recent -->
Active focus is local integration and documentation of Janitor's branch and linked-worktree review. The feature is implemented on local `main`; remote publication and deployment remain separate operations.

### Verified Accomplishments
- Refactored `sweep_repo` to integrate the Butler auto-tidy pre-pass (purging cache litter and checkpointing stale WIP before sweeping), deleted dead `janitor/docs.py` (700+ lines removed), and confirmed 90/90 tests pass (`0f2bf86`).
- Updated Homelab systemd timers to explicit `America/Los_Angeles` schedule (3:00 AM Pacific daily sweep, 4:00 AM Pacific Sunday overview).
- Rewrote root `README.md` with full caretaker architecture, dual-cadence schedule, and CLI usage; updated `homelab.yaml` to active lifecycle (`0a5dfdf`).
- Cleaned central `docs` repository (`Khamel83/docs`): archived legacy Mintlify starter files into `archive/` and populated 80 comprehensive codebase overviews in `/Volumes/2TB_SSD/GitHub/docs/repos/` (`f9d4c5a`).
- Rebuilt `worker.py` with stdin payload streaming (`-p -`) for Gateway2000, eliminating OS `ARG_MAX` limits (`8d52783`).
- Added persistent state layer (`janitor/state.py`) with stable task ID management (`3df61ef`).
- Added `janitor/git_ops.py` with preflight guards, atomic staging/committing, and `Janitor-Run` trailer loop prevention (`4c205c2`).
- Authored caretaker design spec, adversarial review hardening, and implementation plan (`8058a4b`, `226b981`, `b296050`, `36a13d1`).
- Merged the deterministic branch/worktree review into local `main` (`780767a`), including the `janitor branches` CLI and nightly sweep integration.
- Branch review inventories local refs, cached remote-tracking refs, and linked worktrees; compares branches with the discovered default branch; and reports bounded evidence without checkout, merge, deletion, reset, push, or local-branch pruning.
- Updated `README.md`, `LLM-OVERVIEW.md`, and `TODO.md`; verified `PYTHONPATH=. pytest -q` with 159 passing tests and `ruff check janitor tests`.

The local merge has not been pushed or deployed. An unstaged `tests/test_git_ops.py` edit that existed before the merge was preserved in the working tree. Homelab timer and service configuration remains documented under `systemd/`.
<!-- janitor:end:recent -->
