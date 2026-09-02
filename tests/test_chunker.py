"""Tests for the semantic chunker."""
from __future__ import annotations

import pytest

from backend.config import settings
from backend.knowledge.builder import build_knowledge_base
from backend.rag.chunker import build_chunks
from backend.repository.scanner import scan_repository
from tests.conftest import SAMPLE_REPO


def chunk_repo(repo_path):
    scan = scan_repository(repo_path)
    kb = build_knowledge_base(repo_path, scan)
    return build_chunks(repo_path, scan, kb.entities)


@pytest.fixture(scope="module")
def sample_chunks():
    chunks, summary = chunk_repo(SAMPLE_REPO)
    return chunks, summary


class TestPythonChunking:
    def test_method_chunks_carry_entity_metadata(self, sample_chunks):
        chunks, _ = sample_chunks
        login = next(c for c in chunks if c.entity_id == "app/auth.py::AuthService.login")
        assert login.type == "method"
        assert login.symbol == "AuthService.login"
        assert login.text.lstrip().startswith("def login")
        assert "find_user(username)" in login.text

    def test_class_chunk_excludes_method_bodies(self, sample_chunks):
        chunks, _ = sample_chunks
        class_chunks = [c for c in chunks if c.entity_id == "app/auth.py::AuthService" and c.type == "class"]
        assert class_chunks, "the class header/docstring must have its own chunk"
        combined = "\n".join(c.text for c in class_chunks)
        assert "class AuthService(BaseService):" in combined
        assert "Handles user authentication." in combined
        assert "def login" not in combined  # methods live in their own chunks

    def test_module_chunk_carries_top_level_code(self, sample_chunks):
        chunks, _ = sample_chunks
        module_chunks = [c for c in chunks if c.file == "app/database.py" and c.type == "module"]
        combined = "\n".join(c.text for c in module_chunks)
        assert "AWS_ACCESS_KEY_ID" in combined  # module-level constants are module chunks
        assert "import sqlite3" in combined
        assert "def find_user" not in combined  # the function has its own chunk

    def test_every_chunk_matches_its_line_range(self, sample_chunks):
        """The citation invariant: chunk text is exactly its file lines."""
        chunks, _ = sample_chunks
        for chunk in chunks:
            file_lines = (SAMPLE_REPO / chunk.file).read_text(encoding="utf-8").split("\n")
            assert chunk.text == "\n".join(file_lines[chunk.start_line - 1 : chunk.end_line]), chunk.chunk_id

    def test_chunk_ids_are_sequential_and_unique(self, sample_chunks):
        chunks, summary = sample_chunks
        ids = [c.chunk_id for c in chunks]
        assert len(set(ids)) == len(ids)
        assert ids[0] == "chunk_00001"
        assert summary.total == len(chunks)
        assert sum(summary.by_type.values()) == summary.total

    def test_nested_functions_are_not_chunked_separately(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "outer.py").write_text(
            "def outer():\n    def inner():\n        pass\n    return inner\n", encoding="utf-8"
        )
        chunks, _ = chunk_repo(repo)
        assert [c.symbol for c in chunks if c.type == "function"] == ["outer"]
        outer = next(c for c in chunks if c.symbol == "outer")
        assert "def inner" in outer.text  # nested text lives inside the parent chunk

    def test_blank_files_produce_no_chunks(self, sample_chunks):
        chunks, _ = sample_chunks
        assert not any(c.file == "app/__init__.py" for c in chunks)  # empty file


class TestOtherFileTypes:
    @pytest.fixture
    def mixed_repo(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "README.md").write_text(
            "Intro before any heading.\n\n# Setup\npip install x\n\n## Usage\nrun it\n",
            encoding="utf-8",
        )
        (repo / "Dockerfile").write_text("FROM python:3.11\n", encoding="utf-8")
        (repo / "package-lock.json").write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
        (repo / "component.ts").write_text("export const x = 1;\n", encoding="utf-8")
        return repo

    def test_markdown_splits_by_heading(self, mixed_repo):
        chunks, _ = chunk_repo(mixed_repo)
        docs = [c for c in chunks if c.file == "README.md"]
        assert [d.symbol for d in docs] == [None, "Setup", "Usage"]  # preamble + sections
        assert all(d.type == "documentation" for d in docs)
        assert "pip install x" in next(d.text for d in docs if d.symbol == "Setup")

    def test_config_documentation_and_code_fallbacks(self, mixed_repo):
        chunks, _ = chunk_repo(mixed_repo)
        by_file = {c.file: c for c in chunks if c.file != "README.md"}
        assert by_file["Dockerfile"].type == "config"
        assert by_file["component.ts"].type == "module"  # unparsed code: whole file
        assert "package-lock.json" not in by_file  # lockfiles are retrieval noise


class TestOversizedSplitting:
    def test_split_parts_overlap_and_keep_true_line_ranges(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "chunk_max_chars", 200)
        monkeypatch.setattr(settings, "chunk_overlap_lines", 2)
        repo = tmp_path / "repo"
        repo.mkdir()
        body = "\n".join(f"    x{n} = {n}  # padding line" for n in range(30))
        (repo / "big.py").write_text(f"def big():\n{body}\n", encoding="utf-8")
        chunks, summary = chunk_repo(repo)
        parts = [c for c in chunks if c.symbol == "big"]

        assert len(parts) > 1
        assert [p.part for p in parts] == list(range(1, len(parts) + 1))
        assert summary.oversized_split == len(parts)
        file_lines = (repo / "big.py").read_text(encoding="utf-8").split("\n")
        for part in parts:
            assert part.text == "\n".join(file_lines[part.start_line - 1 : part.end_line])
        # Consecutive parts share overlapping lines for context at the seam:
        assert parts[1].start_line <= parts[0].end_line

    def test_single_line_over_the_limit_does_not_loop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "chunk_max_chars", 50)
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "minified.js").write_text("var a=" + "1+" * 200 + "1;\n", encoding="utf-8")
        chunks, _ = chunk_repo(repo)
        assert len(chunks) >= 1  # completed without hanging; the long line stays whole
        assert chunks[0].start_line == 1