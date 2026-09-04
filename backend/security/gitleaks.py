"""Gitleaks adapter: exposed-secret detection over the checked-out files (spec §21).

The report is written inside the session, parsed, redacted and deleted at
once: raw Gitleaks output contains the secret values and must never persist.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from backend.config import settings
from backend.security.models import Finding, ScannerStatus
from backend.security.normalizer import gitleaks_findings, install_hint
from backend.security.runner import (
    ToolNotFoundError,
    ToolOutputTooLargeError,
    ToolTimeoutError,
    find_tool,
    read_report,
    run_tool,
)

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")
_ENV = {"NO_COLOR": "1"}


def gitleaks_version(executable: str, cwd: Path) -> str | None:
    try:
        result = run_tool([executable, "version"], cwd=cwd, timeout_seconds=30, extra_env=_ENV)
    except ToolTimeoutError:
        return None
    match = _VERSION_RE.search(result.stdout or result.stderr)
    return match.group(1) if match else None


def run_gitleaks(repo_dir: Path, work_dir: Path) -> tuple[list[Finding], ScannerStatus]:
    status = ScannerStatus(name="gitleaks", available=False)
    try:
        executable = find_tool("gitleaks", settings.gitleaks_path)
    except ToolNotFoundError:
        status.error = "Gitleaks is not installed."
        status.install_hint = install_hint("gitleaks")
        return [], status
    status.available = True
    work_dir.mkdir(parents=True, exist_ok=True)
    status.version = gitleaks_version(executable, work_dir)

    report_path = work_dir / "gitleaks.json"
    common = [
        "--report-format", "json", "--report-path", str(report_path),
        "--exit-code", "0", "--no-banner", "--no-color",
    ]
    # Gitleaks >= 8.19 scans a directory with `dir`; older releases use
    # `detect --no-git`. The working tree is what matters here (shallow clone).
    invocations = [
        [executable, "dir", ".", *common],
        [executable, "detect", "--no-git", "--source", ".", *common],
    ]
    try:
        result = None
        for args in invocations:
            result = run_tool(args, cwd=repo_dir, timeout_seconds=settings.security_scan_timeout_seconds, extra_env=_ENV)
            if result.returncode == 0 and report_path.is_file():
                break
        status.duration_seconds = round(result.duration_seconds, 2) if result else None
        if result is None or result.returncode != 0 or not report_path.is_file():
            status.error = f"Gitleaks exited with code {result.returncode if result else '?'}: {(result.stderr.strip()[-200:] if result else '') or 'unknown error'}"
            return [], status
        payload = json.loads(read_report(report_path, settings.security_report_limit_bytes) or "[]")
        if payload is None:
            payload = []
        if not isinstance(payload, list):
            raise ValueError("unexpected report shape")
        findings = gitleaks_findings(payload, repo_dir)
        status.ran = True
        status.findings = len(findings)
        return findings, status
    except ToolTimeoutError as exc:
        status.error = f"{exc}. Raise CODEATLAS_SECURITY_SCAN_TIMEOUT_SECONDS for very large repositories."
    except ToolOutputTooLargeError as exc:
        status.error = str(exc)
    except (OSError, ValueError) as exc:
        logger.warning("Gitleaks run failed: %s", exc.__class__.__name__)
        status.error = f"Gitleaks did not produce a readable report ({exc.__class__.__name__})."
    finally:
        # The raw report holds secret values: remove it no matter what happened.
        try:
            report_path.unlink(missing_ok=True)
        except OSError:
            pass
    return [], status
