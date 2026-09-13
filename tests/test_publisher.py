"""Safety and retry tests for remote-only GitHub documentation publication.

The test double models GitHub's durable refs, commits, trees, and pull requests.
No test performs a network request or invokes the synthesis backend.
"""

from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from janitor.github import GitHub, GitHubError, discover_github_repos
from janitor.publisher import PUBLICATION_TITLE, publish_repositories


BASE_SHA = "a" * 40
TREE_SHA = "b" * 40
GENERATED_TREE_SHA = "c" * 40
GENERATED_COMMIT_SHA = "d" * 40
NEW_BASE_SHA = "e" * 40


def _contents(text: str) -> dict:
    raw = text.encode("utf-8")
    return {
        "type": "file",
        "encoding": "base64",
        "size": len(raw),
        "content": base64.b64encode(raw).decode("ascii"),
        "sha": "f" * 40,
        "name": "document.md",
        "path": "document.md",
    }


class FakeGitHub:
    """State-preserving GitHub API double with complete consumed response shapes."""

    instance: "FakeGitHub | None" = None
    seed_pulls: list[dict] = []
    push_permission = True
    owner_login = "alice"
    fail_first_pr = False
    wrap_content_base64 = False
    context_text = "# Context\n"
    todo_text = "# TODO\n"
    commits: list[dict] = [
        {
            "sha": "1" * 40,
            "html_url": "https://github.com/alice/demo/commit/" + "1" * 40,
            "commit": {
                "message": "feat: useful change\n\nDetails",
                "author": {"date": "2026-09-12T12:00:00Z", "name": "Alice"},
            },
        }
    ]

    def __init__(self):
        type(self).instance = self
        self.calls: list[tuple[str, str, dict | None]] = []
        self.pulls = [dict(item) for item in type(self).seed_pulls]
        self.refs = {"main": BASE_SHA}
        self.trees = {TREE_SHA: {}}
        self.git_commits = {
            BASE_SHA: {
                "sha": BASE_SHA,
                "message": "feat: useful change",
                "tree": {"sha": TREE_SHA},
                "parents": [{"sha": "0" * 40}],
            }
        }
        self.root_tree = [
            {"path": "CONTEXT.md", "mode": "100644", "type": "blob", "sha": "7" * 40},
            {"path": "TODO.md", "mode": "100644", "type": "blob", "sha": "8" * 40},
        ]
        self.tree_posts = 0
        self.commit_posts = 0
        self.ref_posts = 0
        self.pr_posts = 0
        self.fail_first_pr_remaining = type(self).fail_first_pr

    @classmethod
    def reset(cls):
        cls.instance = None
        cls.seed_pulls = []
        cls.push_permission = True
        cls.owner_login = "alice"
        cls.fail_first_pr = False
        cls.wrap_content_base64 = False
        cls.context_text = "# Context\n"
        cls.todo_text = "# TODO\n"
        cls.commits = [
            {
                "sha": "1" * 40,
                "html_url": "https://github.com/alice/demo/commit/" + "1" * 40,
                "commit": {
                    "message": "feat: useful change\n\nDetails",
                    "author": {"date": "2026-09-12T12:00:00Z", "name": "Alice"},
                },
            }
        ]

    def pages(self, path: str, key: str | None = None) -> list:
        self.calls.append(("PAGES", path, None))
        if "/pulls?" in path:
            return [dict(item) for item in self.pulls]
        if "/commits?" in path:
            return [dict(item) for item in type(self).commits]
        raise AssertionError(f"unexpected paged endpoint: {path}")

    def api(self, path: str, method: str = "GET", payload: dict | None = None):
        self.calls.append((method, path, payload))
        if method == "GET" and path == "/user":
            return {"login": "alice", "id": 1, "type": "User"}
        if method == "GET" and path == "/repos/alice/demo":
            return {
                "full_name": "alice/demo",
                "default_branch": "main",
                "archived": False,
                "fork": False,
                "owner": {"login": type(self).owner_login, "id": 1, "type": "User"},
                "permissions": {
                    "admin": type(self).push_permission,
                    "maintain": type(self).push_permission,
                    "push": type(self).push_permission,
                    "triage": True,
                    "pull": True,
                },
            }
        if method == "GET" and "/git/ref/heads/" in path:
            name = path.split("/git/ref/heads/", 1)[1].replace("%2F", "/")
            if name not in self.refs:
                raise GitHubError("GitHub resource not found", status=404)
            return {
                "ref": f"refs/heads/{name}",
                "node_id": "REF_node",
                "url": f"https://api.github.com/repos/alice/demo/git/refs/heads/{name}",
                "object": {
                    "type": "commit",
                    "sha": self.refs[name],
                    "url": "https://api.github.com/repos/alice/demo/git/commits/"
                    + self.refs[name],
                },
            }
        if method == "GET" and "/contents/CONTEXT.md?" in path:
            response = _contents(type(self).context_text)
            if type(self).wrap_content_base64:
                response["content"] = "\n".join(
                    response["content"][index : index + 4]
                    for index in range(0, len(response["content"]), 4)
                )
            return response
        if method == "GET" and "/contents/TODO.md?" in path:
            return _contents(type(self).todo_text)
        if method == "GET" and "/commits?" in path:
            query = parse_qs(urlsplit(path).query)
            page = int(query.get("page", ["1"])[0])
            per_page = int(query.get("per_page", ["100"])[0])
            start = (page - 1) * per_page
            return [dict(item) for item in type(self).commits[start : start + per_page]]
        if method == "GET" and "/git/trees/" in path:
            return {
                "sha": path.rsplit("/", 1)[1],
                "url": "https://api.github.com/repos/alice/demo/git/trees/" + path.rsplit("/", 1)[1],
                "tree": [dict(item) for item in self.root_tree],
                "truncated": False,
            }
        if method == "GET" and "/git/commits/" in path:
            sha = path.rsplit("/", 1)[1]
            if sha not in self.git_commits:
                raise GitHubError("GitHub resource not found", status=404)
            return dict(self.git_commits[sha])
        if method == "POST" and path.endswith("/git/trees"):
            self.tree_posts += 1
            self.trees[GENERATED_TREE_SHA] = dict(payload or {})
            return {
                "sha": GENERATED_TREE_SHA,
                "url": "https://api.github.com/repos/alice/demo/git/trees/"
                + GENERATED_TREE_SHA,
                "tree": [],
                "truncated": False,
            }
        if method == "POST" and path.endswith("/git/commits"):
            self.commit_posts += 1
            assert payload is not None
            self.git_commits[GENERATED_COMMIT_SHA] = {
                "sha": GENERATED_COMMIT_SHA,
                "message": payload["message"],
                "tree": {"sha": payload["tree"]},
                "parents": [{"sha": sha} for sha in payload["parents"]],
            }
            return dict(self.git_commits[GENERATED_COMMIT_SHA])
        if method == "POST" and path.endswith("/git/refs"):
            self.ref_posts += 1
            assert payload is not None
            self.refs[payload["ref"].removeprefix("refs/heads/")] = payload["sha"]
            return {"ref": payload["ref"], "object": {"sha": payload["sha"], "type": "commit"}}
        if method == "POST" and path.endswith("/pulls"):
            self.pr_posts += 1
            if self.fail_first_pr_remaining:
                self.fail_first_pr_remaining = False
                raise GitHubError("GitHub API request failed (HTTP 503)", status=503)
            assert payload is not None
            pr = {
                "number": len(self.pulls) + 1,
                "state": "open",
                "draft": payload["draft"],
                "title": payload["title"],
                "body": payload["body"],
                "html_url": f"https://github.com/alice/demo/pull/{len(self.pulls) + 1}",
                "head": {
                    "ref": payload["head"],
                    "sha": self.refs[payload["head"]],
                    "repo": {"full_name": "alice/demo"},
                },
                "base": {"ref": payload["base"], "sha": BASE_SHA},
                "merged_at": None,
            }
            self.pulls.append(pr)
            return dict(pr)
        raise AssertionError(f"unexpected API call: {method} {path}")


