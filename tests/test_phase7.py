"""Tests for documentation drafts, test suggestions and their endpoints (Phase 7)."""
from __future__ import annotations

import json
import re
import shutil

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.documentation.generator import (
    KINDS,
    UnknownKindError,
    build_fact_sheet,
    documentation_status,
    existing_documents,
    generate_documentation,
)
from backend.knowledge.builder import build_knowledge_base
from backend.knowledge.serializer import write_chunks, write_knowledge_base
from backend.knowledge.store import KnowledgeIndex, load_knowledge
from backend.main import app
from backend.rag.chunker import build_chunks
from backend.rag.retriever import build_index
from backend.repository.scanner import scan_repository
from backend.testgen.generator import (
    UnknownTargetError,
    UnsupportedTargetError,
    _categories,
    _parse,
    detect_conventions,
    generate_tests,
)
from tests.conftest import SAMPLE_REPO, FakeEmbeddingModel, FakeLLMClient

_BLOCK_HEADER = re.compile(r"^\[1\] (?P<file>\S+): lines (?P<start>\d+)-(?P<end>\d+)", re.MULTILINE)


def _cite_first_block(body: str):
    """A fake answer that cites the first excerpt it was shown, so the citation validates."""

    def answer(prompt, system, history):
        match = _BLOCK_HEADER.search(prompt)
        assert match is not None, "the prompt must carry at least one excerpt"
        return f"{body}\n\nSources:\n- {match.group('file')}: lines {match.group('start')}-{match.group('end')}"

    return answer


@pytest.fixture(scope="module")
def fixture_index() -> KnowledgeIndex:
    scan = scan_repository(SAMPLE_REPO)
    kb = build_knowledge_base(SAMPLE_REPO, scan)
    readme = (SAMPLE_REPO / "README.md").read_text(encoding="utf-8")
    return KnowledgeIndex(kb.entities, kb.relationships, scan.files, "sample", readme_text=readme)


@pytest.fixture
def analyzed_session(temp_sessions):
    session_id = "abcdef123456"
    session_dir = settings.session_dir(session_id)
    repo = session_dir / "repository"
    shutil.copytree(SAMPLE_REPO, repo)
    scan = scan_repository(repo)
    kb = build_knowledge_base(repo, scan)
    chunks, _ = build_chunks(repo, scan, kb.entities)
    write_knowledge_base(session_dir / "analysis", kb)
    write_chunks(session_dir / "analysis", chunks)
    build_index(session_dir / "vectors", chunks, FakeEmbeddingModel())
    # The overview the real runner persists: the knowledge index reads file metadata and the name from it.
    overview = {"repository": {"name": "sample"}, "scan": {"files": [f.model_dump() for f in scan.files]}}
    (session_dir / "analysis" / "repository.json").write_text(json.dumps(overview), encoding="utf-8")
    return session_id, session_dir


@pytest.fixture
def client(temp_sessions):
    return TestClient(app)


class TestDocumentationEvidence:
    def test_existing_documents_are_detected(self, fixture_index):
        docs = existing_documents(fixture_index)
        assert [(d.path, d.role) for d in docs] == [("README.md", "readme")]
        assert docs[0].size_bytes > 0

    def test_fact_sheet_names_real_files_only(self, fixture_index):
        sheet = build_fact_sheet(fixture_index, existing_documents(fixture_index))
        assert sheet.startswith("Repository: sample")
        assert "Entry points: app/main.py" in sheet
        assert "Dependency and build files: requirements.txt" in sheet
        assert "Existing documentation: README.md (readme)" in sheet
        assert "- app:" in sheet  # the package table
        assert "Test files: tests/test_auth.py" in sheet
        mentioned = set(re.findall(r"\b(?:app|tests)/[\w/]+\.py\b", sheet))
        assert mentioned and mentioned <= set(fixture_index.files)


