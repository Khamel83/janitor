"""Documentation reconciler — sweep and overview jobs.

Regenerates CONTEXT.md/TODO.md from recent git activity ("sweep"), and
LLM-OVERVIEW.md from AGENTS.md + git history ("overview"). Uses
openrouter/free via janitor.worker — same $0 budget and rate limiting as
every other job in this package. No hardcoded repo paths: pass a
project_dir, or run from inside the repo.
"""

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from janitor.worker import call_free, extract_structured

SWEEP_SYSTEM = "You are a documentation reconciler. Be terse, technical, factual. No conversational filler or cheerleading."

SWEEP_PROMPT = """Update CONTEXT.md and TODO.md for this repository based on recent git activity, dirty/untracked files, and current file contents.

Respond with valid JSON only, exactly two keys: "context_md" and "todo_md".

REPOSITORY: {repo_name} (branch: {branch}, HEAD: {current_sha})
TIMESTAMP: {timestamp}

DIRTY / UNTRACKED FILES:
{git_status}

RECENT COMMITS (last 5):
{recent_log}

RECENT DIFF (HEAD~1..HEAD):
{recent_diff}

EXISTING CONTEXT.MD:
{curr_context}

EXISTING TODO.MD:
{curr_todo}

RULES:
1. CONTEXT.md:
   - Active Focus: 1-2 factual sentences on what is actively being built/fixed.
   - Recent Accomplishments: Bulleted list tied directly to verified commits and diffs.
   - Working Tree State: Note any dirty/uncommitted files observed above.
   - Watch Items: Mention known operational traps, failing checks, or stranded migrations.
2. TODO.md:
   - Retain uncompleted items.
   - Mark completed ([x]) items proven done by commits or diff.
   - Add new tactical items discovered from recent uncommitted changes or stated blockers.
"""

OVERVIEW_SYSTEM = "You are a documentation reconciler producing a compressed architectural briefing for AI agents. Be dense and factual."

OVERVIEW_PROMPT = """Update this repository's LLM-OVERVIEW.md — a compressed, high-density architectural briefing for AI agents entering the repository.

Respond with only the raw markdown body for LLM-OVERVIEW.md. No JSON, no code fences, no introductory notes.

REPOSITORY: {repo_name}
DATE: {date}

AGENTS.MD (CONSTITUTION & VOCABULARY):
{agents_text}

RECENT COMMITS (last 20):
{recent_commits}

CURRENT LLM-OVERVIEW.MD:
{curr_overview}

RULES:
1. Header MUST be:
   # LLM-OVERVIEW — {repo_name}
   > Current compressed briefing. Updated {date}. Agent behavior is defined in `AGENTS.md`. This derived file is not an independent authority.
2. If AGENTS.md names words to avoid or retired projects/features, purge them or note them as retired history. Do not describe planned/stranded work as active production unless backed by evidence in the commit log.
3. Required sections: ## What this repo is / ## What is actually built / ## Canonical entry points.
4. Keep it dense (~80-150 lines).
"""


def _sh(args: list[str], cwd: Path) -> str:
    r = subprocess.run(args, capture_output=True, text=True, cwd=str(cwd), timeout=10)
    return r.stdout.strip() if r.returncode == 0 else ""


def _project_dir(project_dir: Optional[str]) -> Path:
    return Path(project_dir).resolve() if project_dir else Path.cwd().resolve()


def ensure_claude_symlink(project_dir: Optional[str] = None) -> bool:
    """Point CLAUDE.md at AGENTS.md if AGENTS.md exists. No-op otherwise."""
    repo = _project_dir(project_dir)
    agents = repo / "AGENTS.md"
    claude = repo / "CLAUDE.md"
    if not agents.exists():
        return False
    if claude.is_symlink() and claude.resolve() == agents.resolve():
        return False
    if claude.exists() or claude.is_symlink():
        claude.unlink()
    claude.symlink_to("AGENTS.md")
    return True


