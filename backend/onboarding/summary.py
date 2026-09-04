"""AI repository summary (spec §32): one grounded answer, cached inside the session."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from backend.llm.ollama_client import LLMClient
from backend.rag.models import RetrievedChunk
from backend.rag.pipeline import answer_question
from backend.rag.sources import SourceReference

logger = logging.getLogger(__name__)

SUMMARY_QUESTION = (
    "What does this project do, and how is it organised? Summarise it for a developer "
    "joining the team: purpose, main components, and how they fit together."
)


class RepositorySummary(BaseModel):
    summary: str
    sources: list[SourceReference] = Field(default_factory=list)
    context: list[RetrievedChunk] = Field(default_factory=list)
    references_removed: int = 0
    model: str
    cached: bool = False
    generated_at: str


def summarize_repository(
    session_id: str, session_dir: Path, refresh: bool = False, llm: LLMClient | None = None
) -> RepositorySummary:
    cache = session_dir / "analysis" / "ai" / "summary.json"
    if not refresh:
        try:
            cached = RepositorySummary.model_validate_json(cache.read_text(encoding="utf-8"))
            cached.cached = True
            return cached
        except (OSError, ValueError):
            pass
    answer = answer_question(session_id, session_dir, SUMMARY_QUESTION, top_k=10, llm=llm)
    result = RepositorySummary(
        summary=answer.answer,
        sources=answer.sources,
        context=answer.context,
        references_removed=answer.references_removed,
        model=answer.model,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(cache)
    except OSError:
        logger.warning("Could not cache repository summary for session %s", session_id)
    return result
