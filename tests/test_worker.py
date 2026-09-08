"""Tests for janitor.worker: backend selection, stdin streaming and JSON parsing.

No real network or gateway calls are made here:
- the gateway CLI and subprocess.run are mocked for the stdin-streaming path,
- urllib.request.urlopen is mocked for the openrouter/free HTTP fallback,
- the no-backend tests run with a PATH that excludes g2k-bg/g2k and with
  OPENROUTER_API_KEY unset, and assert the clean failure instead of a crash.

Run with: python3 -m unittest discover -s tests
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from janitor.worker import call_free, extract_structured
from janitor.docs import sweep_docs

GATEWAY = "/mock/bin/g2k-bg"


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


class GatewayStreamingTestCase(unittest.TestCase):
    """_call_gateway must stream the payload over stdin via [cli, "-p", "-"]."""

    def _completed(self, stdout: str = "", returncode: int = 0, stderr: str = ""):
        return subprocess.CompletedProcess(
            args=[GATEWAY, "-p", "-"],
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def test_call_free_streams_full_prompt_via_stdin(self):
        completed = self._completed(stdout='{"context_md": "ctx", "todo_md": "todo"}')
        with (
            patch("janitor.worker._check_rate_limit", return_value=True),
            patch("janitor.worker._log_usage") as log,
            patch("janitor.worker._gateway_cli", return_value=GATEWAY),
            patch("janitor.worker.subprocess.run", return_value=completed) as mock_run,
        ):
            resp = call_free("test prompt", system="test system")

        self.assertEqual(resp, '{"context_md": "ctx", "todo_md": "todo"}')
        args, kwargs = mock_run.call_args
        # Payload must go via stdin, never argv: ["<cli>", "-p", "-"]
        self.assertEqual(args[0], [GATEWAY, "-p", "-"])
        self.assertEqual(kwargs["input"], "test system\n\ntest prompt")
        self.assertIs(kwargs["capture_output"], True)
        self.assertEqual(kwargs["timeout"], 180)
        log.assert_called_once_with("g2k-bg")

    def test_call_free_without_system_streams_prompt_only(self):
        with (
            patch("janitor.worker._check_rate_limit", return_value=True),
            patch("janitor.worker._log_usage"),
            patch("janitor.worker._gateway_cli", return_value=GATEWAY),
            patch("janitor.worker.subprocess.run", return_value=self._completed(stdout="ok")) as mock_run,
        ):
            resp = call_free("bare prompt")

        args, kwargs = mock_run.call_args
        self.assertEqual(args[0], [GATEWAY, "-p", "-"])
        self.assertEqual(kwargs["input"], "bare prompt")
        self.assertEqual(resp, "ok")

    def test_gateway_timeout_raises_runtime_error(self):
        timeout = subprocess.TimeoutExpired(cmd=[GATEWAY, "-p", "-"], timeout=180)
        with (
            patch("janitor.worker._check_rate_limit", return_value=True),
            patch("janitor.worker._gateway_cli", return_value=GATEWAY),
            patch("janitor.worker.subprocess.run", side_effect=timeout),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                call_free("prompt")
        self.assertIn("timed out", str(ctx.exception))

    def test_gateway_nonzero_exit_raises_runtime_error(self):
        completed = self._completed(returncode=1, stderr="model exploded")
        with (
            patch("janitor.worker._check_rate_limit", return_value=True),
            patch("janitor.worker._gateway_cli", return_value=GATEWAY),
            patch("janitor.worker.subprocess.run", return_value=completed),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                call_free("prompt")
        self.assertIn("model exploded", str(ctx.exception))

    def test_gateway_markdown_fences_stripped_from_response(self):
        completed = self._completed(stdout="```json\n{\"context_md\": \"ctx\"}\n```\n")
        with (
            patch("janitor.worker._check_rate_limit", return_value=True),
            patch("janitor.worker._log_usage"),
            patch("janitor.worker._gateway_cli", return_value=GATEWAY),
            patch("janitor.worker.subprocess.run", return_value=completed),
        ):
            resp = call_free("prompt")
        self.assertEqual(resp, '{"context_md": "ctx"}')


class _FakeHTTPResponse:
    """Minimal context-manager stand-in for urllib's response object."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@contextmanager
