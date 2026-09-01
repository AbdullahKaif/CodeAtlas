"""Tests for URL validation, session creation and repository cloning."""
from __future__ import annotations

import pytest

from backend.repository.clone import (
    GitCloneError,
    InvalidRepoURLError,
    RepoTooLargeError,
    clone_repository,
    create_session,
    repo_name_from_url,
    validate_github_url,
)
from backend.config import settings


class TestValidateGithubUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/pallets/flask",
            "https://github.com/pallets/flask.git",
            "https://github.com/pallets/flask/",
            "http://github.com/pallets/flask",
            "https://www.github.com/pallets/flask",
            "  https://github.com/pallets/flask  ",
        ],
    )
    def test_valid_urls_normalize(self, url):
        assert validate_github_url(url) == "https://github.com/pallets/flask.git"

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "not a url",
            "https://gitlab.com/owner/repo",
            "git@github.com:owner/repo.git",
            "ssh://git@github.com/owner/repo",
            "ftp://github.com/owner/repo",
            "https://github.com/owner",
            "https://github.com/owner/repo/tree/main",
            "https://github.com/../../etc",
            "https://evil.com/github.com/repo",
            "https://github.com@evil.com/owner/repo",
            "https://user:pass@github.com/owner/repo",
            "https://github.com:9999/owner/repo",
            "https://github.com/owner/repo?ref=main",
            "https://github.com/owner/repo#readme",
        ],
    )
    def test_invalid_urls_rejected(self, url):
        with pytest.raises(InvalidRepoURLError):
            validate_github_url(url)

    def test_repo_name_from_url(self):
        assert repo_name_from_url("https://github.com/pallets/flask.git") == "flask"


class TestSessions:
    def test_create_session_makes_isolated_dirs(self, temp_sessions):
        session_id, repo_dir = create_session()
        assert repo_dir.is_dir()
        assert repo_dir.name == "repository"
        assert repo_dir.parent == temp_sessions / f"session_{session_id}"
        assert (repo_dir.parent / "analysis").is_dir()

    def test_sessions_are_unique(self, temp_sessions):
        first, _ = create_session()
        second, _ = create_session()
        assert first != second


class TestCloneRepository:
    def test_clone_local_repo(self, temp_sessions, local_git_repo):
        _, repo_dir = create_session()
        result = clone_repository(str(local_git_repo), repo_dir)
        assert result == repo_dir
        assert (repo_dir / "main.py").is_file()
        assert (repo_dir / ".git").exists()

    def test_clone_missing_repo_raises(self, temp_sessions, tmp_path):
        _, repo_dir = create_session()
        with pytest.raises(GitCloneError):
            clone_repository(str(tmp_path / "does_not_exist"), repo_dir)

    def test_clone_rejects_oversized_repo_when_limit_set(self, temp_sessions, local_git_repo, monkeypatch):
        import backend.repository.clone as clone_module

        monkeypatch.setattr(settings, "max_repo_size_mb", 1)
        monkeypatch.setattr(clone_module, "directory_size_bytes", lambda p: 10 * 1024 * 1024)
        _, repo_dir = create_session()
        with pytest.raises(RepoTooLargeError):
            clone_repository(str(local_git_repo), repo_dir)

    def test_no_limit_by_default(self, temp_sessions, local_git_repo, monkeypatch):
        """max_repo_size_mb=0 (the default) disables both size checks entirely."""
        import backend.repository.clone as clone_module

        monkeypatch.setattr(settings, "max_repo_size_mb", 0)
        monkeypatch.setattr(clone_module, "directory_size_bytes", lambda p: 10**15)
        monkeypatch.setattr(clone_module, "_github_repo_size_kb", lambda o, r: 10**12)
        _, repo_dir = create_session()
        clone_repository(str(local_git_repo), repo_dir)
        assert (repo_dir / "main.py").is_file()


class TestSizePrecheck:
    def test_precheck_rejects_huge_repo_before_download(self, temp_sessions, monkeypatch):
        import backend.repository.clone as clone_module

        monkeypatch.setattr(settings, "max_repo_size_mb", 500)
        monkeypatch.setattr(clone_module, "_github_repo_size_kb", lambda o, r: 10**9)
        _, repo_dir = create_session()
        with pytest.raises(RepoTooLargeError):
            clone_repository("https://github.com/big/monorepo.git", repo_dir)
        assert not any(repo_dir.iterdir())  # nothing was downloaded

    def test_precheck_unavailable_proceeds_with_clone(self, temp_sessions, local_git_repo, monkeypatch):
        """API failure (None) must not block cloning; local paths skip the precheck."""
        import backend.repository.clone as clone_module

        monkeypatch.setattr(clone_module, "_github_repo_size_kb", lambda o, r: None)
        _, repo_dir = create_session()
        clone_repository(str(local_git_repo), repo_dir)
        assert (repo_dir / "main.py").is_file()
