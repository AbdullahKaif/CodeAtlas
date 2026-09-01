"""Tests for the repository scanner."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import settings
from backend.repository.scanner import scan_repository
from tests.conftest import SAMPLE_REPO


@pytest.fixture
def scan_tree(tmp_path) -> Path:
    """A synthetic repository exercising every scanner rule."""
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "app" / "auth.py").write_text("class AuthService:\n    pass\n", encoding="utf-8")
    (repo / "main.py").write_text('print("hi")', encoding="utf-8")
    (repo / "README.md").write_text("# Readme\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("flask\n", encoding="utf-8")
    (repo / "Dockerfile").write_text("FROM python:3.11\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_auth.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    # Should all be ignored:
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "node_modules" / "pkg" / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")
    (repo / "env_dir").mkdir()
    (repo / "env_dir" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (repo / "env_dir" / "junk.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    (repo / "blob.xyz").write_bytes(b"\x00\x01\x02\x03")
    (repo / "notes.xyz").write_text("just text\n", encoding="utf-8")
    (repo / "debug.log").write_text("noise\n", encoding="utf-8")
    return repo


class TestScanRepository:
    def test_missing_path_raises(self, tmp_path):
        with pytest.raises(NotADirectoryError):
            scan_repository(tmp_path / "nope")

    def test_ignores_and_metadata(self, scan_tree):
        scan = scan_repository(scan_tree)
        paths = {f.path for f in scan.files}

        assert "main.py" in paths
        assert "app/auth.py" in paths
        assert "notes.xyz" in paths  # unknown extension but text
        # Ignored directories and files never appear:
        assert not any(p.startswith((".git/", "node_modules/", "env_dir/")) for p in paths)
        assert "logo.png" not in paths
        assert "blob.xyz" not in paths
        assert "debug.log" not in paths

        assert scan.languages["python"] == 4
        assert "main.py" in scan.entry_points
        assert {"README.md", "requirements.txt", "Dockerfile"} <= set(scan.project_files)
        assert scan.summary.files_skipped_binary >= 2
        assert scan.summary.dirs_skipped >= 3

    def test_file_details(self, scan_tree):
        scan = scan_repository(scan_tree)
        by_path = {f.path: f for f in scan.files}

        auth = by_path["app/auth.py"]
        assert auth.language == "python"
        assert auth.line_count == 2
        assert auth.extension == ".py"
        assert not auth.is_test_file

        test_file = by_path["tests/test_auth.py"]
        assert test_file.is_test_file

        docker = by_path["Dockerfile"]
        assert docker.language == "docker"
        assert docker.is_project_file

    def test_large_files_kept_without_content_read(self, scan_tree, monkeypatch):
        monkeypatch.setattr(settings, "max_file_size_bytes", 5)
        scan = scan_repository(scan_tree)
        by_path = {f.path: f for f in scan.files}
        assert by_path["app/auth.py"].line_count is None
        assert scan.summary.files_skipped_large > 0

    def test_max_files_cap(self, scan_tree, monkeypatch):
        monkeypatch.setattr(settings, "max_files", 3)
        scan = scan_repository(scan_tree)
        assert scan.summary.files_included == 3
        assert scan.summary.truncated is True

    def test_scans_fixture_sample_repo(self):
        scan = scan_repository(SAMPLE_REPO)
        paths = {f.path for f in scan.files}
        assert "app/auth.py" in paths
        assert "app/database.py" in paths
        assert scan.languages["python"] >= 5
        assert "app/main.py" in scan.entry_points
        assert any(f.is_test_file for f in scan.files)


class TestReviewRegressions:
    """Regressions for findings from the Phase 1 adversarial review."""

    def test_large_unknown_extension_binary_is_skipped(self, tmp_path, monkeypatch):
        """Files over the size cap must still go through the binary sniff."""
        monkeypatch.setattr(settings, "max_file_size_bytes", 10)
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "model.weights").write_bytes(b"\x00" * 100)  # "large" binary
        (repo / "big_text.custom").write_text("x" * 100, encoding="utf-8")  # large but text
        scan = scan_repository(repo)
        paths = {f.path for f in scan.files}
        assert "model.weights" not in paths
        assert "big_text.custom" in paths
        assert scan.summary.files_skipped_binary == 1

    def test_utf16_source_file_is_text_with_line_count(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "config.py").write_text("a = 1\nb = 2\n", encoding="utf-16")
        scan = scan_repository(repo)
        by_path = {f.path: f for f in scan.files}
        assert "config.py" in by_path
        assert by_path["config.py"].line_count == 2
        assert scan.summary.files_skipped_binary == 0

    def test_counters_reconcile(self, scan_tree):
        s = scan_repository(scan_tree).summary
        assert s.total_files_seen == s.files_included + s.files_skipped_binary + s.files_skipped_other

    def test_exact_max_files_is_not_truncated(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        for i in range(3):
            (repo / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")
        monkeypatch.setattr(settings, "max_files", 3)
        scan = scan_repository(repo)
        assert scan.summary.files_included == 3
        assert scan.summary.truncated is False

    def test_seen_bound_stops_walk(self, tmp_path, monkeypatch):
        """A repo full of skippable files must not be walked forever."""
        repo = tmp_path / "repo"
        repo.mkdir()
        for i in range(30):
            (repo / f"junk{i}.log").write_text("x", encoding="utf-8")
        (repo / "real.py").write_text("x = 1\n", encoding="utf-8")
        monkeypatch.setattr(settings, "max_files", 5)  # max_seen = 15
        scan = scan_repository(repo)
        assert scan.summary.truncated is True
        assert scan.summary.total_files_seen <= 15
