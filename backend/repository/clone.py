"""Repository acquisition: GitHub URL validation, per-session directories, GitPython cloning."""
from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from git import GitCommandError, Repo

from backend.config import settings

logger = logging.getLogger(__name__)

SESSION_ID_RE = re.compile(r"^[a-f0-9]{12}$")

_GITHUB_HOSTS = {"github.com", "www.github.com"}
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Environment for git clone: never prompt for credentials (private repos fail fast
# instead of hanging), disable any configured credential helper (e.g. Windows
# Credential Manager popups), enable long paths (Windows checkout of deep trees
# fails without core.longpaths), and abort stalled transfers.
_CLONE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "credential.helper",
    "GIT_CONFIG_VALUE_0": "",
    "GIT_CONFIG_KEY_1": "core.longpaths",
    "GIT_CONFIG_VALUE_1": "true",
    "GIT_HTTP_LOW_SPEED_LIMIT": str(settings.clone_low_speed_limit_bytes),
    "GIT_HTTP_LOW_SPEED_TIME": str(settings.clone_low_speed_time_seconds),
}


class CloneError(Exception):
    """Base error for repository acquisition failures. Messages are user-facing."""


class InvalidRepoURLError(CloneError):
    """The provided URL is not a valid GitHub HTTPS repository URL."""


class GitCloneError(CloneError):
    """git clone failed (repo missing, private, network problems, ...)."""


class RepoTooLargeError(CloneError):
    """The repository exceeds the configured size limit."""


def validate_github_url(url: str) -> str:
    """Validate a GitHub repository URL and return its normalized HTTPS form.

    Accepts https://github.com/owner/repo with optional .git suffix and trailing
    slash. Rejects everything else (SSH URLs, other hosts, extra path segments).
    """
    url = (url or "").strip()
    if not url:
        raise InvalidRepoURLError("Repository URL is required.")
    if url.startswith("git@") or url.startswith("ssh://"):
        raise InvalidRepoURLError(
            "SSH URLs are not supported. Use an HTTPS URL like https://github.com/owner/repo."
        )

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise InvalidRepoURLError("Only HTTPS GitHub URLs are supported (https://github.com/owner/repo).")
    try:
        port = parsed.port
    except ValueError as exc:
        raise InvalidRepoURLError("Invalid port in repository URL.") from exc
    hostname = (parsed.hostname or "").lower()
    if hostname not in _GITHUB_HOSTS:
        raise InvalidRepoURLError("Only github.com repositories are supported.")
    if parsed.username or parsed.password or port not in (None, 80, 443) or parsed.query or parsed.fragment:
        raise InvalidRepoURLError("Unexpected characters in repository URL.")

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) != 2:
        raise InvalidRepoURLError("URL must look like https://github.com/owner/repo.")
    owner, repo = parts
    if repo.endswith(".git"):
        repo = repo[:-4]
    if (
        not _NAME_RE.match(owner)
        or not _NAME_RE.match(repo)
        or owner in {".", ".."}
        or repo in {".", ".."}
    ):
        raise InvalidRepoURLError("Invalid owner or repository name in URL.")
    return f"https://github.com/{owner}/{repo}.git"


def repo_name_from_url(url: str) -> str:
    """Extract the repository name from a (normalized) GitHub URL."""
    name = urlparse(url).path.rstrip("/").split("/")[-1]
    return name[:-4] if name.endswith(".git") else name


def create_session() -> tuple[str, Path]:
    """Create a unique session directory tree and return (session_id, repository_dir)."""
    session_id = uuid.uuid4().hex[:12]
    session_dir = settings.session_dir(session_id)
    repo_dir = session_dir / "repository"
    repo_dir.mkdir(parents=True, exist_ok=False)
    (session_dir / "analysis").mkdir(exist_ok=True)
    return session_id, repo_dir


def get_session_dir(session_id: str) -> Path | None:
    """Return the session directory if the id is well-formed and the session exists."""
    if not SESSION_ID_RE.match(session_id or ""):
        return None
    session_dir = settings.session_dir(session_id)
    return session_dir if session_dir.is_dir() else None


