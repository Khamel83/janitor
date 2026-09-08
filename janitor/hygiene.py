"""Butler auto-tidy and zero-data-loss WIP checkpointing for janitor.

Two responsibilities:

1. ``purge_ephemeral_trash`` / ``is_wip_stale`` keep working trees tidy
   (cache dirs, macOS/editor droppings) and detect repos whose working
   files have not changed within ``stale_hours`` (candidates for
   abandoned-work checkpointing).

2. ``checkpoint_abandoned_wip`` / ``prune_expired_wip_branches`` safely
   move uncommitted, non-secret changes from a dirty repo onto a
   strictly local ``auto-wip/<timestamp>`` branch and then restore the
   original branch to its *pre-existing local HEAD* (captured up front
   via ``git rev-parse HEAD`` — never ``origin/main`` or any remote).

Safety properties:

- Zero data loss: the restore target is the captured local HEAD SHA, so
  the final ``git reset --hard`` can only ever return the repo to the
  exact commit it was already on. Nothing is fetched, pushed, or
  compared against a remote.
- Secret denylist: ``git add`` runs with exclusion pathspecs and the
  staged set is re-checked in Python, so ``.env*``, ``*.pem``, ``*.key``
  and ``*credential*`` paths at any depth are never staged or committed;
  they stay untouched and uncommitted in the working tree.
- Auto-wip branches are local-only and never pushed.
- Trash purge removes only cache/editor droppings, never source files,
  and never descends into ``.git``.
"""

import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from janitor.state import StateManager

# Cache/editor droppings removed by purge_ephemeral_trash.
TRASH_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
TRASH_FILE_NAMES = {".DS_Store", "Thumbs.db"}
TRASH_FILE_SUFFIXES = {".pyc", ".swp"}

# Pathspec exclusions passed to ``git add -A --`` so secret material is
# never staged or committed:
# - ``.env*`` needs glob magic: without it git matches the whole path, so a
#   literal-prefix pattern only excludes repo-root files. With
#   ``:(exclude,glob)`` the pattern is component-wise and ``**/`` spans any
#   depth, covering deep ``.env.local`` files and whole ``.env*/`` dirs.
# - ``id_rsa*`` is another literal-prefix pattern, same reasoning.
# - ``*.pem`` / ``*.key`` / ``*credential*`` start with a wildcard that
#   spans directory separators, so plain exclusion pathspecs reach any depth.
SECRET_STAGE_EXCLUSIONS = [
    ":(exclude,glob)**/.env*",
    ":(exclude,glob)**/.env*/**",
    ":(exclude,glob)**/id_rsa*",
    ":!*.pem",
    ":!*.key",
    ":!*credential*",
]


def _run(repo_dir: Path, *cmd: str) -> subprocess.CompletedProcess:
    """Run ``cmd`` in ``repo_dir``; caller inspects returncode."""
    return subprocess.run(list(cmd), cwd=repo_dir, capture_output=True, text=True)


def _sh(repo_dir: Path, *cmd: str) -> str:
    """Run ``cmd`` and return trimmed stdout, or '' on any failure."""
    res = _run(repo_dir, *cmd)
    if res.returncode != 0:
        return ""
    return res.stdout.strip()


def _non_git_walk(repo_dir: Path):
    """Yield ``os.walk`` tuples with the ``.git`` directory pruned."""
    for root, dirs, files in os.walk(repo_dir, topdown=True):
        if ".git" in dirs:
            dirs.remove(".git")
        yield root, dirs, files


def purge_ephemeral_trash(repo_dir: Path) -> list[str]:
    """Remove cache dirs and editor droppings under ``repo_dir``.

    Deletes ``__pycache__``/``.pytest_cache``/``.mypy_cache``/
    ``.ruff_cache`` directories plus ``.DS_Store``, ``Thumbs.db``,
    ``*.pyc``, ``*.swp`` and ``*~`` files. Never descends into ``.git``
    and never touches source files. Returns the list of removed paths.
    """
    purged: list[str] = []
    if not repo_dir.is_dir():
        return purged

    for root, dirs, files in _non_git_walk(repo_dir):
        for dir_name in list(dirs):
            if dir_name not in TRASH_DIR_NAMES:
                continue
            target = Path(root) / dir_name
            shutil.rmtree(target, ignore_errors=True)
            if not target.exists():
                purged.append(str(target))
                dirs.remove(dir_name)

        for file_name in files:
            is_trash = (
                file_name in TRASH_FILE_NAMES
                or file_name.endswith(tuple(TRASH_FILE_SUFFIXES))
                or file_name.endswith("~")
            )
            if not is_trash:
                continue
            target = Path(root) / file_name
            try:
                target.unlink()
                purged.append(str(target))
            except OSError:
                pass
    return purged


def is_wip_stale(repo_dir: Path, stale_hours: float = 6.0) -> bool:
    """Return True when every non-git file is older than ``stale_hours``.

    A repo whose working files have all gone quiet for the window is a
    candidate for abandoned-WIP checkpointing. Files inside ``.git`` are
    ignored; a repo with no working files is never stale.
    """
    cutoff = time.time() - (stale_hours * 3600.0)
    most_recent_mtime = 0.0
    for root, _dirs, files in _non_git_walk(repo_dir):
        for file_name in files:
            try:
                mtime = (Path(root) / file_name).stat().st_mtime
            except OSError:
                continue
            if mtime > most_recent_mtime:
                most_recent_mtime = mtime
    return most_recent_mtime > 0 and most_recent_mtime < cutoff


