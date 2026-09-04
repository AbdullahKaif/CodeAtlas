"""AI explanation and fix suggestions for scanner findings (spec §23-24).

The finding itself is a fact produced by a deterministic scanner; the local
LLM only explains it. Both operations follow the RAG contract: the model sees
the flagged region (secrets redacted) plus a few retrieved related chunks,
never the whole repository, and every citation it makes is validated. Results
are cached inside the session so re-opening a finding is instant, and they
disappear with the session.
"""
from __future__ import annotations

import difflib
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from backend.config import settings
from backend.knowledge.serializer import load_entities
from backend.llm.ollama_client import LLMClient, get_llm_client
from backend.parser.models import Entity
from backend.rag.embeddings import EmbeddingError
from backend.rag.models import RetrievedChunk
from backend.rag.prompts import build_context
from backend.rag.retriever import retrieve
from backend.rag.sources import SourceReference, validate_answer
from backend.rag.vector_store import VectorStoreError
from backend.security.models import Finding, SecurityReport
from backend.security.normalizer import redact_literals, redact_span

logger = logging.getLogger(__name__)

DISCLAIMER = "AI-generated suggestion - review before applying."
_MAX_REGION_LINES = 80
_WINDOW_LINES = 12
_RELATED_TOP_K = 4
_FENCE = re.compile(r"```[\w+-]*\n(.*?)```", re.DOTALL)
_SIDE_EFFECTS = re.compile(r"^\s*\**\s*(?:potential\s+)?side[ -]effects?\s*:?\s*\**\s*:?\s*$", re.IGNORECASE | re.MULTILINE)

EXPLAIN_SYSTEM_PROMPT = """You are CodeAtlas's application-security reviewer. A deterministic scanner (Semgrep or Gitleaks) reported a finding. The finding is a fact; your job is to explain it using ONLY the code excerpts provided.

Rules:
1. Use careful, evidence-bound language: "The scanner detected ...", "This code appears vulnerable because ...", "Potential impact includes ...". Never claim the issue is exploitable unless the excerpts show untrusted input reaching the flagged code; otherwise say what would need to be true.
2. Do not invent code, files or behaviour that the excerpts do not show. When something is unknown (for example whether a value is user-controlled), say so.
3. Static analysis is not runtime truth: describe what the code appears to do.
4. [REDACTED] marks a secret value removed on purpose. Never guess or reconstruct it.
5. Write Markdown with exactly these headings, in this order:
## What the scanner detected
## Why it matters
## Potential impact
## Data flow
## Recommended remediation
6. Under "Recommended remediation" include a short corrected code example when practical.
7. End with a "Sources:" section listing the excerpts you relied on, one per line, exactly as:
   - <file path>: lines <start>-<end>
"""

FIX_SYSTEM_PROMPT = """You are CodeAtlas's security fix assistant. Propose a minimal, correct code change for ONE scanner finding, using only the provided code excerpts.

Output exactly this structure:
1. A short explanation of the change (2-5 sentences).
2. ONE fenced code block containing the complete corrected version of the flagged region: the whole region exactly as given, with only the necessary edits, same indentation, no line numbers, nothing omitted or abbreviated.
3. A line "Side effects:" followed by the behavioural changes, new dependencies, or call-site updates the change requires.

Rules: never change unrelated code; keep the public signature unless the fix requires otherwise; [REDACTED] marks a removed secret value - replace such literals with a lookup from the environment or a secrets manager, never guess the value.
"""


class SecurityExplanation(BaseModel):
    finding: Finding
    explanation: str  # Markdown, headings as in the prompt
    sources: list[SourceReference] = Field(default_factory=list)
    context: list[RetrievedChunk] = Field(default_factory=list)
    references_removed: int = 0
    model: str
    cached: bool = False
    generated_at: str
    duration_seconds: float


class SecurityFix(BaseModel):
    finding: Finding
    explanation: str
    suggested_code: str  # the corrected region; empty when the model produced no code block
    diff: str  # unified diff against the file; empty when there is nothing to diff
    side_effects: str
    region_start_line: int
    region_end_line: int
    disclaimer: str = DISCLAIMER
    model: str
    cached: bool = False
    generated_at: str
    duration_seconds: float


