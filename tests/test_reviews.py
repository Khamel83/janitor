import base64
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from janitor.github import GitHubError
from janitor.reviews import collect_reviews


HEAD = "b" * 40
BASE = "a" * 40
BOT = "khamel-homelab-pr-reviewer[bot]"


class FakeGitHub:
    review_body = f"<!-- homelab-github-pr-reviewer:{HEAD} -->\n## Homelab AI review\n**Verdict:** `pass`\nReviewed commit: {HEAD}"
    review_login = BOT
    review_type = "Bot"
    review_state = "COMMENTED"
    review_commit = HEAD
    second_head = HEAD
    patch_value = "@@ -1 +1 @@\n-old\n+new"
    fail_repo = None

    def __init__(self):
        self.pr_reads = 0

    @classmethod
    def reset(cls):
        cls.review_body = f"<!-- homelab-github-pr-reviewer:{HEAD} -->\n## Homelab AI review\n**Verdict:** `pass`\nReviewed commit: {HEAD}"
        cls.review_login = BOT
        cls.review_type = "Bot"
        cls.review_state = "COMMENTED"
        cls.review_commit = HEAD
        cls.second_head = HEAD
        cls.patch_value = "@@ -1 +1 @@\n-old\n+new"
        cls.fail_repo = None

    def pages(self, path, key=None):
        repo = path.split("/repos/", 1)[1].split("/", 2)[:2]
        repo = "/".join(repo)
        if repo == self.fail_repo:
            raise GitHubError("GitHub API request failed (HTTP 503)", status=503)
        if path.endswith("/pulls?state=open"):
            return [self._pr()]
        if path.endswith("/files"):
            return [
                {
                    "filename": "janitor/x.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 1,
                    "changes": 2,
                    "patch": self.patch_value,
                }
            ]
        if path.endswith("/reviews"):
            return [
                {
                    "id": 7,
                    "state": self.review_state,
                    "commit_id": self.review_commit,
                    "submitted_at": "2026-09-13T12:00:00Z",
                    "body": self.review_body,
                    "html_url": "https://github.test/review/7",
                    "user": {"login": self.review_login, "type": self.review_type},
                }
            ]
        if path.endswith("/issues/1/comments"):
            return [
                {
                    "id": 8,
                    "body": "human note",
                    "user": {"login": "alice", "type": "User"},
                }
            ]
        if path.endswith("/pulls/1/comments"):
            return [
                {
                    "id": 9,
                    "body": "inline",
                    "path": "janitor/x.py",
                    "user": {"login": "alice"},
                }
            ]
        if path.endswith("/check-runs"):
            return [
                {
                    "name": "test",
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": "https://github.test/check/1",
                }
            ]
        raise AssertionError(path)

    def api(self, path, method="GET", payload=None):
        if path.endswith("/pulls/1"):
            self.pr_reads += 1
            return self._pr(self.second_head if self.pr_reads > 1 else HEAD)
        if "/contents/" in path:
            name = path.split("/contents/", 1)[1].split("?", 1)[0]
            if name == "HANDOFF.md":
                raise GitHubError("not found", status=404)
            text = {"CONTEXT.md": "original context\n", "TODO.md": "- [ ] original\n"}[
                name
            ]
            raw = text.encode()
            return {
                "type": "file",
                "encoding": "base64",
                "size": len(raw),
                "content": base64.b64encode(raw).decode(),
                "html_url": f"https://github.test/blob/{BASE}/{name}",
            }
        if path.endswith("/status"):
            return {"state": "success", "statuses": []}
        raise AssertionError(path)

    @staticmethod
    def _pr(head=HEAD):
        return {
            "number": 1,
            "title": "Improve x",
            "body": "PR body",
            "html_url": "https://github.test/p/1",
            "base": {"sha": BASE, "ref": "main"},
            "head": {"sha": head, "ref": "feature"},
        }


class ReviewCollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"
        FakeGitHub.reset()

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self, repos=None):
        with patch("janitor.reviews.GitHub", FakeGitHub):
            return collect_reviews(repos or ["alice/demo"], self.state)

    def pr(self, report):
        return report["results"][0]["pull_requests"][0]

    def test_current_head_trusted_comment_pass_is_not_merge_approval(self):
        report = self.collect()
        pr = self.pr(report)
        self.assertEqual(pr["review"]["verdict"], "pass")
        self.assertEqual(pr["review"]["state"], "reviewed-pass")
        self.assertFalse(pr["review"]["github_approval"])
        self.assertTrue(report["complete"])
        self.assertIn("## Reviewed pass", report["briefMarkdown"])
        self.assertIn("## Actionable findings or blocked", report["briefMarkdown"])

    def test_stale_review_is_retained_but_not_current_pass(self):
        FakeGitHub.review_commit = "c" * 40
        FakeGitHub.review_body = FakeGitHub.review_body.replace(HEAD, "c" * 40)
        pr = self.pr(self.collect())
        self.assertEqual(pr["review"]["state"], "stale")
        self.assertIn("Homelab AI review", pr["reviews"][0]["body"])

    def test_spoofed_login_or_type_is_not_trusted(self):
        FakeGitHub.review_login = "khamel-homelab-pr-reviewer"
        pr = self.pr(self.collect())
        self.assertEqual(pr["review"]["state"], "pending")

    def test_unknown_verdict_and_dismissed_reviews_are_not_pass(self):
        FakeGitHub.review_body = FakeGitHub.review_body.replace("`pass`", "`maybe`")
        self.assertEqual(self.pr(self.collect())["review"]["state"], "unknown")
        FakeGitHub.reset()
        FakeGitHub.review_state = "DISMISSED"
        self.assertEqual(self.pr(self.collect())["review"]["state"], "pending")

    def test_missing_review_is_pending_and_valid(self):
        original = FakeGitHub.pages

        def pages(client, path, key=None):
            return [] if path.endswith("/reviews") else original(client, path, key)

        with patch.object(FakeGitHub, "pages", pages):
            report = self.collect()
        self.assertEqual(self.pr(report)["review"]["state"], "pending")
        self.assertTrue(report["complete"])

    def test_failed_checks_remain_distinct(self):
        original = FakeGitHub.pages

        def pages(client, path, key=None):
            if path.endswith("/check-runs"):
                return [
                    {"name": "test", "status": "completed", "conclusion": "failure"}
                ]
            return original(client, path, key)

        with patch.object(FakeGitHub, "pages", pages):
            pr = self.pr(self.collect())
        self.assertEqual(pr["checks"]["state"], "failed")

    def test_empty_combined_pending_shape_means_checks_absent(self):
        original = FakeGitHub.api

        def api(client, path, method="GET", payload=None):
            if path.endswith("/status"):
                return {"state": "pending", "total_count": 0, "statuses": []}
            return original(client, path, method, payload)

        original_pages = FakeGitHub.pages

        def pages(client, path, key=None):
            return (
                []
                if path.endswith("/check-runs")
                else original_pages(client, path, key)
            )

        with (
            patch.object(FakeGitHub, "api", api),
            patch.object(FakeGitHub, "pages", pages),
        ):
            pr = self.pr(self.collect())
        self.assertEqual(pr["checks"]["state"], "absent")
        self.assertEqual(pr["checks"]["combined_status_state"], "absent")

    def test_head_movement_and_missing_patch_make_collection_incomplete(self):
        FakeGitHub.second_head = "d" * 40
        FakeGitHub.patch_value = None
        report = self.collect()
        pr = self.pr(report)
        self.assertFalse(report["complete"])
        self.assertTrue(pr["head_changed"])
        self.assertFalse(pr["evidence"]["diff_complete"])

    def test_repo_error_is_kept_alongside_success(self):
        FakeGitHub.fail_repo = "alice/bad"
        report = self.collect(["alice/demo", "alice/bad"])
        self.assertEqual([r["status"] for r in report["results"]], ["ok", "error"])
        self.assertFalse(report["complete"])
        self.assertIn("alice/bad", report["briefMarkdown"])

    def test_private_atomic_snapshot_preserves_original_context_and_local_references(
        self,
    ):
        intent = self.state / "publication-intents" / "alice--demo" / f"{BASE}.json"
        intent.parent.mkdir(parents=True)
        intent.write_text(
            json.dumps(
                {
                    "repo": "alice/demo",
                    "source_sha": BASE,
                    "original_documents": {
                        "CONTEXT.md": {"exists": True, "text": "intent original"}
                    },
                    "generated_documents": {},
                    "tree_sha": "tree",
                    "commit_sha": HEAD,
                }
            )
        )
        self.state.mkdir(exist_ok=True)
        (self.state / "publication-receipts.jsonl").write_text(
            json.dumps(
                {
                    "repo": "alice/demo",
                    "status": "published",
                    "recorded_at": "2026-09-13T10:00:00Z",
                }
            )
            + "\n"
        )
        report = self.collect()
        pr = self.pr(report)
        self.assertEqual(
            pr["original_documents"]["CONTEXT.md"]["text"], "original context\n"
        )
        self.assertTrue(pr["publication"]["intents"])
        for key in ("json", "markdown", "prompt"):
            path = Path(report["artifacts"][key])
            self.assertTrue(path.exists())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertIn(
            "all PRs together", Path(report["artifacts"]["prompt"]).read_text()
        )
        self.assertIn(
            "original intent", Path(report["artifacts"]["prompt"]).read_text()
        )
        self.assertEqual(
            stat.S_IMODE(Path(report["artifacts"]["directory"]).stat().st_mode), 0o700
        )
        self.assertEqual(
            json.loads((self.state / "morning/latest.json").read_text())["snapshot"],
            report["artifacts"]["directory"],
        )

    def test_pagination_failure_is_visible(self):
        def pages(client, path, key=None):
            raise GitHubError("GitHub pagination limit reached before completion")

        with patch.object(FakeGitHub, "pages", pages):
            report = self.collect()
        self.assertFalse(report["complete"])
        self.assertEqual(report["results"][0]["status"], "error")


if __name__ == "__main__":
    unittest.main()
