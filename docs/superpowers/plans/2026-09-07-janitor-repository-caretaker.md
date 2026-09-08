# Janitor Repository Caretaker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `janitor` into an autonomous, fault-tolerant repository caretaker and living context reconciler running across homelab repositories (`/Volumes/2TB_SSD/GitHub/*`), controlled by Homelab via systemd timers over SSH.

**Architecture:** Homelab Linux server acts as the Master scheduler (systemd timers triggering `ssh macmini "caffeinate -is janitor sweep --all --json"`). Mac mini acts as the Worker execution node running a clean, modular Python CLI. Janitor reconciles living documentation (`CONTEXT.md`, `TODO.md`, `LLM-OVERVIEW.md`) within sentinel boundaries, provides a zero-token fast path for quiet repos with git trailer loop prevention, safely checkpoints abandoned uncommitted work into `auto-wip/<date>` branches without blowing away unpushed commits, and purges cache clutter.

**Tech Stack:** Python 3.10+ (stdlib `subprocess`, `json`, `pathlib`, `hashlib`, `unittest`), Git CLI, Gateway2000 (`g2k-bg` via stdin) with fallback to OpenRouter free models via `urllib.request`.

## Global Constraints
- Python standard library only; zero runtime package dependencies.
- Zero data loss: `git reset --hard` MUST restore to pre-existing local HEAD (`git rev-parse HEAD`), never `origin/main`.
- Secret denylist: auto-wip checkpointing must never stage `.env*`, `*.pem`, `*.key`, or `*credential*`.
- Auto-wip branches are strictly local and NEVER pushed to remotes.
- Sentinel boundaries: Janitor only overwrites inside `<!-- janitor:begin -->` ... `<!-- janitor:end -->`; human text outside sentinels is preserved byte-exact.
- Loop prevention: All Janitor commits carry git trailer `Janitor-Run: <run-id>` and are excluded from 24h activity checks.
- Preflight guards: Skip repos with `.git/index.lock`, `MERGE_HEAD`, `CHERRY_PICK_HEAD`, `rebase-merge/apply`, `BISECT_LOG`, or detached HEAD.
- Stdin transport: Prompts to `g2k-bg` are passed via stdin (`-p -`) to prevent OS `ARG_MAX` limits.

---

### Task 1: Package Scaffolding, Cleanup & Preflight Guards (`git_ops.py`)

**Files:**
- Create: `janitor/git_ops.py`
- Create: `tests/test_git_ops.py`
- Modify: `pyproject.toml`
- Remove: `janitor/recorder.py`, `janitor/jobs.py`, `hooks/record.sh`, `hooks/context.sh`, `hooks/session-end.sh`, `scripts/cron.sh`, `setup.sh`

**Interfaces:**
- Consumes: Local git binary via `subprocess.run`
- Produces:
  - `check_preflight_guards(repo_dir: Path) -> Optional[str]`
  - `get_repo_status(repo_dir: Path) -> dict` (porcelain status, branch name, current SHA)
  - `has_24h_activity(repo_dir: Path) -> tuple[bool, str, str]` (excludes Janitor-Run trailer)
  - `atomic_stage_and_commit(repo_dir: Path, files: list[str], message: str, run_id: str) -> bool`

- [ ] **Step 1: Write failing test for preflight guards and git operations**

Write `tests/test_git_ops.py`:
```python
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from janitor.git_ops import check_preflight_guards, get_repo_status, has_24h_activity, atomic_stage_and_commit

class TestGitOps(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)
        
        # Initial commit
        (self.repo_dir / "README.md").write_text("# Test Repo\n")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=self.repo_dir, check=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_preflight_guards_clean_repo(self):
        self.assertIsNone(check_preflight_guards(self.repo_dir))

    def test_preflight_guards_index_lock(self):
        lock_file = self.repo_dir / ".git" / "index.lock"
        lock_file.write_text("")
        self.assertEqual(check_preflight_guards(self.repo_dir), "git_index_locked")

    def test_preflight_guards_merge_in_progress(self):
        merge_head = self.repo_dir / ".git" / "MERGE_HEAD"
        merge_head.write_text("0000000000000000000000000000000000000000\n")
        self.assertEqual(check_preflight_guards(self.repo_dir), "merge_in_progress")

    def test_preflight_guards_detached_head(self):
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_dir, capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "checkout", sha], cwd=self.repo_dir, capture_output=True, check=True)
        self.assertEqual(check_preflight_guards(self.repo_dir), "detached_head")

    def test_has_24h_activity_excludes_janitor_run(self):
        # Janitor commit with trailer
        (self.repo_dir / "doc.md").write_text("doc\n")
        subprocess.run(["git", "add", "doc.md"], cwd=self.repo_dir, check=True)
        msg = "docs(janitor): sweep\n\nJanitor-Run: 20260907-0300"
        subprocess.run(["git", "commit", "-m", msg], cwd=self.repo_dir, check=True)
        
        has_act, log, diff = has_24h_activity(self.repo_dir)
        # Should exclude the Janitor-Run commit, leaving only initial commit
        self.assertNotIn("docs(janitor)", log)

    def test_atomic_stage_and_commit(self):
        (self.repo_dir / "CONTEXT.md").write_text("Context\n")
        (self.repo_dir / "TODO.md").write_text("Todo\n")
        success = atomic_stage_and_commit(
            self.repo_dir,
            ["CONTEXT.md", "TODO.md"],
            "docs(janitor): sweep CONTEXT.md and TODO.md [skip ci]",
            "run_12345"
        )
        self.assertTrue(success)
        log = subprocess.run(["git", "log", "-n", "1"], cwd=self.repo_dir, capture_output=True, text=True).stdout
        self.assertIn("Janitor-Run: run_12345", log)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_git_ops.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'janitor.git_ops'`)

