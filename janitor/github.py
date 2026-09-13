"""Small, fail-closed GitHub adapter used by Janitor publication jobs."""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class GitHubError(RuntimeError):
    """A sanitized GitHub failure with an optional HTTP status."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class GitHub:
    """Invoke the authenticated ``gh api`` transport with finite bounds."""

    def __init__(self) -> None:
        self._deadline = time.monotonic() + 2700

    def api(
        self, path: str, method: str = "GET", payload: dict | None = None
    ) -> object:
        method = method.upper()
        command = [
            "gh",
            "api",
            "--hostname",
            "github.com",
            path,
            "--method",
            method,
        ]
        serialized = ""
        if payload is not None:
            serialized = json.dumps(payload)
            command.extend(["--input", "-"])
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise GitHubError("GitHub API total deadline exceeded")
        try:
            completed = subprocess.run(
                command,
                input=serialized,
                timeout=min(45, remaining),
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitHubError("GitHub API request timed out") from exc
        except OSError as exc:
            raise GitHubError("GitHub API transport unavailable") from exc

        if completed.returncode != 0:
            match = re.search(r"\bHTTP\s+(\d{3})\b", completed.stderr, re.IGNORECASE)
            status = int(match.group(1)) if match else None
            if status == 404:
                message = "GitHub resource not found (HTTP 404)"
            elif status is not None:
                message = f"GitHub API request failed (HTTP {status})"
            else:
                message = "GitHub API authentication or network failure"
            raise GitHubError(message, status=status)

        if not completed.stdout.strip():
            return {}
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise GitHubError("GitHub API returned invalid JSON") from exc

    def pages(self, path: str, key: str | None = None) -> list:
        """Read a list endpoint at 100 items/page, failing at the 50-page bound."""
        parts = urlsplit(path)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["per_page"] = "100"
        combined: list = []
        for page in range(1, 51):
            query["page"] = str(page)
            page_path = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
            response = self.api(page_path, method="GET")
            if key is None:
                batch = response
            elif isinstance(response, dict):
                batch = response.get(key)
            else:
                batch = None
            if not isinstance(batch, list):
                raise GitHubError("GitHub pagination response had an invalid shape")
            combined.extend(batch)
            if len(batch) < 100:
                return combined
        raise GitHubError("GitHub pagination limit reached before completion")


def _github_origin_identity(origin: str) -> str | None:
    """Return ``owner/repo`` for an exact github.com origin."""
    origin = origin.strip()
    if not origin:
        return None

    scp = re.fullmatch(r"[^/@:]+@([^:]+):([^?#]+)", origin)
    if scp:
        host = scp.group(1).lower()
        path = scp.group(2)
    else:
        parsed = urlsplit(origin)
        if parsed.query or parsed.fragment:
            return None
        host = (parsed.hostname or "").lower()
        path = parsed.path
    if host != "github.com":
        return None

    pieces = [piece for piece in path.strip("/").split("/") if piece]
    if len(pieces) != 2:
        return None
    owner, repo = pieces
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    valid_owner = re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", owner
    )
    valid_repo = re.fullmatch(r"[A-Za-z0-9._-]{1,100}", repo)
    if not valid_owner or not valid_repo or repo in {".", ".."}:
        return None
    return f"{owner}/{repo}"


def discover_github_repos(paths: list[Path]) -> list[str]:
    """Read origin URLs and return case-insensitively deduplicated GitHub repos."""
    found: list[str] = []
    seen: set[str] = set()
    for path in paths:
        try:
            completed = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                cwd=path,
                timeout=10,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode != 0:
            continue
        identity = _github_origin_identity(completed.stdout)
        if identity is None or identity.casefold() in seen:
            continue
        seen.add(identity.casefold())
        found.append(identity)
    return found
