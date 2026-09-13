"""Janitor CLI — fleet discovery, on-demand jobs, and structured reporting.

Subcommands::

    janitor sweep [repo ...]     regenerate CONTEXT.md / TODO.md
    janitor branches [repo ...]  report branches and linked worktrees
    janitor overview [repo ...]  regenerate LLM-OVERVIEW.md
    janitor tidy [repo ...]      purge ephemeral trash + checkpoint abandoned WIP
    janitor status [repo ...]    report git status and last-run info per repo

Target selection is identical for every subcommand:

- ``--all`` targets every git repository directly under the workspace;
- explicit repo paths target exactly those repositories;
- no arguments target the current directory when it is a git repository,
  otherwise the whole fleet.

The workspace defaults to ``/Volumes/2TB_SSD/GitHub`` and can be redirected
with ``JANITOR_WORKSPACE``; persistent state defaults to
``~/.local/state/janitor`` and can be redirected with ``JANITOR_STATE_DIR``
(used by the test suite and by cron wrappers that want a scratch state).
Both environment overrides follow the same convention as
``JANITOR_DOCS_MIRROR`` in the reconciler.

Output is either one human line per repo, ``[<status>] <repo-name>``, or —
with ``--json`` — a single JSON document on stdout::

    {"schema_version": 1, "run_id": "<run_id>", "results": [...]}

The exit code is 0 when every repo succeeded, fast-pathed (``quiet``), or
was cleanly skipped; it is 1 when any repo reported ``synthesis_failed``
or ``error``.
"""

import argparse
import fcntl
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

from janitor.branch_review import collect_branch_report, render_branch_block
from janitor.git_ops import check_preflight_guards, get_repo_status
from janitor.hygiene import (
    checkpoint_abandoned_wip,
    is_wip_stale,
    purge_ephemeral_trash,
)
from janitor.reconciler import overview_repo, sweep_repo
from janitor.state import StateManager

DEFAULT_WORKSPACE = Path("/Volumes/2TB_SSD/GitHub")
WORKSPACE_ENV = "JANITOR_WORKSPACE"
STATE_DIR_ENV = "JANITOR_STATE_DIR"

JSON_SCHEMA_VERSION = 1
# Any result carrying one of these statuses makes the whole run exit 1.
FAILING_STATUSES = ("synthesis_failed", "error")

# Keys whose value is printed below a dry-run human result line.
_DRY_RUN_PREVIEW_KEYS = ("context_md", "todo_md", "overview_md")


def discover_repos(workspace: Path) -> list[Path]:
    """Return git repositories directly under ``workspace``.

    A directory counts when it contains a ``.git`` entry; the list is
    sorted by directory name for deterministic fleet order. A missing
    workspace yields an empty list.
    """
    workspace = Path(workspace)
    if not workspace.is_dir():
        return []
    return sorted(
        (
            entry.resolve()
            for entry in workspace.iterdir()
            if (entry / ".git").exists()
        ),
        key=lambda p: p.name,
    )


def _workspace() -> Path:
    """Workspace root: ``JANITOR_WORKSPACE`` override, else the default."""
    override = os.environ.get(WORKSPACE_ENV)
    return Path(override) if override else DEFAULT_WORKSPACE


def _state_manager() -> StateManager:
    """State manager rooted at ``JANITOR_STATE_DIR`` when set."""
    state_dir = os.environ.get(STATE_DIR_ENV)
    return StateManager(Path(state_dir)) if state_dir else StateManager()


def _resolve_targets(args: argparse.Namespace) -> list[Path]:
    """Turn parsed CLI args into the concrete list of repo directories."""
    if getattr(args, "all", False):
        return discover_repos(_workspace())
    if getattr(args, "repos", None):
        return [Path(repo).expanduser().resolve() for repo in args.repos]
    cwd = Path.cwd()
    if (cwd / ".git").exists():
        return [cwd.resolve()]
    # Not run from inside a git repo: fall back to the whole fleet.
    return discover_repos(_workspace())


def _run_tidy(repo: Path, state_mgr: StateManager, run_id: str) -> dict:
    """Butler pass: purge ephemeral trash, then checkpoint stale abandoned WIP."""
    if not (repo / ".git").exists():
        return {"repo": repo.name, "status": "skipped", "reason": "not_a_git_repo"}
    purged = purge_ephemeral_trash(repo)
    checkpoint = None
    if is_wip_stale(repo):
        checkpoint = checkpoint_abandoned_wip(repo, state_mgr, run_id)
    result = {
        "repo": repo.name,
        "purged": purged,
        "purged_count": len(purged),
        "checkpoint": checkpoint,
    }
    if checkpoint:
        result["status"] = "checkpointed"
    elif purged:
        result["status"] = "cleaned"
    else:
        result["status"] = "clean"
    return result


