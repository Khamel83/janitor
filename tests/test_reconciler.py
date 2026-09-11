"""Tests for janitor.reconciler — sentinel merging, sweep/overview jobs.

Covers: first-run bootstrap and sentinel replacement, the sweep zero-token
fast paths (preflight guards, quiet repos, unchanged input hash), in-place
CONTEXT.md/TODO.md updates inside sentinel boundaries, clean auto-commit with
the Janitor-Run trailer, dry-run, overview generation with required sections,
the relative CLAUDE.md -> AGENTS.md symlink, and central docs mirroring.

Model calls are patched (janitor.reconciler.extract_structured / call_free)
so the suite is deterministic and costs nothing. No fake gateway binary and
no OPENROUTER_API_KEY needed.

Run with: python3 -m unittest tests/test_reconciler.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import janitor.reconciler as reconciler
from janitor.reconciler import (
    ensure_claude_symlink,
    merge_sentinel_block,
    overview_repo,
    sweep_repo,
)
from janitor.state import StateManager

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

SWEEP_RESPONSE = {
    "recent_markdown": "- Added feature A\n",
    "todo_markdown": "- [ ] do thing\n",
}


def fixture_branch_report(changed):
    return {
        "repo": "repo",
        "observed_at": "2026-09-11T00:00:00+00:00",
        "fetch": {"status": "not_attempted", "remote": "origin"},
        "report_stale": True,
        "base": {
            "branch": "main",
            "local_branch": "main",
            "ref": "refs/heads/main",
            "sha": "base",
        },
        "branches": [],
        "attention_flags": [],
        "report_hash": "changed" if changed else "same",
    }


def _old_stamp(days: float = 2) -> str:
    """Git internal-format committer/author stamp ``days`` in the past."""
    ts = int(time.time()) - int(days * 86400)
    return f"@{ts} +0000"


def _git_repo(tmp: Path, name: str = "repo", branch: str = "main") -> Path:
    repo = tmp / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    return repo


def _commit(repo: Path, message: str, files=None, stamp: str | None = None):
    """Commit ``files`` ({name: content}) with an optional backdated stamp."""
    for name, content in (files or {"README.md": "hi\n"}).items():
        (repo / name).write_text(content)
        subprocess.run(["git", "add", "--", name], cwd=repo, check=True)
    env = os.environ.copy()
    if stamp:
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, env=env, check=True)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True
    ).stdout


class TestMergeSentinelBlock(unittest.TestCase):
    def test_first_run_bootstrap_appends_and_preserves_original(self):
        original = "# Human Notes\nKeep this intact.\n"
        merged = merge_sentinel_block(original, "recent", "- Added feature A\n")
        self.assertIn("# Human Notes\nKeep this intact.", merged)
        self.assertIn("<!-- janitor:begin:recent -->", merged)
        self.assertIn("- Added feature A", merged)
        self.assertIn("<!-- janitor:end:recent -->", merged)
        # Human text stays at the top, block is appended after it.
        self.assertLess(
            merged.index("Keep this intact."), merged.index("<!-- janitor:begin:recent -->")
        )

    def test_first_run_bootstrap_on_empty_text(self):
        merged = merge_sentinel_block("", "todo", "- [ ] first\n")
        self.assertEqual(
            merged,
            "<!-- janitor:begin:todo -->\n- [ ] first\n<!-- janitor:end:todo -->\n",
        )

    def test_replaces_existing_sentinel_block(self):
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
        # Surrounding structure is otherwise untouched.
        self.assertIn("# Title\n", merged)

    def test_replacement_is_idempotent_single_block(self):
        text = "top\n"
        for content in ("- A\n", "- B\n", "- C\n"):
            text = merge_sentinel_block(text, "recent", content)
        self.assertEqual(text.count("<!-- janitor:begin:recent -->"), 1)
        self.assertEqual(text.count("<!-- janitor:end:recent -->"), 1)
        self.assertIn("- C", text)
        self.assertNotIn("- A", text)
        self.assertNotIn("- B", text)

    def test_extract_and_remove_sentinel_block_preserve_outside_bytes(self):
        text = (
            "before\n<!-- janitor:begin:branches -->\nold\n"
            "<!-- janitor:end:branches -->\nafter\n"
        )
        self.assertEqual(reconciler.extract_sentinel_block(text, "branches"), "old")
        self.assertEqual(
            reconciler.remove_sentinel_block(text, "branches"), "before\nafter\n"
        )

    def test_missing_or_malformed_sentinel_is_left_unchanged_by_remove(self):
        text = "human <!-- janitor:begin:branches --> no end"
        self.assertEqual(reconciler.remove_sentinel_block(text, "branches"), text)


class TestEnsureClaudeSymlink(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_creates_relative_symlink_when_missing(self):
        repo = _git_repo(self.tmp)
        (repo / "AGENTS.md").write_text("rules\n")

        changed = ensure_claude_symlink(repo)

        self.assertTrue(changed)
        claude = repo / "CLAUDE.md"
        self.assertTrue(claude.is_symlink())
        self.assertEqual(os.readlink(claude), "AGENTS.md")
        self.assertEqual(claude.resolve(), (repo / "AGENTS.md").resolve())

    def test_repairs_wrong_symlink_target(self):
        repo = _git_repo(self.tmp)
        (repo / "AGENTS.md").write_text("rules\n")
        (repo / "CLAUDE.md").symlink_to("OTHER.md")

        changed = ensure_claude_symlink(repo)

        self.assertTrue(changed)
        self.assertEqual(os.readlink(repo / "CLAUDE.md"), "AGENTS.md")

    def test_never_deletes_real_claude_md(self):
        repo = _git_repo(self.tmp)
        (repo / "AGENTS.md").write_text("rules\n")
        (repo / "CLAUDE.md").write_text("unique real content\n")

        changed = ensure_claude_symlink(repo)

        self.assertFalse(changed)
        self.assertFalse((repo / "CLAUDE.md").is_symlink())
        self.assertEqual((repo / "CLAUDE.md").read_text(), "unique real content\n")

    def test_no_agents_md_is_noop(self):
        repo = _git_repo(self.tmp)

        changed = ensure_claude_symlink(repo)

        self.assertFalse(changed)
        self.assertFalse((repo / "CLAUDE.md").exists())


class TestSweepRepo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.sm = StateManager(self.tmp / "state")

    def tearDown(self):
        self._tmp.cleanup()

    @patch("janitor.reconciler.extract_structured")
    def test_preflight_guard_skips_repo(self, mock_extract):
        repo = _git_repo(self.tmp)
        _commit(repo, "initial")
        (repo / ".git" / "index.lock").write_text("")

        result = sweep_repo(repo, self.sm, "run_locked")

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "git_index_locked")
        mock_extract.assert_not_called()
        self.assertFalse((repo / "CONTEXT.md").exists())

    @patch("janitor.reconciler.extract_structured")
    @patch(
        "janitor.reconciler.render_branch_block",
        create=True,
        return_value="<!-- janitor:begin:branches -->\nold\n<!-- janitor:end:branches -->",
    )
    @patch("janitor.reconciler.collect_branch_report", create=True)
    def test_clean_quiet_repo_is_zero_token(self, collect, render, mock_extract):
        repo = _git_repo(self.tmp)
        existing = "<!-- janitor:begin:branches -->\nold\n<!-- janitor:end:branches -->\n"
        _commit(
            repo,
            "initial",
            files={"README.md": "hi\n", "CONTEXT.md": existing},
            stamp=_old_stamp(),
        )
        collect.return_value = fixture_branch_report(changed=False)

        result = sweep_repo(repo, self.sm, "run_quiet", no_fetch=True)

        self.assertEqual(result["status"], "quiet")
        self.assertEqual(result["tokens_spent"], 0)
        mock_extract.assert_not_called()
        self.assertEqual((repo / "CONTEXT.md").read_text(), existing)
        self.assertIsNone(self.sm.get_last_input_hash("repo"))

    @patch("janitor.reconciler.extract_structured")
    def test_unchanged_input_hash_skips_model_call(self, mock_extract):
        repo = _git_repo(self.tmp)
        _commit(repo, "initial", stamp=_old_stamp())
        (repo / "dirty.txt").write_text("human work\n")
        mock_extract.return_value = dict(SWEEP_RESPONSE)

        first = sweep_repo(repo, self.sm, "run_1")
        self.assertEqual(first["status"], "written")
        self.assertTrue((repo / "CONTEXT.md").exists())

        # Same external state on the next run: janitor's own writes are
        # excluded from the hash, so this must be a zero-token skip.
        second = sweep_repo(repo, self.sm, "run_2")

        self.assertEqual(second["status"], "unchanged_hash")
        self.assertEqual(second["tokens_spent"], 0)
        self.assertEqual(mock_extract.call_count, 1)

    @patch("janitor.reconciler.extract_structured")
    def test_updates_docs_inside_sentinels_preserving_human_text(self, mock_extract):
        repo = _git_repo(self.tmp)
        human = "# My Notes\nHuman line.\n"
        _commit(
            repo, "initial",
            files={"README.md": "hi\n", "CONTEXT.md": human},
            stamp=_old_stamp(),
        )
        (repo / "dirty.txt").write_text("x\n")
        mock_extract.return_value = dict(SWEEP_RESPONSE)

        result = sweep_repo(repo, self.sm, "run_1")

        self.assertEqual(result["status"], "written")
        context = (repo / "CONTEXT.md").read_text(encoding="utf-8")
        self.assertIn(human, context)
        self.assertIn("<!-- janitor:begin:recent -->", context)
        self.assertIn("- Added feature A", context)
        self.assertIn("<!-- janitor:end:recent -->", context)
        self.assertEqual(context.count("<!-- janitor:begin:recent -->"), 1)
        todo = (repo / "TODO.md").read_text(encoding="utf-8")
        self.assertIn("<!-- janitor:begin:todo -->", todo)
        self.assertIn("- [ ] do thing", todo)
        self.assertEqual(todo.count("<!-- janitor:begin:todo -->"), 1)

        # New external activity -> regenerate; the old block is replaced,
        # not duplicated, and the human text survives untouched.
        mock_extract.return_value = {
            "recent_markdown": "- Added feature B\n",
            "todo_markdown": "- [ ] do thing\n",
        }
        (repo / "another.txt").write_text("more\n")
        sweep_repo(repo, self.sm, "run_2")

        context = (repo / "CONTEXT.md").read_text(encoding="utf-8")
        self.assertIn(human, context)
        self.assertIn("- Added feature B", context)
        self.assertNotIn("- Added feature A", context)
        self.assertEqual(context.count("<!-- janitor:begin:recent -->"), 1)
        self.assertEqual(context.count("<!-- janitor:end:recent -->"), 1)

    @patch("janitor.reconciler.extract_structured")
    def test_commits_cleanly_on_main_with_janitor_trailer(self, mock_extract):
        repo = _git_repo(self.tmp)
        _commit(repo, "initial")
        mock_extract.return_value = dict(SWEEP_RESPONSE)

        result = sweep_repo(repo, self.sm, "run-abc123")

        self.assertEqual(result["status"], "committed")
        log = _git(repo, "log", "-1", "--format=%B")
        self.assertIn("Janitor-Run: run-abc123", log)
        self.assertIn("docs(janitor): sweep CONTEXT.md and TODO.md", log)
        names = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
        self.assertEqual(sorted(names), ["CONTEXT.md", "TODO.md"])
        porcelain = _git(repo, "status", "--porcelain")
        self.assertEqual(porcelain.strip(), "", "post-commit tree must be clean")
        # Hash and run outcome persisted.
        self.assertIsNotNone(self.sm.get_last_input_hash("repo"))
        state = json.loads(
            (self.tmp / "state" / "state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["repo"]["last_run"]["status"], "committed")
        self.assertEqual(state["repo"]["last_run"]["run_id"], "run-abc123")

    @patch("janitor.reconciler.extract_structured")
    def test_dry_run_writes_nothing(self, mock_extract):
        repo = _git_repo(self.tmp)
        _commit(repo, "initial")
        mock_extract.return_value = dict(SWEEP_RESPONSE)

        result = sweep_repo(repo, self.sm, "run_dry", dry_run=True)

        self.assertEqual(result["status"], "dry_run")
        self.assertIn("<!-- janitor:begin:recent -->", result["context_md"])
        self.assertIn("- Added feature A", result["context_md"])
        self.assertIn("<!-- janitor:begin:todo -->", result["todo_md"])
        self.assertFalse((repo / "CONTEXT.md").exists())
        self.assertFalse((repo / "TODO.md").exists())
        self.assertEqual(_git(repo, "rev-list", "--count", "HEAD").strip(), "1")
        self.assertIsNone(self.sm.get_last_input_hash("repo"))
        self.assertEqual(mock_extract.call_count, 1)

    @patch("janitor.reconciler.extract_structured")
    @patch(
        "janitor.reconciler.render_branch_block",
        create=True,
        return_value="<!-- janitor:begin:branches -->\nnew\n<!-- janitor:end:branches -->",
    )
    @patch("janitor.reconciler.collect_branch_report", create=True)
    def test_quiet_normal_repo_still_writes_changed_branch_block_without_model(
        self, collect, render, extract
    ):
        repo = _git_repo(self.tmp)
        _commit(repo, "initial", stamp=_old_stamp())
        collect.return_value = fixture_branch_report(changed=True)

        result = sweep_repo(repo, self.sm, "run_branch", no_fetch=True)

        self.assertIn(result["status"], {"written", "committed"})
        extract.assert_not_called()
        collect.assert_called_once_with(repo, now=None, fetch=False)
        self.assertIn("<!-- janitor:begin:branches -->", (repo / "CONTEXT.md").read_text())

    @patch("janitor.reconciler.extract_structured")
    @patch(
        "janitor.reconciler.render_branch_block",
        create=True,
        return_value="<!-- janitor:begin:branches -->\nold\n<!-- janitor:end:branches -->",
    )
    @patch("janitor.reconciler.collect_branch_report", create=True)
    def test_unchanged_branch_block_and_normal_hash_do_not_write_or_call_model(
        self, collect, render, extract
    ):
        repo = _git_repo(self.tmp)
        existing = "<!-- janitor:begin:branches -->\nold\n<!-- janitor:end:branches -->\n"
        _commit(repo, "initial", files={"README.md": "hi\n", "CONTEXT.md": existing}, stamp=_old_stamp())
        collect.return_value = fixture_branch_report(changed=False)

        before = (repo / "CONTEXT.md").read_bytes()
        result = sweep_repo(repo, self.sm, "run_noop", no_fetch=True)

        self.assertEqual(result["status"], "quiet")
        self.assertEqual((repo / "CONTEXT.md").read_bytes(), before)
        extract.assert_not_called()

    @patch("janitor.reconciler.extract_structured")
    @patch(
        "janitor.reconciler.render_branch_block",
        create=True,
        return_value="<!-- janitor:begin:branches -->\nold\n<!-- janitor:end:branches -->",
    )
    @patch("janitor.reconciler.collect_branch_report", create=True)
    def test_existing_branch_block_is_masked_from_normal_sweep_prompt(
        self, collect, render, extract
    ):
        repo = _git_repo(self.tmp)
        existing = (
            "human\n<!-- janitor:begin:branches -->\nsecret branch table\n"
            "<!-- janitor:end:branches -->\n"
        )
        _commit(repo, "initial", files={"README.md": "hi\n", "CONTEXT.md": existing}, stamp=_old_stamp())
        (repo / "dirty.txt").write_text("work\n")
        collect.return_value = fixture_branch_report(changed=False)
        extract.return_value = dict(SWEEP_RESPONSE)

        sweep_repo(repo, self.sm, "run_mask", no_fetch=True)

        prompt = extract.call_args.args[0]
        self.assertNotIn("secret branch table", prompt)
        self.assertIn("human", prompt)

    @patch("janitor.reconciler.extract_structured")
    @patch(
        "janitor.reconciler.render_branch_block",
        create=True,
        return_value="<!-- janitor:begin:branches -->\nnew\n<!-- janitor:end:branches -->",
    )
    @patch("janitor.reconciler.collect_branch_report", create=True)
    def test_branch_only_commit_uses_dynamic_default_and_context_only(
        self, collect, render, extract
    ):
        repo = _git_repo(self.tmp, branch="trunk")
        _commit(repo, "initial", stamp=_old_stamp())
        report = fixture_branch_report(changed=True)
        report["base"].update(
            {
                "branch": "trunk",
                "local_branch": "trunk",
                "ref": "refs/remotes/origin/trunk",
            }
        )
        collect.return_value = report

        result = sweep_repo(repo, self.sm, "run_trunk", no_fetch=True)

        self.assertEqual(result["status"], "committed")
        self.assertEqual(_git(repo, "show", "--name-only", "--format=", "HEAD").split(), ["CONTEXT.md"])
        self.assertIn(
            "docs(janitor): update branch and worktree inventory for",
            _git(repo, "log", "-1", "--format=%B"),
        )
        extract.assert_not_called()

    @patch("janitor.reconciler.extract_structured", side_effect=RuntimeError("gateway down"))
    @patch(
        "janitor.reconciler.render_branch_block",
        create=True,
        return_value="<!-- janitor:begin:branches -->\nnew\n<!-- janitor:end:branches -->",
    )
    @patch("janitor.reconciler.collect_branch_report", create=True)
    def test_branch_report_is_persisted_when_normal_synthesis_fails(
        self, collect, render, extract
    ):
        repo = _git_repo(self.tmp)
        _commit(repo, "initial")
        collect.return_value = fixture_branch_report(changed=True)

        result = sweep_repo(repo, self.sm, "run_model_failure", no_fetch=True)

        self.assertEqual(result["status"], "synthesis_failed")
        self.assertEqual(result["branch_status"], "committed")
        self.assertIn("new", (repo / "CONTEXT.md").read_text())
        self.assertFalse((repo / "TODO.md").exists())
        self.assertIn(
            "docs(janitor): update branch and worktree inventory for",
            _git(repo, "log", "-1", "--format=%B"),
        )
        self.assertEqual(self.sm.get_last_run("repo")["status"], "synthesis_failed")

    @patch("janitor.reconciler.extract_structured")
    @patch(
        "janitor.reconciler.render_branch_block",
        create=True,
        return_value="<!-- janitor:begin:branches -->\nnew\n<!-- janitor:end:branches -->",
    )
    @patch("janitor.reconciler.collect_branch_report", create=True)
    def test_branch_and_normal_updates_share_commit_and_stage_only_changed_files(
        self, collect, render, extract
    ):
        repo = _git_repo(self.tmp)
        _commit(repo, "initial")
        collect.return_value = fixture_branch_report(changed=True)
        extract.return_value = dict(SWEEP_RESPONSE)

        result = sweep_repo(repo, self.sm, "run_shared", no_fetch=True)

        self.assertEqual(result["status"], "committed")
        names = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
        self.assertEqual(sorted(names), ["CONTEXT.md", "TODO.md"])
        self.assertIn("docs(janitor): sweep CONTEXT.md and TODO.md", _git(repo, "log", "-1", "--format=%B"))
        self.assertIn("new", (repo / "CONTEXT.md").read_text())

    @patch("janitor.reconciler.extract_structured")
    @patch(
        "janitor.reconciler.render_branch_block",
        create=True,
        return_value="<!-- janitor:begin:branches -->\nnew\n<!-- janitor:end:branches -->",
    )
    @patch("janitor.reconciler.collect_branch_report", create=True)
    def test_dirty_checkout_writes_branch_block_without_committing(
        self, collect, render, extract
    ):
        repo = _git_repo(self.tmp)
        _commit(repo, "initial", stamp=_old_stamp())
        (repo / "dirty.txt").write_text("human work\n")
        collect.return_value = fixture_branch_report(changed=True)
        extract.return_value = dict(SWEEP_RESPONSE)

        result = sweep_repo(repo, self.sm, "run_dirty", no_fetch=True)

        self.assertEqual(result["status"], "written")
        self.assertEqual(result["branch_status"], "written")
        self.assertEqual(_git(repo, "rev-list", "--count", "HEAD").strip(), "1")
        self.assertIn("new", (repo / "CONTEXT.md").read_text())

    @patch("janitor.reconciler.extract_structured")
    @patch(
        "janitor.reconciler.render_branch_block",
        create=True,
        return_value="<!-- janitor:begin:branches -->\nnew\n<!-- janitor:end:branches -->",
    )
    @patch("janitor.reconciler.collect_branch_report", create=True)
    def test_non_default_checkout_writes_branch_block_without_committing(
        self, collect, render, extract
    ):
        repo = _git_repo(self.tmp, branch="feature")
        _commit(repo, "initial", stamp=_old_stamp())
        report = fixture_branch_report(changed=True)
        report["base"]["local_branch"] = "main"
        collect.return_value = report

        result = sweep_repo(repo, self.sm, "run_feature", no_fetch=True)

        self.assertEqual(result["status"], "written")
        self.assertEqual(result["branch_status"], "written")
        self.assertEqual(_git(repo, "rev-list", "--count", "HEAD").strip(), "1")
        extract.assert_not_called()

    @patch("janitor.reconciler.extract_structured")
    @patch(
        "janitor.reconciler.render_branch_block",
        create=True,
        return_value="<!-- janitor:begin:branches -->\nnew\n<!-- janitor:end:branches -->",
    )
    @patch("janitor.reconciler.collect_branch_report", create=True)
    def test_dry_run_disables_fetch_and_leaves_files_and_state_untouched(
        self, collect, render, extract
    ):
        repo = _git_repo(self.tmp)
        _commit(repo, "initial", stamp=_old_stamp())
        collect.return_value = fixture_branch_report(changed=True)

        result = sweep_repo(repo, self.sm, "run_dry_branch", dry_run=True)

        self.assertEqual(result["status"], "dry_run")
        collect.assert_called_once_with(repo, now=None, fetch=False)
        extract.assert_not_called()
        self.assertFalse((repo / "CONTEXT.md").exists())
        self.assertFalse((repo / "TODO.md").exists())
        self.assertIsNone(self.sm.get_branch_review("repo"))

    @patch("janitor.reconciler.extract_structured")
    @patch("janitor.reconciler.render_branch_block", create=True)
    @patch("janitor.reconciler.collect_branch_report", create=True)
    def test_preflight_guard_does_not_collect_or_touch_branch_state(
        self, collect, render, extract
    ):
        repo = _git_repo(self.tmp)
        _commit(repo, "initial", stamp=_old_stamp())
        (repo / ".git" / "index.lock").write_text("")

        result = sweep_repo(repo, self.sm, "run_locked_branch", no_fetch=True)

        self.assertEqual(result["status"], "skipped")
        collect.assert_not_called()
        render.assert_not_called()
        extract.assert_not_called()
        self.assertIsNone(self.sm.get_branch_review("repo"))

    @patch("janitor.reconciler.atomic_stage_and_commit")
    @patch("janitor.reconciler.extract_structured")
    @patch(
        "janitor.reconciler.render_branch_block",
        create=True,
        return_value="<!-- janitor:begin:branches -->\nold\n<!-- janitor:end:branches -->",
    )
    @patch("janitor.reconciler.collect_branch_report", create=True)
    def test_byte_exact_branch_noop_does_not_write_or_stage(
        self, collect, render, extract, commit
    ):
        repo = _git_repo(self.tmp)
        existing = "<!-- janitor:begin:branches -->\nold\n<!-- janitor:end:branches -->\n"
        _commit(repo, "initial", files={"README.md": "hi\n", "CONTEXT.md": existing}, stamp=_old_stamp())
        collect.return_value = fixture_branch_report(changed=False)
        before = (repo / "CONTEXT.md").read_bytes()

        result = sweep_repo(repo, self.sm, "run_exact_noop", no_fetch=True)

        self.assertEqual(result["status"], "quiet")
        self.assertEqual((repo / "CONTEXT.md").read_bytes(), before)
        commit.assert_not_called()
        extract.assert_not_called()

    @patch("janitor.reconciler.extract_structured")
    @patch("janitor.reconciler.render_branch_block", create=True)
    @patch("janitor.reconciler.collect_branch_report", create=True)
    def test_normal_hash_gate_is_independent_of_changed_branch_output(
        self, collect, render, extract
    ):
        repo = _git_repo(self.tmp)
        _commit(repo, "initial", stamp=_old_stamp())
        (repo / "dirty.txt").write_text("human work\n")
        collect.side_effect = [
            fixture_branch_report(changed=False),
            fixture_branch_report(changed=True),
        ]
        render.side_effect = [
            "<!-- janitor:begin:branches -->\nold\n<!-- janitor:end:branches -->",
            "<!-- janitor:begin:branches -->\nnew\n<!-- janitor:end:branches -->",
        ]
        extract.return_value = dict(SWEEP_RESPONSE)

        first = sweep_repo(repo, self.sm, "run_hash_1", no_fetch=True)
        second = sweep_repo(repo, self.sm, "run_hash_2", no_fetch=True)

        self.assertEqual(first["status"], "written")
        self.assertEqual(second["status"], "written")
        self.assertEqual(extract.call_count, 1)
        self.assertIn("new", (repo / "CONTEXT.md").read_text())


class TestOverviewRepo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.sm = StateManager(self.tmp / "state")
        # Never mirror into a real central docs repo during tests: point the
        # mirror env at a path that does not exist (mirror tests override it).
        self._old_mirror = os.environ.get("JANITOR_DOCS_MIRROR")
        os.environ["JANITOR_DOCS_MIRROR"] = str(self.tmp / "no-central-repo")

    def tearDown(self):
        if self._old_mirror is None:
            os.environ.pop("JANITOR_DOCS_MIRROR", None)
        else:
            os.environ["JANITOR_DOCS_MIRROR"] = self._old_mirror
        self._tmp.cleanup()

    @patch("janitor.reconciler.call_free")
    def test_generates_overview_with_required_sections(self, mock_call):
        repo = _git_repo(self.tmp)
        _commit(repo, "initial", files={"README.md": "hi\n", "AGENTS.md": "rules\n"})
        mock_call.return_value = VALID_OVERVIEW_BODY

        result = overview_repo(repo, self.sm)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["wrote"], "LLM-OVERVIEW.md")
        self.assertNotIn("mirrored_to", result)
        overview = (repo / "LLM-OVERVIEW.md").read_text(encoding="utf-8")
        self.assertIn("# LLM-OVERVIEW — repo", overview.splitlines()[0])
        for section in (
            "## What this repo is",
            "## Machine & Host Ownership",
            "## What is actually built",
            "## Canonical entry points",
        ):
            self.assertIn(section, overview)

    @patch("janitor.reconciler.call_free")
    def test_ensures_relative_claude_symlink(self, mock_call):
        repo = _git_repo(self.tmp)
        _commit(repo, "initial", files={"README.md": "hi\n", "AGENTS.md": "rules\n"})
        mock_call.return_value = VALID_OVERVIEW_BODY

        overview_repo(repo, self.sm)

        claude = repo / "CLAUDE.md"
        self.assertTrue(claude.is_symlink())
        self.assertEqual(os.readlink(claude), "AGENTS.md")
        self.assertEqual(claude.resolve(), (repo / "AGENTS.md").resolve())

    @patch("janitor.reconciler.call_free")
    def test_missing_required_sections_rejected(self, mock_call):
        repo = _git_repo(self.tmp)
        _commit(repo, "initial", files={"README.md": "hi\n", "AGENTS.md": "rules\n"})
        mock_call.return_value = "# LLM-OVERVIEW — repo\nJust a header, nothing else.\n"

        result = overview_repo(repo, self.sm)

        self.assertEqual(result["status"], "synthesis_failed")
        self.assertFalse((repo / "LLM-OVERVIEW.md").exists())

    @patch("janitor.reconciler.call_free")
    def test_mirrors_to_central_docs_repo_when_present(self, mock_call):
        central = _git_repo(self.tmp, name="central")
        _commit(central, "docs init", files={"README.md": "central docs\n"})
        os.environ["JANITOR_DOCS_MIRROR"] = str(central)
        repo = _git_repo(self.tmp, name="repo")
        _commit(repo, "initial", files={"README.md": "hi\n", "AGENTS.md": "rules\n"})
        mock_call.return_value = VALID_OVERVIEW_BODY

        result = overview_repo(repo, self.sm)

        self.assertEqual(result["status"], "ok")
        mirror = central / "repos" / "repo.md"
        self.assertEqual(result["mirrored_to"], str(mirror))
        self.assertTrue(mirror.exists())
        self.assertEqual(mirror.read_text(encoding="utf-8").strip(), VALID_OVERVIEW_BODY.strip())
        # The repo's own copy is still the source of truth.
        self.assertTrue((repo / "LLM-OVERVIEW.md").exists())

    @patch("janitor.reconciler.call_free")
    def test_dirty_central_docs_repo_not_mirrored(self, mock_call):
        central = _git_repo(self.tmp, name="central")
        _commit(central, "docs init", files={"README.md": "central docs\n"})
        (central / "uncommitted.txt").write_text("human work\n")
        os.environ["JANITOR_DOCS_MIRROR"] = str(central)
        repo = _git_repo(self.tmp, name="repo")
        _commit(repo, "initial", files={"README.md": "hi\n", "AGENTS.md": "rules\n"})
        mock_call.return_value = VALID_OVERVIEW_BODY

        result = overview_repo(repo, self.sm)

        self.assertEqual(result["status"], "ok")
        self.assertNotIn("mirrored_to", result)
        self.assertFalse((central / "repos" / "repo.md").exists())
        self.assertTrue((repo / "LLM-OVERVIEW.md").exists())


if __name__ == "__main__":
    unittest.main()
