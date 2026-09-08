"""Tests for janitor.worker's backend selection: no real network calls are made here.

Covers the two failure paths that need to fail gracefully rather than crash:
no gateway CLI on PATH and no OPENROUTER_API_KEY set (nothing to call at all),
and the same absence surfacing as a clean "synthesis_failed" from a caller
like sweep_docs rather than an uncaught exception.

Run with: python3 -m unittest discover -s tests
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from janitor.worker import call_free
from janitor.docs import sweep_docs


def _git_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


class NoBackendTestCase(unittest.TestCase):
    """Empty PATH (no g2k-bg/g2k) and no OPENROUTER_API_KEY: nothing to call."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._old_path = os.environ.get("PATH", "")
        # Real git must stay reachable, but g2k-bg/g2k must not be — so scope
        # PATH to just git's own directory rather than an empty one.
        git_path = shutil.which("git")
        assert git_path, "git must be on PATH to run this test"
        os.environ["PATH"] = str(Path(git_path).parent)
        self._old_key = os.environ.pop("OPENROUTER_API_KEY", None)

    def tearDown(self):
        os.environ["PATH"] = self._old_path
        if self._old_key is not None:
            os.environ["OPENROUTER_API_KEY"] = self._old_key
        self._tmp.cleanup()

    def test_call_free_raises_clear_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            call_free("hello")
        self.assertIn("OPENROUTER_API_KEY", str(ctx.exception))

    def test_sweep_docs_fails_gracefully_not_with_exception(self):
        repo = _git_repo(self.tmp)
        result = sweep_docs(project_dir=str(repo))
        self.assertEqual(result["status"], "synthesis_failed")


if __name__ == "__main__":
    unittest.main()
