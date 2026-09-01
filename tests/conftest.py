"""Shared fixtures for the CodeAtlas test-suite."""
from __future__ import annotations

from pathlib import Path

import pytest
from git import Actor, Repo

from backend.config import settings

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SAMPLE_REPO = FIXTURES_DIR / "sample_repo"

AUTHOR = Actor("CodeAtlas Tests", "tests@example.invalid")


@pytest.fixture
def temp_sessions(tmp_path, monkeypatch):
    """Point the session storage at a per-test temporary directory."""
    sessions_root = tmp_path / "temp"
    monkeypatch.setattr(settings, "temp_dir", sessions_root)
    return sessions_root


@pytest.fixture
def local_git_repo(tmp_path):
    """Create a small local git repository (clone source for tests, no network)."""
    src = tmp_path / "source_repo"
    src.mkdir()
    (src / "main.py").write_text('print("hello")\n', encoding="utf-8")
    (src / "util.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (src / "README.md").write_text("# Source repo\n", encoding="utf-8")
    repo = Repo.init(src)
    repo.index.add(["main.py", "util.py", "README.md"])
    repo.index.commit("initial commit", author=AUTHOR, committer=AUTHOR)
    repo.close()
    return src