def _run_status(repo: Path, state_mgr: StateManager) -> dict:
    """Report git status plus the most recent recorded janitor run."""
    if not (repo / ".git").exists():
        return {"repo": repo.name, "status": "skipped", "reason": "not_a_git_repo"}
    git_status = get_repo_status(repo)
    return {
        "repo": repo.name,
        "status": "ok",
        "branch": git_status["branch"],
        "sha": git_status["sha"],
        "dirty": git_status["is_dirty"],
        "last_run": state_mgr.get_last_run(repo.name),
    }


def _run_branches(repo: Path, no_fetch: bool) -> dict:
    """Collect and render a report-only branch review for ``repo``."""
    guard = check_preflight_guards(repo)
    if guard:
        return {"repo": repo.name, "status": "skipped", "reason": guard}
    branch_review = collect_branch_report(repo, fetch=not no_fetch)
    return {
        "repo": repo.name,
        "status": (
            "ok"
            if (branch_review.get("inventory") or {}).get("status", "complete")
            == "complete"
            else "incomplete"
        ),
        "branch_review": branch_review,
        "markdown": render_branch_block(branch_review),
    }


def _human_result(result: dict) -> str:
    """One ``[<status>] <repo-name>`` line with compact trailing details."""
    status = result.get("status", "error")
    name = result.get("repo", "?")
    line = f"[{status}] {name}"
    if result.get("reason"):
        line += f" ({result['reason']})"
    if status == "ok" and result.get("wrote"):
        line += f" wrote {result['wrote']}"
    if result.get("mirrored_to"):
        line += f" (mirrored to {result['mirrored_to']})"
    if "purged_count" in result and result["purged_count"]:
        line += f" purged {result['purged_count']}"
    if result.get("checkpoint"):
        ck = result["checkpoint"]
        if ck.get("wip_branch"):
            line += f" checkpoint {ck['wip_branch']}"
        if ck.get("wip_sha"):
            line += f" ({ck['wip_sha']})"
    if status == "ok" and result.get("branch"):
        state_word = "dirty" if result.get("dirty") else "clean"
        line += f" ({result['branch']} @ {result['sha'] or 'no-commits'}, {state_word})"
        last_run = result.get("last_run")
        if last_run:
            line += f", last run: {last_run.get('status')} {last_run.get('run_id', '')}".rstrip()
    return line


def _emit(results: list[dict], args: argparse.Namespace, run_id: str) -> None:
    """Print results: one JSON document (--json) or one human line per repo."""
    if getattr(args, "json", False):
        payload = {
            "schema_version": JSON_SCHEMA_VERSION,
            "run_id": run_id,
            "results": results,
        }
        print(json.dumps(payload, indent=2))
        return
    for result in results:
        print(_human_result(result))
        if getattr(args, "command", None) == "branches" and result.get("markdown"):
            print(result["markdown"])
        if result.get("status") == "dry_run":
            for key in _DRY_RUN_PREVIEW_KEYS:
                if result.get(key):
                    print(f"\n--- {key} preview: {result['repo']} ---\n{result[key]}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="janitor",
        description="Janitor: autonomous repository caretaker CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sp_sweep = subparsers.add_parser(
        "sweep", help="regenerate CONTEXT.md and TODO.md from recent git activity"
    )
    sp_sweep.add_argument(
        "repos", nargs="*", type=Path,
        help="target repositories (default: current directory, else the fleet)",
    )
    sp_sweep.add_argument(
        "--all", action="store_true",
        help="target every git repository under the workspace",
    )
    sp_sweep.add_argument(
        "--dry-run", action="store_true",
        help="print merged previews without writing or committing anything",
    )
    sp_sweep.add_argument(
        "--no-fetch", action="store_true",
        help="use cached remote-tracking refs without fetching",
    )
    sp_sweep.add_argument("--json", action="store_true", help="emit JSON on stdout")

    sp_branches = subparsers.add_parser(
        "branches", help="report branches and linked worktrees without branch actions"
    )
    sp_branches.add_argument(
        "repos", nargs="*", type=Path,
        help="target repositories (default: current directory, else the fleet)",
    )
    sp_branches.add_argument(
        "--all", action="store_true",
        help="target every git repository under the workspace",
    )
    sp_branches.add_argument(
        "--no-fetch", action="store_true",
        help="use cached remote-tracking refs without fetching",
    )
    sp_branches.add_argument("--json", action="store_true", help="emit JSON on stdout")

    sp_overview = subparsers.add_parser(
        "overview", help="regenerate LLM-OVERVIEW.md from AGENTS.md and git history"
    )
    sp_overview.add_argument(
        "repos", nargs="*", type=Path,
        help="target repositories (default: current directory, else the fleet)",
    )
    sp_overview.add_argument(
        "--all", action="store_true",
        help="target every git repository under the workspace",
    )
    sp_overview.add_argument(
        "--dry-run", action="store_true",
        help="print the synthesized overview without writing anything",
    )
    sp_overview.add_argument("--json", action="store_true", help="emit JSON on stdout")

    sp_tidy = subparsers.add_parser(
        "tidy", help="purge ephemeral trash and checkpoint abandoned WIP"
    )
    sp_tidy.add_argument(
        "repos", nargs="*", type=Path,
        help="target repositories (default: current directory, else the fleet)",
    )
    sp_tidy.add_argument(
        "--all", action="store_true",
        help="target every git repository under the workspace",
    )
    sp_tidy.add_argument("--json", action="store_true", help="emit JSON on stdout")

    sp_status = subparsers.add_parser(
        "status", help="report git status and the last recorded janitor run"
    )
    sp_status.add_argument(
        "repos", nargs="*", type=Path,
        help="target repositories (default: current directory, else the fleet)",
    )
    sp_status.add_argument(
        "--all", action="store_true",
        help="target every git repository under the workspace",
    )
    sp_status.add_argument("--json", action="store_true", help="emit JSON on stdout")

    return parser


