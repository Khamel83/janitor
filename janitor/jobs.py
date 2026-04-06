"""Janitor jobs — bounded background tasks using openrouter/free.

Each job is a self-contained function that:
1. Reads raw data (from recorder, filesystem, etc.)
2. Sends a bounded extraction prompt to the free model
3. Writes structured output (to recorder, files, etc.)

Jobs are designed to be cheap ($0), fast (<30s), and idempotent.
They can run during idle time via CronCreate or on-demand.
"""

import json
import os
import re
import subprocess
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from janitor.recorder import SessionRecorder
from janitor.worker import call_free, extract_structured


# --- Job: Turn Summarizer ---

def summarize_recent_turns(recorder: SessionRecorder, n: int = 10) -> dict:
    """Extract decisions, blockers, and progress from recent turns.

    Reads the last N events from the JSONL log and asks the free model
    to extract structured decisions, blockers, and discoveries.
    """
    events = recorder.get_recent_events(n)
    if not events:
        return {"status": "empty", "decisions": [], "blockers": [], "discoveries": []}

    # Build a compact text representation
    turn_text = "\n".join(
        f"[{e.get('turn', '?')}] ({e['type']}) {e['content'][:200]}"
        for e in events
    )

    result = extract_structured(
        f"Analyze these session events and extract:\n\n{turn_text}\n\n"
        "Extract any:\n"
        "1. Decisions made (approach chosen, tech selected, etc.)\n"
        "2. Blockers (things that are stuck or waiting)\n"
        "3. Discoveries (things learned, bugs found, patterns noticed)\n"
        "4. Progress summary (what was accomplished)\n"
        "If nothing found for a category, return empty list.",
        system="You are a session analyst. Extract structured data from development session logs. Be concise.",
        schema_hint='{"decisions": [{"what": str, "why": str}], "blockers": [{"what": str, "reason": str}], "discoveries": [{"what": str}], "progress": str}',
    )

    # Record the summary back to the log
    if result.get("decisions"):
        for d in result["decisions"]:
            recorder.record_decision(
                d.get("what", ""),
                alternatives=d.get("alternatives", []),
            )
    if result.get("blockers"):
        for b in result["blockers"]:
            recorder.record_blocker(b.get("what", ""), b.get("reason", ""))

    recorder.record_summary(
        f"Turn summary: {len(result.get('decisions', []))} decisions, "
        f"{len(result.get('blockers', []))} blockers, "
        f"{len(result.get('discoveries', []))} discoveries",
        source="janitor_summarize",
    )

    return result


# --- Job: Memory Hygiene ---

