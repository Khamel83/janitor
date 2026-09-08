"""Persistent state layer and stable task ID manager for janitor.

Single JSON file (``~/.local/state/janitor/state.json`` by default) holding
per-repository records:

- ``task_ids``: mapping of normalized task text to stable ``tk_<hash>`` IDs,
  so identical task text always resolves to the same task across runs.
- ``last_hash``: input-content hash of the most recent run, used to skip
  rework when a repo has not changed since the last janitor pass.
- ``last_run``: ``{status, run_id, ts}`` of the most recent run.
- ``wip_branches``: list of ``{branch, sha, created_at}`` records for
  checkpointed abandoned work, consulted for expiry/pruning.

Writes are atomic-ish by construction: the file is only touched inside
``save()`` after an in-memory mutation completes, and a corrupt or
unreadable file degrades to an empty state rather than crashing.
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "janitor"

# Seconds in one day; used for WIP branch expiry cutoffs.
_SECONDS_PER_DAY = 86400


class StateManager:
    """Persist per-repo janitor state in ``state_dir/state.json``."""

    def __init__(self, state_dir: Optional[Path] = None):
        self.state_dir = state_dir or DEFAULT_STATE_DIR
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / "state.json"
        self._data = self._load()

    def _load(self) -> dict:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def save(self):
        """Persist in-memory state to ``state.json`` (indented for diffs)."""
        self.state_file.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def _repo(self, repo_name: str) -> dict:
        """Return the record dict for ``repo_name``, creating it on demand."""
        if repo_name not in self._data:
            self._data[repo_name] = {
                "task_ids": {},
                "last_hash": None,
                "last_run": None,
                "wip_branches": [],
            }
        return self._data[repo_name]

    def get_or_create_task_id(self, repo_name: str, task_text: str) -> str:
        """Return the stable task ID for normalized ``task_text`` in ``repo_name``.

        The task text is normalized (lowercased, whitespace collapsed) and
        hashed with SHA-256; the first 6 hex digits become the ``tk_`` ID, so
        equal task text always maps to one ID per repo.
        """
        repo = self._repo(repo_name)
        cleaned = " ".join(task_text.strip().lower().split())
        if cleaned not in repo["task_ids"]:
            h = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:6]
            repo["task_ids"][cleaned] = f"tk_{h}"
            self.save()
        return repo["task_ids"][cleaned]

    def get_last_input_hash(self, repo_name: str) -> Optional[str]:
        """Return the last recorded input hash for ``repo_name`` or None."""
        return self._repo(repo_name).get("last_hash")

    def set_last_input_hash(self, repo_name: str, input_hash: str):
        """Record ``input_hash`` as the latest input content for ``repo_name``."""
        self._repo(repo_name)["last_hash"] = input_hash
        self.save()

    def record_run(self, repo_name: str, status: str, run_id: str):
        """Record the most recent run's outcome for ``repo_name``."""
        self._repo(repo_name)["last_run"] = {
            "status": status,
            "run_id": run_id,
            "ts": int(time.time()),
        }
        self.save()

    def get_last_run(self, repo_name: str) -> Optional[dict]:
        """Return the most recent recorded run ``{status, run_id, ts}`` or None."""
        return self._repo(repo_name).get("last_run")

    def track_wip_branch(
        self,
        repo_name: str,
        branch_name: str,
        sha: str,
        timestamp: Optional[int] = None,
    ):
        """Record a checkpointed WIP branch; ``timestamp`` is epoch seconds."""
        ts = timestamp if timestamp is not None else int(time.time())
        self._repo(repo_name)["wip_branches"].append(
            {"branch": branch_name, "sha": sha, "created_at": ts}
        )
        self.save()

    def get_expired_wip_branches(
        self,
        repo_name: str,
        max_age_days: int = 30,
        current_timestamp: Optional[int] = None,
    ) -> list[str]:
        """Return branch names older than ``max_age_days`` in ``repo_name``.

        ``current_timestamp`` overrides the clock for deterministic expiry
        checks; a branch is expired when ``created_at < now - max_age_days``.
        """
        now = current_timestamp if current_timestamp is not None else int(time.time())
        cutoff = now - (max_age_days * _SECONDS_PER_DAY)
        repo = self._repo(repo_name)
        return [
            b["branch"]
            for b in repo.get("wip_branches", [])
            if b["created_at"] < cutoff
        ]
