"""Semgrep adapter: static vulnerability detection with the bundled rules (spec §21)."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from backend.config import settings
from backend.security.models import Finding, ScannerStatus
from backend.security.normalizer import install_hint, semgrep_findings
from backend.security.runner import (
    ToolNotFoundError,
    ToolOutputTooLargeError,
    ToolTimeoutError,
    find_tool,
    read_report,
    run_tool,
)

logger = logging.getLogger(__name__)

RULES_DIR = Path(__file__).resolve().parent / "rules"
_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")
# Directories never worth scanning even if a repository ships them.
_EXCLUDES = ["node_modules", "vendor", "dist", "build", ".venv", "venv", "site-packages"]
# Semgrep must stay offline and quiet: no metrics, no version pings.
_ENV = {"SEMGREP_SEND_METRICS": "off", "SEMGREP_ENABLE_VERSION_CHECK": "0", "NO_COLOR": "1"}


def semgrep_version(executable: str, cwd: Path) -> str | None:
    try:
        result = run_tool([executable, "--version"], cwd=cwd, timeout_seconds=60, extra_env=_ENV)
    except ToolTimeoutError:
        return None
    match = _VERSION_RE.search(result.stdout or result.stderr)
    return match.group(1) if match else None


def run_semgrep(repo_dir: Path, work_dir: Path) -> tuple[list[Finding], ScannerStatus]:
    """Scan the repository; the status explains any reason no findings came back."""
    status = ScannerStatus(name="semgrep", available=False)
    try:
        executable = find_tool("semgrep", settings.semgrep_path)
    except ToolNotFoundError:
        status.error = "Semgrep is not installed."
        status.install_hint = install_hint("semgrep")
        return [], status
    status.available = True
    work_dir.mkdir(parents=True, exist_ok=True)
    status.version = semgrep_version(executable, work_dir)

    report_path = work_dir / "semgrep.json"
    args = [
        executable, "scan",
        "--json", "--output", str(report_path),
        "--metrics=off", "--disable-version-check", "--quiet",
        "--no-rewrite-rule-ids",
        "--timeout", "30", "--timeout-threshold", "3",
        "--max-target-bytes", str(settings.max_file_size_bytes),
        "--config", str(RULES_DIR),
    ]
    for config in _extra_configs():
        args += ["--config", config]
    for pattern in _EXCLUDES:
        args += ["--exclude", pattern]
    args.append(".")

    try:
        result = run_tool(args, cwd=repo_dir, timeout_seconds=settings.security_scan_timeout_seconds, extra_env=_ENV)
        status.duration_seconds = round(result.duration_seconds, 2)
        # Exit codes: 0 no findings, 1 findings, anything else is a failure
        # (2 = fatal error, 7 = invalid config, ...). Findings-in-report wins
        # over the exit code when the report parses.
        payload = json.loads(read_report(report_path, settings.security_report_limit_bytes))
        if not isinstance(payload, dict):
            raise ValueError("unexpected report shape")
        if result.returncode not in (0, 1) and not payload.get("results"):
            status.error = f"Semgrep exited with code {result.returncode}: {_first_error(payload) or result.stderr.strip()[-200:] or 'unknown error'}"
            return [], status
        findings = semgrep_findings(payload, repo_dir)
        status.ran = True
        status.findings = len(findings)
        errors = payload.get("errors") or []
        if errors:
            logger.info("Semgrep reported %d non-fatal error(s) (parse failures etc.)", len(errors))
        return findings, status
    except ToolTimeoutError as exc:
        status.error = f"{exc}. Raise CODEATLAS_SECURITY_SCAN_TIMEOUT_SECONDS for very large repositories."
    except ToolOutputTooLargeError as exc:
        status.error = str(exc)
    except (OSError, ValueError) as exc:
        logger.warning("Semgrep run failed: %s", exc.__class__.__name__)
        status.error = f"Semgrep did not produce a readable report ({exc.__class__.__name__})."
    finally:
        try:
            report_path.unlink(missing_ok=True)
        except OSError:
            pass
    return [], status


def _extra_configs() -> list[str]:
    return [c.strip() for c in settings.semgrep_extra_configs.split(",") if c.strip()]


def _first_error(payload: dict) -> str | None:
    for error in payload.get("errors") or []:
        message = error.get("message") if isinstance(error, dict) else None
        if isinstance(message, str) and message.strip():
            return message.strip().split("\n")[0][:200]
    return None