def _github_repo_size_kb(owner: str, repo: str) -> int | None:
    """Best-effort repository size from the GitHub metadata API.

    Sends only the owner/repo name to GitHub (which the clone contacts anyway),
    never any content. Returns None when the API is unreachable, rate-limited,
    or the response is unexpected - callers must treat None as "unknown".
    """
    try:
        response = httpx.get(
            f"https://api.github.com/repos/{owner}/{repo}",
            timeout=5.0,
            headers={"Accept": "application/vnd.github+json"},
            follow_redirects=True,
        )
        if response.status_code == 200:
            size = response.json().get("size")
            return size if isinstance(size, int) else None
    except Exception:  # network down, DNS failure, JSON garbage - all non-fatal
        logger.info("GitHub size pre-check unavailable for %s/%s", owner, repo)
    return None


def _precheck_repo_size(url: str) -> None:
    """Reject obviously oversized repos BEFORE downloading anything.

    The API size field covers full history while our clone is shallow, so only
    sizes far beyond the limit (2x) are rejected here; the post-clone check
    enforces the exact limit. Skipped silently when the API is unavailable.
    """
    parts = urlparse(url).path.strip("/").removesuffix(".git").split("/")
    if len(parts) != 2:
        return
    size_kb = _github_repo_size_kb(parts[0], parts[1])
    if size_kb is not None and size_kb > settings.max_repo_size_mb * 1024 * 2:
        raise RepoTooLargeError(
            f"Repository is about {size_kb // 1024} MB on GitHub, far beyond the "
            f"{settings.max_repo_size_mb} MB limit. Set CODEATLAS_MAX_REPO_SIZE_MB to override."
        )


def clone_repository(url: str, dest: Path, *, depth: int | None = None) -> Path:
    """Clone url into dest and return dest.

    url is expected to be a normalized GitHub HTTPS URL (tests may pass a local
    path). Raises GitCloneError / RepoTooLargeError with user-facing messages.
    """
    depth = depth or settings.clone_depth
    if url.startswith("https://github.com/"):
        _precheck_repo_size(url)
    try:
        repo = Repo.clone_from(url, dest, depth=depth, single_branch=True, env=_CLONE_ENV)
        repo.close()
    except GitCommandError as exc:
        raise GitCloneError(_friendly_git_error(exc)) from exc
    except CloneError:
        raise
    except Exception as exc:  # git executable missing, OS-level failures, ...
        raise GitCloneError(f"Could not clone repository: {exc.__class__.__name__}") from exc

    size_bytes = directory_size_bytes(dest)
    if size_bytes > settings.max_repo_size_mb * 1024 * 1024:
        raise RepoTooLargeError(
            f"Repository is larger than the {settings.max_repo_size_mb} MB limit "
            f"({size_bytes // (1024 * 1024)} MB). Set CODEATLAS_MAX_REPO_SIZE_MB to override."
        )
    return dest


def directory_size_bytes(path: Path) -> int:
    """Total size of all regular files under path (symlinks not followed)."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
        for filename in filenames:
            full = os.path.join(dirpath, filename)
            try:
                if not os.path.islink(full):
                    total += os.stat(full).st_size
            except OSError:
                continue
    return total


def _friendly_git_error(exc: GitCommandError) -> str:
    """Map raw git stderr to a concise, user-facing message."""
    stderr = str(exc.stderr or "").lower()
    if "not found" in stderr or "could not read username" in stderr or "authentication failed" in stderr:
        return (
            "Repository not found. It may not exist or may be private "
            "(only public repositories are supported)."
        )
    if "could not resolve host" in stderr or "unable to access" in stderr:
        return "Could not reach GitHub. Check your network connection and try again."
    if "filename too long" in stderr:
        return (
            "The repository contains paths too long for this Windows setup. "
            "Enable Windows long-path support and try again."
        )
    if "transfer closed" in stderr or "rpc failed" in stderr or "early eof" in stderr:
        return "The clone was interrupted (slow or unstable connection). Try again."
    return "git clone failed. The repository may be unavailable."
