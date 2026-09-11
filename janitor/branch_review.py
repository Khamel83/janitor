"""Deterministic, report-only branch and worktree review."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FETCH_TIMEOUT_SECONDS = 15.0
GIT_QUERY_TIMEOUT_SECONDS = 10.0
MAX_CHANGED_PATHS = 5
MAX_RECENT_SUBJECTS = 5
MAX_DOC_EVIDENCE_CHARS = 1000
ACTIVE_DAYS = 7
AGING_DAYS = 30
BRANCH_SENTINEL_TAG = "branches"

_FETCH_SSH_COMMAND = "ssh -o BatchMode=yes -o ConnectTimeout=5"
_SENTINEL_LIKE_COMMENT = re.compile(
    r"<!--\s*janitor\s*:\s*(?:begin|end)\s*:\s*[^>]*-->",
    re.IGNORECASE | re.DOTALL,
)
_CLASSIFICATION_RANK = {
    "abandoned_auto_wip": 0,
    "active": 1,
    "aging": 2,
    "stale": 3,
}
_FLAG_ORDER = (
    "dirty_worktree",
    "stale_unmerged_ahead",
    "unattached_local_branch",
    "abandoned_auto_wip",
    "missing_worktree",
    "comparison_unknown",
)


@dataclass(frozen=True)
class _GitResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def _run_git(
    repo_dir: Path,
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = GIT_QUERY_TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
) -> _GitResult:
    """Run one bounded Git query and turn ordinary failures into data."""
    try:
        result = subprocess.run(
            args,
            cwd=cwd or repo_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return _GitResult(124, stdout, stderr, True)
    except OSError as exc:
        return _GitResult(127, "", str(exc), False)
    return _GitResult(
        result.returncode, result.stdout or "", result.stderr or "", False
    )


def _fetch_primary_remote(repo_dir: Path, *, enabled: bool) -> dict:
    remotes = _run_git(repo_dir, ["git", "remote"])
    remote_names = sorted(
        {line.strip() for line in remotes.stdout.splitlines() if line.strip()}
    )
    if "origin" in remote_names:
        primary = "origin"
    else:
        cached_default_remotes = [
            remote
            for remote in remote_names
            if _cached_remote_default_ref(repo_dir, remote) is not None
        ]
        primary = (
            cached_default_remotes[0]
            if cached_default_remotes
            else (remote_names[0] if remote_names else None)
        )

    if primary is None:
        return {
            "status": "no_remote",
            "remote": None,
            "attempted": False,
            "timed_out": False,
            "reason": "no remote configured",
        }
    if not enabled:
        return {
            "status": "not_attempted",
            "remote": primary,
            "attempted": False,
            "timed_out": False,
            "reason": "fetch disabled",
        }

    env = os.environ.copy()
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_SSH_COMMAND": _FETCH_SSH_COMMAND,
        }
    )
    result = _run_git(
        repo_dir,
        ["git", "fetch", "--prune", primary],
        timeout=FETCH_TIMEOUT_SECONDS,
        env=env,
    )
    if result.returncode == 0:
        return {
            "status": "fetched",
            "remote": primary,
            "attempted": True,
            "timed_out": False,
        }
    reason = result.stderr.strip() or "fetch failed"
    if result.timed_out:
        reason = "fetch timed out"
    return {
        "status": "fetch_failed",
        "remote": primary,
        "attempted": True,
        "timed_out": result.timed_out,
        "reason": reason,
    }


def _resolve_ref(repo_dir: Path, ref: str) -> str | None:
    result = _run_git(repo_dir, ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"])
    if result.returncode != 0:
        return None
    sha = result.stdout.strip().splitlines()
    return sha[0] if sha else None


def _cached_remote_default_ref(repo_dir: Path, remote: str) -> str | None:
    """Return a usable cached symbolic default ref for ``remote``."""
    result = _run_git(
        repo_dir,
        ["git", "symbolic-ref", "-q", f"refs/remotes/{remote}/HEAD"],
    )
    if result.returncode != 0:
        return None
    symbolic_ref = result.stdout.strip().splitlines()
    if not symbolic_ref:
        return None
    symbolic_ref = symbolic_ref[0]
    if not symbolic_ref.startswith("refs/"):
        symbolic_ref = f"refs/remotes/{symbolic_ref}"
    prefix = f"refs/remotes/{remote}/"
    if not symbolic_ref.startswith(prefix):
        return None
    branch = symbolic_ref[len(prefix) :]
    if not branch or branch == "HEAD":
        return None
    return symbolic_ref if _resolve_ref(repo_dir, symbolic_ref) else None


def _discover_base(repo_dir: Path, primary_remote: str | None) -> dict:
    candidates: list[tuple[str, str]] = []
    if primary_remote:
        symbolic_ref = _cached_remote_default_ref(repo_dir, primary_remote)
        if symbolic_ref:
            prefix = f"refs/remotes/{primary_remote}/"
            candidates.append((symbolic_ref[len(prefix) :], symbolic_ref))
        candidates.extend(
            (
                branch,
                f"refs/remotes/{primary_remote}/{branch}",
            )
            for branch in ("main", "master")
        )
    candidates.extend((branch, f"refs/heads/{branch}") for branch in ("main", "master"))

    seen: set[str] = set()
    for branch, ref in candidates:
        if ref in seen:
            continue
        seen.add(ref)
        sha = _resolve_ref(repo_dir, ref)
        if sha:
            return {
                "status": "ok",
                "branch": branch,
                "local_branch": branch,
                "ref": ref,
                "sha": sha,
            }
    return {
        "status": "base_unavailable",
        "branch": None,
        "ref": None,
        "sha": None,
    }


def _collect_refs(repo_dir: Path) -> list[dict]:
    # ``%00`` asks Git to emit a NUL without placing an embedded NUL in the
    # subprocess argument itself.
    format_string = "%(refname)%00%(objectname)%00%(committerdate:unix)%00%(committerdate:iso-strict)%00%(subject)%00%(symref)"
    result = _run_git(
        repo_dir,
        [
            "git",
            "for-each-ref",
            f"--format={format_string}",
            "refs/heads",
            "refs/remotes",
        ],
    )
    if result.returncode != 0:
        return []

    refs: list[dict] = []
    # ``for-each-ref`` terminates each formatted record with a newline, while
    # the fields within that record are NUL-delimited.
    for line in result.stdout.splitlines():
        values = line.split("\x00")
        if len(values) != 6:
            continue
        refname, sha, timestamp, iso_date, subject, symref = values
        refname = refname.strip()
        if not refname or symref or not sha:
            continue
        if refname.startswith("refs/heads/"):
            source = "local"
            logical_name = refname[len("refs/heads/") :]
            remote_name = None
        elif refname.startswith("refs/remotes/"):
            remote_path = refname[len("refs/remotes/") :]
            if "/" not in remote_path or remote_path.endswith("/HEAD"):
                continue
            remote, remote_branch = remote_path.split("/", 1)
            source = "remote"
            logical_name = remote_branch
            remote_name = f"{remote}/{remote_branch}"
        else:
            continue
        try:
            committer_timestamp = int(timestamp.strip())
        except ValueError:
            committer_timestamp = None
        refs.append(
            {
                "name": refname,
                "ref": refname,
                "sha": sha.strip(),
                "committer_timestamp": committer_timestamp,
                "committer_date": iso_date.strip(),
                "subject": subject,
                "symref": "",
                "source": source,
                "logical_name": logical_name,
                "remote": remote_name.split("/", 1)[0] if remote_name else None,
                "remote_name": remote_name,
            }
        )
    return refs


def _worktree_blocks(output: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        if not line:
            if current:
                blocks.append(current)
                current = {}
            continue
        key, separator, value = line.partition(" ")
        if separator:
            current[key] = value
    if current:
        blocks.append(current)
    return blocks


def _worktree_sort_key(worktree: dict) -> tuple[str, str, str, str]:
    return (
        str(worktree.get("path") or ""),
        str(worktree.get("branch") or ""),
        str(worktree.get("head") or ""),
        str(worktree.get("status") or ""),
    )


def _collect_worktrees(repo_dir: Path) -> list[dict]:
    result = _run_git(repo_dir, ["git", "worktree", "list", "--porcelain"])
    if result.returncode != 0:
        return []
    worktrees: list[dict] = []
    for block in _worktree_blocks(result.stdout):
        path_text = block.get("worktree")
        if not path_text:
            continue
        path = Path(path_text)
        row: dict[str, Any] = {
            "path": str(path),
            "head": block.get("HEAD"),
            "branch": block.get("branch"),
            "detached": block.get("branch") is None,
        }
        if not path.is_dir():
            row["status"] = "missing"
            worktrees.append(row)
            continue
        status = _run_git(
            repo_dir,
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=path,
        )
        if status.returncode != 0:
            row["status"] = "probe_error"
        else:
            row["status"] = "dirty" if status.stdout else "clean"
        worktrees.append(row)
    return sorted(worktrees, key=_worktree_sort_key)


def _unknown_comparison(status: str = "unknown") -> dict:
    return {
        "status": status,
        "ahead": None,
        "behind": None,
        "merged": None,
        "changed_paths": [],
        "changed_path_count": None,
    }


def _compare_ref(repo_dir: Path, base_ref: str | None, ref: str) -> dict:
    if not base_ref:
        return _unknown_comparison("base_unavailable")
    common_ancestor = _run_git(repo_dir, ["git", "merge-base", base_ref, ref])
    if common_ancestor.returncode != 0:
        return _unknown_comparison("no_common_ancestor")
    counts = _run_git(
        repo_dir, ["git", "rev-list", "--left-right", "--count", f"{base_ref}...{ref}"]
    )
    if counts.returncode != 0:
        return _unknown_comparison("no_common_ancestor")
    try:
        behind_text, ahead_text = counts.stdout.strip().split()[:2]
        behind, ahead = int(behind_text), int(ahead_text)
    except (ValueError, IndexError):
        return _unknown_comparison("comparison_unknown")

    merged_result = _run_git(
        repo_dir, ["git", "merge-base", "--is-ancestor", ref, base_ref]
    )
    if merged_result.returncode not in (0, 1):
        return _unknown_comparison("no_common_ancestor")
    merged = merged_result.returncode == 0
    diff = _run_git(
        repo_dir,
        ["git", "diff", "--name-only", "-z", "--no-renames", f"{base_ref}...{ref}"],
    )
    changed_paths: list[str] = []
    changed_path_count: int | None = 0
    diff_status = "ok"
    if diff.returncode == 0:
        paths = [path for path in diff.stdout.split("\x00") if path]
        changed_path_count = len(paths)
        changed_paths = paths[:MAX_CHANGED_PATHS]
    else:
        changed_path_count = None
        diff_status = "query_error"
    comparison = {
        "status": "ok",
        "ahead": ahead,
        "behind": behind,
        "merged": merged,
        "changed_paths": changed_paths,
        "changed_path_count": changed_path_count,
    }
    if diff_status != "ok":
        comparison["diff_status"] = diff_status
    return comparison


def _classify(row: dict, now_epoch: int) -> tuple[str, list[str]]:
    tip = row.get("tip") or {}
    flags: set[str] = set()
    worktrees = row.get("worktrees") or []
    present_worktrees = [item for item in worktrees if item.get("status") != "missing"]
    if any(item.get("status") == "dirty" for item in present_worktrees):
        flags.add("dirty_worktree")
    if any(item.get("status") == "missing" for item in worktrees):
        flags.add("missing_worktree")
    if row.get("local_ref") and not any(
        item.get("branch") == row["local_ref"].get("ref") for item in worktrees
    ):
        flags.add("unattached_local_branch")

    logical_name = row.get("name", "")
    timestamp = tip.get("committer_timestamp")
    age = None if timestamp is None else max(0, now_epoch - timestamp)
    if logical_name.startswith("auto-wip/"):
        classification = "abandoned_auto_wip"
        flags.add("abandoned_auto_wip")
    elif "dirty_worktree" in flags:
        classification = "active"
    elif age is None or age > AGING_DAYS * 86400:
        classification = "stale"
    elif age > ACTIVE_DAYS * 86400:
        classification = "aging"
    else:
        classification = "active"

    comparison = tip.get("comparison") or {}
    if comparison.get("status") != "ok":
        flags.add("comparison_unknown")
    elif (
        classification == "stale"
        and comparison.get("ahead", 0) > 0
        and not comparison.get("merged", False)
    ):
        flags.add("stale_unmerged_ahead")
    return classification, [flag for flag in _FLAG_ORDER if flag in flags]


def _recent_subjects(repo_dir: Path, ref: str) -> list[str]:
    result = _run_git(
        repo_dir, ["git", "log", "-n", str(MAX_RECENT_SUBJECTS), "--format=%s", ref]
    )
    if result.returncode != 0:
        return []
    return result.stdout.splitlines()[:MAX_RECENT_SUBJECTS]


def _document_evidence(repo_dir: Path, ref: str) -> dict:
    documents: dict[str, dict[str, str]] = {}
    for filename in ("CONTEXT.md", "TODO.md"):
        result = _run_git(repo_dir, ["git", "show", f"{ref}:{filename}"])
        if result.returncode == 0:
            documents[filename] = {
                "source": "repository evidence",
                "content": result.stdout[:MAX_DOC_EVIDENCE_CHARS],
            }
    return documents


def _enrich_ref(repo_dir: Path, base_ref: str | None, ref: dict) -> dict:
    enriched = dict(ref)
    enriched["comparison"] = _compare_ref(repo_dir, base_ref, ref["ref"])
    return enriched


def _focus(ref: dict) -> str:
    comparison = ref.get("comparison") or {}
    subjects = ref.get("recent_subjects") or []
    paths = comparison.get("changed_paths") or []
    pieces: list[str] = []
    if subjects:
        pieces.append(f"subject: {subjects[0]}")
    if paths:
        pieces.append(f"paths: {', '.join(paths)}")
    value = "; ".join(pieces) or "unknown"
    return value[:MAX_DOC_EVIDENCE_CHARS]


def _row_sort_key(row: dict) -> tuple:
    flags = row.get("attention_flags") or []
    timestamp = (row.get("tip") or {}).get("committer_timestamp")
    return (
        0 if flags else 1,
        _CLASSIFICATION_RANK.get(row.get("classification"), 99),
        -(timestamp if isinstance(timestamp, int) else 0),
        row.get("name", ""),
    )


def collect_branch_report(
    repo_dir: Path, *, now: datetime | None = None, fetch: bool = True
) -> dict:
    repo_dir = Path(repo_dir)
    observation = now or datetime.now(timezone.utc)
    if observation.tzinfo is None:
        observation = observation.replace(tzinfo=timezone.utc)
    observation = observation.astimezone(timezone.utc)
    fetch_result = _fetch_primary_remote(repo_dir, enabled=fetch)
    primary_remote = fetch_result.get("remote")
    base = _discover_base(repo_dir, primary_remote)
    raw_refs = _collect_refs(repo_dir)
    worktrees = sorted(_collect_worktrees(repo_dir), key=_worktree_sort_key)

    grouped: dict[str, list[dict]] = {}
    for ref in raw_refs:
        grouped.setdefault(ref["logical_name"], []).append(
            _enrich_ref(repo_dir, base.get("ref"), ref)
        )

    rows: list[dict] = []
    for name, refs in grouped.items():
        refs.sort(key=lambda item: (0 if item["source"] == "local" else 1, item["ref"]))
        local_ref = next((ref for ref in refs if ref["source"] == "local"), None)
        remote_refs = [ref for ref in refs if ref["source"] == "remote"]
        remote_refs.sort(key=lambda item: item["remote_name"] or "")
        tip = local_ref
        if tip is None and primary_remote:
            tip = next(
                (ref for ref in remote_refs if ref.get("remote") == primary_remote),
                None,
            )
        if tip is None and remote_refs:
            tip = remote_refs[0]
        if tip is None:
            continue

        attached = sorted(
            (
                dict(item)
                for item in worktrees
                if item.get("branch") == (local_ref or tip).get("ref")
            ),
            key=_worktree_sort_key,
        )
        tip = dict(tip)
        tip["recent_subjects"] = _recent_subjects(repo_dir, tip["ref"])
        documents = _document_evidence(repo_dir, tip["ref"])
        evidence = {
            "recent_subjects": tip["recent_subjects"],
            "changed_paths": list(
                (tip.get("comparison") or {}).get("changed_paths") or []
            ),
            "changed_path_count": (tip.get("comparison") or {}).get(
                "changed_path_count"
            ),
            "documents": documents,
        }
        row = {
            "name": name,
            "local_ref": local_ref,
            "remote_refs": [dict(ref, name=ref["remote_name"]) for ref in remote_refs],
            "refs": refs,
            "tip": tip,
            "worktrees": attached,
            "classification": "stale",
            "attention_flags": [],
            "focus": _focus(tip),
            "evidence": evidence,
        }
        row["classification"], row["attention_flags"] = _classify(
            row, int(observation.timestamp())
        )
        rows.append(row)

    rows.sort(key=_row_sort_key)
    report_stale = fetch_result.get("status") != "fetched"
    attention_flags: list[str] = []
    if report_stale:
        attention_flags.append("stale_fetch_data")
    for row in rows:
        for flag in row["attention_flags"]:
            if flag not in attention_flags:
                attention_flags.append(flag)
    report = {
        "repo": str(repo_dir.resolve()),
        "observed_at": observation.isoformat(),
        "fetch": fetch_result,
        "report_stale": report_stale,
        "base": base,
        "branches": rows,
        "worktrees": worktrees,
        "attention_flags": attention_flags,
        "report_hash": "",
    }
    report["report_hash"] = branch_report_hash(report)
    return report


def branch_report_hash(report: dict) -> str:
    canonical = {
        key: value
        for key, value in report.items()
        if key not in {"observed_at", "report_hash"}
    }
    fetch = canonical.get("fetch")
    if isinstance(fetch, dict):
        canonical["fetch"] = {
            "status": fetch.get("status"),
            "remote": fetch.get("remote"),
            "attempted": bool(fetch.get("attempted", False)),
            "timed_out": bool(fetch.get("timed_out", False)),
        }
    payload = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _markdown_cell(value: Any) -> str:
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = text[:MAX_DOC_EVIDENCE_CHARS]
    text = _SENTINEL_LIKE_COMMENT.sub(
        lambda match: "&lt;!--" + match.group(0)[4:], text
    )
    return text.replace("|", "\\|").replace("\n", "<br>")


def _sources(row: dict) -> str:
    values = []
    if row.get("local_ref"):
        values.append("local")
    values.extend(ref.get("name", "") for ref in row.get("remote_refs", []))
    return ", ".join(values) or "unknown"


def _worktree_summary(row: dict) -> str:
    statuses = [
        item.get("status") for item in row.get("worktrees", []) if item.get("status")
    ]
    if not statuses:
        return "unattached"
    unique = list(dict.fromkeys(statuses))
    return ", ".join(unique)


def _comparison_summary(row: dict) -> tuple[str, str]:
    comparison = (row.get("tip") or {}).get("comparison") or {}
    if comparison.get("status") != "ok":
        return "?", "?"
    merged = "yes" if comparison.get("merged") else "no"
    ahead = comparison.get("ahead")
    behind = comparison.get("behind")
    if not isinstance(ahead, int) or not isinstance(behind, int):
        return merged, "?"
    return merged, f"+{ahead}/-{behind}"


def render_branch_block(report: dict) -> str:
    base = report.get("base") or {}
    if base.get("status") == "ok" and base.get("ref") and base.get("sha"):
        base_text = (
            f"{_markdown_cell(base['ref'])} @ {_markdown_cell(base['sha'])}"
        )
    else:
        base_text = (
            "unavailable ("
            f"{_markdown_cell(base.get('status', 'base_unavailable'))})"
        )
    fetch = report.get("fetch") or {}
    if report.get("report_stale"):
        reason = _markdown_cell(fetch.get("status", "stale"))
        base_text_freshness = f"stale ({reason})"
    else:
        base_text_freshness = "current"

    lines = [
        "<!-- janitor:begin:branches -->",
        "## Branch and Worktree Review",
        f"Base: {base_text}",
        f"Freshness: {base_text_freshness}",
        "",
        "| Branch | Class | Sources | Merged | Ahead/behind | Worktree | Evidence |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    rows = sorted(report.get("branches", []), key=_row_sort_key)
    for row in rows:
        merged, ahead_behind = _comparison_summary(row)
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(row.get("name", "")),
                    _markdown_cell(row.get("classification", "unknown")),
                    _markdown_cell(_sources(row)),
                    merged,
                    ahead_behind,
                    _markdown_cell(_worktree_summary(row)),
                    _markdown_cell(row.get("focus", "unknown")),
                ]
            )
            + " |"
        )
    detached_worktrees = sorted(
        (
            item
            for item in report.get("worktrees", [])
            if item.get("branch") is None
        ),
        key=_worktree_sort_key,
    )
    if detached_worktrees:
        lines.extend(["", "Detached worktrees:"])
        for worktree in detached_worktrees:
            path = _markdown_cell(worktree.get("path", "unknown"))
            status = _markdown_cell(worktree.get("status", "unknown"))
            head = worktree.get("head")
            head_text = f" @ {_markdown_cell(head)}" if head else ""
            lines.append(f"- {path} ({status}{head_text})")
    lines.append("<!-- janitor:end:branches -->")
    return "\n".join(lines)
