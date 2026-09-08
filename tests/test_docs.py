"""Regression tests for janitor.docs — covers bugs found and fixed via PR review:
first-sweep auto-commit, CLAUDE.md symlink safety, and overview validation.

Uses a fake `g2k-bg` on PATH instead of a real model gateway or OPENROUTER_API_KEY.
Run with: python3 -m unittest discover -s tests
"""

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from janitor.docs import ensure_claude_symlink, generate_overview, sweep_docs

VALID_OVERVIEW_BODY = """# LLM-OVERVIEW — repo
## What this repo is
Test repo.
## Machine & Host Ownership
Single host.
## What is actually built
Nothing real.
## Canonical entry points
None.
"""


def _write_fake_gateway(bin_dir: Path, response: str):
    script = bin_dir / "g2k-bg"
    script.write_text(f"#!/bin/bash\ncat <<'RESPONSE'\n{response}\nRESPONSE\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


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


class GatewayTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()
        self._old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{self.bin_dir}:{self._old_path}"
        self._old_probe = os.environ.pop("JANITOR_RUN_STATUS_PROBE", None)

    def tearDown(self):
        os.environ["PATH"] = self._old_path
        if self._old_probe is not None:
            os.environ["JANITOR_RUN_STATUS_PROBE"] = self._old_probe
        self._tmp.cleanup()


class TestSweepDocs(GatewayTestCase):
    def test_first_sweep_creates_and_commits(self):
        """Regression: CONTEXT.md/TODO.md didn't exist yet -> commit guard never fired."""
        _write_fake_gateway(self.bin_dir, '{"context_md":"# Context\\nfake","todo_md":"# TODO\\n- [ ] fake"}')
        repo = _git_repo(self.tmp)

        result = sweep_docs(project_dir=str(repo))

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["committed"])
        self.assertTrue((repo / "CONTEXT.md").exists())
        self.assertTrue((repo / "TODO.md").exists())
        log = subprocess.run(["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True).stdout
        self.assertIn("sweep CONTEXT.md and TODO.md", log)

    def test_dirty_tree_writes_drafts_and_does_not_commit(self):
        _write_fake_gateway(self.bin_dir, '{"context_md":"# Context\\nfake","todo_md":"# TODO\\n- [ ] fake"}')
        repo = _git_repo(self.tmp)
        (repo / "dirty.txt").write_text("uncommitted\n")

        result = sweep_docs(project_dir=str(repo))

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["committed"])
        self.assertTrue((repo / "CONTEXT.draft.md").exists())
        self.assertTrue((repo / "TODO.draft.md").exists())
        self.assertFalse((repo / "CONTEXT.md").exists())

    def test_creating_claude_symlink_does_not_block_first_commit(self):
        """Regression: creating CLAUDE.md made the tree look dirty, forcing draft
        mode on every first sweep of a repo with AGENTS.md but no CLAUDE.md yet."""
        _write_fake_gateway(self.bin_dir, '{"context_md":"# Context\\nfake","todo_md":"# TODO\\n- [ ] fake"}')
        repo = _git_repo(self.tmp)
        (repo / "AGENTS.md").write_text("rules\n")
        subprocess.run(["git", "add", "AGENTS.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add agents"], cwd=repo, check=True)

        result = sweep_docs(project_dir=str(repo))

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["committed"])
        self.assertFalse(result["dirty_tree"])
        self.assertTrue((repo / "CLAUDE.md").is_symlink())
        self.assertTrue((repo / "CONTEXT.md").exists())
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
        ).stdout
        self.assertEqual(status.strip(), "", "symlink + docs should all be committed, not left dirty")


class TestEnsureClaudeSymlink(GatewayTestCase):
    def test_creates_symlink_when_missing(self):
        repo = _git_repo(self.tmp)
        (repo / "AGENTS.md").write_text("rules\n")

        changed = ensure_claude_symlink(str(repo))

        self.assertTrue(changed)
        self.assertTrue((repo / "CLAUDE.md").is_symlink())

    def test_never_deletes_real_claude_md(self):
        """Regression: a real CLAUDE.md file was silently deleted and replaced."""
        repo = _git_repo(self.tmp)
        (repo / "AGENTS.md").write_text("rules\n")
        (repo / "CLAUDE.md").write_text("unique real content\n")

        changed = ensure_claude_symlink(str(repo))

        self.assertFalse(changed)
        self.assertFalse((repo / "CLAUDE.md").is_symlink())
        self.assertEqual((repo / "CLAUDE.md").read_text(), "unique real content\n")


class TestGenerateOverview(GatewayTestCase):
    def test_valid_output_accepted(self):
        _write_fake_gateway(self.bin_dir, VALID_OVERVIEW_BODY)
        repo = _git_repo(self.tmp)

        result = generate_overview(project_dir=str(repo), dry_run=True)

        self.assertEqual(result["status"], "dry_run")

    def test_missing_required_sections_rejected(self):
        _write_fake_gateway(self.bin_dir, "# LLM-OVERVIEW — repo\nJust a header, nothing else.\n")
        repo = _git_repo(self.tmp)

        result = generate_overview(project_dir=str(repo), dry_run=True)

        self.assertEqual(result["status"], "synthesis_failed")

    def test_missing_header_rejected(self):
        _write_fake_gateway(self.bin_dir, "no header at all\n")
        repo = _git_repo(self.tmp)

        result = generate_overview(project_dir=str(repo), dry_run=True)

        self.assertEqual(result["status"], "synthesis_failed")


if __name__ == "__main__":
    unittest.main()
