"""Tests for the security engine: normalization, redaction, degradation, AI explain/fix, API."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.security.engine as engine_module
import backend.security.gitleaks as gitleaks_module
import backend.security.semgrep as semgrep_module
from backend.config import settings
from backend.knowledge.builder import build_knowledge_base
from backend.knowledge.serializer import write_chunks, write_knowledge_base
from backend.main import app
from backend.rag.chunker import build_chunks
from backend.rag.retriever import build_index
from backend.repository.scanner import scan_repository
from backend.security.engine import load_report, run_security_scan, summarize, write_report
from backend.security.explain import explain_finding, suggest_fix
from backend.security.models import Finding, ScannerStatus, SecurityReport
from backend.security.normalizer import (
    fingerprint,
    gitleaks_findings,
    merge_and_number,
    redact_literals,
    redact_span,
    semgrep_findings,
)
from backend.security.runner import ToolTimeoutError
from tests.conftest import SAMPLE_REPO, FakeEmbeddingModel, FakeLLMClient

FAKE_AWS_KEY = "AKIA2TESTFAKEKEY0001"  # synthetic; lives in the fixture repo
FAKE_PASSWORD = "hunter2-fake-password"
FAKE_API_KEY = "c1a4f0e9b7d2486e9f3b5a7d0c2e8f41"  # synthetic generic key in the fixture

HAVE_SEMGREP = shutil.which("semgrep") is not None
HAVE_GITLEAKS = shutil.which("gitleaks") is not None


def semgrep_payload(**overrides) -> dict:
    result = {
        "check_id": "codeatlas.python.sql-injection",
        "path": "app/database.py",
        "start": {"line": 17, "col": 5},
        "end": {"line": 18, "col": 26},
        "extra": {
            "message": "SQL statement built from a variable.",
            "severity": "ERROR",
            "lines": "requires login",
            "metadata": {
                "title": "SQL Injection",
                "severity": "CRITICAL",
                "cwe": ["CWE-89"],
                "owasp": ["A03:2021 - Injection"],
                "references": ["https://example.invalid/sqli"],
            },
        },
    }
    result.update(overrides)
    return {"results": [result], "errors": []}


def gitleaks_payload(path: str) -> list:
    return [
        {
            "Description": "Identified a pattern that may indicate AWS credentials.",
            "StartLine": 9, "EndLine": 9, "StartColumn": 22, "EndColumn": 41,
            "Match": f'AWS_ACCESS_KEY_ID = "{FAKE_AWS_KEY}"', "Secret": FAKE_AWS_KEY,
            "File": path, "RuleID": "aws-access-token", "Fingerprint": "x",
        },
        {
            "Description": "Generic API Key", "StartLine": 9, "EndLine": 9,
            "StartColumn": 22, "EndColumn": 41, "Match": FAKE_AWS_KEY, "Secret": FAKE_AWS_KEY,
            "File": path, "RuleID": "generic-api-key", "Fingerprint": "y",
        },
    ]


class TestSemgrepNormalization:
    def test_reads_flagged_lines_from_disk_when_withheld(self):
        (finding,) = semgrep_findings(semgrep_payload(), SAMPLE_REPO)
        assert finding.source == "semgrep" and finding.category == "vulnerability"
        assert finding.severity == "CRITICAL"  # metadata.severity overrides ERROR->HIGH
        assert finding.type == "SQL Injection"
        assert (finding.file, finding.line, finding.end_line) == ("app/database.py", 17, 18)
        assert "username" in (finding.code_context or "")  # read from the fixture file
        assert finding.cwe == ["CWE-89"] and finding.references
        assert finding.fingerprint == fingerprint("semgrep", finding.rule, "app/database.py", 17)

    def test_severity_mapping_without_override(self):
        payload = semgrep_payload()
        payload["results"][0]["extra"]["metadata"] = {}
        payload["results"][0]["extra"]["severity"] = "WARNING"
        (finding,) = semgrep_findings(payload, SAMPLE_REPO)
        assert finding.severity == "MEDIUM"
        assert finding.type == "Sql injection"  # derived from the rule id

    def test_credential_rule_redacts_literals(self):
        payload = semgrep_payload(
            check_id="codeatlas.python.hardcoded-credential",
            path="app/config.py",
            start={"line": 13, "col": 1},
            end={"line": 13, "col": 40},
        )
        payload["results"][0]["extra"]["metadata"] = {"category": "secret", "severity": "HIGH"}
        (finding,) = semgrep_findings(payload, SAMPLE_REPO)
        assert finding.category == "secret"
        assert FAKE_PASSWORD not in finding.code_context
        assert '"[REDACTED]"' in finding.code_context

    def test_ignored_and_malformed_results_are_skipped(self):
        payload = semgrep_payload()
        payload["results"][0]["extra"]["is_ignored"] = True
        payload["results"].append({"check_id": "broken"})  # missing everything else
        assert semgrep_findings(payload, SAMPLE_REPO) == []


class TestGitleaksNormalization:
    def test_secret_is_redacted_and_never_stored(self):
        findings = gitleaks_findings(gitleaks_payload(str(SAMPLE_REPO / "app" / "config.py")), SAMPLE_REPO)
        assert len(findings) == 1  # generic rule on the same line collapsed into the specific one
        finding = findings[0]
        assert finding.rule == "aws-access-token" and finding.type == "AWS Access Token"
        assert finding.file == "app/config.py" and finding.line == 9  # absolute path normalized
        assert finding.category == "secret" and finding.severity == "HIGH"
        assert finding.code_context == 'AWS_ACCESS_KEY_ID = "[REDACTED]"'
        assert FAKE_AWS_KEY not in finding.model_dump_json()
        assert (finding.column, finding.end_column) == (22, 41)

    def test_relative_paths_and_missing_secret_field(self):
        payload = gitleaks_payload("./app/config.py")
        del payload[1]
        payload[0]["Secret"] = ""
        (finding,) = gitleaks_findings(payload, SAMPLE_REPO)
        assert finding.file == "app/config.py"
        # Falls back to the column span when the value itself is unknown.
        assert FAKE_AWS_KEY not in finding.code_context and "[REDACTED]" in finding.code_context


class TestRedaction:
    def test_redact_literals(self):
        assert redact_literals('x = "secret-value"; y = "ab"') == 'x = "[REDACTED]"; y = "ab"'
        assert redact_literals("k = 'longsecret'") == "k = '[REDACTED]'"

    def test_redact_span(self):
        line = 'KEY = "abcdefgh"'
        assert redact_span(line, 8, 15, "abcdefgh") == 'KEY = "[REDACTED]"'
        assert redact_span(line, 8, 15) == 'KEY = "[REDACTED]"'
        assert redact_span(line, None, None) == 'KEY = "[REDACTED]"'


def make_finding(**kw) -> Finding:
    base = dict(
        id="", fingerprint="f", severity="MEDIUM", category="vulnerability", type="T",
        file="a.py", line=1, source="semgrep", rule="r", message="m",
    )
    base.update(kw)
    base["fingerprint"] = fingerprint(base["source"], base["rule"], base["file"], base["line"])
    return Finding(**base)


class TestMergeAndNumber:
    def test_orders_by_severity_then_location_and_numbers(self):
        findings = [
            make_finding(severity="LOW", file="z.py", line=5),
            make_finding(severity="CRITICAL", file="b.py", line=9),
            make_finding(severity="CRITICAL", file="a.py", line=30),
            make_finding(severity="HIGH", file="a.py", line=2),
        ]
        merged, truncated = merge_and_number(findings, max_findings=10)
        assert [(f.severity, f.file) for f in merged] == [
            ("CRITICAL", "a.py"), ("CRITICAL", "b.py"), ("HIGH", "a.py"), ("LOW", "z.py"),
        ]
        assert [f.id for f in merged] == ["SEC-001", "SEC-002", "SEC-003", "SEC-004"]
        assert truncated is False

    def test_semgrep_credential_dropped_when_gitleaks_has_the_line(self):
        findings = [
            make_finding(source="semgrep", category="secret", file="c.py", line=3, rule="hardcoded"),
            make_finding(source="gitleaks", category="secret", file="c.py", line=3, rule="aws", severity="HIGH"),
            make_finding(source="semgrep", category="secret", file="c.py", line=4, rule="hardcoded"),
        ]
        merged, _ = merge_and_number(findings, max_findings=10)
        assert [(f.source, f.line) for f in merged] == [("gitleaks", 3), ("semgrep", 4)]

    def test_cap_marks_truncation(self):
        findings = [make_finding(line=n) for n in range(1, 6)]
        merged, truncated = merge_and_number(findings, max_findings=3)
        assert len(merged) == 3 and truncated is True

    def test_summary(self):
        summary = summarize([
            make_finding(severity="HIGH", category="secret"),
            make_finding(severity="HIGH"),
            make_finding(severity="LOW"),
        ])
        assert summary.total == 3 and summary.secrets == 1 and summary.vulnerabilities == 2
        assert summary.by_severity["HIGH"] == 2 and summary.by_severity["CRITICAL"] == 0


@pytest.fixture
def session_with_repo(temp_sessions):
    session_id = "abcdef123456"
    repo = settings.session_dir(session_id) / "repository"
    shutil.copytree(SAMPLE_REPO, repo)
    return session_id, repo


class TestEngineDegradation:
    def test_missing_tools_are_reported_not_raised(self, session_with_repo, monkeypatch):
        monkeypatch.setattr("backend.security.runner.shutil.which", lambda *_: None)
        monkeypatch.setattr(settings, "semgrep_path", None)
        monkeypatch.setattr(settings, "gitleaks_path", None)
        session_id, repo = session_with_repo
        report = run_security_scan(session_id, repo)
        assert report.findings == [] and report.summary.total == 0
        assert [s.name for s in report.scanners] == ["semgrep", "gitleaks"]
        assert all(not s.available and not s.ran for s in report.scanners)
        assert "pip install semgrep" in report.scanners[0].install_hint
        assert "gitleaks" in report.scanners[1].install_hint
        assert load_report(settings.session_dir(session_id)).summary.total == 0  # persisted anyway

    def test_timeout_is_a_status_error(self, session_with_repo, monkeypatch):
        monkeypatch.setattr(semgrep_module, "find_tool", lambda *_: "/fake/semgrep")
        monkeypatch.setattr(semgrep_module, "semgrep_version", lambda *_: "9.9.9")

        def _slow(args, cwd, timeout_seconds, extra_env=None):
            raise ToolTimeoutError("semgrep timed out after 1s")

        monkeypatch.setattr(semgrep_module, "run_tool", _slow)
        monkeypatch.setattr(gitleaks_module, "find_tool", lambda *_: (_ for _ in ()).throw(gitleaks_module.ToolNotFoundError("gitleaks")))
        session_id, repo = session_with_repo
        report = run_security_scan(session_id, repo)
        semgrep_status = report.scanners[0]
        assert semgrep_status.available and not semgrep_status.ran
        assert "timed out" in semgrep_status.error and "TIMEOUT" in semgrep_status.error
        assert semgrep_status.version == "9.9.9"

    def test_adapter_crash_is_contained(self, session_with_repo, monkeypatch):
        def _boom(repo_dir, work_dir):
            raise RuntimeError("adapter bug")

        monkeypatch.setattr(engine_module, "run_semgrep", _boom)
        monkeypatch.setattr(engine_module, "run_gitleaks", lambda *_: ([], ScannerStatus(name="gitleaks", available=False, error="x")))
        session_id, repo = session_with_repo
        report = run_security_scan(session_id, repo)
        assert "crashed" in report.scanners[0].error


@pytest.mark.skipif(not HAVE_SEMGREP, reason="semgrep not installed")
class TestRealSemgrep:
    def test_bundled_rules_find_fixture_vulnerabilities(self, session_with_repo):
        session_id, repo = session_with_repo
        findings, status = semgrep_module.run_semgrep(repo, settings.session_dir(session_id) / "security")
        assert status.ran and status.error is None and status.version
        by_rule = {(f.rule.rsplit(".", 1)[-1], f.file): f for f in findings}
        sqli = by_rule[("sql-injection", "app/database.py")]
        assert sqli.severity == "CRITICAL" and sqli.line == 17
        assert by_rule[("command-injection", "app/utils.py")].severity == "CRITICAL"
        assert by_rule[("yaml-unsafe-load", "app/utils.py")].severity == "HIGH"
        assert by_rule[("insecure-deserialization-pickle", "app/utils.py")].line == 20
        credential = by_rule[("hardcoded-credential", "app/config.py")]
        assert credential.category == "secret" and FAKE_PASSWORD not in credential.code_context
        # The environment lookup on the next line is NOT a hard-coded credential:
        assert all(f.line != 17 for f in findings if f.file == "app/config.py")
        assert not (settings.session_dir(session_id) / "security" / "semgrep.json").exists()


@pytest.mark.skipif(not HAVE_GITLEAKS, reason="gitleaks not installed")
class TestRealGitleaks:
    def test_finds_and_redacts_fixture_secrets(self, session_with_repo):
        session_id, repo = session_with_repo
        findings, status = gitleaks_module.run_gitleaks(repo, settings.session_dir(session_id) / "security")
        assert status.ran and status.error is None and status.version
        rules = {f.rule for f in findings}
        assert "aws-access-token" in rules and "generic-api-key" in rules
        for finding in findings:
            assert FAKE_AWS_KEY not in finding.model_dump_json()
            assert "[REDACTED]" in (finding.code_context or "")
        assert not (settings.session_dir(session_id) / "security" / "gitleaks.json").exists()


@pytest.mark.skipif(not (HAVE_SEMGREP and HAVE_GITLEAKS), reason="scanners not installed")
class TestRealEngine:
    def test_full_scan_persists_redacted_report(self, session_with_repo):
        session_id, repo = session_with_repo
        report = run_security_scan(session_id, repo)
        assert report.summary.by_severity["CRITICAL"] >= 2 and report.summary.secrets >= 3
        assert report.findings[0].id == "SEC-001" and report.findings[0].severity == "CRITICAL"
        raw = (settings.session_dir(session_id) / "security" / "findings.json").read_text(encoding="utf-8")
        assert FAKE_AWS_KEY not in raw and FAKE_PASSWORD not in raw and FAKE_API_KEY not in raw


# ---------------------------------------------------------------------------
# AI explanation / fix (offline: fake LLM, synthetic report)
# ---------------------------------------------------------------------------

def synthetic_report(session_id: str) -> SecurityReport:
    findings = [
        make_finding(
            severity="CRITICAL", type="SQL Injection", file="app/database.py", line=17, end_line=18,
            rule="codeatlas.python.sql-injection", message="SQL built from a variable.",
            code_context='query = "SELECT ..." + username', cwe=["CWE-89"],
        ),
        make_finding(
            severity="HIGH", category="secret", type="AWS Access Token", file="app/config.py", line=9,
            column=22, end_column=41, source="gitleaks", rule="aws-access-token",
            message="AWS credentials.", code_context='AWS_ACCESS_KEY_ID = "[REDACTED]"',
        ),
    ]
    merged, _ = merge_and_number(findings, 10)
    return SecurityReport(
        session_id=session_id, scanned_at="now", scanners=[], summary=summarize(merged), findings=merged
    )


@pytest.fixture
def analyzed_session(temp_sessions):
    """Fixture repo copied into a session with knowledge base, index and a synthetic report."""
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
    report = synthetic_report(session_id)
    write_report(session_dir / "security", report)
    return session_id, session_dir, report


class TestExplainFinding:
    def test_explanation_cites_flagged_region(self, analyzed_session, fake_embeddings):
        session_id, session_dir, report = analyzed_session
        seen = {}

        def _answer(prompt, system, history):
            seen["prompt"] = prompt
            return (
                "## What the scanner detected\nThe scanner detected string concatenation.\n\n"
                "## Why it matters\n...\n\n## Potential impact\n...\n\n## Data flow\n...\n\n"
                "## Recommended remediation\nUse parameters.\n\n"
                "Sources:\n- app/database.py: lines 12-23\n- app/nowhere.py: lines 1-2"
            )

        llm = FakeLLMClient(answer=_answer)
        result = explain_finding(session_id, session_dir, report, "SEC-001", llm=llm)
        assert result.finding.id == "SEC-001" and result.cached is False
        assert result.context[0].chunk_id == "flagged-region"
        assert (result.context[0].start_line, result.context[0].end_line) == (12, 23)  # find_user()
        assert result.context[0].symbol == "find_user"
        assert len(result.context) > 1  # related chunks retrieved through the index
        assert result.sources[0].file == "app/database.py" and result.references_removed == 1
        assert "## What the scanner detected" in result.explanation and "Sources:" not in result.explanation
        assert "Finding SEC-001: SQL Injection" in seen["prompt"] and "[1] app/database.py: lines 12-23" in seen["prompt"]

    def test_secret_never_reaches_the_model_or_the_result(self, analyzed_session):
        session_id, session_dir, report = analyzed_session
        seen = {}

        def _answer(prompt, system, history):
            seen["prompt"] = prompt
            return "## What the scanner detected\nA key.\n\nSources: none"

        llm = FakeLLMClient(answer=_answer)
        result = explain_finding(session_id, session_dir, report, "SEC-002", llm=llm)
        assert FAKE_AWS_KEY not in seen["prompt"]
        assert "[REDACTED]" in seen["prompt"]
        assert FAKE_AWS_KEY not in result.model_dump_json()
        # No index in this test (no fake_embeddings): the flagged region alone is the evidence.
        assert [c.chunk_id for c in result.context] == ["flagged-region"]

    def test_results_are_cached_per_finding(self, analyzed_session):
        session_id, session_dir, report = analyzed_session
        llm = FakeLLMClient(answer="## What the scanner detected\nx\n\nSources: none")
        first = explain_finding(session_id, session_dir, report, "SEC-001", llm=llm)
        second = explain_finding(session_id, session_dir, report, "SEC-001", llm=llm)
        assert first.cached is False and second.cached is True and len(llm.calls) == 1
        third = explain_finding(session_id, session_dir, report, "SEC-001", refresh=True, llm=llm)
        assert third.cached is False and len(llm.calls) == 2
        cache_dir = session_dir / "security" / "ai"
        assert any(p.name.endswith(".explanation.json") for p in cache_dir.iterdir())

    def test_unknown_finding(self, analyzed_session):
        from backend.security.explain import FindingNotFoundError

        session_id, session_dir, report = analyzed_session
        with pytest.raises(FindingNotFoundError):
            explain_finding(session_id, session_dir, report, "SEC-999", llm=FakeLLMClient())


class TestSuggestFix:
    def test_fix_is_parsed_and_diffed(self, analyzed_session):
        session_id, session_dir, report = analyzed_session
        original = (session_dir / "repository" / "app" / "database.py").read_text(encoding="utf-8")
        region = "\n".join(original.split("\n")[11:23])  # find_user(): lines 12-23
        fixed = region.replace(
            "query = \"SELECT * FROM users WHERE username = '\" + username + \"'\"\n    cursor.execute(query)",
            'query = "SELECT * FROM users WHERE username = ?"\n    cursor.execute(query, (username,))',
        )
        assert fixed != region
        llm = FakeLLMClient(
            answer=f"Use a parameterized query.\n\n```python\n{fixed}\n```\n\nSide effects:\nNone expected; callers are unchanged."
        )
        result = suggest_fix(session_id, session_dir, report, "SEC-001", llm=llm)
        assert result.explanation == "Use a parameterized query."
        assert result.suggested_code == fixed
        assert result.side_effects == "None expected; callers are unchanged."
        assert (result.region_start_line, result.region_end_line) == (12, 23)
        assert result.diff.startswith("--- a/app/database.py\n+++ b/app/database.py")
        assert "-    cursor.execute(query)" in result.diff and "+    cursor.execute(query, (username,))" in result.diff
        assert result.disclaimer.startswith("AI-generated suggestion")
        # Never applied to the repository:
        assert (session_dir / "repository" / "app" / "database.py").read_text(encoding="utf-8") == original

    def test_fix_without_code_block(self, analyzed_session):
        session_id, session_dir, report = analyzed_session
        llm = FakeLLMClient(answer="I cannot propose a safe change without more context.")
        result = suggest_fix(session_id, session_dir, report, "SEC-002", llm=llm)
        assert result.suggested_code == "" and result.diff == ""
        assert "cannot propose" in result.explanation

    def test_secret_fix_diff_shows_only_redacted_values(self, analyzed_session):
        session_id, session_dir, report = analyzed_session
        llm = FakeLLMClient(answer='Read from the environment.\n\n```python\nAWS_ACCESS_KEY_ID = os.environ["AWS_ACCESS_KEY_ID"]\n```\n\nSide effects: none')
        result = suggest_fix(session_id, session_dir, report, "SEC-002", llm=llm)
        assert FAKE_AWS_KEY not in result.model_dump_json()
        assert "[REDACTED]" in result.diff
        assert result.suggested_code == 'AWS_ACCESS_KEY_ID = os.environ["AWS_ACCESS_KEY_ID"]'


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@pytest.fixture
def client(temp_sessions):
    return TestClient(app)


class TestSecurityApi:
    def test_unknown_session_is_404(self, client):
        assert client.get("/api/security/0123456789ab").status_code == 404

    def test_session_without_report_is_404(self, client, temp_sessions):
        (temp_sessions / "session_abcdef123456").mkdir(parents=True)
        response = client.get("/api/security/abcdef123456")
        assert response.status_code == 404 and "Re-analyze" in response.json()["detail"]

    def test_running_session_is_409(self, client, temp_sessions):
        from backend.analysis.status import StatusTracker

        (temp_sessions / "session_abcdef123456").mkdir(parents=True)
        StatusTracker("abcdef123456", "repo", "https://github.com/x/repo")
        assert client.get("/api/security/abcdef123456").status_code == 409

    def test_report_after_analysis(self, client, fake_clone):
        started = client.post("/api/analyze", json={"repo_url": "https://github.com/octo/sample"})
        session_id = started.json()["session_id"]
        response = client.get(f"/api/security/{session_id}")
        assert response.status_code == 200
        body = response.json()
        assert {s["name"] for s in body["scanners"]} == {"semgrep", "gitleaks"}
        assert body["summary"]["total"] == len(body["findings"])
        for finding in body["findings"]:
            assert FAKE_AWS_KEY not in json.dumps(finding)

    def test_explain_and_fix_endpoints(self, client, analyzed_session, fake_llm):
        session_id, _, _ = analyzed_session
        fake_llm.answer = "## What the scanner detected\nx\n\nSources:\n- app/database.py: lines 12-23"
        response = client.post("/api/security/explain", json={"session_id": session_id, "finding_id": "SEC-001"})
        assert response.status_code == 200, response.text
        assert response.json()["sources"][0]["file"] == "app/database.py"
        assert response.json()["cached"] is False
        again = client.post("/api/security/explain", json={"session_id": session_id, "finding_id": "SEC-001"})
        assert again.json()["cached"] is True

        fake_llm.answer = "Fix it.\n\n```python\nx = 1\n```\n\nSide effects: none"
        response = client.post("/api/security/fix", json={"session_id": session_id, "finding_id": "SEC-001"})
        assert response.status_code == 200, response.text
        assert response.json()["suggested_code"] == "x = 1" and response.json()["diff"]

    def test_unknown_finding_is_404(self, client, analyzed_session, fake_llm):
        session_id, _, _ = analyzed_session
        response = client.post("/api/security/explain", json={"session_id": session_id, "finding_id": "SEC-404"})
        assert response.status_code == 404

    def test_llm_unavailable_is_503(self, client, analyzed_session):
        session_id, _, _ = analyzed_session
        response = client.post("/api/security/fix", json={"session_id": session_id, "finding_id": "SEC-001"})
        assert response.status_code == 503 and "Ollama" in response.json()["detail"]

    def test_invalid_body_is_422(self, client):
        assert client.post("/api/security/explain", json={"session_id": "x"}).status_code == 422
