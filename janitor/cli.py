"""janitor CLI — on-demand entry point for jobs that don't need a Claude Code hook.

    janitor sweep [repo ...]     # regenerate CONTEXT.md / TODO.md
    janitor overview [repo ...]  # regenerate LLM-OVERVIEW.md

Both default to the current directory. All other jobs (event recording,
test gaps, code smells, ...) run via hooks/cron.sh, not this CLI.
"""

import argparse
import sys
from pathlib import Path

from janitor.docs import generate_overview, sweep_docs


def _run(fn, repos: list[Path], dry_run: bool, label: str) -> int:
    targets = repos or [Path.cwd()]
    exit_code = 0
    for repo in targets:
        result = fn(project_dir=str(repo), dry_run=dry_run)
        status = result.get("status")
        if status == "quiet":
            print(f"[+] {repo}: quiet for 24h and clean, nothing to {label}.")
        elif status == "not_a_repo":
            print(f"[-] {repo}: not a git repository, skipping.")
        elif status == "dry_run":
            for key, val in result.items():
                if key in ("context_md", "todo_md", "overview_md"):
                    print(f"\n--- [DRY RUN {key}: {repo}] ---\n{val}")
        elif status == "ok":
            print(f"[+] {repo}: wrote {result.get('wrote')}" + (" (committed)" if result.get("committed") else ""))
        else:
            print(f"[!] {repo}: {label} failed: {result}", file=sys.stderr)
            exit_code = 1
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(prog="janitor", description="Janitor: background intelligence + docs reconciler")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sweep_cmd = subparsers.add_parser("sweep", help="Regenerate CONTEXT.md and TODO.md")
    sweep_cmd.add_argument("repos", nargs="*", type=Path)
    sweep_cmd.add_argument("--dry-run", action="store_true")

    overview_cmd = subparsers.add_parser("overview", help="Regenerate LLM-OVERVIEW.md")
    overview_cmd.add_argument("repos", nargs="*", type=Path)
    overview_cmd.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    if args.command == "sweep":
        return _run(sweep_docs, args.repos, args.dry_run, "sweep")
    elif args.command == "overview":
        return _run(generate_overview, args.repos, args.dry_run, "generate overview for")
    return 1


if __name__ == "__main__":
    sys.exit(main())
