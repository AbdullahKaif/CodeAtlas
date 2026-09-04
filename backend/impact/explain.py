"""AI-assisted impact explanation (spec §26): what a change may break, from evidence.

The static result is the input: the local model sees the target's code and
the code of its nearest dependents, never the whole repository, and every
reference it makes is validated. Cached per target and depth inside the
session's analysis directory.
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from backend.impact.analyzer import ImpactResult
from backend.knowledge.store import KnowledgeIndex
from backend.llm.ollama_client import LLMClient, get_llm_client
from backend.parser.models import Entity
from backend.rag.models import RetrievedChunk
from backend.rag.prompts import build_context
from backend.rag.sources import SourceReference, validate_answer

logger = logging.getLogger(__name__)

_TARGET_MAX_LINES = 120
_DEPENDENT_MAX_LINES = 40
_MAX_DEPENDENTS_SHOWN = 6

IMPACT_SYSTEM_PROMPT = """You are CodeAtlas's change-impact reviewer. Static analysis of ONE repository found which code depends on a target the developer wants to change. Using ONLY the code excerpts provided, explain the likely consequences of changing the target.

Rules:
1. Ground every statement in the excerpts: name the dependent functions, classes and files that appear in them. Never invent callers, files or behaviour.
2. Static analysis is not runtime truth: dynamic dispatch, reflection and configuration may add dependents the excerpts do not show. Say so where relevant.
3. Structure the answer in Markdown with exactly these headings:
## What depends on it
## Likely consequences of a change
## What to check before changing it
## Tests to run
4. Keep it concise and concrete. If the excerpts show no dependents, say that the change appears isolated as far as static analysis can tell.
5. End with a "Sources:" section listing the excerpts you relied on, one per line, exactly as:
   - <file path>: lines <start>-<end>
"""


class ImpactExplanation(BaseModel):
    target: str
    depth: int
    explanation: str
    sources: list[SourceReference] = Field(default_factory=list)
    context: list[RetrievedChunk] = Field(default_factory=list)
    references_removed: int = 0
    model: str
    cached: bool = False
    generated_at: str
    duration_seconds: float
    note: str = "AI-assisted reading of a static impact analysis - review against the code before relying on it."


def explain_impact(
    session_dir: Path,
    index: KnowledgeIndex,
    result: ImpactResult,
    refresh: bool = False,
    llm: LLMClient | None = None,
) -> ImpactExplanation:
    cache = _cache_path(session_dir, result)
    if not refresh:
        cached = _load_cached(cache)
        if cached is not None:
            return cached

    started = time.monotonic()
    llm = llm or get_llm_client()
    chunks = _evidence(index, result)
    context_text, shown = build_context(chunks)
    raw = llm.generate(_prompt(result, context_text), system=IMPACT_SYSTEM_PROMPT)
    validated = validate_answer(raw, shown, index.entities)
    explanation = ImpactExplanation(
        target=result.target.id,
        depth=result.depth,
        explanation=validated.answer or raw.strip(),
        sources=validated.sources,
        context=shown,
        references_removed=validated.references_removed,
        model=llm.name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        duration_seconds=round(time.monotonic() - started, 2),
    )
    _store(cache, explanation)
    return explanation


def _evidence(index: KnowledgeIndex, result: ImpactResult) -> list[RetrievedChunk]:
    chunks: list[RetrievedChunk] = []
    target = index.entity(result.target.id)
    if target is not None:
        chunk = entity_chunk(index, target, "target", _TARGET_MAX_LINES)
        if chunk is not None:
            chunks.append(chunk)
    # Nearest dependents first; production code before tests.
    for affected in result.affected[: _MAX_DEPENDENTS_SHOWN * 2]:
        if len(chunks) > _MAX_DEPENDENTS_SHOWN:
            break
        entity = index.entity(affected.id)
        if entity is None or entity.type == "file":
            continue
        chunk = entity_chunk(index, entity, f"dependent-{len(chunks)}", _DEPENDENT_MAX_LINES)
        if chunk is not None:
            chunks.append(chunk)
    return chunks


def entity_chunk(index: KnowledgeIndex, entity: Entity, chunk_id: str, max_lines: int) -> RetrievedChunk | None:
    """An entity's source as a citable chunk, truncated on line boundaries when long."""
    text = entity.source_code
    if not text:
        text = _file_slice(index, entity)
    if not text:
        return None
    lines = text.split("\n")[:max_lines]
    return RetrievedChunk(
        chunk_id=chunk_id,
        file=entity.file,
        symbol=entity.id.split("::", 1)[1] if "::" in entity.id else None,
        entity_id=entity.id,
        type=entity.type if entity.type in {"function", "method", "class"} else "module",
        start_line=entity.start_line,
        end_line=entity.start_line + len(lines) - 1,
        text="\n".join(lines),
        score=1.0,
    )


def _file_slice(index: KnowledgeIndex, entity: Entity) -> str | None:
    return None  # files carry no source_code by design; dependents that are files are skipped


def _prompt(result: ImpactResult, context_text: str) -> str:
    counts = result.counts
    lines = [
        f"Target: {result.target.id} ({result.target.type} in {result.target.file}, lines {result.target.start_line}-{result.target.end_line})",
        f"Static impact level: {result.level}. Reasons: {'; '.join(result.reasons)}.",
        f"Direct dependents: {counts.callers} callers, {counts.importers} importers, {counts.subclasses} subclasses; "
        f"{counts.transitive} transitive dependents across {counts.files} files; {counts.tests} test files.",
    ]
    if result.affected:
        listed = ", ".join(f"{a.id} ({a.via}, depth {a.depth})" for a in result.affected[:12])
        lines.append(f"Dependents found statically: {listed}.")
    lines.append(f"\nCode excerpts (block [1] is the target):\n\n{context_text}\n\nExplain the likely impact of changing the target.")
    return "\n".join(lines)


def _cache_path(session_dir: Path, result: ImpactResult) -> Path:
    key = hashlib.sha1(f"{result.target.id}|{result.depth}".encode("utf-8")).hexdigest()[:16]
    return session_dir / "analysis" / "ai" / f"impact-{key}.json"


def _load_cached(path: Path) -> ImpactExplanation | None:
    try:
        cached = ImpactExplanation.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cached.cached = True
    return cached


def _store(path: Path, result: ImpactExplanation) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        logger.warning("Could not cache impact explanation under %s", path.parent)
