"""Turn raw scanner output into the unified Finding model, with redaction (spec §21-22).

Secret values never survive normalization: Gitleaks' ``Secret``/``Match``
fields are used only to locate and mask the value in the flagged line, then
dropped. Semgrep findings that a rule marks as credential-related get their
string literals masked too.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from backend.security.models import REDACTED, SEVERITY_ORDER, Finding, Severity

_SEMGREP_SEVERITY: dict[str, Severity] = {"ERROR": "HIGH", "WARNING": "MEDIUM", "INFO": "LOW"}
MAX_CONTEXT_LINES = 12  # a finding's code_context never exceeds this many lines
_VALID_SEVERITIES = set(SEVERITY_ORDER)
_STRING_LITERAL = re.compile(r"""(?P<q>["'])(?P<body>(?:(?!(?P=q)).){4,}?)(?P=q)""")
_INSTALL_HINTS = {
    "semgrep": "Install Semgrep with `pip install semgrep` (or `pipx install semgrep`) and make sure it is on PATH.",
    "gitleaks": "Install Gitleaks from https://github.com/gitleaks/gitleaks/releases (or `brew install gitleaks`) and make sure it is on PATH.",
}


def install_hint(tool: str) -> str:
    return _INSTALL_HINTS.get(tool, f"Install {tool} and make sure it is on PATH.")


# ---------------------------------------------------------------------------
# Semgrep
# ---------------------------------------------------------------------------

def semgrep_findings(payload: dict, repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for result in payload.get("results", []) or []:
        try:
            finding = _semgrep_one(result, repo_root)
        except (KeyError, TypeError, ValueError):
            continue  # one malformed result must not sink the report
        if finding is not None:
            findings.append(finding)
    return findings


def _semgrep_one(result: dict, repo_root: Path) -> Finding | None:
    extra = result.get("extra") or {}
    if extra.get("is_ignored"):
        return None
    metadata = extra.get("metadata") or {}
    file = _normalize_path(str(result["path"]), repo_root)
    line = int(result["start"]["line"])
    end_line = int(result.get("end", {}).get("line") or line)
    rule = str(result["check_id"])
    is_secret = str(metadata.get("category", "")).lower() == "secret" or _looks_like_secret_rule(rule)

    severity = _SEMGREP_SEVERITY.get(str(extra.get("severity", "")).upper(), "MEDIUM")
    declared = str(metadata.get("severity", "")).upper()
    if declared in _VALID_SEVERITIES:
        severity = declared  # type: ignore[assignment]

    # Recent Semgrep releases withhold the matched source in JSON output
    # ("requires login"), so the flagged lines are read from the clone instead.
    code = extra.get("lines")
    code_context = _clean_lines(code) if isinstance(code, str) else None
    if code_context is None:
        code_context = _source_lines(repo_root, file, line, end_line)
    if code_context and is_secret:
        code_context = redact_literals(code_context)

    return Finding(
        id="",  # assigned once the whole report is sorted
        fingerprint=fingerprint("semgrep", rule, file, line),
        severity=severity,
        category="secret" if is_secret else "vulnerability",
        type=_semgrep_title(rule, metadata),
        file=file,
        line=line,
        end_line=end_line,
        column=_int_or_none(result.get("start", {}).get("col")),
        end_column=_int_or_none(result.get("end", {}).get("col")),
        source="semgrep",
        rule=rule,
        message=redact_literals(str(extra.get("message", "")).strip()) if is_secret else str(extra.get("message", "")).strip(),
        code_context=code_context,
        cwe=_str_list(metadata.get("cwe")),
        owasp=_str_list(metadata.get("owasp")),
        references=_str_list(metadata.get("references")),
    )


def _semgrep_title(rule: str, metadata: dict) -> str:
    for key in ("title", "vulnerability_class"):
        value = metadata.get(key)
        if isinstance(value, list) and value:
            value = value[0]
        if isinstance(value, str) and value.strip():
            return value.strip()
    tail = rule.rsplit(".", 1)[-1]
    return tail.replace("-", " ").replace("_", " ").strip().capitalize()


def _looks_like_secret_rule(rule: str) -> bool:
    lowered = rule.lower()
    return any(token in lowered for token in ("hardcoded", "hard-coded", "secret", "credential", "password"))


# ---------------------------------------------------------------------------
# Gitleaks
# ---------------------------------------------------------------------------

def gitleaks_findings(payload: list, repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for leak in payload or []:
        try:
            findings.append(_gitleaks_one(leak, repo_root))
        except (KeyError, TypeError, ValueError):
            continue
    return _dedupe_secrets(findings)


def _gitleaks_one(leak: dict, repo_root: Path) -> Finding:
    file = _normalize_path(str(leak["File"]), repo_root)
    line = int(leak["StartLine"])
    end_line = int(leak.get("EndLine") or line)
    rule = str(leak.get("RuleID") or "secret")
    secret = str(leak.get("Secret") or "")
    match = str(leak.get("Match") or "")
    column = _int_or_none(leak.get("StartColumn"))
    end_column = _int_or_none(leak.get("EndColumn"))

    code_context = _redacted_source_lines(repo_root, file, line, end_line, secret or match, column, end_column)
    return Finding(
        id="",
        fingerprint=fingerprint("gitleaks", rule, file, line),
        severity="HIGH",
        category="secret",
        type=_gitleaks_title(rule, str(leak.get("Description") or "")),
        file=file,
        line=line,
        end_line=end_line,
        column=column,
        end_column=end_column,
        source="gitleaks",
        rule=rule,
        message=_scrub(str(leak.get("Description") or "Possible secret detected."), secret, match),
        code_context=code_context,
        cwe=["CWE-798: Use of Hard-coded Credentials"],
        owasp=["A07:2021 - Identification and Authentication Failures"],
        references=[],
    )


def _gitleaks_title(rule: str, description: str) -> str:
    # Rule ids are the concise names ("aws-access-token"); descriptions are sentences.
    words = rule.replace("-", " ").replace("_", " ").split()
    if not words:
        return description[:60] or "Secret"
    acronyms = {"aws", "gcp", "api", "jwt", "pat", "ssh", "pgp", "rsa", "dsa", "ec", "npm", "url", "oauth"}
    return " ".join(w.upper() if w in acronyms else w.capitalize() for w in words)


def _dedupe_secrets(findings: list[Finding]) -> list[Finding]:
    """One secret finding per file+line; a specific rule beats the generic one."""
    best: dict[tuple[str, int], Finding] = {}
    for finding in findings:
        key = (finding.file, finding.line)
        current = best.get(key)
        if current is None or (current.rule.startswith("generic") and not finding.rule.startswith("generic")):
            best[key] = finding
    return list(best.values())


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def merge_and_number(findings: list[Finding], max_findings: int) -> tuple[list[Finding], bool]:
    """Cross-scanner dedupe, severity ordering, SEC-nnn ids and the cap."""
    secret_lines = {(f.file, f.line) for f in findings if f.source == "gitleaks"}
    merged = [
        f
        for f in findings
        if not (f.source == "semgrep" and f.category == "secret" and (f.file, f.line) in secret_lines)
    ]
    merged.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.file, f.line, f.source, f.rule))
    truncated = len(merged) > max_findings
    merged = merged[:max_findings]
    for index, finding in enumerate(merged, start=1):
        finding.id = f"SEC-{index:03d}"
    return merged, truncated


def fingerprint(source: str, rule: str, file: str, line: int) -> str:
    return hashlib.sha1(f"{source}|{rule}|{file}|{line}".encode("utf-8")).hexdigest()[:16]


def redact_literals(text: str) -> str:
    """Mask the contents of every string literal of 4+ characters."""
    return _STRING_LITERAL.sub(lambda m: f"{m.group('q')}{REDACTED}{m.group('q')}", text)


def redact_span(line: str, column: int | None, end_column: int | None, secret: str = "") -> str:
    """Mask a secret in one source line: by exact value when known, else by column span."""
    if secret and secret in line:
        return line.replace(secret, REDACTED)
    if column is not None and end_column is not None and 1 <= column <= len(line):
        return line[: column - 1] + REDACTED + line[end_column:]
    return redact_literals(line)


def _redacted_source_lines(
    repo_root: Path, file: str, line: int, end_line: int, secret: str, column: int | None, end_column: int | None
) -> str | None:
    try:
        lines = (repo_root / file).read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return None
    selected = lines[line - 1 : end_line]
    if not selected:
        return None
    if len(selected) == 1:
        return redact_span(selected[0], column, end_column, secret).strip()
    return "\n".join(l.replace(secret, REDACTED) if secret else redact_literals(l) for l in selected).strip()


def _source_lines(repo_root: Path, file: str, line: int, end_line: int) -> str | None:
    try:
        lines = (repo_root / file).read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return None
    selected = lines[line - 1 : min(end_line, line + MAX_CONTEXT_LINES - 1)]
    return "\n".join(selected).rstrip() or None


def _scrub(text: str, *values: str) -> str:
    for value in values:
        if value and len(value) >= 4:
            text = text.replace(value, REDACTED)
    return text


def _normalize_path(path: str, repo_root: Path) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(repo_root.resolve())
        except ValueError:
            candidate = Path(candidate.name)
    posix = candidate.as_posix()
    return posix[2:] if posix.startswith("./") else posix


def _clean_lines(code: str) -> str | None:
    if code.strip() == "requires login":  # Semgrep's placeholder when it withholds source
        return None
    return code.rstrip()


def _str_list(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, (str, int))]
    return []


def _int_or_none(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