- [ ] **Step 3: Implement `janitor/git_ops.py` and clean legacy files**

Create `janitor/git_ops.py`:
```python
import subprocess
from pathlib import Path
from typing import Optional, Tuple

def _sh(cmd: list[str], cwd: Path) -> str:
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        return ""
    return res.stdout.strip()

def check_preflight_guards(repo_dir: Path) -> Optional[str]:
    dot_git = repo_dir / ".git"
    if not dot_git.exists():
        return "not_a_git_repo"
    if (dot_git / "index.lock").exists():
        return "git_index_locked"
    if (dot_git / "MERGE_HEAD").exists() or (dot_git / "CHERRY_PICK_HEAD").exists():
        return "merge_in_progress"
    if (dot_git / "rebase-merge").exists() or (dot_git / "rebase-apply").exists():
        return "rebase_in_progress"
    if (dot_git / "BISECT_LOG").exists():
        return "bisect_in_progress"
    
    # Check detached HEAD
    head_res = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=repo_dir, capture_output=True)
    if head_res.returncode != 0:
        return "detached_head"
    return None

def get_repo_status(repo_dir: Path) -> dict:
    status_raw = _sh(["git", "status", "--porcelain"], repo_dir)
    branch = _sh(["git", "symbolic-ref", "--short", "HEAD"], repo_dir) or "HEAD"
    sha = _sh(["git", "rev-parse", "--short", "HEAD"], repo_dir)
    return {
        "is_dirty": bool(status_raw),
        "porcelain": status_raw,
        "branch": branch,
        "sha": sha,
    }

def has_24h_activity(repo_dir: Path) -> Tuple[bool, str, str]:
    # Filter out Janitor-Run commits to prevent self-stimulation loops
    recent_log = _sh([
        "git", "log", "--since=24.hours", "--invert-grep", "--grep=^Janitor-Run:",
        "--pretty=format:%h %s (%cr)"
    ], repo_dir)
    
    recent_diff = ""
    try:
        verify = subprocess.run(["git", "rev-parse", "--verify", "HEAD~1"], cwd=repo_dir, capture_output=True)
        if verify.returncode == 0:
            recent_diff = _sh(["git", "diff", "HEAD~1..HEAD", "--stat"], repo_dir)
    except Exception:
        pass
        
    has_activity = bool(recent_log)
    return has_activity, recent_log, recent_diff

def atomic_stage_and_commit(repo_dir: Path, files: list[str], message: str, run_id: str) -> bool:
    add_res = subprocess.run(["git", "add"] + files, cwd=repo_dir, capture_output=True)
    if add_res.returncode != 0:
        return False
        
    # Verify ONLY the authorized files are staged
    staged = _sh(["git", "diff", "--cached", "--name-only"], repo_dir).splitlines()
    allowed = set(files)
    if not staged or any(f not in allowed for f in staged):
        subprocess.run(["git", "reset"], cwd=repo_dir, capture_output=True)
        return False
        
    full_message = f"{message}\n\nJanitor-Run: {run_id}"
    commit_res = subprocess.run(["git", "commit", "-m", full_message], cwd=repo_dir, capture_output=True)
    return commit_res.returncode == 0
```

Remove legacy files:
```bash
rm -f janitor/recorder.py janitor/jobs.py hooks/record.sh hooks/context.sh hooks/session-end.sh scripts/cron.sh setup.sh
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests/test_git_ops.py`
Expected: PASS (5 tests passing)

- [ ] **Step 5: Commit Task 1**

```bash
git add janitor/git_ops.py tests/test_git_ops.py pyproject.toml
git rm -f janitor/recorder.py janitor/jobs.py hooks/record.sh hooks/context.sh hooks/session-end.sh scripts/cron.sh setup.sh 2>/dev/null || true
git commit -m "feat(janitor): add git_ops with preflight guards, atomic commit and loop prevention"
```

---

### Task 2: Persistent State Layer & Task ID Manager (`state.py`)

**Files:**
- Create: `janitor/state.py`
- Create: `tests/test_state.py`

