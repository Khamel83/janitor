import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from janitor import branch_review
from janitor.branch_review import (
    AGING_DAYS,
    MAX_CHANGED_PATHS,
    MAX_DOC_EVIDENCE_CHARS,
    ACTIVE_DAYS,
    branch_report_hash,
    collect_branch_report,
    render_branch_block,
)


FIXED_NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


class BranchReviewFixture(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name) / "repo"
        self.repo.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.name", "Test User")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "commit.gpgsign", "false")

        (self.repo / "README.md").write_text("# branch review\n")
        (self.repo / "CONTEXT.md").write_text("repository context\n")
        (self.repo / "TODO.md").write_text("repository todo\n")
        self._commit("initial", "2026-09-01T12:00:00+00:00")

        self._make_branch(
            "feature",
            "2026-09-10T12:00:00+00:00",
            ["one.txt", "two.txt", "three.txt", "four.txt", "five.txt", "six.txt"],
        )
        self._make_branch("fresh", "2026-09-10T12:00:00+00:00", ["fresh.txt"])
        self._make_branch("eight-days", "2026-09-03T12:00:00+00:00", ["eight.txt"])
        self._make_branch("old", "2026-07-01T12:00:00+00:00", ["old.txt"])
        self._make_branch("dirty", "2026-07-01T12:00:00+00:00", ["dirty.txt"])
        self._make_branch("auto-wip/old", "2026-01-01T12:00:00+00:00", ["wip.txt"])
        self._make_branch("remote-only", "2026-09-10T12:00:00+00:00", ["remote.txt"])
        unrelated_sha = self._make_unrelated_branch()

        self._git("remote", "add", "origin", "https://example.test/origin.git")
        self._git("remote", "add", "upstream", "https://example.test/upstream.git")
        self._git("update-ref", "refs/remotes/origin/feature", self._rev("feature"))
        self._git("update-ref", "refs/remotes/origin/fresh", self._rev("fresh"))
        self._git(
            "update-ref", "refs/remotes/upstream/remote-only", self._rev("remote-only")
        )
        self._git("update-ref", "refs/remotes/upstream/unrelated", unrelated_sha)
        self._git(
            "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"
        )
        self._git("update-ref", "refs/remotes/origin/main", self._rev("main"))
        self._git("tag", "release-tag", self._rev("main"))

        missing_path = self.repo.parent / "missing-feature-worktree"
        self._git("worktree", "add", "-q", "-b", "feature-worktree", str(missing_path))
        self._git("worktree", "remove", "--force", str(missing_path))
        # Keep a stale porcelain record for feature by removing the directory
        # without asking Git to prune the linked worktree metadata.
        present_path = self.repo.parent / "missing-feature-worktree-2"
        self._git(
            "worktree", "add", "-q", "--detach", str(present_path), self._rev("feature")
        )
        (present_path / "dirty.txt").write_text("dirty\n")
        self.dirty_worktree = present_path
        self._git(
            "worktree", "add", "-q", str(self.repo.parent / "dirty-worktree"), "dirty"
        )
        (self.repo.parent / "dirty-worktree" / "dirty-live.txt").write_text("dirty\n")

        # The branch under test is represented by an attached-but-missing
        # worktree record created by deleting its directory after registration.
        missing_branch_path = self.repo.parent / "feature-missing"
        self._git("worktree", "add", "-q", str(missing_branch_path), "feature")
        shutil.rmtree(missing_branch_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _git(self, *args, cwd=None, env=None, check=True):
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.repo,
            env=env,
            capture_output=True,
            text=True,
            check=check,
        )

    def _commit(self, subject, date):
        env = os.environ.copy()
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
        self._git("add", ".")
        self._git("commit", "-m", subject, env=env)

    def _make_branch(self, name, date, files):
        self._git("checkout", "-q", "-b", name, "main")
        for filename in files:
            (self.repo / filename).write_text(f"{name}: {filename}\n")
        self._commit(f"subject {name}", date)
        self._git("checkout", "-q", "main")

    def _make_unrelated_branch(self):
        self._git("checkout", "-q", "--orphan", "unrelated")
        for path in self.repo.iterdir():
            if path.name != ".git":
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
        (self.repo / "unrelated.txt").write_text("unrelated\n")
        self._commit("subject unrelated", "2026-09-10T12:00:00+00:00")
        sha = self._rev("HEAD")
        self._git("checkout", "-q", "main")
        self._git("branch", "-D", "unrelated")
        return sha

    def _rev(self, ref):
        return self._git("rev-parse", ref).stdout.strip()