def memory_hygiene(memory_dir: Optional[str] = None) -> dict:
    """Deduplicate and compact memory files.

    Reads all memory .md files, asks the free model to identify
    overlapping content, and reports what should be merged.
    Does NOT modify files — reports only. Caller decides what to merge.
    """
    if not memory_dir:
        # Auto-detect: find first Claude memory directory
        claude_projects = Path(os.path.expanduser("~/.claude/projects"))
        if claude_projects.exists():
            for p in claude_projects.iterdir():
                mem = p / "memory"
                if mem.is_dir():
                    memory_dir = str(mem)
                    break

    mem_path = Path(memory_dir)
    if not mem_path.exists():
        return {"status": "no_memory_dir", "files": 0, "overlaps": []}

    # Read all memory files (skip MEMORY.md index)
    files = {}
    for f in sorted(mem_path.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        content = f.read_text()
        if content.strip():
            files[f.name] = content[:1000]  # first 1000 chars for comparison

    if len(files) < 2:
        return {"status": "too_few_files", "files": len(files), "overlaps": []}

    # Build a compact comparison prompt
    file_list = "\n".join(f"## {name}\n{content[:500]}" for name, content in files.items())

    result = extract_structured(
        f"Analyze these memory files for overlapping or duplicate content:\n\n{file_list}\n\n"
        "Identify which files cover overlapping topics and should be merged.\n"
        "Be conservative — only flag clear overlaps, not tangential connections.",
        system="You are a memory system analyst. Identify duplicate or overlapping content in knowledge base files.",
        schema_hint='{"overlaps": [{"files": [str], "topic": str, "overlap_description": str, "recommendation": str}]}',
    )

    result["total_files"] = len(files)
    result["total_bytes"] = sum(len(c) for c in files.values())
    return result


# --- Job: Session Digest ---

def generate_session_digest(recorder: SessionRecorder) -> str:
    """Generate a full session digest from the event log.

    Produces a structured markdown summary suitable for handoff
    or session-end review.
    """
    events = recorder.get_events_by_session(recorder.session_id)
    if not events:
        return "No events this session."

    events_text = "\n".join(
        f"- [{e['turn']}] ({e['type']}) {e['content'][:150]}"
        for e in events
        if e["type"] != "summary"  # skip self-referential summaries
    )

    digest = call_free(
        f"Generate a structured session digest from these events:\n\n{events_text}\n\n"
        "Output a markdown section with:\n"
        "## What Was Asked (user requests)\n"
        "## What Was Done (actions taken)\n"
        "## Decisions (with reasoning)\n"
        "## Files Changed (list)\n"
        "## Blockers (if any)\n"
        "## Next Steps (what should happen next)\n"
        "Be specific with file names and code references. Keep it factual.",
        system="You are a session summarizer. Generate factual, structured digests of development sessions.",
        max_tokens=2048,
    )

    recorder.record_summary(digest, source="session_digest")
    return digest


# --- Job: File Change Tracker ---

def analyze_file_changes(project_dir: Optional[str] = None) -> dict:
    """Analyze recent git changes and categorize them.

    Uses git diff/log to understand what changed and why,
    then uses the free model to categorize changes.
    """
    import subprocess

    if not project_dir:
        project_dir = os.getcwd()

    # Get recent commits (last 10)
    try:
        r = subprocess.run(
            ["git", "log", "--oneline", "-10", "--name-status"],
            capture_output=True, text=True, timeout=5, cwd=project_dir
        )
        if r.returncode != 0:
            return {"status": "git_error"}
        log_text = r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"status": "git_unavailable"}

    # Get current diff (uncommitted)
    try:
        r = subprocess.run(
            ["git", "diff", "--stat"],
            capture_output=True, text=True, timeout=5, cwd=project_dir
        )
        diff_stat = r.stdout if r.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        diff_stat = ""

    if not log_text.strip() and not diff_stat.strip():
        return {"status": "no_changes"}

    result = extract_structured(
        f"Analyze these git changes and categorize them:\n\n"
        f"## Recent Commits\n{log_text}\n\n"
        f"## Uncommitted Changes\n{diff_stat}\n\n"
        "Categorize the changes and identify patterns.",
        system="You are a codebase analyst. Categorize git changes by type and identify patterns.",
        schema_hint='{"categories": {"features": int, "bugs": int, "refactors": int, "docs": int, "config": int, "tests": int}, "patterns": [str], "hotspot_files": [str]}',
    )

    return result


# --- Job: Stale File Detection ---