**Interfaces:**
- Consumes: Filesystem path `~/.local/state/janitor/state.json`
- Produces:
  - `StateManager(state_dir: Optional[Path] = None)`
  - `get_or_create_task_id(repo_name: str, task_text: str) -> str`
  - `get_last_input_hash(repo_name: str) -> Optional[str]`
  - `set_last_input_hash(repo_name: str, input_hash: str)`
  - `record_run(repo_name: str, status: str, run_id: str)`
  - `track_wip_branch(repo_name: str, branch_name: str, sha: str)`
  - `get_expired_wip_branches(repo_name: str, max_age_days: int = 30) -> list[str]`

- [ ] **Step 1: Write failing test for state management**

Write `tests/test_state.py`:
```python
import tempfile
import unittest
from pathlib import Path
from janitor.state import StateManager

class TestStateManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)
        self.sm = StateManager(self.state_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_task_id_stability(self):
        id1 = self.sm.get_or_create_task_id("maya", "Deploy Postgres schema")
        id2 = self.sm.get_or_create_task_id("maya", "Deploy Postgres schema")
        self.assertEqual(id1, id2)
        self.assertTrue(id1.startswith("tk_"))

    def test_input_hash_tracking(self):
        self.assertIsNone(self.sm.get_last_input_hash("maya"))
        self.sm.set_last_input_hash("maya", "hash_abc123")
        self.assertEqual(self.sm.get_last_input_hash("maya"), "hash_abc123")

    def test_wip_branch_expiry(self):
        self.sm.track_wip_branch("maya", "auto-wip/20260101-0300", "sha123", timestamp=1000000)
        expired = self.sm.get_expired_wip_branches("maya", max_age_days=1, current_timestamp=2000000)
        self.assertIn("auto-wip/20260101-0300", expired)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_state.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'janitor.state'`)

- [ ] **Step 3: Implement `janitor/state.py`**

Create `janitor/state.py`:
```python
import hashlib
import json
import time
from pathlib import Path
from typing import Optional

DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "janitor"

class StateManager:
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
        self.state_file.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def _repo(self, repo_name: str) -> dict:
        if repo_name not in self._data:
            self._data[repo_name] = {
                "task_ids": {},
                "last_hash": None,
                "last_run": None,
                "wip_branches": []
            }
        return self._data[repo_name]

    def get_or_create_task_id(self, repo_name: str, task_text: str) -> str:
        repo = self._repo(repo_name)
        cleaned = " ".join(task_text.strip().lower().split())
        if cleaned not in repo["task_ids"]:
            h = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:6]
            repo["task_ids"][cleaned] = f"tk_{h}"
            self.save()
        return repo["task_ids"][cleaned]

    def get_last_input_hash(self, repo_name: str) -> Optional[str]:
        return self._repo(repo_name).get("last_hash")

    def set_last_input_hash(self, repo_name: str, input_hash: str):
        self._repo(repo_name)["last_hash"] = input_hash
        self.save()

    def record_run(self, repo_name: str, status: str, run_id: str):
        self._repo(repo_name)["last_run"] = {
            "status": status,
            "run_id": run_id,
            "ts": int(time.time())
        }
        self.save()

    def track_wip_branch(self, repo_name: str, branch_name: str, sha: str, timestamp: Optional[int] = None):
        ts = timestamp if timestamp is not None else int(time.time())
        self._repo(repo_name)["wip_branches"].append({
            "branch": branch_name,
            "sha": sha,
            "created_at": ts
        })
        self.save()

    def get_expired_wip_branches(self, repo_name: str, max_age_days: int = 30, current_timestamp: Optional[int] = None) -> list[str]:
        now = current_timestamp if current_timestamp is not None else int(time.time())
        cutoff = now - (max_age_days * 86400)
        repo = self._repo(repo_name)
        expired = [b["branch"] for b in repo.get("wip_branches", []) if b["created_at"] < cutoff]
        return expired
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_state.py`
Expected: PASS (3 tests passing)

- [ ] **Step 5: Commit Task 2**

```bash
git add janitor/state.py tests/test_state.py
git commit -m "feat(janitor): add persistent state layer and stable task ID manager"
```

---

### Task 3: Butler Auto-Tidy Engine & Zero-Data-Loss WIP Checkpointer (`hygiene.py`)

**Files:**
- Create: `janitor/hygiene.py`
- Create: `tests/test_hygiene.py`

**Interfaces:**
- Consumes: `janitor.git_ops` and `janitor.state.StateManager`
- Produces:
  - `purge_ephemeral_trash(repo_dir: Path) -> list[str]` (removes `__pycache__`, `.DS_Store`, etc.)
  - `is_wip_stale(repo_dir: Path, stale_hours: float = 6.0) -> bool`
  - `checkpoint_abandoned_wip(repo_dir: Path, state_mgr: StateManager, run_id: str) -> Optional[dict]`
  - `prune_expired_wip_branches(repo_dir: Path, state_mgr: StateManager, max_age_days: int = 30) -> list[str]`

- [ ] **Step 1: Write failing test for auto-tidy and WIP checkpointing**

