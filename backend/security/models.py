"""Unified security model (spec §22).

Every Finding originates from a deterministic scanner (Semgrep or Gitleaks);
AI only ever explains findings, it never creates them. Secret values are never
stored: a secret finding carries the redacted line, the column span of the
secret, and nothing else.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
SEVERITY_ORDER: dict[str, int] = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}

ScannerName = Literal["semgrep", "gitleaks"]
FindingCategory = Literal["vulnerability", "secret"]

REDACTED = "[REDACTED]"


class Finding(BaseModel):
    id: str  # SEC-001 ... ordered by severity, then file and line, within one scan
    fingerprint: str  # stable across re-scans of the same repository state
    severity: Severity
    category: FindingCategory
    type: str  # human-readable class: "SQL Injection", "AWS Access Key"
    file: str  # POSIX path relative to the repository root
    line: int  # 1-based
    end_line: int | None = None
    column: int | None = None  # 1-based; for secrets: where the redacted value starts
    end_column: int | None = None
    source: ScannerName
    rule: str
    message: str
    code_context: str | None = None  # the flagged lines, secrets redacted
    cwe: list[str] = Field(default_factory=list)
    owasp: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)


class ScannerStatus(BaseModel):
    name: ScannerName
    available: bool
    version: str | None = None
    ran: bool = False
    findings: int = 0
    duration_seconds: float | None = None
    error: str | None = None  # user-facing: not installed, timed out, crashed
    install_hint: str | None = None


class SecuritySummary(BaseModel):
    total: int = 0
    by_severity: dict[str, int] = Field(default_factory=dict)  # CRITICAL/HIGH/MEDIUM/LOW/INFO
    vulnerabilities: int = 0
    secrets: int = 0


class SecurityReport(BaseModel):
    session_id: str
    scanned_at: str
    scanners: list[ScannerStatus]
    summary: SecuritySummary
    findings: list[Finding]
    truncated: bool = False  # more findings than the configured cap were reported


class SecurityOverview(BaseModel):
    """The slice of the report embedded in the analysis overview."""

    summary: SecuritySummary
    scanners: list[ScannerStatus]
