"""Tests for knowledge base construction and serialization (fixture repo end-to-end)."""
from __future__ import annotations

import json

import pytest

from backend.config import settings
from backend.knowledge.builder import build_knowledge_base
from backend.knowledge.serializer import load_entities, load_relationships, write_knowledge_base
from backend.repository.scanner import scan_repository
from tests.conftest import SAMPLE_REPO


@pytest.fixture(scope="module")
def sample_kb():
    scan = scan_repository(SAMPLE_REPO)
    return build_knowledge_base(SAMPLE_REPO, scan)


class TestBuildKnowledgeBase:
    def test_entities_from_fixture_repo(self, sample_kb):
        by_id = {e.id: e for e in sample_kb.entities}

        auth_file = by_id["app/auth.py"]
        assert auth_file.type == "file"
        assert auth_file.docstring == "Authentication logic for the sample application."

        service = by_id["app/auth.py::AuthService"]
        assert service.type == "class"
        assert service.parent_classes == ["BaseService"]

        login = by_id["app/auth.py::AuthService.login"]
        assert login.type == "method"
        assert login.parameters == ["self", "username", "password"]
        assert login.docstring == "Validate credentials against the user store."

        # Non-Python source files still get file entities (for later chunking):
        assert "README.md" not in by_id or by_id["README.md"].type == "file"

    def test_import_edges_from_fixture_repo(self, sample_kb):
        imports = {(r.source, r.target) for r in sample_kb.relationships if r.relation == "imports"}
        assert ("app/auth.py", "app/base.py") in imports
        assert ("app/auth.py", "app/database.py") in imports
        assert ("app/main.py", "app/auth.py") in imports
        assert ("tests/test_auth.py", "app/auth.py") in imports
        # sqlite3 is external - no edge may exist for it:
        assert not any(t.startswith("sqlite3") for _, t in imports)

    def test_contains_edges_from_fixture_repo(self, sample_kb):
        contains = {(r.source, r.target) for r in sample_kb.relationships if r.relation == "contains"}
        assert ("app/auth.py", "app/auth.py::AuthService") in contains
        assert ("app/auth.py::AuthService", "app/auth.py::AuthService.login") in contains

    def test_inherits_edges_from_fixture_repo(self, sample_kb):
        inherits = {(r.source, r.target) for r in sample_kb.relationships if r.relation == "inherits"}
        # AuthService(BaseService) crosses files via `from app.base import BaseService`:
        assert ("app/auth.py::AuthService", "app/base.py::BaseService") in inherits

    def test_call_edges_from_fixture_repo(self, sample_kb):
        calls = {(r.source, r.target) for r in sample_kb.relationships if r.relation == "calls"}
        # login() calls the imported find_user():
        assert ("app/auth.py::AuthService.login", "app/database.py::find_user") in calls
        # run() instantiates AuthService (a call edge to the class):
        assert ("app/main.py::run", "app/auth.py::AuthService") in calls
        assert ("tests/test_auth.py::test_login_unknown_user_fails", "app/auth.py::AuthService") in calls
        # sqlite3.connect etc. are external - never edges:
        assert not any("sqlite3" in t for _, t in calls)

    def test_self_calls_and_local_calls(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "svc.py").write_text(
            "class Service:\n"
            "    def go(self):\n"
            "        self.stop()\n"
            "    def stop(self):\n"
            "        pass\n"
            "\n"
            "def helper():\n"
            "    return Service()\n"
            "\n"
            "def recurse(n):\n"
            "    return recurse(n - 1)\n",
            encoding="utf-8",
        )
        kb = build_knowledge_base(repo, scan_repository(repo))
        calls = {(r.source, r.target) for r in kb.relationships if r.relation == "calls"}
        assert ("svc.py::Service.go", "svc.py::Service.stop") in calls
        assert ("svc.py::helper", "svc.py::Service") in calls
        # Recursion is a real edge, deliberately kept:
        assert ("svc.py::recurse", "svc.py::recurse") in calls

    def test_generic_bases_resolve_and_external_bases_do_not(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "models.py").write_text(
            "from enum import Enum\n"
            "\n"
            "class Base:\n"
            "    pass\n"
            "\n"
            "class Typed(Base[int]):\n"
            "    pass\n"
            "\n"
            "class Color(Enum):\n"
            "    RED = 1\n",
            encoding="utf-8",
        )
        kb = build_knowledge_base(repo, scan_repository(repo))
        inherits = {(r.source, r.target) for r in kb.relationships if r.relation == "inherits"}
        assert ("models.py::Typed", "models.py::Base") in inherits  # Base[int] -> Base
        assert not any(s == "models.py::Color" for s, _ in inherits)  # Enum is external

    def test_summary_invariant_and_counts(self, sample_kb):
        s = sample_kb.summary
        assert s.python_files == s.files_parsed + s.files_failed + s.files_skipped_large
        assert s.files_failed == 0 and s.failed_files == []
        assert s.files_with_syntax_errors == 0
        assert s.entities["class"] >= 2  # AuthService, BaseService
        assert s.entities["method"] >= 4
        assert sum(s.entities.values()) == len(sample_kb.entities)
        assert sum(s.relationships.values()) == len(sample_kb.relationships)

    def test_oversized_files_are_inventoried_but_not_parsed(self, monkeypatch):
        monkeypatch.setattr(settings, "max_file_size_bytes", 10)
        scan = scan_repository(SAMPLE_REPO)
        kb = build_knowledge_base(SAMPLE_REPO, scan)
        assert kb.summary.files_skipped_large > 0
        # File entities exist, but no code entities were extracted from them:
        assert any(e.id == "app/auth.py" for e in kb.entities)
        assert not any(e.id.startswith("app/auth.py::") for e in kb.entities)

    def test_unreadable_file_is_counted_failed_not_fatal(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "ok.py").write_text("def fine():\n    pass\n", encoding="utf-8")
        (repo / "gone.py").write_text("def poof():\n    pass\n", encoding="utf-8")
        scan = scan_repository(repo)
        (repo / "gone.py").unlink()  # vanishes between scan and parse
        kb = build_knowledge_base(repo, scan)
        assert kb.summary.files_failed == 1
        assert kb.summary.failed_files == ["gone.py"]
        assert any(e.id == "ok.py::fine" for e in kb.entities)

    def test_duplicate_definition_last_wins(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "dup.py").write_text(
            "def twice():\n    return 1\n\n\ndef twice():\n    return 2\n", encoding="utf-8"
        )
        kb = build_knowledge_base(repo, scan_repository(repo))
        matches = [e for e in kb.entities if e.id == "dup.py::twice"]
        assert len(matches) == 1
        assert matches[0].start_line == 5  # the later definition, like the Python runtime
        # And containment holds exactly one edge to it:
        edges = [r for r in kb.relationships if r.target == "dup.py::twice"]
        assert len(edges) == 1


class TestSerializer:
    def test_round_trip_and_atomicity(self, sample_kb, tmp_path):
        write_knowledge_base(tmp_path, sample_kb)
        assert load_entities(tmp_path) == sample_kb.entities
        assert load_relationships(tmp_path) == sample_kb.relationships
        # No temp files may survive a successful write:
        assert list(tmp_path.glob("*.tmp")) == []

    def test_files_are_valid_json_with_expected_shape(self, sample_kb, tmp_path):
        write_knowledge_base(tmp_path, sample_kb)
        entities = json.loads((tmp_path / "entities.json").read_text(encoding="utf-8"))
        relationships = json.loads((tmp_path / "relationships.json").read_text(encoding="utf-8"))
        assert isinstance(entities["entities"], list)
        assert isinstance(relationships["relationships"], list)
        assert {"source", "relation", "target"} <= set(relationships["relationships"][0])