def _is_secret_path(path: str) -> bool:
    """True when any path component matches the secret denylist.

    Mirrors ``SECRET_STAGE_EXCLUSIONS`` so pre-staged secrets can be
    detected from ``git diff --cached --name-only`` output.
    """
    return any(
        part.startswith(".env")
        or part.startswith("id_rsa")
        or part.endswith(".pem")
        or part.endswith(".key")
        or "credential" in part
        for part in path.split("/")
    )


def _restore_failed_checkpoint(
    repo_dir: Path, original_branch: str, wip_branch: str
) -> None:
    """Abort a checkpoint that never committed: keep every byte of work.

    Unstages anything (never destroying worktree content), returns to the
    original branch, and drops the empty ``wip_branch``. Dirty changes
    stay in the working tree exactly as the user left them.
    """
    _run(repo_dir, "git", "reset", "-q")
    _run(repo_dir, "git", "checkout", original_branch)
    _run(repo_dir, "git", "branch", "-D", wip_branch)


def checkpoint_abandoned_wip(
    repo_dir: Path, state_mgr: StateManager, run_id: str
) -> Optional[dict]:
    """Commit abandoned non-secret changes to ``auto-wip/<timestamp>``.

    Returns a dict describing the checkpoint, or None when there was
    nothing to checkpoint or the repo could not be checkpointed safely
    (detached HEAD, checkpoint branch creation failure, empty stage).

    On success the original branch is restored to its pre-existing local
    HEAD (captured before any checkout) with a clean tracked state, the
    non-secret dirt is committed on the local ``auto-wip/<timestamp>``
    branch with a ``Janitor-Run: <run_id>`` trailer, and the branch is
    recorded in ``state_mgr`` for later pruning.
    """
    # Capture the pre-existing local HEAD *before* anything moves: this is
    # the only SHA we ever reset --hard to. Never origin/main.
    branch_res = _run(repo_dir, "git", "symbolic-ref", "--short", "HEAD")
    if branch_res.returncode != 0:
        return None  # detached HEAD
    original_branch = branch_res.stdout.strip()

    base_sha = _sh(repo_dir, "git", "rev-parse", "HEAD")
    if not base_sha:
        return None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    wip_branch = f"auto-wip/{stamp}"

    if _run(repo_dir, "git", "checkout", "-b", wip_branch).returncode != 0:
        return None

    # Stage everything except secret paths (local-only; never pushed).
    add_res = _run(repo_dir, "git", "add", "-A", "--", *SECRET_STAGE_EXCLUSIONS)
    if add_res.returncode != 0:
        _restore_failed_checkpoint(repo_dir, original_branch, wip_branch)
        return None

    # A human may have staged a secret before janitor ran; unstage exactly
    # those paths rather than ever committing them.
    staged_secrets = [
        p
        for p in _sh(repo_dir, "git", "diff", "--cached", "--name-only").splitlines()
        if _is_secret_path(p)
    ]
    if staged_secrets:
        if _run(
            repo_dir, "git", "reset", "-q", "--", *staged_secrets
        ).returncode != 0:
            _restore_failed_checkpoint(repo_dir, original_branch, wip_branch)
            return None
        still_staged = [
            p
            for p in _sh(
                repo_dir, "git", "diff", "--cached", "--name-only"
            ).splitlines()
            if _is_secret_path(p)
        ]
        if still_staged:
            _restore_failed_checkpoint(repo_dir, original_branch, wip_branch)
            return None

    message = (
        f"wip(janitor): auto-checkpoint uncommitted work left on {stamp}\n\n"
        f"Janitor-Run: {run_id}"
    )
    commit_res = _run(repo_dir, "git", "commit", "-m", message)
    if commit_res.returncode != 0:
        # Nothing to commit — leave the dirty work exactly where it was.
        _restore_failed_checkpoint(repo_dir, original_branch, wip_branch)
        return None

    wip_sha = _sh(repo_dir, "git", "rev-parse", "HEAD")
    if not wip_sha:
        return None

    # Return to the original branch; git restores tracked files to base.
    if _run(repo_dir, "git", "checkout", original_branch).returncode != 0:
        return None

    # Restore the branch ref to the captured pre-existing local HEAD.
    # Untracked leftovers (secrets) survive reset --hard untouched.
    if _run(repo_dir, "git", "reset", "--hard", base_sha).returncode != 0:
        return None

    state_mgr.track_wip_branch(repo_dir.name, wip_branch, wip_sha)
    return {
        "wip_branch": wip_branch,
        "wip_sha": wip_sha,
        "base_sha": base_sha,
        "original_branch": original_branch,
    }


def prune_expired_wip_branches(
    repo_dir: Path, state_mgr: StateManager, max_age_days: int = 30
) -> list[str]:
    """Delete local ``auto-wip/*`` branches older than ``max_age_days``.

    Expiry comes from the timestamps recorded in ``state_mgr`` when each
    branch was checkpointed. Only local branches whose recorded name
    still carries the ``auto-wip/`` prefix are eligible (defense against
    tampered/corrupt state), and deletion uses ``git branch -D`` which
    never touches remotes. Returns the names successfully deleted.
    """
    pruned: list[str] = []
    for branch_name in state_mgr.get_expired_wip_branches(
        repo_dir.name, max_age_days=max_age_days
    ):
        if not branch_name.startswith("auto-wip/"):
            continue
        res = _run(repo_dir, "git", "branch", "-D", branch_name)
        if res.returncode == 0:
            pruned.append(branch_name)
    return pruned