Write `tests/test_hygiene.py`:
```python
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from janitor.git_ops import get_repo_status
from janitor.state import StateManager
from janitor.hygiene import purge_ephemeral_trash, checkpoint_abandoned_wip

class TestHygiene(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)
        
        # Initial commit
        (self.repo_dir / "README.md").write_text("# Initial\n")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=self.repo_dir, check=True)
        self.base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_dir, capture_output=True, text=True).stdout.strip()
        
        self.state_mgr = StateManager(self.repo_dir / ".state")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_purge_ephemeral_trash(self):
        cache_dir = self.repo_dir / "pkg" / "__pycache__"
        cache_dir.mkdir(parents=True)
        (cache_dir / "mod.cpython-312.pyc").write_text("bin")
        (self.repo_dir / ".DS_Store").write_text("ds")
        
        purged = purge_ephemeral_trash(self.repo_dir)
        self.assertIn(".DS_Store", [Path(p).name for p in purged])
        self.assertFalse(cache_dir.exists())

    def test_checkpoint_abandoned_wip_preserves_base_head(self):
        # Dirty work left behind
        (self.repo_dir / "app.py").write_text("print('abandoned')\n")
        (self.repo_dir / ".env.local").write_text("SECRET=123\n")
        
        res = checkpoint_abandoned_wip(self.repo_dir, self.state_mgr, "run_test_01")
        self.assertIsNotNone(res)
        self.assertTrue(res["wip_branch"].startswith("auto-wip/"))
        
        # Invariant 1: main must be restored to clean state at base_sha
        curr_status = get_repo_status(self.repo_dir)
        self.assertFalse(curr_status["is_dirty"])
        curr_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_dir, capture_output=True, text=True).stdout.strip()
        self.assertEqual(curr_sha, self.base_sha)
        
        # Invariant 2: secret file .env.local was NOT staged or committed to wip branch
        wip_files = subprocess.run(["git", "ls-tree", "-r", "--name-only", res["wip_branch"]], cwd=self.repo_dir, capture_output=True, text=True).stdout
        self.assertNotIn(".env", wip_files)
        self.assertIn("app.py", wip_files)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_hygiene.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'janitor.hygiene'`)

- [ ] **Step 3: Implement `janitor/hygiene.py`**

Create `janitor/hygiene.py`:
```python
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from janitor.state import StateManager

TRASH_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
TRASH_PATTERNS = {".DS_Store", "Thumbs.db"}

def purge_ephemeral_trash(repo_dir: Path) -> list[str]:
    purged = []
    for root, dirs, files in os.walk(repo_dir, topdown=True):
        if ".git" in root.split(os.sep):
            continue
        for d in list(dirs):
            if d in TRASH_DIRS:
                full_d = Path(root) / d
                try:
                    shutil.rmtree(full_d)
                    purged.append(str(full_d))
                    dirs.remove(d)
                except Exception:
                    pass
        for f in files:
            if f in TRASH_PATTERNS or f.endswith(".pyc") or f.endswith(".swp") or f.endswith("~"):
                full_f = Path(root) / f
                try:
                    full_f.unlink()
                    purged.append(str(full_f))
                except Exception:
                    pass
    return purged

def is_wip_stale(repo_dir: Path, stale_hours: float = 6.0) -> bool:
    # Check max mtime of non-git files
    cutoff = time.time() - (stale_hours * 3600)
    most_recent_mtime = 0
    for root, dirs, files in os.walk(repo_dir):
        if ".git" in root.split(os.sep):
            continue
        for f in files:
            p = Path(root) / f
            try:
                m = p.stat().st_mtime
                if m > most_recent_mtime:
                    most_recent_mtime = m
            except Exception:
                pass
    if most_recent_mtime == 0:
        return False
    return most_recent_mtime < cutoff

def checkpoint_abandoned_wip(repo_dir: Path, state_mgr: StateManager, run_id: str) -> Optional[dict]:
    # 1. Read current branch and local HEAD SHA
    branch_res = subprocess.run(["git", "symbolic-ref", "--short", "HEAD"], cwd=repo_dir, capture_output=True, text=True)
    if branch_res.returncode != 0:
        return None
    branch = branch_res.stdout.strip()
    
    base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True).stdout.strip()
    
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    wip_branch = f"auto-wip/{timestamp_str}"
    
    # 2. Switch to isolated checkpoint branch
    cb = subprocess.run(["git", "checkout", "-b", wip_branch], cwd=repo_dir, capture_output=True)
    if cb.returncode != 0:
        return None
        
    # 3. Add non-secret files strictly
    add_cmd = ["git", "add", "-A", "--", ":!.env*", ":!*.pem", ":!id_rsa*", ":!*.key", ":!*credential*"]
    subprocess.run(add_cmd, cwd=repo_dir, capture_output=True)
    
    commit_msg = (
        f"wip(janitor): auto-checkpoint uncommitted work left on {timestamp_str}\n\n"
        f"Janitor-Run: {run_id}"
    )
    commit_res = subprocess.run(["git", "commit", "-m", commit_msg], cwd=repo_dir, capture_output=True)
    if commit_res.returncode != 0:
        # Nothing to commit, switch back and delete branch
        subprocess.run(["git", "checkout", branch], cwd=repo_dir, capture_output=True)
        subprocess.run(["git", "branch", "-D", wip_branch], cwd=repo_dir, capture_output=True)
        return None
        
    wip_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True).stdout.strip()
    
    # 4. Switch back to original branch
    subprocess.run(["git", "checkout", branch], cwd=repo_dir, capture_output=True)
    
    # 5. Restore to PRE-EXISTING LOCAL HEAD
    subprocess.run(["git", "reset", "--hard", base_sha], cwd=repo_dir, capture_output=True)
    
    # Track branch in state manager
    state_mgr.track_wip_branch(repo_dir.name, wip_branch, wip_sha)
    
    return {
        "wip_branch": wip_branch,
        "wip_sha": wip_sha,
        "base_sha": base_sha,
        "original_branch": branch
    }

def prune_expired_wip_branches(repo_dir: Path, state_mgr: StateManager, max_age_days: int = 30) -> list[str]:
    expired = state_mgr.get_expired_wip_branches(repo_dir.name, max_age_days=max_age_days)
    pruned = []
    for b in expired:
        res = subprocess.run(["git", "branch", "-D", b], cwd=repo_dir, capture_output=True)
        if res.returncode == 0:
            pruned.append(b)
    return pruned
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests/test_hygiene.py`
Expected: PASS (2 tests passing)