class TestDocumentationDrafts:
    def test_status_lists_kinds_and_cache_state(self, analyzed_session, fake_embeddings):
        session_id, session_dir = analyzed_session
        index = load_knowledge(session_dir)
        status = documentation_status(session_id, session_dir, index)
        assert [k.kind for k in status.kinds] == list(KINDS) and not any(k.cached for k in status.kinds)
        assert status.existing_documents[0].path == "README.md"
        generate_documentation(session_id, session_dir, index, "architecture", llm=FakeLLMClient())
        status = documentation_status(session_id, session_dir, index)
        assert {k.kind: k.cached for k in status.kinds}["architecture"] is True
        assert {k.kind: k.cached for k in status.kinds}["readme"] is False

    def test_readme_draft_is_grounded_and_cached(self, analyzed_session, fake_embeddings):
        session_id, session_dir = analyzed_session
        index = load_knowledge(session_dir)
        llm = FakeLLMClient(answer=_cite_first_block("# Sample\n\n## Overview\nA tiny sample application."))
        draft = generate_documentation(session_id, session_dir, index, "readme", llm=llm)

        prompt = llm.calls[0]["prompt"]
        assert "README draft (README.md)" in prompt and "Fact sheet" in prompt
        assert "## Overview" in prompt and "## Installation" in prompt  # the required headings
        assert "Repository: sample" in prompt and "Existing documentation: README.md (readme)" in prompt
        assert draft.kind == "readme" and draft.retrieval_used and draft.context
        assert draft.markdown == "# Sample\n\n## Overview\nA tiny sample application."  # Sources section stripped
        assert len(draft.sources) == 1 and draft.references_removed == 0
        assert draft.filename == "README.codeatlas.md"  # README.md exists and is never overwritten
        assert [d.path for d in draft.existing_documents] == ["README.md"]
        assert not draft.cached and draft.model == "fake-llm"
        assert "never overwritten" in draft.note

        again = generate_documentation(session_id, session_dir, index, "readme", llm=llm)
        assert again.cached and again.markdown == draft.markdown and len(llm.calls) == 1
        fresh = generate_documentation(session_id, session_dir, index, "readme", refresh=True, llm=llm)
        assert not fresh.cached and len(llm.calls) == 2

    def test_draft_without_vector_index_uses_entity_code(self, temp_sessions):
        session_dir = settings.session_dir("abcdef123456")
        scan = scan_repository(SAMPLE_REPO)
        kb = build_knowledge_base(SAMPLE_REPO, scan)
        write_knowledge_base(session_dir / "analysis", kb)
        index = load_knowledge(session_dir)
        llm = FakeLLMClient(answer=_cite_first_block("# Sample\n\n## Purpose\nFrom entities."))
        draft = generate_documentation("abcdef123456", session_dir, index, "architecture", llm=llm)
        assert not draft.retrieval_used
        assert draft.context and all(c.chunk_id.startswith("entity-") for c in draft.context)
        assert draft.filename == "ARCHITECTURE.md"  # no README in this minimal session, nothing to collide with
        assert draft.sources and draft.sources[0].file == draft.context[0].file

    def test_unknown_kind(self, analyzed_session):
        session_id, session_dir = analyzed_session
        with pytest.raises(UnknownKindError):
            generate_documentation(session_id, session_dir, load_knowledge(session_dir), "wiki", llm=FakeLLMClient())


class TestTestingConventions:
    def test_pytest_layout_is_detected(self, fixture_index):
        conventions = detect_conventions(fixture_index)
        assert conventions.framework == "pytest" and conventions.test_files == 1
        assert conventions.test_directories == ["tests"] and conventions.naming_pattern == "test_*.py"
        assert conventions.fixtures_file is None and conventions.example_test_file == "tests/test_auth.py"
        assert any("pytest-style" in e for e in conventions.evidence)

    def test_no_tests_is_reported_not_guessed(self, fixture_index):
        production_only = [e for e in fixture_index.entities if not fixture_index.is_test(e.file)]
        files = [f for p, f in fixture_index.files.items() if not fixture_index.is_test(p)]
        index = KnowledgeIndex(production_only, [], files, "sample")
        conventions = detect_conventions(index)
        assert conventions.framework == "unknown" and conventions.test_files == 0
        assert conventions.evidence == ["no test files found"]