def detect_stale_files(project_dir: Optional[str] = None, days: int = 30) -> dict:
    """Find files not modified in N days that might need attention."""
    import subprocess

    if not project_dir:
        project_dir = os.getcwd()

    try:
        r = subprocess.run(
            ["git", "ls-files"],
            capture_output=True, text=True, timeout=10, cwd=project_dir
        )
        if r.returncode != 0:
            return {"status": "git_error"}
        tracked_files = r.stdout.strip().split("\n")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"status": "git_unavailable"}

    # Find files not modified in N days
    cutoff = days * 86400  # seconds
    now = time.time() if (time := __import__("time")) else 0
    stale = []
    important_stale = []

    for f in tracked_files:
        fpath = Path(project_dir) / f
        if not fpath.exists():
            continue
        age = now - fpath.stat().st_mtime
        if age > cutoff:
            stale.append({"path": f, "age_days": int(age / 86400)})
            # Flag potentially important stale files
            name_lower = f.lower()
            if any(kw in name_lower for kw in ["config", "readme", "setup", "install", "deploy"]):
                important_stale.append({"path": f, "age_days": int(age / 86400)})

    return {
        "total_tracked": len(tracked_files),
        "stale_count": len(stale),
        "important_stale": important_stale,
        "stale_by_age": {
            "30-60 days": len([s for s in stale if 30 <= s["age_days"] < 60]),
            "60-90 days": len([s for s in stale if 60 <= s["age_days"] < 90]),
            "90+ days": len([s for s in stale if s["age_days"] >= 90]),
        },
    }


# --- Job: Test Gap Detector (pure compute) ---

