"""Janitor — autonomous repository caretaker for the Homelab fleet.

Preflight-guarded git operations (``janitor.git_ops``), persistent state
(``janitor.state``), Butler auto-tidy and WIP checkpointing (``janitor.hygiene``),
and sentinel-based living documentation reconciler (``janitor.reconciler``).
"""

from janitor.reconciler import overview_repo, sweep_repo
from janitor.worker import call_free, extract_structured

__all__ = [
    "overview_repo",
    "sweep_repo",
    "call_free",
    "extract_structured",
]