def sweep_docs(project_dir: Optional[str] = None, dry_run: bool = False) -> dict:
    """Regenerate CONTEXT.md and TODO.md from recent git activity.

    Never commits into a dirty working tree — writes CONTEXT.draft.md /
    TODO.draft.md instead. On a clean main/master branch where the only
    diff is CONTEXT.md/TODO.md themselves, commits directly.
    """
    repo = _project_dir(project_dir)
    if not (repo / ".git").exists():
        return {"status": "not_a_repo"}

    ensure_claude_symlink(repo)

    status = _sh(["git", "status", "--porcelain"], repo)
    commits_24h = _sh(["git", "log", "--since=24.hours", "--oneline"], repo)
    if not status and not commits_24h:
        return {"status": "quiet"}

    branch = _sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo)
    current_sha = _sh(["git", "rev-parse", "--short", "HEAD"], repo)
    recent_log = _sh(["git", "log", "-n", "5", "--pretty=format:%h %s (%cr)"], repo)
    recent_diff = _sh(["git", "diff", "HEAD~1..HEAD", "--stat"], repo)

    context_file = repo / "CONTEXT.md"
    todo_file = repo / "TODO.md"
    curr_context = context_file.read_text() if context_file.exists() else "(None)"
    curr_todo = todo_file.read_text() if todo_file.exists() else "(None)"

    prompt = SWEEP_PROMPT.format(
        repo_name=repo.name,
        branch=branch or "(detached)",
        current_sha=current_sha,
        timestamp=datetime.now(timezone.utc).isoformat(),
        git_status=status or "(Clean working tree)",
        recent_log=recent_log or "(No commits found)",
        recent_diff=recent_diff or "(No commit diff available)",
        curr_context=curr_context,
        curr_todo=curr_todo,
    )

    result = extract_structured(
        prompt,
        system=SWEEP_SYSTEM,
        schema_hint='{"context_md": str, "todo_md": str}',
    )
    if "context_md" not in result or "todo_md" not in result:
        return {"status": "synthesis_failed", "raw": result}

    new_context = result["context_md"].strip() + "\n"
    new_todo = result["todo_md"].strip() + "\n"

    if dry_run:
        return {"status": "dry_run", "context_md": new_context, "todo_md": new_todo}

    is_dirty = bool(status)
    target_context = repo / ("CONTEXT.draft.md" if is_dirty else "CONTEXT.md")
    target_todo = repo / ("TODO.draft.md" if is_dirty else "TODO.md")
    target_context.write_text(new_context)
    target_todo.write_text(new_todo)

    committed = False
    if not is_dirty and branch in ("main", "master"):
        diff_files = [f for f in _sh(["git", "diff", "--name-only"], repo).splitlines() if f]
        untracked = [
            line[3:] for line in _sh(["git", "status", "--porcelain"], repo).splitlines()
            if line.startswith("?? ")
        ]
        allowed = {"CONTEXT.md", "TODO.md"}
        if diff_files and all(f in allowed for f in diff_files) and not untracked:
            subprocess.run(["git", "add", "CONTEXT.md", "TODO.md"], cwd=str(repo), check=True)
            subprocess.run(
                ["git", "commit", "-m", f"docs(janitor): sweep CONTEXT.md and TODO.md for {current_sha} [skip ci]"],
                cwd=str(repo), check=True,
            )
            committed = True

    return {
        "status": "ok",
        "wrote": [str(target_context.relative_to(repo)), str(target_todo.relative_to(repo))],
        "committed": committed,
        "dirty_tree": is_dirty,
    }


def generate_overview(project_dir: Optional[str] = None, dry_run: bool = False) -> dict:
    """Regenerate LLM-OVERVIEW.md from AGENTS.md and recent git history."""
    repo = _project_dir(project_dir)
    if not (repo / ".git").exists():
        return {"status": "not_a_repo"}

    ensure_claude_symlink(repo)

    agents_file = repo / "AGENTS.md"
    overview_file = repo / "docs" / "LLM-OVERVIEW.md"
    if not overview_file.parent.exists():
        overview_file = repo / "LLM-OVERVIEW.md"

    agents_text = agents_file.read_text() if agents_file.exists() else "(No AGENTS.md present)"
    curr_overview = overview_file.read_text() if overview_file.exists() else "(No previous LLM-OVERVIEW.md)"
    recent_commits = _sh(["git", "log", "-n", "20", "--oneline"], repo)

    prompt = OVERVIEW_PROMPT.format(
        repo_name=repo.name,
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        agents_text=agents_text,
        recent_commits=recent_commits or "(No commits found)",
        curr_overview=curr_overview,
    )

    new_overview = call_free(prompt, system=OVERVIEW_SYSTEM, max_tokens=2048, timeout=45).strip() + "\n"

    if dry_run:
        return {"status": "dry_run", "overview_md": new_overview, "path": str(overview_file.relative_to(repo))}

    overview_file.parent.mkdir(parents=True, exist_ok=True)
    overview_file.write_text(new_overview)

    return {"status": "ok", "wrote": str(overview_file.relative_to(repo))}