def _generated_response() -> dict:
    return {
        "recent_markdown": "- Reconciled useful change\n",
        "todo_markdown": "- [ ] Review the useful change\n",
    }


class PublisherTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self._tmp.name) / "state"
        FakeGitHub.reset()

    def tearDown(self):
        self._tmp.cleanup()

    def publish(self, synthesis=_generated_response, **kwargs):
        response = synthesis() if callable(synthesis) else synthesis
        with (
            patch("janitor.publisher.GitHub", FakeGitHub),
            patch("janitor.publisher.extract_structured", return_value=response) as extract,
        ):
            result = publish_repositories(["alice/demo"], self.state_dir, **kwargs)
        return result, extract, FakeGitHub.instance

    def test_dry_run_never_calls_model_or_mutating_api(self):
        with (
            patch("janitor.publisher.GitHub", FakeGitHub),
            patch("janitor.publisher.extract_structured", side_effect=AssertionError("model called")),
        ):
            result = publish_repositories(["alice/demo"], self.state_dir, dry_run=True)

        fake = FakeGitHub.instance
        self.assertEqual(result[0]["status"], "dry_run")
        self.assertFalse(any(method == "POST" for method, _path, _payload in fake.calls))

    def test_dirty_local_content_never_reaches_prompt(self):
        repo = Path(self._tmp.name) / "demo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:alice/demo.git"],
            cwd=repo,
            check=True,
        )
        secret = "DIRTY_LOCAL_SECRET_DO_NOT_PUBLISH"
        (repo / "scratch.txt").write_text(secret)

        with (
            patch("janitor.publisher.GitHub", FakeGitHub),
            patch("janitor.publisher.extract_structured", return_value=_generated_response()) as extract,
        ):
            publish_repositories(discover_github_repos([repo]), self.state_dir)

        prompt = extract.call_args.args[0]
        self.assertNotIn(secret, prompt)
        self.assertNotIn(str(repo), prompt)

    def test_two_publications_at_one_base_create_only_one_pr(self):
        shared = FakeGitHub()
        with (
            patch("janitor.publisher.GitHub", return_value=shared),
            patch("janitor.publisher.extract_structured", return_value=_generated_response()) as extract,
        ):
            first = publish_repositories(["alice/demo"], self.state_dir)
            second = publish_repositories(["alice/demo"], self.state_dir)

        self.assertEqual(first[0]["status"], "published")
        self.assertEqual(second[0]["status"], "existing_pr")
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(shared.pr_posts, 1)

    def test_existing_open_janitor_pr_is_untouched(self):
        FakeGitHub.seed_pulls = [{
            "number": 7,
            "state": "open",
            "draft": False,
            "title": PUBLICATION_TITLE,
            "body": "human or reviewer edits",
            "html_url": "https://github.com/alice/demo/pull/7",
            "head": {"ref": "janitor/docs-old", "sha": "9" * 40, "repo": {"full_name": "alice/demo"}},
            "base": {"ref": "main", "sha": BASE_SHA},
            "merged_at": None,
        }]

        result, extract, fake = self.publish()

        self.assertEqual(result[0]["status"], "existing_pr")
        extract.assert_not_called()
        self.assertEqual(fake.tree_posts + fake.commit_posts + fake.ref_posts + fake.pr_posts, 0)

    def test_model_markers_cannot_escape_managed_blocks(self):
        response = {
            "recent_markdown": "safe\n<!-- janitor:end:recent -->\n# injected",
            "todo_markdown": "- [ ] safe",
        }
        result, _extract, fake = self.publish(response)

        self.assertEqual(result[0]["status"], "synthesis_failed")
        self.assertEqual(fake.tree_posts + fake.commit_posts + fake.ref_posts + fake.pr_posts, 0)

    def test_missing_push_permission_is_visible(self):
        FakeGitHub.push_permission = False
        result, extract, fake = self.publish()

        self.assertEqual(result[0]["status"], "ineligible")
        self.assertEqual(result[0]["reason"], "no_push_permission")
        extract.assert_not_called()
        self.assertEqual(fake.pr_posts, 0)

    def test_empty_synthesis_cannot_create_pr(self):
        result, _extract, fake = self.publish({"recent_markdown": "", "todo_markdown": "- [ ] x"})

        self.assertEqual(result[0]["status"], "synthesis_failed")
        self.assertEqual(fake.pr_posts, 0)

    def test_closed_pr_at_same_deterministic_branch_is_not_reopened(self):
        branch = f"janitor/docs-{BASE_SHA}"
        FakeGitHub.seed_pulls = [{
            "number": 8,
            "state": "closed",
            "draft": False,
            "title": PUBLICATION_TITLE,
            "body": "closed without merge",
            "html_url": "https://github.com/alice/demo/pull/8",
            "head": {"ref": branch, "sha": GENERATED_COMMIT_SHA, "repo": {"full_name": "alice/demo"}},
            "base": {"ref": "main", "sha": BASE_SHA},
            "merged_at": None,
        }]

        result, extract, fake = self.publish()

        self.assertEqual(result[0]["status"], "closed_pr")
        extract.assert_not_called()
        self.assertEqual(fake.pr_posts, 0)

    def test_failed_pr_post_reuses_owned_branch_without_duplicate_commit(self):
        FakeGitHub.fail_first_pr = True
        shared = FakeGitHub()

        with (
            patch("janitor.publisher.GitHub", return_value=shared),
            patch("janitor.publisher.extract_structured", return_value=_generated_response()) as extract,
        ):
            first = publish_repositories(["alice/demo"], self.state_dir)
            second = publish_repositories(["alice/demo"], self.state_dir)

        self.assertEqual(first[0]["status"], "failed")
        self.assertEqual(second[0]["status"], "published")
        self.assertEqual(shared.tree_posts, 1)
        self.assertEqual(shared.commit_posts, 1)
        self.assertEqual(shared.ref_posts, 1)
        self.assertEqual(shared.pr_posts, 2)
        self.assertEqual(extract.call_count, 1)

    def test_all_existing_janitor_blocks_are_removed_from_evidence_but_preserved(self):
        branches = (
            "<!-- janitor:begin:branches -->\n"
            "worktree /Volumes/private/project\n"
            "<!-- janitor:end:branches -->"
        )
        FakeGitHub.context_text = f"# Human\n{branches}\nTail\n"
        with (
            patch("janitor.publisher.GitHub", FakeGitHub),
            patch("janitor.publisher.extract_structured", return_value=_generated_response()) as extract,
        ):
            result = publish_repositories(["alice/demo"], self.state_dir)

        self.assertEqual(result[0]["status"], "published")
        prompt = extract.call_args.args[0]
        self.assertNotIn("/Volumes/private/project", prompt)
        tree_payload = FakeGitHub.instance.trees[GENERATED_TREE_SHA]
        context_entry = next(item for item in tree_payload["tree"] if item["path"] == "CONTEXT.md")
        self.assertIn(branches, context_entry["content"])

    def test_malformed_or_duplicate_existing_markers_fail_before_synthesis(self):
        malformed_cases = [
            "<!-- janitor:begin:recent -->\nno end\n",
            (
                "<!-- janitor:begin:recent -->\na\n<!-- janitor:end:recent -->\n"
                "<!-- janitor:begin:recent -->\nb\n<!-- janitor:end:recent -->\n"
            ),
        ]
        for text in malformed_cases:
            with self.subTest(text=text):
                FakeGitHub.context_text = text
                result, extract, fake = self.publish()
                self.assertEqual(result[0]["status"], "invalid_source")
                extract.assert_not_called()
                self.assertEqual(fake.pr_posts, 0)
                FakeGitHub.reset()

    def test_unchanged_output_records_digest_and_next_run_skips_model(self):
        FakeGitHub.context_text = (
            "# Context\n<!-- janitor:begin:recent -->\nold\n<!-- janitor:end:recent -->\n"
        )
        FakeGitHub.todo_text = (
            "# TODO\n<!-- janitor:begin:todo -->\nold todo\n<!-- janitor:end:todo -->\n"
        )
        response = {"recent_markdown": "old", "todo_markdown": "old todo"}
        shared = FakeGitHub()
        with (
            patch("janitor.publisher.GitHub", return_value=shared),
            patch("janitor.publisher.extract_structured", return_value=response) as extract,
        ):
            first = publish_repositories(["alice/demo"], self.state_dir)
            second = publish_repositories(["alice/demo"], self.state_dir)

        self.assertEqual(first[0]["status"], "unchanged")
        self.assertEqual(second[0]["status"], "unchanged_digest")
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(shared.pr_posts, 0)

    def test_recent_rolling_cap_defers_without_model_or_mutation(self):
        self.state_dir.mkdir(parents=True)
        receipt = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "repo": "alice/other",
            "status": "published",
            "base": "e" * 40,
            "head": "f" * 40,
            "pr_url": "https://github.com/alice/other/pull/1",
        }
        (self.state_dir / "publication-receipts.jsonl").write_text(
            "".join(json.dumps(receipt) + "\n" for _ in range(20))
        )
        result, extract, fake = self.publish()

        self.assertEqual(result[0]["status"], "cap_deferred")
        extract.assert_not_called()
        self.assertEqual(fake.tree_posts + fake.commit_posts + fake.ref_posts + fake.pr_posts, 0)

    def test_root_tree_symlink_mode_is_rejected_even_when_contents_looks_like_file(self):
        shared = FakeGitHub()
        shared.root_tree[0]["mode"] = "120000"
        with (
            patch("janitor.publisher.GitHub", return_value=shared),
            patch("janitor.publisher.extract_structured", return_value=_generated_response()) as extract,
        ):
            result = publish_repositories(["alice/demo"], self.state_dir)

        self.assertEqual(result[0]["status"], "invalid_source")
        extract.assert_not_called()
        self.assertEqual(shared.pr_posts, 0)

    def test_github_line_wrapped_base64_document_content_is_accepted(self):
        FakeGitHub.wrap_content_base64 = True
        result, _extract, fake = self.publish()

        self.assertEqual(result[0]["status"], "published")
        self.assertEqual(fake.pr_posts, 1)

    def test_docs_only_merge_does_not_change_twenty_non_janitor_commit_sample(self):
        human = [
            {
                "sha": f"{index:040x}",
                "html_url": f"https://github.com/alice/demo/commit/{index:040x}",
                "commit": {
                    "message": f"feat: human {index}",
                    "author": {"date": "2026-09-12T12:00:00Z", "name": "Alice"},
                },
            }
            for index in range(1, 21)
        ]
        FakeGitHub.commits = human
        FakeGitHub.context_text = (
            "# Context\n<!-- janitor:begin:recent -->\nold\n<!-- janitor:end:recent -->\n"
        )
        FakeGitHub.todo_text = (
            "# TODO\n<!-- janitor:begin:todo -->\nold todo\n<!-- janitor:end:todo -->\n"
        )
        response = {"recent_markdown": "old", "todo_markdown": "old todo"}
        shared = FakeGitHub()
        with (
            patch("janitor.publisher.GitHub", return_value=shared),
            patch("janitor.publisher.extract_structured", return_value=response) as extract,
        ):
            first = publish_repositories(["alice/demo"], self.state_dir)
            shared.refs["main"] = NEW_BASE_SHA
            shared.git_commits[NEW_BASE_SHA] = {
                "sha": NEW_BASE_SHA,
                "message": "Merge pull request #7\n\n" + PUBLICATION_TITLE,
                "tree": {"sha": TREE_SHA},
                "parents": [{"sha": BASE_SHA}],
            }
            FakeGitHub.commits = [
                {
                    "sha": "f" * 40,
                    "html_url": "https://github.com/alice/demo/commit/" + "f" * 40,
                    "commit": {
                        "message": "Merge pull request #7\n\n" + PUBLICATION_TITLE,
                        "author": {"date": "2026-09-13T12:00:00Z", "name": "GitHub"},
                    },
                },
                {
                    "sha": GENERATED_COMMIT_SHA,
                    "html_url": "https://github.com/alice/demo/commit/" + GENERATED_COMMIT_SHA,
                    "commit": {
                        "message": PUBLICATION_TITLE + "\n\nJanitor-Publication: digest",
                        "author": {"date": "2026-09-13T11:00:00Z", "name": "Janitor"},
                    },
                },
                *human,
            ]
            second = publish_repositories(["alice/demo"], self.state_dir)

        self.assertEqual(first[0]["status"], "unchanged")
        self.assertEqual(second[0]["status"], "unchanged_digest")
        self.assertEqual(extract.call_count, 1)

    def test_limit_must_be_between_one_and_twenty(self):
        for invalid in (0, 21):
            with self.subTest(limit=invalid), self.assertRaises(ValueError):
                publish_repositories([], self.state_dir, limit=invalid)