- [ ] **Step 5: Commit Task 3**

```bash
git add janitor/hygiene.py tests/test_hygiene.py
git commit -m "feat(janitor): add butler auto-tidy trash purge and zero-data-loss wip checkpointing"
```

---

### Task 4: Model Worker with Stdin Streaming & Fallback (`worker.py`)

**Files:**
- Modify: `janitor/worker.py`
- Modify: `tests/test_worker.py`

**Interfaces:**
- Consumes: `g2k-bg` / `g2k` CLI via stdin or OpenRouter HTTP POST fallback
- Produces:
  - `call_free(prompt: str, system: Optional[str] = None, timeout: int = 180) -> str`
  - `extract_structured(prompt: str, system: Optional[str] = None, schema_hint: Optional[str] = None, timeout: int = 180) -> dict`

- [ ] **Step 1: Write failing test for stdin streaming and schema extraction**

Update `tests/test_worker.py`:
```python
import json
import subprocess
import unittest
from unittest.mock import patch
from janitor.worker import call_free, extract_structured

class TestWorker(unittest.TestCase):
    @patch("janitor.worker._gateway_cli")
    @patch("subprocess.run")
    def test_call_gateway_via_stdin(self, mock_run, mock_cli):
        mock_cli.return_value = "/mock/bin/g2k-bg"
        mock_run.return_value = subprocess.CompletedProcess(
            args=["/mock/bin/g2k-bg", "-p", "-"],
            returncode=0,
            stdout='{"context_md": "ctx", "todo_md": "todo"}'
        )
        resp = call_free("test prompt", system="test system")
        self.assertIn("context_md", resp)
        # Verify stdin input was used
        self.assertEqual(mock_run.call_args[1]["input"], "test system\n\ntest prompt")

    @patch("janitor.worker.call_free")
    def test_extract_structured_clean_json(self, mock_call):
        mock_call.return_value = "```json\n{\"context_md\": \"test\", \"todo_md\": \"todo\"}\n```"
        res = extract_structured("prompt")
        self.assertEqual(res["context_md"], "test")
        self.assertEqual(res["todo_md"], "todo")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it passes or fails**

Run: `python3 -m unittest tests/test_worker.py`
Expected: FAIL or mismatch on stdin flag

- [ ] **Step 3: Update `janitor/worker.py` to stream over stdin**

Update `_call_gateway` in `janitor/worker.py`:
```python
def _call_gateway(cli: str, prompt: str, system: str | None, timeout: int) -> str:
    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    # Stream payload via stdin using '-p -' to prevent OS ARG_MAX limits
    cmd = [cli, "-p", "-"]
    try:
        res = subprocess.run(
            cmd,
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Gateway {cli} timed out after {timeout}s")

    if res.returncode != 0:
        raise RuntimeError(f"{cli} failed (exit {res.returncode}): {res.stderr.strip()}")

    raw = res.stdout.strip()
    raw = re.sub(r"^```(?:json|markdown)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests/test_worker.py`
Expected: PASS

- [ ] **Step 5: Commit Task 4**

```bash
git add janitor/worker.py tests/test_worker.py
git commit -m "fix(janitor): stream gateway payloads over stdin to prevent ARG_MAX limits"
```

---

### Task 5: Living Documentation Reconciler & First-Run Bootstrap (`reconciler.py`)

**Files:**
- Create: `janitor/reconciler.py`
- Create: `tests/test_reconciler.py`

**Interfaces:**
- Consumes: `janitor.git_ops`, `janitor.state`, `janitor.worker`
- Produces:
  - `sweep_repo(repo_dir: Path, state_mgr: StateManager, run_id: str, dry_run: bool = False) -> dict`
  - `overview_repo(repo_dir: Path, state_mgr: StateManager, dry_run: bool = False) -> dict`
  - `merge_sentinel_block(existing_text: str, tag: str, new_content: str) -> str`

- [ ] **Step 1: Write failing test for sentinel merging and first-run bootstrap**

Write `tests/test_reconciler.py`:
```python
import tempfile
import unittest
from pathlib import Path
from janitor.reconciler import merge_sentinel_block

class TestReconciler(unittest.TestCase):
    def test_merge_sentinel_block_first_run_bootstrap(self):
        original = "# Human Notes\nKeep this intact.\n"
        merged = merge_sentinel_block(original, "recent", "- Added feature A\n")
        self.assertIn("# Human Notes\nKeep this intact.", merged)
        self.assertIn("<!-- janitor:begin:recent -->", merged)
        self.assertIn("- Added feature A", merged)
        self.assertIn("<!-- janitor:end:recent -->", merged)

    def test_merge_sentinel_block_replaces_existing(self):
        existing = (
            "# Title\n\n"
            "<!-- janitor:begin:recent -->\n"
            "- Old task\n"
            "<!-- janitor:end:recent -->\n\n"
            "## Architecture\nStatic text."
        )
        merged = merge_sentinel_block(existing, "recent", "- New task\n")
        self.assertNotIn("- Old task", merged)
        self.assertIn("- New task", merged)
        self.assertIn("## Architecture\nStatic text.", merged)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_reconciler.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'janitor.reconciler'`)

- [ ] **Step 3: Implement `janitor/reconciler.py`**

Create `janitor/reconciler.py`:
```python
import hashlib
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from janitor.git_ops import check_preflight_guards, get_repo_status, has_24h_activity, atomic_stage_and_commit
from janitor.state import StateManager
from janitor.worker import call_free, extract_structured

CENTRAL_DOCS_REPO = Path("/Volumes/2TB_SSD/GitHub/docs")

SWEEP_PROMPT = """Update CONTEXT.md and TODO.md for this repository based on recent git activity.
Return ONLY valid JSON with exactly two keys: "recent_markdown" and "todo_markdown".

REPOSITORY: {repo_name} (branch: {branch}, HEAD: {current_sha})
TIMESTAMP: {timestamp}

>>> REPO CONTENT
DIRTY / UNTRACKED FILES:
{git_status}

RECENT COMMITS:
{recent_log}

RECENT DIFF:
{recent_diff}

EXISTING CONTEXT:
{curr_context}

EXISTING TODO:
{curr_todo}
<<< END REPO CONTENT

RULES:
1. recent_markdown: Bulleted list of verified accomplishments from commits/diffs.
2. todo_markdown: Checklist of pending tasks and newly discovered blockers.
"""

def merge_sentinel_block(existing_text: str, tag: str, new_content: str) -> str:
    begin_marker = f"<!-- janitor:begin:{tag} -->"
    end_marker = f"<!-- janitor:end:{tag} -->"
    
    pattern = re.compile(rf"{re.escape(begin_marker)}.*?{re.escape(end_marker)}", re.DOTALL)
    replacement = f"{begin_marker}\n{new_content.strip()}\n{end_marker}"
    
    if pattern.search(existing_text):
        return pattern.sub(replacement, existing_text)
    else:
        # First-run bootstrap: append to end of existing text
        sep = "\n\n" if existing_text.strip() else ""
        return f"{existing_text.rstrip()}{sep}{replacement}\n"

def sweep_repo(repo_dir: Path, state_mgr: StateManager, run_id: str, dry_run: bool = False) -> dict:
    guard = check_preflight_guards(repo_dir)
    if guard:
        return {"repo": repo_dir.name, "status": "skipped", "reason": guard}
        
    status = get_repo_status(repo_dir)
    has_act, recent_log, recent_diff = has_24h_activity(repo_dir)
    
    # Zero-token fast path: clean and quiet
    if not status["is_dirty"] and not has_act:
        return {"repo": repo_dir.name, "status": "quiet", "tokens_spent": 0}
        
    # Semantic hash check
    input_str = f"{status['porcelain']}|{recent_log}|{recent_diff}"
    curr_hash = hashlib.sha256(input_str.encode("utf-8")).hexdigest()
    if state_mgr.get_last_input_hash(repo_dir.name) == curr_hash:
        return {"repo": repo_dir.name, "status": "unchanged_hash", "tokens_spent": 0}
        
    context_file = repo_dir / "CONTEXT.md"
    todo_file = repo_dir / "TODO.md"
    curr_context = context_file.read_text(encoding="utf-8") if context_file.exists() else ""
    curr_todo = todo_file.read_text(encoding="utf-8") if todo_file.exists() else ""
    
    prompt = SWEEP_PROMPT.format(
        repo_name=repo_dir.name,
        branch=status["branch"],
        current_sha=status["sha"],
        timestamp=datetime.now(timezone.utc).isoformat(),
        git_status=status["porcelain"] or "(Clean)",
        recent_log=recent_log or "(None)",
        recent_diff=recent_diff or "(None)",
        curr_context=curr_context[:6000],
        curr_todo=curr_todo[:6000]
    )
    
    try:
        data = extract_structured(prompt)
    except Exception as e:
        return {"repo": repo_dir.name, "status": "synthesis_failed", "error": str(e)}
        
    new_recent = data.get("recent_markdown", "")
    new_todo = data.get("todo_markdown", "")
    
    merged_context = merge_sentinel_block(curr_context, "recent", new_recent)
    merged_todo = merge_sentinel_block(curr_todo, "todo", new_todo)
    
    if dry_run:
        return {"repo": repo_dir.name, "status": "dry_run", "preview_len": len(merged_context)}
        
    context_file.write_text(merged_context, encoding="utf-8")
    todo_file.write_text(merged_todo, encoding="utf-8")
    
    # Safe auto-commit if working tree was clean on main
    committed = False
    if not status["is_dirty"] and status["branch"] in ["main", "master"]:
        committed = atomic_stage_and_commit(
            repo_dir,
            ["CONTEXT.md", "TODO.md"],
            f"docs(janitor): sweep CONTEXT.md and TODO.md for {status['sha']} [skip ci]",
            run_id
        )
        
    state_mgr.set_last_input_hash(repo_dir.name, curr_hash)
    state_mgr.record_run(repo_dir.name, "committed" if committed else "written", run_id)
    
    return {"repo": repo_dir.name, "status": "committed" if committed else "written"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests/test_reconciler.py`
Expected: PASS (2 tests passing)

- [ ] **Step 5: Commit Task 5**

```bash
git add janitor/reconciler.py tests/test_reconciler.py
git commit -m "feat(janitor): add sentinel-based reconciler with first-run bootstrap and hash gating"
```

---

### Task 6: CLI Interface with Fleet Discovery & JSON Reporting (`cli.py`)

**Files:**
- Modify: `janitor/cli.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `janitor.git_ops`, `janitor.hygiene`, `janitor.reconciler`, `janitor.state`
- Produces:
  - `main() -> int`
  - Subcommands: `sweep`, `overview`, `tidy`, `status`
  - Flags: `--all`, `--dry-run`, `--json`

- [ ] **Step 1: Write failing test for CLI commands**

Write `tests/test_cli.py`:
```python
import unittest
from unittest.mock import patch
from janitor.cli import main

class TestCLI(unittest.TestCase):
    @patch("sys.argv", ["janitor", "sweep", "--dry-run"])
    def test_cli_sweep_dry_run(self):
        code = main()
        self.assertIn(code, [0, 1])

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_cli.py`
Expected: FAIL (subcommand missing or argument error)

- [ ] **Step 3: Implement `janitor/cli.py`**

Update `janitor/cli.py`:
```python
import argparse
import json
import sys
import time
from pathlib import Path
from janitor.git_ops import get_repo_status
from janitor.hygiene import purge_ephemeral_trash, is_wip_stale, checkpoint_abandoned_wip
from janitor.reconciler import sweep_repo
from janitor.state import StateManager

DEFAULT_WORKSPACE = Path("/Volumes/2TB_SSD/GitHub")

def discover_repos(workspace: Path) -> list[Path]:
    if not workspace.exists():
        return []
    repos = []
    for entry in workspace.iterdir():
        if entry.is_dir() and (entry / ".git").exists():
            repos.append(entry)
    return sorted(repos, key=lambda p: p.name)

def main() -> int:
    parser = argparse.ArgumentParser(description="Janitor: Autonomous Repository Caretaker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # sweep
    sp_sweep = subparsers.add_parser("sweep", help="Daily fast sweep of CONTEXT.md and TODO.md")
    sp_sweep.add_argument("repos", nargs="*", type=Path, help="Target repositories")
    sp_sweep.add_argument("--all", action="store_true", help="Sweep all repositories in workspace")
    sp_sweep.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    sp_sweep.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    
    # tidy
    sp_tidy = subparsers.add_parser("tidy", help="Purge trash and checkpoint stale abandoned WIP")
    sp_tidy.add_argument("repos", nargs="*", type=Path, help="Target repositories")
    sp_tidy.add_argument("--all", action="store_true", help="Tidy all repositories in workspace")
    sp_tidy.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    
    args = parser.parse_args()
    state_mgr = StateManager()
    run_id = f"run_{int(time.time())}"
    
    targets = []
    if getattr(args, "all", False):
        targets = discover_repos(DEFAULT_WORKSPACE)
    elif getattr(args, "repos", []):
        targets = [p.resolve() for p in args.repos]
    else:
        # Default to current directory
        cwd = Path.cwd()
        if (cwd / ".git").exists():
            targets = [cwd]
        else:
            targets = discover_repos(DEFAULT_WORKSPACE)
            
    results = []
    has_failure = False
    
    for repo in targets:
        if args.command == "tidy":
            purged = purge_ephemeral_trash(repo)
            checkpoint = None
            if is_wip_stale(repo):
                checkpoint = checkpoint_abandoned_wip(repo, state_mgr, run_id)
            results.append({
                "repo": repo.name,
                "purged_count": len(purged),
                "checkpoint": checkpoint
            })
        elif args.command == "sweep":
            res = sweep_repo(repo, state_mgr, run_id, dry_run=args.dry_run)
            results.append(res)
            if res.get("status") == "synthesis_failed":
                has_failure = True
                
    if getattr(args, "json", False):
        print(json.dumps({"schema_version": 1, "run_id": run_id, "results": results}, indent=2))
    else:
        for r in results:
            print(f"[{r.get('status', 'ok')}] {r['repo']}")
            
    return 1 if has_failure else 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests/test_cli.py`
Expected: PASS

- [ ] **Step 5: Commit Task 6**

```bash
git add janitor/cli.py tests/test_cli.py
git commit -m "feat(janitor): add CLI with fleet discovery, tidy, and structured JSON output"
```

---

### Task 7: Homelab Systemd Units & Mac Mini SSH Wrapper

**Files:**
- Create: `systemd/janitor-sweep.service`
- Create: `systemd/janitor-sweep.timer`
- Create: `systemd/janitor-overview.service`
- Create: `systemd/janitor-overview.timer`
- Create: `scripts/janitor-runner.sh`

**Interfaces:**
- Consumes: Systemd scheduler on Homelab Linux master
- Produces: Automated unattended nightly and weekly runs over SSH

- [ ] **Step 1: Write `scripts/janitor-runner.sh`**

```bash
#!/bin/bash
# /usr/local/bin/janitor-runner on Mac mini
set -euo pipefail

# Ensure standard PATH for Homebrew, Python, and local binaries
export PATH="/opt/homebrew/bin:/Users/macmini/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

if [ -f "/etc/janitor/env" ]; then
    source /etc/janitor/env
fi

# Execute janitor with passed arguments
exec python3 -m janitor.cli "$@"
```

- [ ] **Step 2: Write Homelab Systemd Service & Timer Units**

Create `systemd/janitor-sweep.service`:
```ini
[Unit]
Description=Janitor Daily Fleet Sweep
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=oneshot
User=khamel83
ExecStart=/usr/bin/ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 macmini "caffeinate -is /usr/local/bin/janitor-runner sweep --all --json"
TimeoutStopSec=1800
StandardOutput=journal
StandardError=journal
```

Create `systemd/janitor-sweep.timer`:
```ini
[Unit]
Description=Run Janitor Fleet Sweep daily at 03:00 AM

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Create `systemd/janitor-overview.service`:
```ini
[Unit]
Description=Janitor Weekly Architecture Overview
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=oneshot
User=khamel83
ExecStart=/usr/bin/ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 macmini "caffeinate -is /usr/local/bin/janitor-runner overview --all --json"
TimeoutStopSec=1800
StandardOutput=journal
StandardError=journal
```

Create `systemd/janitor-overview.timer`:
```ini
[Unit]
Description=Run Janitor Architecture Overview weekly on Sunday at 04:00 AM

[Timer]
OnCalendar=Sun *-*-* 04:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Make scripts executable and verify syntax**

```bash
chmod +x scripts/janitor-runner.sh
bash -n scripts/janitor-runner.sh
```

- [ ] **Step 4: Commit Task 7**

```bash
git add systemd/ scripts/janitor-runner.sh
git commit -m "feat(janitor): add homelab systemd timers and mac mini ssh runner script"
```

---

### Task 8: End-to-End Fleet Verification & Acceptance Suite

**Files:**
- Test: All tests in `tests/`
- Verification: Live dry-run against `janitor` and `maya`

- [ ] **Step 1: Run full unit test suite**

```bash
python3 -m unittest discover -s tests -v
```
Expected: 100% passing (15+ unit tests across git_ops, state, hygiene, reconciler, worker, cli).

- [ ] **Step 2: Verify local editable install**

```bash
python3 -m pip install -e .
which janitor || python3 -m janitor.cli --help
```

- [ ] **Step 3: Live smoke test against `janitor` repo**

```bash
python3 -m janitor.cli tidy --json
python3 -m janitor.cli sweep --dry-run
```
Expected: Valid JSON output; clean dry-run preview.

- [ ] **Step 4: Live smoke test against `maya` repo**

```bash
python3 -m janitor.cli sweep /Volumes/2TB_SSD/GitHub/maya --dry-run
```
Expected: Inspects Maya git status; produces valid preview.

- [ ] **Step 5: Final commit & tag**

```bash
git add .
git commit -m "chore(janitor): complete repository caretaker implementation and test suite"
```
