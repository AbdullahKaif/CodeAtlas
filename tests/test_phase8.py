"""Tests for Codebase Health Indicators, the session footprint, and the demonstration repository (Phase 8)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.documentation.generator import _route_entities
from backend.health.indicators import (
    MANY_PARAMETERS,
    VERY_LARGE_FILE_LINES,
    VERY_LARGE_FUNCTION_LINES,
    _parse_package_json,
    _parse_pyproject,
    _parse_requirements,
    compute_health,
)
from backend.knowledge.builder import build_knowledge_base
from backend.knowledge.serializer import write_chunks, write_knowledge_base
from backend.knowledge.store import KnowledgeIndex, load_knowledge
from backend.main import app
from backend.onboarding.generator import generate_onboarding
from backend.privacy.cleanup import delete_session
from backend.privacy.footprint import session_footprint
from backend.rag.chunker import build_chunks
from backend.rag.retriever import build_index, retrieve
from backend.repository.scanner import scan_repository
from backend.security.models import Finding, ScannerStatus, SecurityReport, SecuritySummary
from backend.testgen.generator import detect_conventions
from tests.conftest import FIXTURES_DIR, SAMPLE_REPO, FakeEmbeddingModel

DEMO_REPO = FIXTURES_DIR.parent.parent / "fixtures" / "demo-repository"


def _analyze(source: Path, session_id: str, name: str) -> tuple[str, Path]:
    """Run the offline part of the pipeline on a fixture repository, as the runner would persist it."""
    session_dir = settings.session_dir(session_id)
    repo = session_dir / "repository"
    shutil.copytree(source, repo)
    scan = scan_repository(repo)
    kb = build_knowledge_base(repo, scan)
    chunks, _ = build_chunks(repo, scan, kb.entities)
    write_knowledge_base(session_dir / "analysis", kb)
    write_chunks(session_dir / "analysis", chunks)
    build_index(session_dir / "vectors", chunks, FakeEmbeddingModel())
    overview = {"repository": {"name": name}, "scan": {"files": [f.model_dump() for f in scan.files]}}
    (session_dir / "analysis" / "repository.json").write_text(json.dumps(overview), encoding="utf-8")
    (session_dir / "status.json").write_text("{}", encoding="utf-8")
    return session_id, session_dir


@pytest.fixture
def sample_session(temp_sessions):
    return _analyze(SAMPLE_REPO, "abcdef123456", "sample")


@pytest.fixture
def demo_session(temp_sessions):
    return _analyze(DEMO_REPO, "0123456789ab", "taskflow")


@pytest.fixture
def client(temp_sessions):
    return TestClient(app)


def _report(session_id: str) -> SecurityReport:
    finding = Finding(
        id="SEC-001", fingerprint="fp1", severity="HIGH", category="vulnerability", type="SQL Injection",
        file="app/database.py", line=18, source="semgrep", rule="python.sqli", message="SQL built from input",
    )
    secret = Finding(
        id="SEC-002", fingerprint="fp2", severity="CRITICAL", category="secret", type="AWS Access Key",
        file="app/config.py", line=9, source="gitleaks", rule="aws-access-key", message="secret detected",
    )
    return SecurityReport(
        session_id=session_id, scanned_at="2026-09-07T00:00:00Z",
        scanners=[ScannerStatus(name="semgrep", available=True, ran=True, findings=1),
                  ScannerStatus(name="gitleaks", available=True, ran=True, findings=1)],
        summary=SecuritySummary(total=2, by_severity={"HIGH": 1, "CRITICAL": 1}, vulnerabilities=1, secrets=1),
        findings=[finding, secret],
    )


class TestHealthIndicators:
    def test_indicators_are_counts_from_evidence(self, sample_session):
        session_id, session_dir = sample_session
        index = load_knowledge(session_dir)
        health = compute_health(session_id, session_dir, index, _report(session_id))

        assert health.repository == "sample" and "not standardised" in health.note
        assert health.security.scanned and health.security.high == 1 and health.security.critical == 1
        assert health.security.secrets == 1 and health.security.scanners_ran == ["semgrep", "gitleaks"]

        doc = health.documentation
        assert doc.readme_present and doc.readme_path == "README.md" and doc.documentation_files == 0
        assert doc.documentable_entities > 0 and 0 < doc.docstring_coverage <= 1
        assert doc.documented_entities <= doc.documentable_entities

        maint = health.maintainability
        assert maint.source_files == 7 and maint.functions >= 8  # app/*.py (7 modules), never the test file
        assert maint.very_large_files == [] and maint.very_large_functions == [] and maint.many_parameter_functions == []
        assert maint.largest_file_lines > 0 and maint.longest_function_lines >= maint.median_function_lines > 0
        assert maint.thresholds == {
            "very_large_file_lines": VERY_LARGE_FILE_LINES,
            "very_large_function_lines": VERY_LARGE_FUNCTION_LINES,
            "many_parameters": MANY_PARAMETERS,
        }

        deps = health.dependencies
        assert deps.files == ["requirements.txt"] and deps.count == 1 and deps.packages == ["requests"]
        assert deps.pinned == 0 and deps.unparsed_files == []
        assert [f.name for f in health.frameworks] == ["Requests"]
        assert health.frameworks[0].evidence == "requirements.txt"

    def test_without_scan_or_report_nothing_is_claimed(self, sample_session):
        session_id, session_dir = sample_session
        index = load_knowledge(session_dir)
        health = compute_health(session_id, session_dir, index, None)
        assert not health.security.scanned and health.security.critical == 0
        assert "nothing is claimed" in health.security.note

    def test_large_items_are_listed_with_thresholds(self, tmp_path, temp_sessions):
        repo = tmp_path / "big"
        repo.mkdir()
        body = "\n".join(f"    x{i} = {i}" for i in range(VERY_LARGE_FUNCTION_LINES + 5))
        params = ", ".join(f"p{i}" for i in range(MANY_PARAMETERS + 2))
        (repo / "wide.py").write_text(
            f"def long_one():\n{body}\n    return x0\n\n\ndef many({params}):\n    return p0\n", encoding="utf-8"
        )
        (repo / "tall.py").write_text("\n".join(f"v{i} = {i}" for i in range(VERY_LARGE_FILE_LINES + 10)) + "\n", encoding="utf-8")
        session_id, session_dir = _analyze(repo, "fedcba987654", "big")
        health = compute_health(session_id, session_dir, load_knowledge(session_dir), None)
        maint = health.maintainability
        assert [i.id for i in maint.very_large_files] == ["tall.py"] and maint.very_large_files_total == 1
        assert [i.id for i in maint.very_large_functions] == ["wide.py::long_one"]
        assert [i.id for i in maint.many_parameter_functions] == ["wide.py::many"]
        assert maint.many_parameter_functions[0].parameters == MANY_PARAMETERS + 2
        assert health.dependencies.count is None and health.dependencies.files == []
        assert not health.documentation.readme_present and health.documentation.docstring_coverage == 0.0

    def test_dependency_file_parsers(self):
        assert _parse_requirements("# c\nrequests==2.31.0\nfastapi>=0.1  # x\n-r base.txt\n\ngit+https://x/y\n") == [
            ("requests", True), ("fastapi", False),
        ]
        py = _parse_pyproject('[project]\ndependencies=["httpx>=0.27","numpy==1.26"]\n[project.optional-dependencies]\ndev=["pytest"]\n[tool.poetry.dependencies]\npython="^3.11"\nflask="3.0.0"\n')
        assert py == [("httpx", False), ("numpy", True), ("pytest", False), ("flask", True)]
        assert _parse_package_json('{"dependencies":{"react":"18.3.1"},"devDependencies":{"vitest":"^2.0.0"}}') == [
            ("react", True), ("vitest", False),
        ]

    def test_frameworks_from_decorators_when_no_dependency_file(self, tmp_path, temp_sessions):
        repo = tmp_path / "svc"
        repo.mkdir()
        (repo / "app.py").write_text(
            "class R:\n    def get(self, p):\n        return lambda f: f\n\nrouter = R()\n\n@router.get('/x')\ndef x():\n    return 1\n",
            encoding="utf-8",
        )
        session_id, session_dir = _analyze(repo, "aaaaaaaaaaaa", "svc")
        health = compute_health(session_id, session_dir, load_knowledge(session_dir), None)
        assert [(f.name, f.category, f.evidence) for f in health.frameworks] == [
            ("FastAPI or Flask-style routing", "web", "decorators in app.py"),
        ]


class TestSessionFootprint:
    def test_footprint_lists_artifacts_and_deletion_removes_them(self, sample_session):
        session_id, session_dir = sample_session
        footprint = session_footprint(session_id, session_dir)
        names = [a.name for a in footprint.artifacts]
        assert names == ["analysis", "repository", "status.json", "vectors"]
        by_name = {a.name: a for a in footprint.artifacts}
        assert by_name["repository"].files == 10 and by_name["repository"].bytes > 0
        assert "source code" in by_name["repository"].description and "FAISS" in by_name["vectors"].description
        assert footprint.total_files == sum(a.files for a in footprint.artifacts) and footprint.total_bytes > 0
        assert footprint.location == str(session_dir) and "this one directory" in footprint.note

        assert delete_session(session_id) is True
        assert not session_dir.exists()


class TestDemoRepository:
    """The demonstration repository must exercise every stage the demo story shows (spec §45, §47, §48)."""

    def test_no_real_credentials_and_defects_are_labelled(self):
        text = "\n".join(p.read_text(encoding="utf-8") for p in DEMO_REPO.rglob("*.py"))
        assert "AKIAIOSFODNN7EXAMPLE" not in text  # not even the AWS documentation key
        assert text.count("DELIBERATE DEFECT") >= 4
        assert "fake" in (DEMO_REPO / "README.md").read_text(encoding="utf-8").lower()

    def test_layers_relationships_and_onboarding(self, demo_session):
        session_id, session_dir = demo_session
        index = load_knowledge(session_dir)
        assert index.repo_name == "taskflow"
        packages = {index.package_of(p) for p in index.source_files()}
        assert {"taskflow", "tests"} <= packages
        assert len(index.entities) >= 40
        calls = {(e.source, e.target) for e in index.relationships if e.relation == "calls"}
        imports = {(e.source, e.target) for e in index.relationships if e.relation == "imports"}
        # The layers depend on each other as documented: API imports auth and services,
        # services and auth call the database module, auth calls the token helpers.
        assert ("taskflow/api/routes.py", "taskflow/auth/service.py") in imports
        assert ("taskflow/api/routes.py", "taskflow/services/tasks.py") in imports
        assert ("taskflow/services/tasks.py::TaskService.list_for", "taskflow/db/repository.py::find_tasks_by_owner") in calls
        assert ("taskflow/auth/service.py::AuthService.login", "taskflow/db/repository.py::find_user_by_name") in calls
        assert ("taskflow/auth/service.py::AuthService.authenticate", "taskflow/auth/tokens.py::decode_token") in calls
        inherits = [e for e in index.relationships if e.relation == "inherits"]
        assert inherits == []  # dataclasses and plain classes only; no inheritance to mislead the graph

        guide = generate_onboarding(index)
        stages = {s.number: s for s in guide.stages}
        assert stages["03"].detected and "taskflow/auth/service.py" in stages["03"].files
        assert stages["05"].detected and "taskflow/db/repository.py" in stages["05"].files
        assert stages["06"].detected and "tests/test_auth.py" in stages["06"].files
        assert "taskflow/main.py" in guide.overview["entry_points"]
        assert [r.id for r in _route_entities(index)] == [
            "taskflow/api/routes.py::login", "taskflow/api/routes.py::list_tasks",
            "taskflow/api/routes.py::create_task", "taskflow/api/routes.py::complete_task",
        ]
        conventions = detect_conventions(index)
        assert conventions.framework == "pytest" and conventions.fixtures_file == "tests/conftest.py"

    def test_health_and_retrieval(self, demo_session, fake_embeddings):
        session_id, session_dir = demo_session
        index = load_knowledge(session_dir)
        health = compute_health(session_id, session_dir, index, None)
        assert health.documentation.readme_present and health.documentation.documentation_paths == ["docs/architecture.md"]
        assert health.documentation.docstring_coverage >= 0.8  # the demo app is documented on purpose
        assert health.maintainability.very_large_functions == [] and health.maintainability.very_large_files == []
        assert health.dependencies.files == ["pyproject.toml", "requirements.txt"]
        assert health.dependencies.count == 6 and health.dependencies.pinned == 1  # requests==2.31.0
        assert {f.name for f in health.frameworks} >= {"FastAPI", "SQLAlchemy", "Pydantic", "pytest"}
        hits = retrieve(session_dir, "How does authentication work?", top_k=5)
        assert hits and all(h.file in index.files for h in hits)


class TestPhase8Endpoints:
    def test_health_endpoint(self, client, sample_session):
        session_id, _ = sample_session
        response = client.get(f"/api/repository/{session_id}/health")
        assert response.status_code == 200
        body = response.json()
        assert body["documentation"]["readme_present"] is True
        assert body["security"]["scanned"] is False  # no scanner report in this fixture session
        assert body["dependencies"]["packages"] == ["requests"] and body["frameworks"][0]["name"] == "Requests"
        assert body["maintainability"]["thresholds"]["very_large_file_lines"] == VERY_LARGE_FILE_LINES
        assert client.get("/api/repository/000000000000/health").status_code == 404

    def test_footprint_endpoint_then_delete(self, client, sample_session):
        session_id, session_dir = sample_session
        response = client.get(f"/api/session/{session_id}/footprint")
        assert response.status_code == 200
        body = response.json()
        assert body["total_files"] > 0 and {a["name"] for a in body["artifacts"]} >= {"repository", "analysis", "vectors"}
        assert client.delete(f"/api/session/{session_id}").json() == {"session_id": session_id, "deleted": True}
        assert client.get(f"/api/session/{session_id}/footprint").status_code == 404
        assert not session_dir.exists()
        assert client.get("/api/session/notvalid!/footprint").status_code == 404

    def test_demo_repository_through_the_api(self, client, fake_embeddings, temp_sessions, monkeypatch):
        import backend.analysis.runner as runner_module

        def _copy_demo(url, dest, **kwargs):
            shutil.copytree(DEMO_REPO, dest, dirs_exist_ok=True)
            return dest

        monkeypatch.setattr(runner_module, "clone_repository", _copy_demo)
        started = client.post("/api/analyze", json={"repo_url": "https://github.com/example/taskflow"})
        assert started.status_code == 202
        session_id = started.json()["session_id"]
        status = client.get(f"/api/analysis/{session_id}/status").json()
        assert status["state"] == "completed"
        health = client.get(f"/api/repository/{session_id}/health").json()
        assert "FastAPI" in {f["name"] for f in health["frameworks"]}
        onboarding = client.get(f"/api/onboarding/{session_id}").json()
        assert onboarding["repository"] == "taskflow" and onboarding["learning_path"]
        graph = client.get(f"/api/architecture/{session_id}").json()
        assert {"taskflow/api/routes.py", "taskflow/db/repository.py"} <= {n["id"] for n in graph["nodes"]}
        impact = client.post("/api/impact", json={"session_id": session_id, "target": "taskflow/auth/service.py::AuthService"}).json()
        assert "taskflow/api/routes.py" in impact["files"]
        docs = client.get(f"/api/documentation/{session_id}").json()
        assert {d["path"] for d in docs["existing_documents"]} == {"README.md", "docs/architecture.md"}
