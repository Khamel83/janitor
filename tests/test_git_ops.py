import subprocess
import tempfile
from unittest import mock
import unittest
from pathlib import Path

from janitor.git_ops import (
    atomic_stage_and_commit,
    check_preflight_guards,
    get_repo_status,
    has_24h_activity,
)


class TestGitOps(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)
        subprocess.run(
            ["git", "init", "-b", "main"], cwd=self.repo_dir, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"], cwd=self.repo_dir, check=True
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True
        )
        subprocess.run(
            ["git", "config", "commit.gpgsign", "false"], cwd=self.repo_dir, check=True
        )

        # Initial commit
        (self.repo_dir / "README.md").write_text("# Test Repo\n")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo_dir, check=True)
        subprocess.run(
            ["git", "commit", "-m", "initial commit"], cwd=self.repo_dir, check=True
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_preflight_guards_clean_repo(self):
        self.assertIsNone(check_preflight_guards(self.repo_dir))

    def test_preflight_guards_not_a_repo(self):
        bare = self.repo_dir / "no-git-here"
        bare.mkdir()
        self.assertEqual(check_preflight_guards(bare), "not_a_git_repo")

    def test_preflight_guards_index_lock(self):
        lock_file = self.repo_dir / ".git" / "index.lock"
        lock_file.write_text("")
        self.assertEqual(check_preflight_guards(self.repo_dir), "git_index_locked")

    def test_preflight_guards_merge_in_progress(self):
        merge_head = self.repo_dir / ".git" / "MERGE_HEAD"
        merge_head.write_text("0000000000000000000000000000000000000000\n")
        self.assertEqual(check_preflight_guards(self.repo_dir), "merge_in_progress")

    def test_preflight_resolves_git_pointer_and_detects_worktree_lock(self):
        worktree = Path(self.temp_dir.name) / "linked"
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", "feature", str(worktree)],
            cwd=self.repo_dir,
            check=True,
        )
        git_dir = Path(
            subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=worktree,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        if not git_dir.is_absolute():
            git_dir = (worktree / git_dir).resolve()
        (git_dir / "index.lock").write_text("")
        self.assertEqual(check_preflight_guards(worktree), "git_index_locked")

    def test_preflight_detects_common_operation_marker_from_linked_worktree(self):
        worktree = Path(self.temp_dir.name) / "linked"
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", "feature", str(worktree)],
            cwd=self.repo_dir,
            check=True,
        )
        git_dir = Path(
            subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                cwd=worktree,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        if not git_dir.is_absolute():
            git_dir = (worktree / git_dir).resolve()
        (git_dir / "MERGE_HEAD").write_text("0" * 40 + "\n")
        self.assertEqual(check_preflight_guards(worktree), "merge_in_progress")

    def test_preflight_guards_detached_head(self):
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_dir, capture_output=True, text=True
        ).stdout.strip()
        subprocess.run(["git", "checkout", sha], cwd=self.repo_dir, capture_output=True, check=True)
        self.assertEqual(check_preflight_guards(self.repo_dir), "detached_head")

    def test_repo_status_clean(self):
        status = get_repo_status(self.repo_dir)
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(
            status,
            {"is_dirty": False, "porcelain": "", "branch": "main", "sha": sha},
        )

    def test_repo_status_dirty(self):
        (self.repo_dir / "README.md").write_text("# Test Repo\nchanged\n")
        (self.repo_dir / "scratch.txt").write_text("untracked\n")
        status = get_repo_status(self.repo_dir)
        self.assertTrue(status["is_dirty"])
        self.assertIn("README.md", status["porcelain"])
        self.assertIn("scratch.txt", status["porcelain"])
        self.assertEqual(status["branch"], "main")
        self.assertTrue(status["sha"])

    def test_has_24h_activity_excludes_janitor_run(self):
        # Janitor commit with trailer
        (self.repo_dir / "doc.md").write_text("doc\n")
        subprocess.run(["git", "add", "doc.md"], cwd=self.repo_dir, check=True)
        msg = "docs(janitor): sweep\n\nJanitor-Run: 20260907-0300"
        subprocess.run(["git", "commit", "-m", msg], cwd=self.repo_dir, check=True)

        has_act, log, diff = has_24h_activity(self.repo_dir)
        # The Janitor-Run commit must be excluded; the initial commit remains.
        self.assertTrue(has_act)
        self.assertNotIn("docs(janitor)", log)
        self.assertIn("initial commit", log)

    def test_atomic_stage_and_commit(self):
        (self.repo_dir / "CONTEXT.md").write_text("Context\n")
        (self.repo_dir / "TODO.md").write_text("Todo\n")
        success = atomic_stage_and_commit(
            self.repo_dir,
            ["CONTEXT.md", "TODO.md"],
            "docs(janitor): sweep CONTEXT.md and TODO.md [skip ci]",
            "run_12345",
        )
        self.assertTrue(success)
        log = subprocess.run(
            ["git", "log", "-n", "1"], cwd=self.repo_dir, capture_output=True, text=True
        ).stdout
        self.assertIn("docs(janitor): sweep CONTEXT.md and TODO.md [skip ci]", log)
        self.assertIn("Janitor-Run: run_12345", log)
        # Commit contains exactly the authorized files, nothing left staged.
        committed = subprocess.run(
            ["git", "show", "--pretty=format:", "--name-only", "HEAD"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
        ).stdout.split()
        self.assertEqual(sorted(committed), ["CONTEXT.md", "TODO.md"])
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
        ).stdout
        self.assertEqual(staged, "")

    def test_atomic_stage_and_commit_rejects_unauthorized_staged_files(self):
        (self.repo_dir / "CONTEXT.md").write_text("Context\n")
        (self.repo_dir / "TODO.md").write_text("Todo\n")
        # An unauthorized file is already staged before the call.
        subprocess.run(["git", "add", "TODO.md"], cwd=self.repo_dir, check=True)

        success = atomic_stage_and_commit(
            self.repo_dir,
            ["CONTEXT.md"],
            "docs(janitor): context only",
            "run_1",
        )
        self.assertFalse(success)
        # Nothing was committed and the index was reset.
        log = subprocess.run(
            ["git", "log", "-n", "1"], cwd=self.repo_dir, capture_output=True, text=True
        ).stdout
        self.assertNotIn("context only", log)
        self.assertNotIn("Janitor-Run: run_1", log)
        self.assertNotIn("TODO.md", log)
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
        ).stdout
        self.assertEqual(staged, "")

    def test_atomic_stage_and_commit_missing_file(self):
        success = atomic_stage_and_commit(
            self.repo_dir,
            ["CONTEXT.md", "DOES-NOT-EXIST.md"],
            "docs(janitor): bad add",
            "run_2",
        )
        self.assertFalse(success)
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
        ).stdout
        self.assertEqual(staged, "")

    def test_sh_timeout_returns_empty_string(self):
        # Importing _sh inside the test keeps the patched symbol local.
        from janitor.git_ops import _sh as imported_sh

        with mock.patch("janitor.git_ops.subprocess.run") as mocked_run:
            mocked_run.side_effect = subprocess.TimeoutExpired(
                cmd=["git", "rev-parse"], timeout=0.1
            )
            self.assertEqual(imported_sh(["git", "rev-parse"], self.repo_dir), "")


if __name__ == "__main__":
    unittest.main()