class FindingNotFoundError(Exception):
    pass


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def explain_finding(
    session_id: str,
    session_dir: Path,
    report: SecurityReport,
    finding_id: str,
    refresh: bool = False,
    llm: LLMClient | None = None,
) -> SecurityExplanation:
    finding = _find(report, finding_id)
    cache = _cache_path(session_dir, finding, "explanation")
    if not refresh:
        cached = _load_cached(cache, SecurityExplanation)
        if cached is not None:
            return cached

    started = time.monotonic()
    llm = llm or get_llm_client()
    entities = _entities(session_dir)
    region = _flagged_region(session_dir, report, finding, entities)
    context = [region] + _related_chunks(session_dir, finding, region, entities)
    context_text, shown = build_context(context)
    raw = llm.generate(_explain_prompt(finding, context_text), system=EXPLAIN_SYSTEM_PROMPT)
    validated = validate_answer(raw, shown, entities)
    result = SecurityExplanation(
        finding=finding,
        explanation=validated.answer or raw.strip(),
        sources=validated.sources,
        context=shown,
        references_removed=validated.references_removed,
        model=llm.name,
        generated_at=_now(),
        duration_seconds=round(time.monotonic() - started, 2),
    )
    _store(cache, result)
    return result


def suggest_fix(
    session_id: str,
    session_dir: Path,
    report: SecurityReport,
    finding_id: str,
    refresh: bool = False,
    llm: LLMClient | None = None,
) -> SecurityFix:
    finding = _find(report, finding_id)
    cache = _cache_path(session_dir, finding, "fix")
    if not refresh:
        cached = _load_cached(cache, SecurityFix)
        if cached is not None:
            return cached

    started = time.monotonic()
    llm = llm or get_llm_client()
    entities = _entities(session_dir)
    region = _flagged_region(session_dir, report, finding, entities)
    related = _related_chunks(session_dir, finding, region, entities)
    context_text, _ = build_context([region] + related)
    raw = llm.generate(_fix_prompt(finding, region, context_text), system=FIX_SYSTEM_PROMPT)
    explanation, code, side_effects = _parse_fix(raw)
    diff = _unified_diff(session_dir, report, finding, region, code) if code else ""
    result = SecurityFix(
        finding=finding,
        explanation=explanation,
        suggested_code=code,
        diff=diff,
        side_effects=side_effects,
        region_start_line=region.start_line,
        region_end_line=region.end_line,
        model=llm.name,
        generated_at=_now(),
        duration_seconds=round(time.monotonic() - started, 2),
    )
    _store(cache, result)
    return result


# ---------------------------------------------------------------------------
# Evidence assembly
# ---------------------------------------------------------------------------

def _find(report: SecurityReport, finding_id: str) -> Finding:
    for finding in report.findings:
        if finding.id == finding_id or finding.fingerprint == finding_id:
            return finding
    raise FindingNotFoundError(finding_id)


def _entities(session_dir: Path) -> list[Entity]:
    try:
        return load_entities(session_dir / "analysis")
    except (OSError, ValueError):
        return []


def _flagged_region(
    session_dir: Path, report: SecurityReport, finding: Finding, entities: list[Entity]
) -> RetrievedChunk:
    """The code around the finding, secrets redacted, as a citable chunk."""
    lines = _file_lines(session_dir, finding.file)
    total = len(lines)
    owner = _enclosing_entity(finding, entities)
    if owner is not None and owner.end_line - owner.start_line + 1 <= _MAX_REGION_LINES:
        start, end = owner.start_line, owner.end_line
    else:
        start = max(1, finding.line - _WINDOW_LINES)
        end = min(total or finding.line, (finding.end_line or finding.line) + _WINDOW_LINES)
    start, end = max(1, start), max(start, min(end, total or end))

    redact_lines = {
        f.line: f for f in report.findings if f.file == finding.file and f.category == "secret"
    }
    rendered: list[str] = []
    for number in range(start, end + 1):
        text = lines[number - 1] if number - 1 < total else ""
        secret_finding = redact_lines.get(number)
        if secret_finding is not None:
            text = _redact_line(text, secret_finding)
        rendered.append(text)

    symbol = owner.id.split("::", 1)[1] if owner is not None and "::" in owner.id else None
    return RetrievedChunk(
        chunk_id="flagged-region",
        file=finding.file,
        symbol=symbol,
        entity_id=owner.id if owner is not None else None,
        type=owner.type if owner is not None and owner.type in {"function", "method", "class"} else "module",
        start_line=start,
        end_line=end,
        text="\n".join(rendered),
        score=1.0,
    )


def _redact_line(text: str, finding: Finding) -> str:
    if finding.source == "gitleaks":
        return redact_span(text, finding.column, finding.end_column)
    return redact_literals(text)