def _main(argv: Optional[list[str]] = None) -> int:
    """Run one janitor job across its target repos; return the exit code."""
    args = _build_parser().parse_args(argv)
    # Branch reports are intentionally independent of persistent StateManager
    # state. Construct it only for commands that use the existing stateful
    # paths.
    state_mgr = None if args.command == "branches" else _state_manager()
    run_id = f"run_{int(time.time())}"

    results: list[dict] = []
    failed_syntheses = 0
    started = time.monotonic()
    deadline = started + float(os.environ.get("JANITOR_RUN_TIMEOUT", "10800" if args.command == "overview" else "2700"))
    for repo in _resolve_targets(args):
        repo_started = time.monotonic()
        try:
            if args.command in {"sweep", "overview"} and (
                time.monotonic() >= deadline or (getattr(args, "all", False) and failed_syntheses >= 3)
            ):
                result = {"repo": repo.name, "status": "error", "error": "run_stopped",
                          "reason": "deadline_or_repeated_synthesis_failure"}
            elif args.command == "branches":
                result = _run_branches(repo, no_fetch=args.no_fetch)
            elif args.command == "sweep":
                result = sweep_repo(
                    repo,
                    state_mgr,
                    run_id,
                    dry_run=getattr(args, "dry_run", False),
                    no_fetch=args.no_fetch,
                )
            elif args.command == "overview":
                result = overview_repo(
                    repo, state_mgr, dry_run=getattr(args, "dry_run", False)
                )
            elif args.command == "tidy":
                result = _run_tidy(repo, state_mgr, run_id)
            elif args.command == "status":
                result = _run_status(repo, state_mgr)
            else:  # pragma: no cover - argparse required=True prevents this
                result = {
                    "repo": repo.name,
                    "status": "error",
                    "error": f"unknown command {args.command!r}",
                }
        except Exception as exc:
            # One broken repo must never abort the fleet run.
            result = {
                "repo": repo.name,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }
        results.append(result)
        if args.command in {"sweep", "overview", "tidy"} and not getattr(args, "dry_run", False):
            state_mgr.record_run(repo.name, result["status"], run_id,
                                 failure=str(result.get("raw") or result.get("error") or "unknown")
                                 if result["status"] in FAILING_STATUSES else None)
            receipt = dict(state_mgr.get_last_run(repo.name), repo=repo.name,
                           command=args.command, elapsed_seconds=round(time.monotonic() - repo_started, 3))
            with (state_mgr.state_dir / "runs.jsonl").open("a") as log:
                log.write(json.dumps(receipt) + "\n")
            print(f"[janitor] {repo.name}: {result['status']} ({receipt['elapsed_seconds']}s)", file=sys.stderr, flush=True)
        if result.get("status") == "synthesis_failed":
            failed_syntheses += 1
        elif result.get("status") in {"committed", "written", "ok", "dry_run"}:
            failed_syntheses = 0

    _emit(results, args, run_id)
    return 1 if any(r.get("status") in FAILING_STATUSES for r in results) else 0


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command not in {"sweep", "overview", "tidy"} or getattr(args, "dry_run", False):
        return _main(argv)
    root = Path(os.environ.get(STATE_DIR_ENV, str(Path.home() / ".local/state/janitor")))
    root.mkdir(parents=True, exist_ok=True)
    with (root / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("[janitor] Another mutating run is active; not starting a duplicate.", file=sys.stderr)
            return 1
        def stop(signum, frame):
            raise SystemExit(128 + signum)
        previous = signal.signal(signal.SIGTERM, stop)
        try:
            return _main(argv)
        finally:
            signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    sys.exit(main())
