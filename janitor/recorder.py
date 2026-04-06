"""Session recorder — append-only event log + SQLite index.

Records every significant turn in a Claude Code session:
- User requests, assistant actions, files read/written
- Decisions, blockers, discoveries
- Commits, errors, summaries

Storage: {project}/.janitor/events.jsonl (append-only)
         {project}/.janitor/intelligence.db (SQLite index, rebuilt on demand)
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _project_dir() -> Path:
    """Get current project root (directory with .git)."""
    cwd = Path(os.getcwd())
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".git").exists():
            return parent
    return cwd


def _janitor_dir(project_dir: Path | None = None) -> Path:
    """Get/create .janitor directory in project root."""
    d = Path(project_dir) / ".janitor" if project_dir else _project_dir() / ".janitor"
    d.mkdir(exist_ok=True)
    return d


EVENT_TYPES = [
    "user_request",
    "action_taken",
    "file_read",
    "file_written",
    "decision",
    "blocker",
    "discovery",
    "commit",
    "session_start",
    "session_end",
    "summary",
    "error",
]


class SessionRecorder:
    """Append-only session event recorder."""

    def __init__(self, project_dir: Optional[str] = None):
        self._dir = _janitor_dir(project_dir)
        self._events_path = self._dir / "events.jsonl"
        self._db_path = self._dir / "intelligence.db"
        self._session_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self._turn_count = 0

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def events_path(self) -> Path:
        return self._events_path

    @property
    def janitor_dir(self) -> Path:
        return self._dir

    def record(
        self,
        event_type: str,
        content: str,
        metadata: Optional[dict] = None,
        files: Optional[list[str]] = None,
    ) -> dict:
        """Record an event to the append-only JSONL log."""
        self._turn_count += 1
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": self._session_id,
            "turn": self._turn_count,
            "type": event_type,
            "content": content,
            "meta": metadata or {},
            "files": files or [],
        }
        with open(self._events_path, "a") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")
        return event

    def record_user_request(self, what: str):
        return self.record("user_request", what)

    def record_action(self, what: str, tool: str = "", files: list[str] | None = None):
        return self.record("action_taken", what, metadata={"tool": tool}, files=files)

    def record_file_read(self, path: str):
        return self.record("file_read", f"Read {path}", files=[path])

    def record_file_written(self, path: str, action: str = "edited"):
        return self.record("file_written", f"{action} {path}", files=[path])

    def record_decision(self, decision: str, alternatives: list[str] | None = None):
        return self.record("decision", decision, metadata={"alternatives": alternatives or []})

    def record_blocker(self, what: str, reason: str = ""):
        return self.record("blocker", what, metadata={"reason": reason})

    def record_discovery(self, what: str):
        return self.record("discovery", what)

    def record_commit(self, message: str, files: list[str] | None = None):
        return self.record("commit", message, files=files)

    def record_error(self, what: str, context: str = ""):
        return self.record("error", what, metadata={"context": context})

    def record_summary(self, summary: str, source: str = "janitor"):
        return self.record("summary", summary, metadata={"source": source})

    def get_recent_events(self, n: int = 20) -> list[dict]:
        """Get the last N events. Uses tail for O(1) reads."""
        if not self._events_path.exists():
            return []
        try:
            r = subprocess.run(
                ["tail", "-n", str(n)],
                capture_output=True, text=True, timeout=3,
            )
            if r.returncode != 0:
                return []
            events = []
            for line in r.stdout.strip().split("\n"):
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            return events
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

    def get_events_by_type(self, event_type: str, limit: int = 50) -> list[dict]:
        """Get events of a specific type from recent history."""
        recent = self.get_recent_events(min(limit * 3, 500))
        return [e for e in recent if e.get("type") == event_type][:limit]

    def get_events_by_session(self, session_id: str) -> list[dict]:
        """Get all events from a specific session."""
        if not self._events_path.exists():
            return []
        try:
            r = subprocess.run(
                ["grep", f'"session":"{session_id}"', str(self._events_path)],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0:
                return []
            events = []
            for line in r.stdout.strip().split("\n"):
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            return events
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

    def get_events_since(self, cutoff_iso: str) -> list[dict]:
        """Get events with timestamp >= cutoff_iso."""
        if not self._events_path.exists():
            return []
        try:
            cutoff = cutoff_iso[:19]
            events = []
            with open(self._events_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        ts = e.get("ts", "")[:19]
                        if ts >= cutoff:
                            events.append(e)
                    except json.JSONDecodeError:
                        continue
            return events
        except (OSError, FileNotFoundError):
            return []

    def get_decisions(self) -> list[dict]:
        return self.get_events_by_type("decision")

    def get_blockers(self) -> list[dict]:
        return self.get_events_by_type("blocker")

    def get_files_touched(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {"read": [], "written": [], "committed": []}
        for event in self.get_recent_events(200):
            t = event.get("type")
            for f in event.get("files", []):
                if t == "file_read" and f not in result["read"]:
                    result["read"].append(f)
                elif t == "file_written" and f not in result["written"]:
                    result["written"].append(f)
                elif t == "commit" and f not in result["committed"]:
                    result["committed"].append(f)
        return result

    def session_summary_text(self) -> str:
        """Generate a plain-text summary of the current session."""
        events = self.get_events_by_session(self._session_id)
        if not events:
            return "No events recorded this session."

        lines = [f"Session {self._session_id} — {len(events)} events", ""]

        decisions = [e for e in events if e["type"] == "decision"]
        if decisions:
            lines.append("DECISIONS:")
            for d in decisions:
                lines.append(f"  - {d['content']}")
            lines.append("")

        blockers = [e for e in events if e["type"] == "blocker"]
        if blockers:
            lines.append("BLOCKERS:")
            for b in blockers:
                lines.append(f"  - {b['content']}")
            lines.append("")

        files_touched = set()
        for e in events:
            for f in e.get("files", []):
                files_touched.add(f)
        if files_touched:
            lines.append("FILES TOUCHED:")
            for f in sorted(files_touched):
                lines.append(f"  - {f}")
            lines.append("")

        user_requests = [e for e in events if e["type"] == "user_request"]
        if user_requests:
            lines.append("USER REQUESTS:")
            for r in user_requests:
                lines.append(f"  [{r['turn']}] {r['content'][:100]}")
            lines.append("")

        return "\n".join(lines)

    # --- SQLite index ---

    def _get_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                session TEXT NOT NULL,
                turn INTEGER NOT NULL,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                meta TEXT DEFAULT '{}',
                files TEXT DEFAULT '[]'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")
        conn.commit()
        return conn

    def rebuild_index(self):
        """Rebuild the SQLite index from the JSONL source of truth."""
        conn = self._get_db()
        conn.execute("DELETE FROM events")

        if not self._events_path.exists():
            conn.close()
            return

        count = 0
        with open(self._events_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    conn.execute(
                        "INSERT INTO events (ts, session, turn, type, content, meta, files) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            e["ts"], e["session"], e["turn"], e["type"], e["content"],
                            json.dumps(e.get("meta", {})), json.dumps(e.get("files", [])),
                        )
                    )
                    count += 1
                except (json.JSONDecodeError, KeyError):
                    continue

        conn.commit()
        conn.close()
        return count

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Run a read-only query against the SQLite index."""
        conn = self._get_db()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def stats(self) -> dict:
        if not self._events_path.exists():
            return {"total_events": 0, "sessions": 0, "file_size_bytes": 0}

        total = 0
        sessions = set()
        with open(self._events_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    total += 1
                    sessions.add(e.get("session", ""))
                except json.JSONDecodeError:
                    continue

        return {
            "total_events": total,
            "sessions": len(sessions),
            "file_size_bytes": self._events_path.stat().st_size,
        }