def _mock_http_fallback(content: str):
    """Patch call_free's openrouter/free HTTP deps; yield the urlopen mock."""
    body = json.dumps({
        "choices": [{"message": {"content": content}}],
        "model": "openrouter/free",
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }).encode()
    with (
        patch("janitor.worker._check_rate_limit", return_value=True),
        patch("janitor.worker._log_usage"),
        patch("janitor.worker._gateway_cli", return_value=None),
        patch("janitor.worker._get_api_key", return_value="sk-test-key"),
        patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(body)) as urlopen,
    ):
        yield urlopen


class OpenRouterFallbackTestCase(unittest.TestCase):
    """No gateway CLI on PATH: call_free falls back to openrouter/free HTTP."""

    def test_http_fallback_when_no_gateway_cli(self):
        content = '{"context_md": "ctx", "todo_md": "todo"}'
        with _mock_http_fallback(content) as urlopen:
            resp = call_free("hello", system="sys")
        self.assertEqual(resp, content)
        urlopen.assert_called_once()
        # Request targets the openrouter/free chat endpoint with both roles.
        req = urlopen.call_args[0][0]
        self.assertIn("openrouter.ai/api/v1/chat/completions", req.full_url)
        payload = json.loads(req.data)
        self.assertEqual(payload["model"], "openrouter/free")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1], {"role": "user", "content": "hello"})

    def test_http_fallback_returns_raw_content_string(self):
        # The HTTP path returns the raw content string, same as the gateway path.
        with _mock_http_fallback('{"ok": true}'):
            resp = call_free("hello")
        self.assertEqual(resp, '{"ok": true}')


class ExtractStructuredTestCase(unittest.TestCase):
    """extract_structured parses JSON objects, incl. markdown-fenced ones."""

    def test_parses_plain_json_object(self):
        with patch("janitor.worker.call_free", return_value='{"context_md": "a", "todo_md": "b"}'):
            res = extract_structured("prompt")
        self.assertEqual(res, {"context_md": "a", "todo_md": "b"})

    def test_parses_json_in_json_code_fence(self):
        raw = "```json\n{\"context_md\": \"test\", \"todo_md\": \"todo\"}\n```"
        with patch("janitor.worker.call_free", return_value=raw) as mock_call:
            res = extract_structured("prompt")
        self.assertEqual(res, {"context_md": "test", "todo_md": "todo"})
        self.assertEqual(mock_call.call_args.kwargs["timeout"], 180)

    def test_parses_json_in_markdown_code_fence(self):
        raw = "```markdown\n{\"context_md\": \"test\", \"todo_md\": \"todo\"}\n```"
        with patch("janitor.worker.call_free", return_value=raw):
            res = extract_structured("prompt")
        self.assertEqual(res, {"context_md": "test", "todo_md": "todo"})

    def test_propagates_timeout_to_call_free(self):
        with patch("janitor.worker.call_free", return_value="{}") as mock_call:
            extract_structured("prompt", timeout=42)
        self.assertEqual(mock_call.call_args.kwargs["timeout"], 42)

    def test_non_dict_json_falls_back_to_unstructured(self):
        with patch("janitor.worker.call_free", return_value='["not", "a", "dict"]'):
            res = extract_structured("prompt")
        self.assertEqual(res["status"], "unstructured")
        self.assertIn("raw", res)

    def test_unparsable_text_falls_back_to_unstructured(self):
        with patch("janitor.worker.call_free", return_value="no json here"):
            res = extract_structured("prompt")
        self.assertEqual(res, {"raw": "no json here", "status": "unstructured"})

    def test_empty_response_reports_empty_status(self):
        with patch("janitor.worker.call_free", return_value=""):
            res = extract_structured("prompt")
        self.assertEqual(res, {"raw": "", "status": "empty_response"})

    def test_call_failure_reports_failed_status(self):
        with patch("janitor.worker.call_free", side_effect=RuntimeError("boom")):
            res = extract_structured("prompt")
        self.assertEqual(res["status"], "failed")
        self.assertIn("boom", res["raw"])


if __name__ == "__main__":
    unittest.main()