def _enclosing_entity(finding: Finding, entities: list[Entity]) -> Entity | None:
    candidates = [
        e for e in entities
        if e.file == finding.file and e.type in {"function", "method", "class"}
        and e.start_line <= finding.line <= e.end_line
    ]
    if not candidates:
        return None
    # Innermost callable first; a class only when nothing smaller contains the line.
    candidates.sort(key=lambda e: (e.type == "class", e.end_line - e.start_line))
    return candidates[0]


def _related_chunks(
    session_dir: Path, finding: Finding, region: RetrievedChunk, entities: list[Entity]
) -> list[RetrievedChunk]:
    """A few semantically related chunks, minus anything overlapping the region."""
    query = f"{finding.type}: {finding.message} ({finding.file}"
    if region.symbol:
        query += f", {region.symbol}"
    query += ")"
    try:
        retrieved = retrieve(session_dir, query, top_k=_RELATED_TOP_K + 2)
    except (VectorStoreError, EmbeddingError):
        return []  # no index: the flagged region alone is still real evidence
    except OSError:
        return []
    related = [
        c for c in retrieved
        if not (c.file == region.file and c.start_line <= region.end_line and region.start_line <= c.end_line)
    ]
    return related[:_RELATED_TOP_K]


def _file_lines(session_dir: Path, file: str) -> list[str]:
    try:
        return (session_dir / "repository" / file).read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return []


# ---------------------------------------------------------------------------
# Prompts and parsing
# ---------------------------------------------------------------------------

def _finding_header(finding: Finding) -> str:
    lines = [
        f"Finding {finding.id}: {finding.type} (severity {finding.severity}, reported by {finding.source}, rule {finding.rule})",
        f"Scanner message: {finding.message}",
        f"Location: {finding.file}: line {finding.line}",
    ]
    if finding.cwe:
        lines.append("CWE: " + "; ".join(finding.cwe))
    if finding.owasp:
        lines.append("OWASP: " + "; ".join(finding.owasp))
    return "\n".join(lines)


def _explain_prompt(finding: Finding, context_text: str) -> str:
    return (
        f"{_finding_header(finding)}\n\n"
        f"Code excerpts (block [1] is the flagged region):\n\n{context_text}\n\n"
        f"Explain this finding."
    )


def _fix_prompt(finding: Finding, region: RetrievedChunk, context_text: str) -> str:
    return (
        f"{_finding_header(finding)}\n\n"
        f"The flagged region is block [1] ({region.file}: lines {region.start_line}-{region.end_line}); "
        f"return the complete corrected version of exactly that region.\n\n"
        f"Code excerpts:\n\n{context_text}\n\n"
        f"Propose the fix."
    )


def _parse_fix(raw: str) -> tuple[str, str, str]:
    match = _FENCE.search(raw)
    if match is None:
        explanation, side_effects = _split_side_effects(raw)
        return explanation.strip(), "", side_effects
    code = match.group(1).rstrip("\n")
    before = raw[: match.start()].strip()
    after = raw[match.end():].strip()
    explanation, pre_side = _split_side_effects(before)
    _, post_side = _split_side_effects(after) if _SIDE_EFFECTS.search(after) else ("", after)
    side_effects = (pre_side + "\n" + post_side).strip()
    return explanation.strip(), code, side_effects


def _split_side_effects(text: str) -> tuple[str, str]:
    match = _SIDE_EFFECTS.search(text)
    if match is None:
        return text, ""
    return text[: match.start()].strip(), text[match.end():].strip()


def _unified_diff(
    session_dir: Path, report: SecurityReport, finding: Finding, region: RetrievedChunk, code: str
) -> str:
    original = _file_lines(session_dir, finding.file)
    if not original:
        return ""
    # Diff against the redacted view of the file so a secret can never leak
    # through the "before" side of a suggestion.
    secret_lines = {f.line: f for f in report.findings if f.file == finding.file and f.category == "secret"}
    before = [
        _redact_line(text, secret_lines[n]) if n in secret_lines else text
        for n, text in enumerate(original, start=1)
    ]
    after = before[: region.start_line - 1] + code.split("\n") + before[region.end_line:]
    diff = difflib.unified_diff(
        before, after, fromfile=f"a/{finding.file}", tofile=f"b/{finding.file}", lineterm="", n=3
    )
    return "\n".join(diff)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_path(session_dir: Path, finding: Finding, kind: str) -> Path:
    return session_dir / "security" / "ai" / f"{finding.fingerprint}.{kind}.json"


def _load_cached(path: Path, model_type):
    try:
        cached = model_type.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cached.cached = True
    return cached


def _store(path: Path, result: BaseModel) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        logger.warning("Could not cache AI result under %s", path.parent)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
