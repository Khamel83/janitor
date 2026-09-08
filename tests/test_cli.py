"""Tests for the janitor CLI: fleet discovery, subcommands, JSON output, exit codes.

Every ``main()`` invocation is redirected to a scratch ``JANITOR_STATE_DIR``
and (where fleet discovery is exercised) a scratch ``JANITOR_WORKSPACE``, so
the suite never touches the real fleet under /Volumes/2TB_SSD/GitHub or the
real state under ~/.local/state/janitor. Model-touching reconciler and
hygiene functions are mocked; only ``status`` and ``discover_repos`` run
against real (fixture) git repos.
"""

import contextlib
import json
import os
import subprocess
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from janitor.cli import discover_repos, main
from janitor.state import StateManager


class CliTestCase(unittest.TestCase):
    """Shared fixture helpers: scratch roots, fake/real repos, CLI runner."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state_dir = self.root / "state"
        self.state_dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def fake_repo(self, name: str) -> Path:
        """A directory that looks like a git repo (contains ``.git``)."""
        repo = self.root / name
        (repo / ".git").mkdir(parents=True)
        return repo

    def plain_dir(self, name: str) -> Path:
        """A directory that is not a git repository."""
        repo = self.root / name
        repo.mkdir(parents=True)
        return repo

    def git_repo(self, name: str) -> Path:
        """A real git repository with one commit on ``main``."""
        repo = self.root / name
        repo.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo, check=True, capture_output=True,
        )
        (repo / "README.md").write_text(f"# {name}\n")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
        return repo

    def short_sha(self, repo: Path) -> str:
        """Short HEAD SHA of ``repo`` as ``git_ops`` reports it."""
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo, check=True, capture_output=True, text=True,
        )
        return res.stdout.strip()

    def run_cli(self, argv, workspace=None, cwd=None, state_dir=None):
        """Run ``main(argv)`` with redirected state (and optional workspace/cwd)."""
        env = {"JANITOR_STATE_DIR": str(state_dir or self.state_dir)}
        if workspace is not None:
            env["JANITOR_WORKSPACE"] = str(workspace)
        out = StringIO()
        ctx = [patch.dict(os.environ, env), contextlib.redirect_stdout(out)]
        if cwd is not None:
            ctx.append(patch.object(Path, "cwd", return_value=cwd))
        with contextlib.ExitStack() as stack:
            for c in ctx:
                stack.enter_context(c)
            code = main(argv)
        return code, out.getvalue()


class DiscoverReposTests(CliTestCase):
    def test_finds_only_directories_containing_git(self):
        alpha = self.fake_repo("alpha")
        zeta = self.fake_repo("zeta")
        self.plain_dir("beta")  # no .git -> must be ignored
        self.assertEqual(
            discover_repos(self.root),
            [alpha.resolve(), zeta.resolve()],
        )

    def test_missing_workspace_returns_empty_list(self):
        self.assertEqual(discover_repos(self.root / "does-not-exist"), [])


class SweepCommandTests(CliTestCase):
    def test_sweep_dry_run_prints_preview_without_writing(self):
        repo = self.fake_repo("demo")
        with patch(
            "janitor.cli.sweep_repo",
            return_value={
                "repo": "demo",
                "status": "dry_run",
                "context_md": "CONTEXT-PREVIEW",
                "todo_md": "TODO-PREVIEW",
            },
        ) as sweep:
            code, out = self.run_cli(["sweep", "--dry-run", str(repo)])
        self.assertEqual(code, 0)
        sweep.assert_called_once()
        call_args, call_kwargs = sweep.call_args
        self.assertEqual(call_args[0], repo.resolve())
        self.assertIsInstance(call_args[1], StateManager)
        self.assertTrue(call_args[2].startswith("run_"))
        self.assertEqual(call_kwargs, {"dry_run": True})
        lines = out.splitlines()
        self.assertEqual(lines[0], "[dry_run] demo")
        self.assertIn("CONTEXT-PREVIEW", out)
        self.assertIn("TODO-PREVIEW", out)

    def test_sweep_json_quiet_structure_and_exit_zero(self):
        repo = self.fake_repo("demo")
        with patch(
            "janitor.cli.sweep_repo",
            return_value={"repo": "demo", "status": "quiet", "tokens_spent": 0},
        ) as sweep:
            code, out = self.run_cli(["sweep", "--json", str(repo)])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["schema_version"], 1)
        self.assertTrue(payload["run_id"].startswith("run_"))
        self.assertEqual(
            payload["results"],
            [{"repo": "demo", "status": "quiet", "tokens_spent": 0}],
        )
        sweep.assert_called_once()

    def test_sweep_explicit_repo_targets_only_that_repo(self):
        repo_a = self.fake_repo("aaa")
        repo_b = self.fake_repo("bbb")
        with patch(
            "janitor.cli.sweep_repo",
            return_value={"repo": "aaa", "status": "quiet", "tokens_spent": 0},
        ) as sweep:
            code, _out = self.run_cli(["sweep", "--dry-run", str(repo_a)])
        self.assertEqual(code, 0)
        sweep.assert_called_once()
        self.assertEqual(sweep.call_args.args[0], repo_a.resolve())
        self.assertNotEqual(repo_a.resolve(), repo_b.resolve())

    def test_sweep_all_targets_every_git_repo_in_workspace(self):
        # Build the fleet inside the workspace, not under self.root.
        fleet = self.plain_dir("fleet")
        for name in ("zeta", "alpha", "middle"):
            (fleet / name / ".git").mkdir(parents=True)
        (fleet / "not-a-repo").mkdir()

        def fake_sweep(repo, state_mgr, run_id, dry_run=False):
            return {"repo": repo.name, "status": "quiet", "tokens_spent": 0}

        with patch("janitor.cli.sweep_repo", side_effect=fake_sweep) as sweep:
            code, out = self.run_cli(["sweep", "--all", "--json"], workspace=fleet)
        self.assertEqual(code, 0)
        names = [r["repo"] for r in json.loads(out)["results"]]
        self.assertEqual(names, ["alpha", "middle", "zeta"])  # sorted, non-repos ignored
        self.assertEqual(sweep.call_count, 3)
        called_repos = [c.args[0].resolve() for c in sweep.call_args_list]
        self.assertEqual(
            called_repos,
            [(fleet / n).resolve() for n in ("alpha", "middle", "zeta")],
        )

    def test_sweep_defaults_to_current_directory_when_git_repo(self):
        repo = self.fake_repo("here")
        with patch(
            "janitor.cli.sweep_repo",
            return_value={"repo": "here", "status": "quiet", "tokens_spent": 0},
        ) as sweep:
            code, _out = self.run_cli(["sweep", "--dry-run"], cwd=repo)
        self.assertEqual(code, 0)
        sweep.assert_called_once()
        self.assertEqual(sweep.call_args.args[0], repo.resolve())

    def test_sweep_outside_git_repo_falls_back_to_fleet(self):
        fleet = self.plain_dir("fleet")
        for name in ("one", "two"):
            (fleet / name / ".git").mkdir(parents=True)
        outside = self.plain_dir("elsewhere")  # no .git in cwd

        def fake_sweep(repo, state_mgr, run_id, dry_run=False):
            return {"repo": repo.name, "status": "quiet", "tokens_spent": 0}

        with patch("janitor.cli.sweep_repo", side_effect=fake_sweep) as sweep:
            code, _out = self.run_cli(["sweep"], workspace=fleet, cwd=outside)
        self.assertEqual(code, 0)
        self.assertEqual(sweep.call_count, 2)
        self.assertEqual(
            [c.args[0].resolve() for c in sweep.call_args_list],
            [(fleet / n).resolve() for n in ("one", "two")],
        )


class OverviewCommandTests(CliTestCase):
    def test_overview_dry_run_prints_synthesized_preview(self):
        repo = self.fake_repo("demo")
        with patch(
            "janitor.cli.overview_repo",
            return_value={
                "repo": "demo",
                "status": "dry_run",
                "overview_md": "OVERVIEW-PREVIEW",
                "path": "LLM-OVERVIEW.md",
            },
        ) as overview:
            code, out = self.run_cli(["overview", "--dry-run", str(repo)])
        self.assertEqual(code, 0)
        overview.assert_called_once()
        call_args, call_kwargs = overview.call_args
        self.assertEqual(call_args[0], repo.resolve())
        self.assertIsInstance(call_args[1], StateManager)
        self.assertEqual(call_kwargs, {"dry_run": True})
        lines = out.splitlines()
        self.assertEqual(lines[0], "[dry_run] demo")
        self.assertIn("OVERVIEW-PREVIEW", out)

    def test_overview_json_reports_written_overview(self):
        repo = self.fake_repo("demo")
        expected = {
            "repo": "demo",
            "status": "ok",
            "wrote": "LLM-OVERVIEW.md",
        }
        with patch("janitor.cli.overview_repo", return_value=expected) as overview:
            code, out = self.run_cli(["overview", "--json", str(repo)])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["results"], [expected])
        overview.assert_called_once_with(
            repo.resolve(),
            unittest.mock.ANY,
            dry_run=False,
        )


class TidyCommandTests(CliTestCase):
    def test_tidy_calls_purge_and_checkpoint_abandoned_wip(self):
        repo = self.fake_repo("demo")
        checkpoint = {
            "wip_branch": "auto-wip/20260908-120000",
            "wip_sha": "abc1234",
            "base_sha": "def5678",
            "original_branch": "main",
        }
        with patch("janitor.cli.is_wip_stale", return_value=True), patch(
            "janitor.cli.purge_ephemeral_trash",
            return_value=["/tmp/demo/.DS_Store"],
        ) as purge, patch(
            "janitor.cli.checkpoint_abandoned_wip", return_value=checkpoint
        ) as checkpoint_fn:
            code, out = self.run_cli(["tidy", str(repo)])
        self.assertEqual(code, 0)
        purge.assert_called_once_with(repo.resolve())
        checkpoint_fn.assert_called_once()
        args = checkpoint_fn.call_args.args
        self.assertEqual(args[0], repo.resolve())
        self.assertIsInstance(args[1], StateManager)
        self.assertTrue(args[2].startswith("run_"))
        self.assertIn("[checkpointed] demo", out.splitlines()[0])
        self.assertIn("purged 1", out)
        self.assertIn("auto-wip/20260908-120000", out)

    def test_tidy_skips_checkpoint_when_no_stale_wip(self):
        repo = self.fake_repo("demo")
        with patch("janitor.cli.is_wip_stale", return_value=False), patch(
            "janitor.cli.purge_ephemeral_trash", return_value=[]
        ) as purge, patch(
            "janitor.cli.checkpoint_abandoned_wip"
        ) as checkpoint_fn:
            code, out = self.run_cli(["tidy", "--json", str(repo)])
        self.assertEqual(code, 0)
        purge.assert_called_once_with(repo.resolve())
        checkpoint_fn.assert_not_called()
        result = json.loads(out)["results"][0]
        self.assertEqual(result["status"], "clean")
        self.assertIsNone(result["checkpoint"])
        self.assertEqual(result["purged_count"], 0)

    def test_tidy_skips_non_git_directory(self):
        repo = self.plain_dir("not-repo")
        with patch("janitor.cli.is_wip_stale") as stale, patch(
            "janitor.cli.purge_ephemeral_trash"
        ) as purge, patch(
            "janitor.cli.checkpoint_abandoned_wip"
        ) as checkpoint_fn:
            code, out = self.run_cli(["tidy", str(repo)])
        self.assertEqual(code, 0)
        purge.assert_not_called()
        stale.assert_not_called()
        checkpoint_fn.assert_not_called()
        self.assertEqual(out.splitlines()[0], "[skipped] not-repo (not_a_git_repo)")


class StatusCommandTests(CliTestCase):
    def test_status_reports_git_status_and_last_run(self):
        repo = self.git_repo("proj")
        StateManager(self.state_dir).record_run("proj", "committed", "run_abc")
        code, out = self.run_cli(["status", str(repo)])
        self.assertEqual(code, 0)
        sha = self.short_sha(repo)
        self.assertEqual(
            out.splitlines()[0],
            f"[ok] proj (main @ {sha}, clean), last run: committed run_abc",
        )

    def test_status_json_reports_dirty_flag_and_no_last_run(self):
        repo = self.git_repo("dirtyrepo")
        (repo / "README.md").write_text("# dirtyrepo\nuncommitted change\n")
        code, out = self.run_cli(["status", "--json", str(repo)])
        self.assertEqual(code, 0)
        result = json.loads(out)["results"][0]
        self.assertEqual(
            result,
            {
                "repo": "dirtyrepo",
                "status": "ok",
                "branch": "main",
                "sha": self.short_sha(repo),
                "dirty": True,
                "last_run": None,
            },
        )

    def test_status_skips_non_git_directory(self):
        repo = self.plain_dir("not-repo")
        code, out = self.run_cli(["status", str(repo)])
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], "[skipped] not-repo (not_a_git_repo)")


class ExitCodeTests(CliTestCase):
    def test_sweep_synthesis_failure_exits_1(self):
        repo = self.fake_repo("demo")
        with patch(
            "janitor.cli.sweep_repo",
            return_value={"repo": "demo", "status": "synthesis_failed", "raw": "x"},
        ):
            code, out = self.run_cli(["sweep", str(repo)])
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines()[0], "[synthesis_failed] demo")

    def test_overview_synthesis_failure_json_exits_1(self):
        repo = self.fake_repo("demo")
        with patch(
            "janitor.cli.overview_repo",
            return_value={"repo": "demo", "status": "synthesis_failed", "raw": "x"},
        ):
            code, out = self.run_cli(["overview", "--json", str(repo)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["results"][0]["status"], "synthesis_failed")

    def test_exception_in_command_becomes_error_result_and_exit_1(self):
        repo = self.fake_repo("demo")
        with patch(
            "janitor.cli.sweep_repo", side_effect=RuntimeError("boom")
        ):
            code, out = self.run_cli(["sweep", "--json", str(repo)])
        self.assertEqual(code, 1)
        result = json.loads(out)["results"][0]
        self.assertEqual(result["status"], "error")
        self.assertIn("RuntimeError", result["error"])

    def test_tidy_exception_becomes_error_result_and_exit_1(self):
        repo = self.fake_repo("demo")
        with patch(
            "janitor.cli.purge_ephemeral_trash", side_effect=OSError("denied")
        ):
            code, out = self.run_cli(["tidy", "--json", str(repo)])
        self.assertEqual(code, 1)
        result = json.loads(out)["results"][0]
        self.assertEqual(result["status"], "error")
        self.assertIn("OSError", result["error"])

    def test_preflight_skipped_repo_exits_0(self):
        repo = self.fake_repo("demo")
        with patch(
            "janitor.cli.sweep_repo",
            return_value={"repo": "demo", "status": "skipped", "reason": "git_index_locked"},
        ):
            code, out = self.run_cli(["sweep", str(repo)])
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], "[skipped] demo (git_index_locked)")

    def test_mixed_fleet_one_failure_exits_1(self):
        fleet = self.plain_dir("fleet")
        for name in ("good", "bad"):
            (fleet / name / ".git").mkdir(parents=True)

        def fake_sweep(repo, state_mgr, run_id, dry_run=False):
            if repo.name == "bad":
                return {"repo": repo.name, "status": "synthesis_failed", "raw": "x"}
            return {"repo": repo.name, "status": "quiet", "tokens_spent": 0}

        with patch("janitor.cli.sweep_repo", side_effect=fake_sweep):
            code, out = self.run_cli(["sweep", "--all", "--json"], workspace=fleet)
        self.assertEqual(code, 1)
        statuses = [r["status"] for r in json.loads(out)["results"]]
        # Alphabetical fleet order: "bad" runs before "good".
        self.assertEqual(statuses, ["synthesis_failed", "quiet"])


if __name__ == "__main__":
    unittest.main()
