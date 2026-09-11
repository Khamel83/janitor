import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from janitor.git_ops import get_repo_status
from janitor.hygiene import (
    checkpoint_abandoned_wip,
    is_wip_stale,
    prune_expired_wip_branches,
    purge_ephemeral_trash,
)
from janitor.state import StateManager


class TestHygiene(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.repo_dir = base / "repo"
        self.repo_dir.mkdir()

        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=self.repo_dir,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.repo_dir,
            check=True,
        )

        (self.repo_dir / "README.md").write_text("# Initial\n")
        subprocess.run(
            ["git", "add", "README.md"], cwd=self.repo_dir, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "initial commit"],
            cwd=self.repo_dir,
            check=True,
        )
        self.base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        # State lives OUTSIDE the repo so it never shows up as untracked
        # dirt in porcelain assertions.
        self.state_mgr = StateManager(base / "state")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _git(self, *args, check=True):
        res = subprocess.run(
            ["git", *args], cwd=self.repo_dir, capture_output=True, text=True
        )
        if check and res.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {res.stderr}")
        return res.stdout.strip()

    def _wip_records(self, branch):
        data = json.loads(self.state_mgr.state_file.read_text(encoding="utf-8"))
        records = data[self.repo_dir.name]["wip_branches"]
        return [r for r in records if r["branch"] == branch]

    # ------------------------------------------------------------- purge

    def test_purge_ephemeral_trash_removes_cache_and_droppings_keeps_sources(self):
        pkg = self.repo_dir / "pkg"
        pkg.mkdir()
        (pkg / "mod.py").write_text("print('source')\n")
        cache_dir = pkg / "__pycache__"
        cache_dir.mkdir(parents=True)
        (cache_dir / "mod.cpython-312.pyc").write_text("bin")
        (self.repo_dir / ".pytest_cache").mkdir()
        (self.repo_dir / ".pytest_cache" / "README.md").write_text("cache")
        (self.repo_dir / ".DS_Store").write_text("ds")
        (self.repo_dir / "Thumbs.db").write_text("thumbs")
        (self.repo_dir / "stray.pyc").write_text("bin")
        (self.repo_dir / "notes.txt~").write_text("backup")
        # Junk that must survive: source file and anything under .git.
        git_cache = self.repo_dir / ".git" / "__pycache__"
        git_cache.mkdir()
        (git_cache / "keep.pyc").write_text("bin")

        purged = purge_ephemeral_trash(self.repo_dir)
        purged_names = [Path(p).name for p in purged]

        self.assertIn(".DS_Store", purged_names)
        self.assertIn("Thumbs.db", purged_names)
        self.assertIn("stray.pyc", purged_names)
        self.assertIn("notes.txt~", purged_names)
        self.assertFalse(cache_dir.exists())
        self.assertFalse((self.repo_dir / ".pytest_cache").exists())
        # Source and git internals left intact.
        self.assertTrue(pkg.exists())
        self.assertEqual((pkg / "mod.py").read_text(), "print('source')\n")
        self.assertEqual((self.repo_dir / "README.md").read_text(), "# Initial\n")
        self.assertTrue((git_cache / "keep.pyc").exists())
        self.assertNotIn("keep.pyc", purged_names)

    # ------------------------------------------------------------ staleness

    def test_is_wip_stale_true_when_every_working_file_is_old(self):
        old = time.time() - (48 * 3600)
        os.utime(self.repo_dir / "README.md", (old, old))
        draft = self.repo_dir / "draft.py"
        draft.write_text("wip\n")
        os.utime(draft, (old, old))

        self.assertTrue(is_wip_stale(self.repo_dir))

    def test_is_wip_stale_false_after_a_recent_change(self):
        old = time.time() - (48 * 3600)
        os.utime(self.repo_dir / "README.md", (old, old))
        draft = self.repo_dir / "draft.py"
        draft.write_text("wip\n")
        os.utime(draft, (old, old))

        (self.repo_dir / "fresh.md").write_text("now\n")
        self.assertFalse(is_wip_stale(self.repo_dir))

    def test_is_wip_stale_ignores_linked_worktree_git_pointer(self):
        worktree = Path(self.temp_dir.name) / "linked"
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", "feature", str(worktree)],
            cwd=self.repo_dir,
            check=True,
        )
        old = time.time() - 48 * 3600
        os.utime(worktree / ".git", (old, old))
        self.assertFalse(is_wip_stale(worktree))

    # --------------------------------------------------------- checkpoint

    def test_checkpoint_creates_wip_branch_commits_trailer_restores_base(self):
        # Dirty work: a tracked modification plus a brand-new untracked file.
        (self.repo_dir / "README.md").write_text("# Modified\n")
        (self.repo_dir / "notes.py").write_text("print('notes')\n")

        res = checkpoint_abandoned_wip(self.repo_dir, self.state_mgr, "run_test_01")

        self.assertIsNotNone(res)
        self.assertTrue(res["wip_branch"].startswith("auto-wip/"))
        self.assertEqual(res["base_sha"], self.base_sha)
        self.assertEqual(res["original_branch"], "main")
        self.assertNotEqual(res["wip_sha"], self.base_sha)

        # Invariant: original branch restored to the pre-existing local HEAD,
        # tracked working tree clean again.
        self.assertEqual(self._git("rev-parse", "HEAD"), self.base_sha)
        self.assertEqual(self._git("symbolic-ref", "--short", "HEAD"), "main")
        self.assertEqual(self._git("status", "--porcelain"), "")
        self.assertFalse(get_repo_status(self.repo_dir)["is_dirty"])
        self.assertEqual(
            (self.repo_dir / "README.md").read_text(), "# Initial\n"
        )

        # WIP branch exists locally and captured both pieces of work with a
        # Janitor-Run trailer.
        branches = [b.strip() for b in self._git("branch", "--list", "auto-wip/*").splitlines()]
        self.assertIn(res["wip_branch"], branches)
        self.assertEqual(
            self._git("show", f"{res['wip_branch']}:README.md"), "# Modified"
        )
        self.assertEqual(
            self._git("show", f"{res['wip_branch']}:notes.py"), "print('notes')"
        )
        message = self._git("log", "-1", "--pretty=%B", res["wip_branch"])
        self.assertIn("Janitor-Run: run_test_01", message)
        self.assertIn("wip(janitor)", message)

        # Invariant: the branch was recorded in StateManager.
        records = self._wip_records(res["wip_branch"])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["sha"], res["wip_sha"])

    def test_checkpoint_leaves_secrets_untouched_and_uncommitted(self):
        (self.repo_dir / "app.py").write_text("print('abandoned')\n")
        secret = self.repo_dir / ".env.local"
        secret.write_text("SECRET=123\n")

        res = checkpoint_abandoned_wip(self.repo_dir, self.state_mgr, "run_test_02")

        self.assertIsNotNone(res)
        self.assertTrue(res["wip_branch"].startswith("auto-wip/"))

        # Secret file still present, byte-identical, and uncommitted anywhere:
        # the only thing git sees is the untracked .env.local.
        self.assertTrue(secret.exists())
        self.assertEqual(secret.read_text(), "SECRET=123\n")
        self.assertEqual(self._git("status", "--porcelain"), "?? .env.local")
        self.assertEqual(self._git("diff", "--cached", "--name-only"), "")

        # Non-secret work was captured on the WIP branch; the secret was not.
        self.assertFalse((self.repo_dir / "app.py").exists())
        tree = self._git("ls-tree", "-r", "--name-only", res["wip_branch"]).splitlines()
        self.assertIn("app.py", tree)
        self.assertFalse(any(".env" in entry for entry in tree))

        # Original branch back at the pre-existing local HEAD.
        self.assertEqual(self._git("rev-parse", "HEAD"), self.base_sha)
        self.assertEqual(self._git("symbolic-ref", "--short", "HEAD"), "main")
        self.assertEqual(
            (self.repo_dir / "README.md").read_text(), "# Initial\n"
        )

    def test_checkpoint_returns_none_when_nothing_to_commit(self):
        res = checkpoint_abandoned_wip(self.repo_dir, self.state_mgr, "run_test_03")

        self.assertIsNone(res)
        self.assertEqual(self._git("rev-parse", "HEAD"), self.base_sha)
        self.assertEqual(self._git("symbolic-ref", "--short", "HEAD"), "main")
        self.assertEqual(self._git("branch", "--list", "auto-wip/*"), "")

    def test_checkpoint_restores_local_head_when_origin_is_ahead(self):
        # Fabricate an origin/main ref that is AHEAD of local main. A buggy
        # "restore to origin/main" implementation would move HEAD to this
        # commit; the real one must stay on the pre-existing local HEAD.
        self._git("checkout", "-q", "-b", "diverged")
        (self.repo_dir / "remote.txt").write_text("remote work\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "remote work")
        origin_sha = self._git("rev-parse", "HEAD")
        self.assertNotEqual(origin_sha, self.base_sha)
        self._git("checkout", "-q", "main")
        self._git("branch", "-q", "-D", "diverged")
        self._git("update-ref", "refs/remotes/origin/main", origin_sha)
        self.assertEqual(self._git("rev-parse", "origin/main"), origin_sha)

        (self.repo_dir / "app.py").write_text("print('abandoned')\n")
        res = checkpoint_abandoned_wip(self.repo_dir, self.state_mgr, "run_test_04")

        self.assertIsNotNone(res)
        self.assertEqual(res["base_sha"], self.base_sha)
        # Restored to pre-existing LOCAL HEAD, not the newer origin/main.
        self.assertEqual(self._git("rev-parse", "HEAD"), self.base_sha)
        self.assertNotEqual(self._git("rev-parse", "HEAD"), origin_sha)
        self.assertEqual(self._git("status", "--porcelain"), "")

    # -------------------------------------------------------------- prune

    def test_prune_expired_wip_branches_deletes_only_old_local_branches(self):
        old_branch = "auto-wip/20250101-000000"
        fresh_branch = "auto-wip/20250908-120000"
        ghost_branch = "auto-wip/20240101-000000"  # recorded but never created
        self._git("branch", old_branch, self.base_sha)
        self._git("branch", fresh_branch, self.base_sha)
        now = int(time.time())
        self.state_mgr.track_wip_branch(
            self.repo_dir.name, old_branch, self.base_sha,
            timestamp=now - (40 * 86400),
        )
        self.state_mgr.track_wip_branch(
            self.repo_dir.name, fresh_branch, self.base_sha, timestamp=now
        )
        self.state_mgr.track_wip_branch(
            self.repo_dir.name, ghost_branch, self.base_sha,
            timestamp=now - (40 * 86400),
        )

        pruned = prune_expired_wip_branches(self.repo_dir, self.state_mgr)

        self.assertEqual(pruned, [old_branch])
        branches = [
            b.strip()
            for b in self._git("branch", "--list", "auto-wip/*").splitlines()
        ]
        self.assertNotIn(old_branch, branches)
        self.assertIn(fresh_branch, branches)

        # Current branch untouched by pruning.
        self.assertEqual(self._git("symbolic-ref", "--short", "HEAD"), "main")
        self.assertEqual(self._git("rev-parse", "HEAD"), self.base_sha)

    def test_prune_expired_wip_branches_never_deletes_non_wip_names(self):
        decoy = "release-candidate"
        self._git("branch", decoy, self.base_sha)
        now = int(time.time())
        self.state_mgr.track_wip_branch(
            self.repo_dir.name, decoy, self.base_sha, timestamp=now - (40 * 86400)
        )

        pruned = prune_expired_wip_branches(self.repo_dir, self.state_mgr)

        self.assertEqual(pruned, [])
        branches = [b.strip() for b in self._git("branch", "--list").splitlines()]
        self.assertIn(decoy, branches)


if __name__ == "__main__":
    unittest.main()