class TestBranchCollector(BranchReviewFixture):
    def test_collects_local_origin_and_remote_only_refs_into_logical_rows(self):
        report = collect_branch_report(self.repo, now=FIXED_NOW, fetch=False)
        rows = {row["name"]: row for row in report["branches"]}
        self.assertTrue({"main", "feature", "remote-only"} <= set(rows))
        self.assertEqual(rows["feature"]["local_ref"]["name"], "refs/heads/feature")
        self.assertEqual(
            [r["name"] for r in rows["feature"]["remote_refs"]], ["origin/feature"]
        )

    def test_excludes_symbolic_remote_head_and_tags(self):
        report = collect_branch_report(self.repo, now=FIXED_NOW, fetch=False)
        names = {row["name"] for row in report["branches"]}
        self.assertNotIn("HEAD", names)
        self.assertNotIn("release-tag", names)

    def test_classification_uses_committer_age_dirty_worktree_and_auto_wip_priority(
        self,
    ):
        rows = {
            row["name"]: row
            for row in collect_branch_report(self.repo, now=FIXED_NOW, fetch=False)[
                "branches"
            ]
        }
        self.assertEqual(rows["auto-wip/old"]["classification"], "abandoned_auto_wip")
        self.assertEqual(rows["fresh"]["classification"], "active")
        self.assertEqual(rows["eight-days"]["classification"], "aging")
        self.assertEqual(rows["old"]["classification"], "stale")
        self.assertIn("dirty_worktree", rows["dirty"]["attention_flags"])
        self.assertEqual(ACTIVE_DAYS, 7)
        self.assertEqual(AGING_DAYS, 30)

    def test_records_base_ref_sha_comparison_and_bounded_changed_paths(self):
        report = collect_branch_report(self.repo, now=FIXED_NOW, fetch=False)
        self.assertEqual(report["base"]["branch"], "main")
        self.assertEqual(report["base"]["ref"], "refs/remotes/origin/main")
        self.assertEqual(report["base"]["sha"], self._rev("main"))
        row = next(item for item in report["branches"] if item["name"] == "feature")
        comparison = row["tip"]["comparison"]
        self.assertEqual(comparison["ahead"], 1)
        self.assertEqual(comparison["behind"], 0)
        self.assertFalse(comparison["merged"])
        self.assertLessEqual(len(comparison["changed_paths"]), MAX_CHANGED_PATHS)
        self.assertEqual(comparison["changed_path_count"], 6)

    def test_no_common_ancestor_is_reported_without_raising(self):
        report = collect_branch_report(self.repo, now=FIXED_NOW, fetch=False)
        row = next(item for item in report["branches"] if item["name"] == "unrelated")
        self.assertEqual(row["tip"]["comparison"]["status"], "no_common_ancestor")

    def test_missing_worktree_is_reported_without_status_probe(self):
        report = collect_branch_report(self.repo, now=FIXED_NOW, fetch=False)
        row = next(item for item in report["branches"] if item["name"] == "feature")
        self.assertTrue(any(item["status"] == "missing" for item in row["worktrees"]))
        self.assertIn("missing_worktree", row["attention_flags"])

    def test_report_contract_contains_required_fields_and_repository_evidence(self):
        report = collect_branch_report(self.repo, now=FIXED_NOW, fetch=False)
        self.assertEqual(
            {
                "repo",
                "observed_at",
                "fetch",
                "report_stale",
                "base",
                "branches",
                "attention_flags",
                "report_hash",
            },
            set(report),
        )
        required_row_fields = {
            "name",
            "local_ref",
            "remote_refs",
            "refs",
            "tip",
            "worktrees",
            "classification",
            "attention_flags",
            "focus",
            "evidence",
        }
        self.assertTrue(
            all(required_row_fields <= set(row) for row in report["branches"])
        )
        feature = next(row for row in report["branches"] if row["name"] == "feature")
        self.assertLessEqual(
            len(feature["evidence"]["documents"]["CONTEXT.md"]["content"]),
            MAX_DOC_EVIDENCE_CHARS,
        )
        self.assertEqual(
            feature["evidence"]["documents"]["CONTEXT.md"]["source"],
            "repository evidence",
        )

    def test_fetch_false_never_runs_fetch(self):
        original = branch_review._run_git
        calls = []

        def recording(repo_dir, args, **kwargs):
            calls.append(list(args))
            return original(repo_dir, args, **kwargs)

        with mock.patch.object(branch_review, "_run_git", side_effect=recording):
            collect_branch_report(self.repo, now=FIXED_NOW, fetch=False)
        self.assertFalse(any(args[:3] == ["git", "fetch", "--prune"] for args in calls))


