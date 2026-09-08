"""Janitor — free background intelligence for Claude Code sessions.

Rebuilt as an autonomous repository caretaker: preflight-guarded git
operations (``janitor.git_ops``), persistent run/task state
(``janitor.state``), WIP checkpointing and hygiene (``janitor.hygiene``),
and living-documentation reconciliation (``janitor.reconciler``).

All LLM tasks use openrouter/free ($0) via ``janitor.worker``.
"""

from janitor.docs import generate_overview, sweep_docs
from janitor.worker import call_free, extract_structured

__all__ = [
    "call_free",
    "extract_structured",
    "sweep_docs",
    "generate_overview",
]
