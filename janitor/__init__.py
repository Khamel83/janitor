"""Janitor — Free background intelligence for Claude Code sessions.

Records session events, extracts decisions/patterns, detects test gaps and code
smells, and injects accumulated context at session start.

All LLM tasks use openrouter/free ($0). Storage: append-only JSONL.
"""

from janitor.recorder import SessionRecorder
from janitor.worker import call_free, extract_structured
from janitor.jobs import (
    summarize_recent_turns,
    memory_hygiene,
    generate_session_digest,
    analyze_file_changes,
    detect_stale_files,
    detect_test_gaps,
    scan_code_smells,
    detect_config_drift,
    build_dependency_map,
    enrich_commit_messages,
    mine_patterns,
    generate_onboarding_summary,
)

__all__ = [
    "SessionRecorder",
    "call_free",
    "extract_structured",
    "summarize_recent_turns",
    "memory_hygiene",
    "generate_session_digest",
    "analyze_file_changes",
    "detect_stale_files",
    "detect_test_gaps",
    "scan_code_smells",
    "detect_config_drift",
    "build_dependency_map",
    "enrich_commit_messages",
    "mine_patterns",
    "generate_onboarding_summary",
]