class TestFetch(unittest.TestCase):
    def _run_result(self, returncode=0, stdout="", stderr=""):
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    def test_successful_fetch_uses_noninteractive_environment(self):
        calls = []

        def run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            if cmd[1:3] == ["remote", "-v"] or cmd[1:2] == ["remote"]:
                return self._run_result(stdout="origin\nupstream\n")
            return self._run_result()

        with mock.patch.object(branch_review.subprocess, "run", side_effect=run):
            result = branch_review._fetch_primary_remote(Path("/repo"), enabled=True)
        self.assertEqual(result["status"], "fetched")
        fetch_call = next(
            item for item in calls if item[0][1:3] == ["fetch", "--prune"]
        )
        self.assertEqual(fetch_call[1]["timeout"], branch_review.FETCH_TIMEOUT_SECONDS)
        self.assertEqual(fetch_call[1]["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(
            fetch_call[1]["env"]["GIT_SSH_COMMAND"],
            "ssh -o BatchMode=yes -o ConnectTimeout=5",
        )

    def test_nonzero_fetch_is_reported(self):
        def run(cmd, **kwargs):
            if cmd[1:2] == ["remote"]:
                return self._run_result(stdout="origin\n")
            return self._run_result(returncode=128, stderr="network down")

        with mock.patch.object(branch_review.subprocess, "run", side_effect=run):
            result = branch_review._fetch_primary_remote(Path("/repo"), enabled=True)
        self.assertEqual(result["status"], "fetch_failed")
        self.assertIn("network down", result["reason"])

    def test_timeout_fetch_is_reported(self):
        def run(cmd, **kwargs):
            if cmd[1:2] == ["remote"]:
                return self._run_result(stdout="origin\n")
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

        with mock.patch.object(branch_review.subprocess, "run", side_effect=run):
            result = branch_review._fetch_primary_remote(Path("/repo"), enabled=True)
        self.assertEqual(result["status"], "fetch_failed")
        self.assertEqual(result["timed_out"], True)

    def test_no_remote_does_not_attempt_fetch(self):
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            return self._run_result(stdout="")

        with mock.patch.object(branch_review.subprocess, "run", side_effect=run):
            result = branch_review._fetch_primary_remote(Path("/repo"), enabled=True)
        self.assertEqual(result["status"], "no_remote")
        self.assertFalse(any(cmd[1:2] == ["fetch"] for cmd in calls))

    def test_disabled_fetch_is_not_attempted(self):
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            return self._run_result(stdout="origin\n")

        with mock.patch.object(branch_review.subprocess, "run", side_effect=run):
            result = branch_review._fetch_primary_remote(Path("/repo"), enabled=False)
        self.assertEqual(result["status"], "not_attempted")
        self.assertEqual(result["remote"], "origin")
        self.assertFalse(any(cmd[1:2] == ["fetch"] for cmd in calls))


class TestBranchRenderer(unittest.TestCase):
    def _report(self, rows, **overrides):
        report = {
            "repo": "/repo",
            "observed_at": "2026-09-11T12:00:00+00:00",
            "fetch": {"status": "fetched", "remote": "origin"},
            "report_stale": False,
            "base": {
                "branch": "main",
                "ref": "refs/remotes/origin/main",
                "sha": "a" * 40,
                "status": "ok",
            },
            "branches": rows,
            "attention_flags": [],
            "report_hash": "",
        }
        report.update(overrides)
        return report

    def _row(
        self,
        name,
        classification="active",
        flags=None,
        timestamp=1,
        subject="subject",
        path="README.md",
    ):
        comparison = {
            "status": "ok",
            "ahead": 0,
            "behind": 0,
            "merged": True,
            "changed_paths": [path],
            "changed_path_count": 1,
        }
        ref = {
            "name": f"refs/heads/{name}",
            "ref": f"refs/heads/{name}",
            "sha": "b" * 40,
            "committer_timestamp": timestamp,
            "committer_date": "2026-09-01T00:00:00+00:00",
            "subject": subject,
            "comparison": comparison,
        }
        return {
            "name": name,
            "local_ref": ref,
            "remote_refs": [],
            "refs": [ref],
            "tip": ref,
            "worktrees": [{"path": "/repo", "status": "clean", "branch": ref["ref"]}],
            "classification": classification,
            "attention_flags": flags or [],
            "focus": f"subject: {subject}; paths: {path}",
            "evidence": {
                "recent_subjects": [subject],
                "changed_paths": [path],
                "changed_path_count": 1,
                "documents": {},
            },
        }

    def test_attention_first_and_name_tie_sorting(self):
        text = render_branch_block(
            self._report(
                [
                    self._row("zeta", timestamp=10),
                    self._row("alpha", timestamp=10),
                    self._row("attention", flags=["dirty_worktree"], timestamp=1),
                ]
            )
        )
        self.assertLess(text.index("| attention |"), text.index("| alpha |"))
        self.assertLess(text.index("| alpha |"), text.index("| zeta |"))

    def test_escaping_pipes_newlines_and_evidence_cap(self):
        long_subject = "x|y\n" + ("z" * (MAX_DOC_EVIDENCE_CHARS + 100))
        text = render_branch_block(
            self._report([self._row("feature", subject=long_subject, path="a|b\nc")])
        )
        self.assertIn("x\\|y<br>", text)
        self.assertNotIn("\n", text.split("| feature |")[1].split("\n", 1)[0])
        self.assertLessEqual(len(text), MAX_DOC_EVIDENCE_CHARS * 2)

    def test_no_branches_and_base_unavailable(self):
        text = render_branch_block(
            self._report(
                [],
                base={
                    "status": "base_unavailable",
                    "branch": None,
                    "ref": None,
                    "sha": None,
                },
            )
        )
        self.assertIn("Base: unavailable (base_unavailable)", text)
        self.assertIn("| Branch | Class |", text)
        self.assertNotIn("| main |", text)

    def test_stale_no_remote_reason_and_unknown_comparison(self):
        row = self._row("remote", flags=["comparison_unknown"])
        row["tip"]["comparison"] = {
            "status": "no_common_ancestor",
            "ahead": None,
            "behind": None,
            "merged": None,
            "changed_paths": [],
            "changed_path_count": None,
        }
        text = render_branch_block(
            self._report([row], fetch={"status": "no_remote"}, report_stale=True)
        )
        self.assertIn("Freshness: stale (no_remote)", text)
        self.assertIn("| remote | active | local | ? | ? |", text)

    def test_repeated_render_is_byte_identical(self):
        report = self._report([self._row("main")])
        self.assertEqual(
            render_branch_block(report),
            render_branch_block(json.loads(json.dumps(report))),
        )

    def test_hash_ignores_observation_timestamp(self):
        first = self._report(
            [self._row("main")], observed_at="2026-09-11T12:00:00+00:00"
        )
        second = json.loads(json.dumps(first))
        second["observed_at"] = "2026-09-12T12:00:00+00:00"
        self.assertEqual(branch_report_hash(first), branch_report_hash(second))


if __name__ == "__main__":
    unittest.main()