class DiscoverGitHubReposTests(unittest.TestCase):
    def test_deduplicates_ssh_https_aliases_and_ignores_other_hosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origins = [
                "git@github.com:Alice/Demo.git",
                "https://github.com/alice/demo.git",
                "ssh://git@github.com/Alice/Demo.git",
                "https://gitlab.com/alice/not-this.git",
            ]
            paths = []
            for index, origin in enumerate(origins):
                repo = root / str(index)
                repo.mkdir()
                subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
                subprocess.run(["git", "remote", "add", "origin", origin], cwd=repo, check=True)
                paths.append(repo)

            self.assertEqual(discover_github_repos(paths), ["Alice/Demo"])

    def test_rejects_origin_components_that_can_escape_api_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origins = [
                "https://github.com/alice/demo?state=all.git",
                "git@github.com:alice/demo%2Fpulls.git",
                "https://github.com/-alice/demo.git",
            ]
            paths = []
            for index, origin in enumerate(origins):
                repo = root / str(index)
                repo.mkdir()
                subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
                subprocess.run(["git", "remote", "add", "origin", origin], cwd=repo, check=True)
                paths.append(repo)
            self.assertEqual(discover_github_repos(paths), [])


class GitHubAdapterTests(unittest.TestCase):
    def test_api_uses_explicit_get_and_payload_via_stdin(self):
        get_result = subprocess.CompletedProcess([], 0, '{"login":"alice"}', "")
        post_result = subprocess.CompletedProcess([], 0, '{"sha":"abc"}', "")
        with patch("janitor.github.subprocess.run", side_effect=[get_result, post_result]) as run:
            client = GitHub()
            self.assertEqual(client.api("/user"), {"login": "alice"})
            self.assertEqual(client.api("/repos/a/b/git/refs", "POST", {"ref": "x"}), {"sha": "abc"})

        get_call, post_call = run.call_args_list
        self.assertIn("--hostname", get_call.args[0])
        self.assertIn("github.com", get_call.args[0])
        self.assertEqual(get_call.kwargs["input"], "")
        self.assertIn("GET", get_call.args[0])
        self.assertEqual(post_call.kwargs["input"], json.dumps({"ref": "x"}))
        self.assertIn("--input", post_call.args[0])
        for call in (get_call, post_call):
            self.assertEqual(call.kwargs["timeout"], 45)
            self.assertTrue(call.kwargs["capture_output"])
            self.assertTrue(call.kwargs["text"])

    def test_errors_are_sanitized_and_404_is_distinct(self):
        raw_secret = "token ghp_do_not_echo"
        failures = [
            subprocess.CompletedProcess([], 1, "", f"HTTP 404: {raw_secret}"),
            subprocess.CompletedProcess([], 1, "", f"network failed {raw_secret}"),
        ]
        with patch("janitor.github.subprocess.run", side_effect=failures):
            with self.assertRaises(GitHubError) as missing:
                GitHub().api("/missing")
            with self.assertRaises(GitHubError) as network:
                GitHub().api("/user")

        self.assertEqual(missing.exception.status, 404)
        self.assertIsNone(network.exception.status)
        self.assertNotIn(raw_secret, str(missing.exception))
        self.assertNotIn(raw_secret, str(network.exception))

    def test_pages_supports_key_and_fails_at_bound_instead_of_truncating(self):
        client = GitHub()
        hundred = [{"id": i} for i in range(100)]
        with patch.object(client, "api", side_effect=[{"check_runs": hundred}] * 50):
            with self.assertRaises(GitHubError) as ctx:
                client.pages("/repos/a/b/commits/x/check-runs", key="check_runs")
        self.assertIn("pagination", str(ctx.exception).lower())

    def test_adapter_deadline_prevents_an_api_call_after_total_bound(self):
        with patch("janitor.github.time.monotonic", side_effect=[10.0, 2710.0]), patch(
            "janitor.github.subprocess.run"
        ) as run:
            client = GitHub()
            with self.assertRaises(GitHubError) as ctx:
                client.api("/user")
        self.assertIn("deadline", str(ctx.exception).lower())
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