class TestTestSuggestions:
    def test_method_suggestion_gathers_evidence_and_caches(self, analyzed_session, fake_embeddings):
        session_id, session_dir = analyzed_session
        index = load_knowledge(session_dir)
        code = (
            "# Normal cases\n"
            "def test_login_known_user():\n    assert True\n\n"
            "# Edge cases\n"
            "def test_login_empty_password():\n    assert True\n\n"
            "# Invalid inputs\n"
            "# not applicable: the excerpts show no validation of the argument types\n\n"
            "# Error conditions\n"
            "def test_login_raises_when_store_unavailable():\n    assert True\n"
        )
        llm = FakeLLMClient(answer=_cite_first_block(f"These tests cover login.\n\n```python\n{code}```\n\nAssumptions: none"))
        result = generate_tests(session_id, session_dir, index, "app/auth.py::AuthService.login", llm=llm)

        prompt = llm.calls[0]["prompt"]
        assert "Target: app/auth.py::AuthService.login (method" in prompt
        assert "Import path: from app.auth import AuthService" in prompt
        assert "Testing conventions: framework pytest; 1 test file(s) under tests; naming test_*.py." in prompt
        assert "Suggested test file: tests/test_auth.py" in prompt
        assert "tests/test_auth.py::test_login_unknown_user_fails" in prompt  # existing test, found statically

        assert result.target.id == "app/auth.py::AuthService.login" and result.target.parent == "app/auth.py::AuthService"
        assert result.suggested_file == "tests/test_auth.py" and result.suggested_file_exists
        assert result.existing_tests == ["tests/test_auth.py::test_login_unknown_user_fails"]
        ids = [c.chunk_id for c in result.context]
        assert ids[0] == "target" and "enclosing-class" in ids
        assert any(i.startswith("callee-") for i in ids)  # login calls find_user
        assert any(i.startswith("existing-test-") for i in ids)
        assert result.explanation == "These tests cover login."
        assert result.code.startswith("# Normal cases") and "def test_login_raises_when_store_unavailable" in result.code
        assert result.assumptions == ""
        covered = {c.name: (c.covered, c.tests) for c in result.categories}
        assert covered["normal"] == (True, ["test_login_known_user"])
        assert covered["edge"] == (True, ["test_login_empty_password"])
        assert covered["invalid_input"] == (False, [])
        assert covered["error_conditions"] == (True, ["test_login_raises_when_store_unavailable"])
        assert len(result.sources) == 1 and result.references_removed == 0
        assert "nothing has been written" in result.disclaimer and not result.cached

        again = generate_tests(session_id, session_dir, index, "app/auth.py::AuthService.login", llm=llm)
        assert again.cached and len(llm.calls) == 1

    def test_function_without_tests_gets_a_new_file_suggestion(self, analyzed_session, fake_embeddings):
        session_id, session_dir = analyzed_session
        index = load_knowledge(session_dir)
        llm = FakeLLMClient(answer="No code here.\n\nAssumptions: the helper is pure.\n\nSources: none")
        result = generate_tests(session_id, session_dir, index, "app/database.py::find_user", llm=llm)
        assert "Import path: from app.database import find_user" in llm.calls[0]["prompt"]
        assert "No existing tests exercise the target." in llm.calls[0]["prompt"]
        assert result.suggested_file == "tests/test_database.py" and not result.suggested_file_exists
        assert result.code == "" and result.assumptions == "the helper is pure."
        assert all(not c.covered for c in result.categories)
        assert result.context[0].chunk_id == "target" and any(c.chunk_id.startswith("caller-") for c in result.context)

    def test_unsupported_and_unknown_targets(self, analyzed_session):
        session_id, session_dir = analyzed_session
        index = load_knowledge(session_dir)
        with pytest.raises(UnsupportedTargetError):
            generate_tests(session_id, session_dir, index, "app/auth.py::AuthService", llm=FakeLLMClient())
        with pytest.raises(UnsupportedTargetError):
            generate_tests(session_id, session_dir, index, "app/auth.py", llm=FakeLLMClient())
        with pytest.raises(UnknownTargetError):
            generate_tests(session_id, session_dir, index, "nope.py::missing", llm=FakeLLMClient())

    def test_parsing_and_categories(self):
        explanation, code, assumptions = _parse("Prose only.\n\nAssumptions: none")
        assert (explanation, code, assumptions) == ("Prose only.", "", "")
        explanation, code, assumptions = _parse("Intro.\n```python\ndef test_x():\n    pass\n```\nAssumptions:\n- input is a str")
        assert explanation == "Intro." and code == "def test_x():\n    pass" and assumptions == "- input is a str"
        categories = _categories("def test_invalid_type_raises():\n    pass\ndef test_edge_empty():\n    pass\n")
        by_name = {c.name: c.tests for c in categories}
        assert by_name["invalid_input"] == ["test_invalid_type_raises"]
        assert by_name["edge"] == ["test_edge_empty"]
        assert by_name["error_conditions"] == ["test_invalid_type_raises"]  # "raises" counts as an error test
        assert by_name["normal"] == []