def detect_test_gaps(project_dir: Optional[str] = None, commits_back: int = 5) -> dict:
    """Find source files changed without corresponding test files.

    Compares files changed in last N commits against known test files.
    Pure git + file matching, no LLM call.
    """
    project_dir = project_dir or os.getcwd()

    r = subprocess.run(
        ["git", "diff", f"HEAD~{commits_back}", "--name-only"],
        capture_output=True, text=True, timeout=10, cwd=project_dir
    )
    if r.returncode != 0:
        return {"status": "git_error"}
    changed_files = set(f for f in r.stdout.strip().split("\n") if f)

    # Filter to source files (skip test files, docs, configs)
    SOURCE_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java"}
    source_changed = [
        f for f in changed_files
        if any(f.endswith(ext) for ext in SOURCE_EXTENSIONS)
        and "/test" not in f
        and "/tests/" not in f
        and not f.startswith("test_")
        and not f.endswith("_test.py")
    ]

    # Get all tracked files to check for existing tests
    r = subprocess.run(
        ["git", "ls-files"],
        capture_output=True, text=True, timeout=10, cwd=project_dir
    )
    all_files = set(f for f in r.stdout.strip().split("\n") if f)
    test_files = {
        f for f in all_files
        if "test" in f.lower() and f.endswith((".py", ".js", ".ts"))
    }

    gaps = []
    for src in source_changed:
        stem = Path(src).stem
        parent = str(Path(src).parent)
        candidates = [
            f"tests/test_{stem}.py",
            f"tests/{parent}/test_{stem}.py",
            f"{parent}/test_{stem}.py",
        ]
        if not any(c in test_files for c in candidates):
            gaps.append({"source_file": src, "expected_test_candidates": candidates})

    result = {
        "status": "ok",
        "changed_source_files": len(source_changed),
        "total_changed_files": len(changed_files),
        "gaps": gaps,
        "gap_count": len(gaps),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    oneshot_dir = Path(project_dir) / ".oneshot"
    oneshot_dir.mkdir(exist_ok=True)
    with open(oneshot_dir / "test-gaps.json", "w") as f:
        json.dump(result, f, indent=2)

    return result


# --- Job: Code Smell Scanner (pure compute) ---

def scan_code_smells(
    project_dir: Optional[str] = None,
    max_file_lines: int = 500,
    max_func_lines: int = 100,
) -> dict:
    """Find oversized files and functions via line counting.

    Pure computation, no LLM call.
    """
    project_dir = project_dir or os.getcwd()

    r = subprocess.run(
        ["git", "ls-files", "*.py"],
        capture_output=True, text=True, timeout=10, cwd=project_dir
    )
    if r.returncode != 0:
        return {"status": "git_error"}
    py_files = [f for f in r.stdout.strip().split("\n") if f]

    oversized_files = []
    oversized_functions = []

    for filepath in py_files:
        full_path = Path(project_dir) / filepath
        if not full_path.exists():
            continue

        lines = full_path.read_text().split("\n")
        line_count = len(lines)

        if line_count > max_file_lines:
            oversized_files.append({
                "path": filepath,
                "line_count": line_count,
                "over_by": line_count - max_file_lines,
            })

        # Find function definitions and measure their length
        func_starts = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("def ") and stripped.endswith(":"):
                func_starts.append(i)

        for idx, start_idx in enumerate(func_starts):
            end_idx = func_starts[idx + 1] if idx + 1 < len(func_starts) else len(lines)
            func_lines = end_idx - start_idx
            if func_lines > max_func_lines:
                func_name = lines[start_idx].strip().split("(")[0].replace("def ", "")
                oversized_functions.append({
                    "path": filepath,
                    "function": func_name,
                    "line_start": start_idx + 1,
                    "line_count": func_lines,
                    "over_by": func_lines - max_func_lines,
                })

    oversized_files.sort(key=lambda x: x["line_count"], reverse=True)
    oversized_functions.sort(key=lambda x: x["line_count"], reverse=True)

    result = {
        "status": "ok",
        "files_scanned": len(py_files),
        "thresholds": {"max_file_lines": max_file_lines, "max_func_lines": max_func_lines},
        "oversized_files": oversized_files,
        "oversized_functions": oversized_functions[:20],
        "oversized_file_count": len(oversized_files),
        "oversized_function_count": len(oversized_functions),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    oneshot_dir = Path(project_dir) / ".oneshot"
    oneshot_dir.mkdir(exist_ok=True)
    with open(oneshot_dir / "code-smells.json", "w") as f:
        json.dump(result, f, indent=2)

    return result


# --- Job: Config Drift Monitor (pure compute) ---

def detect_config_drift(project_dir: Optional[str] = None, config_dir: str = "config") -> dict:
    """Detect uncommitted changes in config files.

    Pure git diff, no LLM call.
    """
    project_dir = project_dir or os.getcwd()
    config_path = Path(project_dir) / config_dir
    if not config_path.exists():
        return {"status": "no_config_dir"}

    r = subprocess.run(
        ["git", "ls-files", "--", config_dir],
        capture_output=True, text=True, timeout=5, cwd=project_dir
    )
    if r.returncode != 0:
        return {"status": "git_error"}
    tracked_configs = [f for f in r.stdout.strip().split("\n") if f]

    drifted = []
    for config_file in tracked_configs:
        full_path = Path(project_dir) / config_file
        if not full_path.exists():
            continue

        r = subprocess.run(
            ["git", "diff", "HEAD", "--", config_file],
            capture_output=True, text=True, timeout=5, cwd=project_dir
        )
        if r.returncode == 0 and r.stdout.strip():
            added = r.stdout.count("\n+")
            removed = r.stdout.count("\n-")
            drifted.append({
                "file": config_file,
                "added_lines": added,
                "removed_lines": removed,
            })

    result = {
        "status": "ok",
        "config_files_checked": len(tracked_configs),
        "drifted_files": drifted,
        "drift_count": len(drifted),
        "drifted_file_names": [d["file"] for d in drifted],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    oneshot_dir = Path(project_dir) / ".oneshot"
    oneshot_dir.mkdir(exist_ok=True)
    with open(oneshot_dir / "config-drift.json", "w") as f:
        json.dump(result, f, indent=2)

    return result


# --- Job: Dependency Impact Predictor (pure compute) ---

def build_dependency_map(project_dir: Optional[str] = None) -> dict:
    """Build import dependency graph from Python files.

    Scans imports, builds reverse dependency map, ranks files by impact.
    Pure computation, no LLM call.
    """
    project_dir = project_dir or os.getcwd()

    r = subprocess.run(
        ["git", "ls-files", "*.py"],
        capture_output=True, text=True, timeout=10, cwd=project_dir
    )
    if r.returncode != 0:
        return {"status": "git_error"}
    py_files = [f for f in r.stdout.strip().split("\n") if f]

    import_pattern = re.compile(r'^(?:from\s+(\S+)\s+import|import\s+(\S+))')
    graph = {}

    for filepath in py_files:
        full_path = Path(project_dir) / filepath
        if not full_path.exists():
            continue

        content = full_path.read_text()
        file_imports = set()
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("#"):
                continue
            m = import_pattern.match(line)
            if m:
                module = m.group(1) or m.group(2)
                # Only keep project-internal modules
                if (Path(project_dir) / f"{module.replace('.', '/')}.py").exists():
                    file_imports.add(module)

        if file_imports:
            graph[filepath] = sorted(file_imports)

    # Build reverse map: module -> files that import it
    reverse_deps = {}
    for filepath, imports in graph.items():
        for imp in imports:
            reverse_deps.setdefault(imp, []).append(filepath)

    # Impact ranking: files with most dependents
    impact_scores = {}
    for module, dependents in reverse_deps.items():
        module_file = f"{module.replace('.', '/')}.py"
        impact_scores[module_file] = {
            "dependents": dependents,
            "downstream_count": len(dependents),
        }

    sorted_impact = sorted(
        impact_scores.items(),
        key=lambda x: x[1]["downstream_count"],
        reverse=True,
    )

    result = {
        "status": "ok",
        "files_scanned": len(py_files),
        "files_with_imports": len(graph),
        "graph": graph,
        "reverse_deps": reverse_deps,
        "impact_ranking": [
            {"file": f, "downstream_count": info["downstream_count"], "dependents": info["dependents"]}
            for f, info in sorted_impact[:30]
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    oneshot_dir = Path(project_dir) / ".oneshot"
    oneshot_dir.mkdir(exist_ok=True)
    with open(oneshot_dir / "dep-graph.json", "w") as f:
        json.dump(result, f, indent=2)

    return result


# --- Job: Commit Message Enricher (1 LLM call) ---

def enrich_commit_messages(
    recorder: SessionRecorder,
    project_dir: Optional[str] = None,
    commits_back: int = 1,
) -> dict:
    """Enrich recent commit messages with semantic tags and summaries.

    Skips commits already enriched. 1 LLM call per new commit.
    """
    project_dir = project_dir or os.getcwd()

    r = subprocess.run(
        ["git", "log", f"-{commits_back}", "--format=%H", "--no-merges"],
        capture_output=True, text=True, timeout=10, cwd=project_dir
    )
    if r.returncode != 0 or not r.stdout.strip():
        return {"status": "no_commits"}
    commit_hashes = r.stdout.strip().split("\n")

    enrichments_path = Path(project_dir) / ".oneshot" / "commit-enrichments.json"
    existing = {}
    if enrichments_path.exists():
        try:
            existing = json.loads(enrichments_path.read_text())
        except json.JSONDecodeError:
            existing = {}

    new_enrichments = {}
    for commit_hash in commit_hashes:
        if commit_hash in existing:
            continue

        r_msg = subprocess.run(
            ["git", "log", "-1", "--format=%s", commit_hash],
            capture_output=True, text=True, timeout=5, cwd=project_dir
        )
        r_diff = subprocess.run(
            ["git", "diff", f"{commit_hash}~1..{commit_hash}", "--stat"],
            capture_output=True, text=True, timeout=10, cwd=project_dir
        )
        message = r_msg.stdout.strip() if r_msg.returncode == 0 else commit_hash[:8]
        diff_stat = r_diff.stdout.strip() if r_diff.returncode == 0 else ""

        result = extract_structured(
            f"Enrich this git commit with semantic analysis:\n\n"
            f"Message: {message}\n"
            f"Changed files:\n{diff_stat}\n\n"
            f"Provide a concise summary and tags.",
            system="You are a commit analyzer. Extract meaning and categorize briefly.",
            schema_hint='{"summary": str, "tags": [str], "category": str}',
        )

        enrichment = {
            "hash": commit_hash[:12],
            "original_message": message,
            "enriched": result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        new_enrichments[commit_hash] = enrichment
        recorder.record_commit(message)

    if new_enrichments:
        existing.update(new_enrichments)
        oneshot_dir = Path(project_dir) / ".oneshot"
        oneshot_dir.mkdir(exist_ok=True)
        with open(oneshot_dir / "commit-enrichments.json", "w") as f:
            json.dump(existing, f, indent=2)

    return {
        "status": "ok",
        "enriched_count": len(new_enrichments),
        "skipped_count": len(commit_hashes) - len(new_enrichments),
        "total_enriched": len(existing),
    }


# --- Job: Pattern Miner (1 LLM call/day) ---

def mine_patterns(
    recorder: SessionRecorder,
    project_dir: Optional[str] = None,
    days_back: int = 7,
) -> dict:
    """Mine recurring patterns from session events.

    Pre-computes frequency tables, then 1 LLM call to interpret.
    Should run once per day (daily gate in cron).
    """
    project_dir = project_dir or os.getcwd()
    events_path = Path(project_dir) / ".oneshot" / "events.jsonl"
    if not events_path.exists():
        return {"status": "no_events"}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    events = recorder.get_events_since(cutoff)

    if len(events) < 10:
        return {"status": "too_few_events", "event_count": len(events)}

    # Pre-compute frequency tables
    file_touches: dict[str, list[str]] = {}
    error_types: dict[str, int] = {}
    decisions: dict[str, int] = {}

    for e in events:
        if e["type"] in ("file_written", "file_read"):
            for f in e.get("files", []):
                file_touches.setdefault(f, []).append(e.get("session", ""))

        if e["type"] == "error":
            key = e["content"][:80]
            error_types[key] = error_types.get(key, 0) + 1

        if e["type"] == "decision":
            key = e["content"][:80]
            decisions[key] = decisions.get(key, 0) + 1

    hot_files = {
        f: {"sessions": len(set(s)), "unique_sessions": list(set(s))}
        for f, s in file_touches.items()
        if len(set(s)) >= 3
    }
    recurring_errors = {k: v for k, v in error_types.items() if v >= 2}
    revisited_decisions = {k: v for k, v in decisions.items() if v >= 2}

    pattern_text = ""
    if hot_files:
        pattern_text += "## Hot Files (touched 3+ sessions)\n"
        for f, info in hot_files.items():
            pattern_text += f"- {f}: {info['sessions']} sessions\n"
    if recurring_errors:
        pattern_text += "\n## Recurring Errors (2+)\n"
        for err, count in recurring_errors.items():
            pattern_text += f"- [{count}x] {err}\n"
    if revisited_decisions:
        pattern_text += "\n## Revisited Decisions (2+)\n"
        for d, count in revisited_decisions.items():
            pattern_text += f"- [{count}x] {d}\n"

    if not pattern_text.strip():
        return {"status": "no_patterns", "events_analyzed": len(events)}

    result = extract_structured(
        f"Analyze patterns from {days_back} days of development:\n\n"
        f"{pattern_text}\n\n"
        f"Identify actionable insights. Events analyzed: {len(events)}",
        system="You are a development pattern analyst. Identify recurring issues and suggest systemic fixes.",
        schema_hint='{"patterns": [{"type": str, "description": str, "frequency": str, "recommendation": str}]}',
    )

    if result.get("patterns"):
        for p in result["patterns"]:
            recorder.record_discovery(f"Pattern: {p.get('description', '')}")

    final_result = {
        "status": "ok",
        "events_analyzed": len(events),
        "days_scanned": days_back,
        "hot_files": hot_files,
        "recurring_errors": recurring_errors,
        "revisited_decisions": revisited_decisions,
        "insights": result.get("patterns", []),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    oneshot_dir = Path(project_dir) / ".oneshot"
    oneshot_dir.mkdir(exist_ok=True)
    with open(oneshot_dir / "patterns.json", "w") as f:
        json.dump(final_result, f, indent=2)

    return final_result


# --- Job: Onboarding Summary Generator (1 LLM call/day) ---

def generate_onboarding_summary(
    recorder: SessionRecorder,
    project_dir: Optional[str] = None,
) -> dict:
    """Generate a 'state of the project' summary from all janitor data.

    Gathers all .janitor/*.json outputs, feeds to 1 LLM call.
    Should run once per day (daily gate in cron).
    """
    project_dir = project_dir or os.getcwd()
    oneshot_dir = Path(project_dir) / ".oneshot"
    sections: dict[str, str] = {}

    # Last digest
    digest_path = oneshot_dir / "last-digest.md"
    if digest_path.exists():
        sections["last_digest"] = digest_path.read_text()[:500]

    # Event stats
    event_count = 0
    decisions_count = 0
    blockers_count = 0
    events_path = oneshot_dir / "events.jsonl"
    if events_path.exists():
        with open(events_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                event_count += 1
                if '"type":"decision"' in line:
                    decisions_count += 1
                if '"type":"blocker"' in line:
                    blockers_count += 1
    if event_count:
        sections["events"] = f"{event_count} events, {decisions_count} decisions, {blockers_count} blockers"

    # Active blockers
    active_blockers = recorder.get_blockers()
    if active_blockers:
        sections["active_blockers"] = "; ".join(b["content"][:100] for b in active_blockers[-5:])

    # Gather JSON data from other jobs
    json_sources = [
        ("test_gaps", "test-gaps.json",
         lambda d: f"{d.get('gap_count', 0)} gaps: " + ", ".join(g["source_file"] for g in d.get("gaps", [])[:5])
         if d.get("gap_count", 0) > 0 else ""),
        ("code_smells", "code-smells.json",
         lambda d: f"{d.get('oversized_file_count', 0)} oversized files, {d.get('oversized_function_count', 0)} long functions"
         if d.get("oversized_file_count", 0) > 0 or d.get("oversized_function_count", 0) > 0 else ""),
        ("config_drift", "config-drift.json",
         lambda d: "Drifted: " + ", ".join(d.get("drifted_file_names", []))
         if d.get("drift_count", 0) > 0 else ""),
        ("patterns", "patterns.json",
         lambda d: "; ".join(p.get("description", "")[:80] for p in d.get("insights", [])[:3])
         if d.get("insights") else ""),
        ("dep_graph", "dep-graph.json",
         lambda d: ", ".join(f'{t["file"]} ({t["downstream_count"]} deps)' for t in d.get("impact_ranking", [])[:3])
         if d.get("impact_ranking") else ""),
    ]

    for key, filename, extractor in json_sources:
        filepath = oneshot_dir / filename
        if filepath.exists():
            try:
                data = json.loads(filepath.read_text())
                text = extractor(data)
                if text:
                    sections[key] = text
            except (json.JSONDecodeError, KeyError):
                pass

    context_text = "\n".join(f"## {k}\n{v}\n" for k, v in sections.items())
    if not context_text.strip():
        return {"status": "no_data"}

    summary = call_free(
        f"Generate a concise 'state of the project' onboarding summary:\n\n{context_text}\n\n"
        "Output markdown with: Project Status, Active Blockers, Recent Activity, "
        "Attention Items, Patterns, Recommended Next Steps. 200 words max.",
        system="You are a project onboarding assistant. Generate clear, actionable summaries.",
        max_tokens=2048,
    )

    summary_path = oneshot_dir / "onboarding-summary.md"
    with open(summary_path, "w") as f:
        f.write(summary)

    recorder.record_summary(summary, source="onboarding_generator")

    return {
        "status": "ok",
        "data_sources_used": list(sections.keys()),
        "summary_length": len(summary),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
