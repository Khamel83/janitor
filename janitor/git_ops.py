"""Git operations for the janitor repository caretaker.

All functions operate on a local git repository via the git CLI and are
safe to call on foreign/remote repositories: nothing here pushes, hard-
resets away from the pre-existing local HEAD, or mutates state outside
the repository.

Safety properties:
- Preflight guards skip repos that are mid-operation (merge, rebase,
  bisect, cherry-pick), locked (``.git/index.lock``), or on a detached
  HEAD, so janitor never interferes with human or tool work.
- Loop prevention: every janitor commit carries a ``Janitor-Run:``
  trailer, and ``has_24h_activity`` excludes such commits so janitor
  never sees its own output as reason to act again.
- Atomic commits: only the exact authorized file set is ever staged and
  committed; anything else staged causes the whole operation to abort
  and the index to be reset.
"""

import subprocess
from pathlib import Path
from typing import Optional, Tuple


def _sh(
    cmd: list[str], cwd: Path, timeout: float = 10.0, env: dict[str, str] | None = None
) -> str:
    """Run a git command and return trimmed stdout ('' on any failure)."""
    try:
        res = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except (subprocess.SubprocessError, OSError, subprocess.TimeoutExpired):
        return ""
    if res.returncode != 0:
        return ""
    return res.stdout.strip()


def resolve_git_metadata(repo_dir: Path) -> tuple[Path, Path] | None:
    """Return resolved git worktree and common git directories for a repo."""
    git_dir = _sh(["git", "rev-parse", "--git-dir"], repo_dir)
    common_dir = _sh(["git", "rev-parse", "--git-common-dir"], repo_dir)
    if not git_dir or not common_dir:
        return None

    def absolute(path: str) -> Path:
        git_path = Path(path)
        return git_path if git_path.is_absolute() else (Path(repo_dir) / git_path).resolve()

    return absolute(git_dir), absolute(common_dir)


def check_preflight_guards(repo_dir: Path) -> Optional[str]:
    """Return a guard name (str) when the repo must be skipped, else None.

    Guards, in check order: not a git repo, index lock held, merge or
    cherry-pick in progress, rebase in progress, bisect in progress,
    detached HEAD.
    """
    if not repo_dir.exists():
        return "not_a_git_repo"

    toplevel = _sh(["git", "rev-parse", "--show-toplevel"], repo_dir)
    if not toplevel:
        return "not_a_git_repo"
    if Path(toplevel).resolve() != repo_dir.resolve():
        return "not_a_git_repo"

    git_metadata = resolve_git_metadata(repo_dir)
    if not git_metadata:
        return "not_a_git_repo"

    git_dir, common_dir = git_metadata
    checked_dirs: list[Path] = []
    for d in (git_dir, common_dir):
        if d not in checked_dirs:
            checked_dirs.append(d)

    for candidate in checked_dirs:
        if (candidate / "index.lock").exists():
            return "git_index_locked"

    for candidate in checked_dirs:
        if (candidate / "MERGE_HEAD").exists() or (candidate / "CHERRY_PICK_HEAD").exists():
            return "merge_in_progress"

    for candidate in checked_dirs:
        if (candidate / "rebase-merge").exists() or (candidate / "rebase-apply").exists():
            return "rebase_in_progress"

    for candidate in checked_dirs:
        if (candidate / "BISECT_LOG").exists():
            return "bisect_in_progress"

    # Detached HEAD: `git symbolic-ref -q HEAD` exits non-zero with no
    # output when HEAD points directly at a commit instead of a branch.
    head_ref = _sh(["git", "symbolic-ref", "-q", "HEAD"], repo_dir)
    if not head_ref:
        return "detached_head"
    return None


def get_repo_status(repo_dir: Path) -> dict:
    """Return porcelain status, dirty flag, branch name, and short SHA."""
    status_raw = _sh(["git", "status", "--porcelain"], repo_dir)
    branch = _sh(["git", "symbolic-ref", "--short", "HEAD"], repo_dir) or "HEAD"
    sha = _sh(["git", "rev-parse", "--short", "HEAD"], repo_dir)
    return {
        "is_dirty": bool(status_raw),
        "porcelain": status_raw,
        "branch": branch,
        "sha": sha,
    }


def has_24h_activity(repo_dir: Path) -> Tuple[bool, str, str]:
    """Report non-janitor activity in the last 24h.

    Returns ``(has_activity, recent_log, recent_diff)``. Commits carrying
    a ``Janitor-Run:`` trailer are excluded from the log so janitor never
    triggers on its own sweep commits (loop prevention).
    """
    recent_log = _sh(
        [
            "git",
            "log",
            "--since=24.hours",
            "--invert-grep",
            "--grep=^Janitor-Run:",
            "--pretty=format:%h %s %cI",
        ],
        repo_dir,
    )

    # The latest commit may be Janitor's own output. Anchor the diff to the
    # latest non-Janitor commit so a Janitor-Run commit does not change the
    # normal sweep evidence while real activity remains visible.
    latest_activity = _sh(
        [
            "git",
            "log",
            "--since=24.hours",
            "-n",
            "1",
            "--invert-grep",
            "--grep=^Janitor-Run:",
            "--format=%H",
        ],
        repo_dir,
    )
    recent_diff = ""
    if latest_activity:
        parent = _sh(
            ["git", "rev-parse", "--verify", f"{latest_activity}^"], repo_dir
        )
        if parent:
            recent_diff = _sh(
                ["git", "diff", f"{parent}..{latest_activity}", "--stat"],
                repo_dir,
            )

    has_activity = bool(recent_log)
    return has_activity, recent_log, recent_diff


def atomic_stage_and_commit(
    repo_dir: Path, files: list[str], message: str, run_id: str
) -> bool:
    """Stage exactly ``files`` and commit with a ``Janitor-Run`` trailer.

    Returns True only when every staged path is in ``files`` and the
    commit succeeds. Any failure (bad add, extra staged paths, empty
    stage) resets the index and returns False — nothing is committed
    unless the authorized set was committed atomically.
    """
    allowed = set(files)
    add_res = subprocess.run(
        ["git", "add"] + files, cwd=repo_dir, capture_output=True
    )
    if add_res.returncode != 0:
        subprocess.run(["git", "reset"], cwd=repo_dir, capture_output=True)
        return False

    # Verify ONLY the authorized files are staged.
    staged = _sh(["git", "diff", "--cached", "--name-only"], repo_dir).splitlines()
    if not staged or any(f not in allowed for f in staged):
        subprocess.run(["git", "reset"], cwd=repo_dir, capture_output=True)
        return False

    full_message = f"{message}\n\nJanitor-Run: {run_id}"
    commit_res = subprocess.run(
        ["git", "commit", "-m", full_message], cwd=repo_dir, capture_output=True
    )
    if commit_res.returncode != 0:
        subprocess.run(["git", "reset"], cwd=repo_dir, capture_output=True)
        return False
    return True