class TestPhase7Endpoints:
    def test_documentation_status_and_unknown_session(self, client, analyzed_session):
        session_id, _ = analyzed_session
        response = client.get(f"/api/documentation/{session_id}")
        assert response.status_code == 200
        body = response.json()
        assert [k["kind"] for k in body["kinds"]] == ["readme", "architecture", "developer_guide", "api_overview"]
        assert body["existing_documents"] == [{"path": "README.md", "role": "readme", "size_bytes": body["existing_documents"][0]["size_bytes"]}]
        assert client.get("/api/documentation/000000000000").status_code == 404

    def test_documentation_draft_endpoint(self, client, analyzed_session, fake_embeddings, fake_llm):
        session_id, _ = analyzed_session
        fake_llm.answer = "# Sample\n\n## Scope\nNo routes.\n\nSources: none"
        response = client.post("/api/documentation", json={"session_id": session_id, "kind": "api_overview"})
        assert response.status_code == 200
        body = response.json()
        assert body["markdown"] == "# Sample\n\n## Scope\nNo routes." and body["filename"] == "API.md"
        assert body["sources"] == [] and body["context"] and body["retrieval_used"] is True
        assert client.post("/api/documentation", json={"session_id": session_id, "kind": "api_overview"}).json()["cached"] is True
        assert client.post("/api/documentation", json={"session_id": session_id, "kind": "wiki"}).status_code == 422

    def test_documentation_without_llm_is_503(self, client, analyzed_session, fake_embeddings):
        session_id, _ = analyzed_session
        response = client.post("/api/documentation", json={"session_id": session_id, "kind": "readme"})
        assert response.status_code == 503 and "Ollama" in response.json()["detail"]

    def test_tests_endpoints(self, client, analyzed_session, fake_embeddings, fake_llm):
        session_id, _ = analyzed_session
        conventions = client.get(f"/api/tests/{session_id}/conventions")
        assert conventions.status_code == 200 and conventions.json()["framework"] == "pytest"

        fake_llm.answer = "Covers the helper.\n\n```python\n# Normal cases\ndef test_find_user_known():\n    assert True\n```\n\nAssumptions: none\n\nSources: none"
        response = client.post("/api/tests", json={"session_id": session_id, "target": "app/database.py::find_user"})
        assert response.status_code == 200
        body = response.json()
        assert body["target"]["name"] == "find_user" and body["suggested_file"] == "tests/test_database.py"
        assert body["code"].startswith("# Normal cases") and body["categories"][0]["covered"] is True
        assert body["conventions"]["framework"] == "pytest" and "nothing has been written" in body["disclaimer"]

        assert client.post("/api/tests", json={"session_id": session_id, "target": "app/auth.py::AuthService"}).status_code == 400
        assert client.post("/api/tests", json={"session_id": session_id, "target": "nope.py::x"}).status_code == 404
        assert client.post("/api/tests", json={"session_id": "000000000000", "target": "x"}).status_code == 404

    def test_tests_without_llm_is_503(self, client, analyzed_session, fake_embeddings):
        session_id, _ = analyzed_session
        response = client.post("/api/tests", json={"session_id": session_id, "target": "app/database.py::find_user"})
        assert response.status_code == 503
