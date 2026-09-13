import contextlib
import fcntl
import json
import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from janitor.cli import main


class PullRequestCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.repo = self.root / "demo"
        (self.repo / ".git").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, argv):
        out = StringIO()
        with (
            patch.dict(os.environ, {"JANITOR_STATE_DIR": str(self.state)}),
            contextlib.redirect_stdout(out),
        ):
            return main(argv), out.getvalue()

    @patch("janitor.cli.discover_github_repos", return_value=["alice/demo"])
    @patch(
        "janitor.cli.publish_repositories",
        return_value=[
            {"repo": "alice/demo", "status": "published", "pr_url": "https://x/p/1"}
        ],
    )
    def test_publish_dispatches_before_legacy_loop_with_default_limit(
        self, publish, discover
    ):
        code, out = self.run_cli(["publish", str(self.repo), "--json"])
        self.assertEqual(code, 0)
        publish.assert_called_once_with(
            ["alice/demo"], self.state, dry_run=False, limit=20
        )
        self.assertEqual(json.loads(out)["results"][0]["status"], "published")

    @patch("janitor.cli.discover_github_repos", return_value=["alice/demo"])
    @patch(
        "janitor.cli.publish_repositories",
        return_value=[
            {
                "repo": "alice/demo",
                "status": "existing_pr",
                "state_error": "receipt_failed",
            }
        ],
    )
    def test_publish_state_error_is_nonzero_and_limit_accepts_100(
        self, publish, discover
    ):
        code, _ = self.run_cli(["publish", str(self.repo), "--limit", "100"])
        self.assertEqual(code, 1)
        publish.assert_called_once_with(
            ["alice/demo"], self.state, dry_run=False, limit=100
        )

    @patch("janitor.cli.discover_github_repos", return_value=["alice/demo"])
    @patch("janitor.cli.collect_reviews")
    def test_reviews_uses_artifact_result_and_incomplete_is_nonzero(
        self, collect, discover
    ):
        collect.return_value = {
            "complete": False,
            "results": [],
            "artifacts": {
                "json": "/x/report.json",
                "markdown": "/x/report.md",
                "prompt": "/x/prompt.md",
            },
        }
        code, out = self.run_cli(["reviews", str(self.repo)])
        self.assertEqual(code, 1)
        self.assertIn("/x/report.json", out)
        collect.assert_called_once_with(["alice/demo"], self.state)

    def test_publish_limit_is_bounded(self):
        with self.assertRaises(SystemExit):
            self.run_cli(["publish", str(self.repo), "--limit", "101"])

    @patch("janitor.cli.collect_reviews")
    def test_reviews_collector_lock_rejects_overlap(self, collect):
        self.state.mkdir()
        with (self.state / "reviews.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            code, _ = self.run_cli(["reviews", str(self.repo)])
        self.assertEqual(code, 1)
        collect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
