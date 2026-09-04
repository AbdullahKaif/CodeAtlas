"""Unified security engine: run every scanner, normalize, persist, load (spec §21-22).

Scanners are optional components: a missing or failing tool is reported in
the scanner status, never raised, so the analysis and the rest of the report
survive it (spec §42).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from backend.config import settings
from backend.security.gitleaks import run_gitleaks
from backend.security.models import (
    SEVERITY_ORDER,
    Finding,
    ScannerStatus,
    SecurityOverview,
    SecurityReport,
    SecuritySummary,
)
from backend.security.normalizer import merge_and_number
from backend.security.semgrep import run_semgrep

logger = logging.getLogger(__name__)

REPORT_FILE = "findings.json"
ProgressCallback = Callable[[str], None]


def run_security_scan(
    session_id: str, repo_dir: Path, progress: ProgressCallback | None = None
) -> SecurityReport:
    """Scan one session's repository with every available scanner."""
    security_dir = settings.session_dir(session_id) / "security"
    security_dir.mkdir(parents=True, exist_ok=True)
    findings: list[Finding] = []
    statuses: list[ScannerStatus] = []

    for label, scanner in (("semgrep", run_semgrep), ("gitleaks", run_gitleaks)):
        if progress is not None:
            progress(f"running {label}")
        try:
            tool_findings, status = scanner(repo_dir, security_dir)
        except Exception:  # defensive: a scanner adapter bug must not sink the analysis
            logger.exception("%s adapter crashed", label)
            status = ScannerStatus(name=label, available=True, error=f"{label} crashed unexpectedly.")  # type: ignore[arg-type]
            tool_findings = []
        findings.extend(tool_findings)
        statuses.append(status)
        logger.info(
            "Session %s: %s %s (%d findings)",
            session_id, label, "ran" if status.ran else f"skipped: {status.error}", status.findings,
        )

    merged, truncated = merge_and_number(findings, settings.security_max_findings)
    report = SecurityReport(
        session_id=session_id,
        scanned_at=datetime.now(timezone.utc).isoformat(),
        scanners=statuses,
        summary=summarize(merged),
        findings=merged,
        truncated=truncated,
    )
    write_report(security_dir, report)
    return report


def summarize(findings: list[Finding]) -> SecuritySummary:
    by_severity = {name: 0 for name in SEVERITY_ORDER}
    for finding in findings:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
    return SecuritySummary(
        total=len(findings),
        by_severity=by_severity,
        vulnerabilities=sum(1 for f in findings if f.category == "vulnerability"),
        secrets=sum(1 for f in findings if f.category == "secret"),
    )


def overview_of(report: SecurityReport) -> SecurityOverview:
    return SecurityOverview(summary=report.summary, scanners=report.scanners)


def write_report(security_dir: Path, report: SecurityReport) -> None:
    security_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = security_dir / (REPORT_FILE + ".tmp")
    tmp_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp_path, security_dir / REPORT_FILE)


def load_report(session_dir: Path) -> SecurityReport | None:
    path = session_dir / "security" / REPORT_FILE
    try:
        return SecurityReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
