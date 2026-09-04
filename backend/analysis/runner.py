"""The background analysis pipeline: clone -> scan -> parse -> chunk -> embed -> index -> security.

Runs off the request thread (FastAPI background task). Progress is reported
through a StatusTracker; results persist under the session's analysis dir.
On failure the session's repository content is scrubbed immediately - a failed
run must not leave cloned code on disk - but status.json survives so the
frontend can show what went wrong until the session is deleted.
"""
from __future__ import annotations

import logging
import os
import shutil

from pydantic import BaseModel

from backend.analysis.status import StatusTracker
from backend.config import settings
from backend.knowledge.builder import KnowledgeBase, build_knowledge_base
from backend.knowledge.serializer import write_chunks, write_knowledge_base
from backend.parser.models import ParseSummary
from backend.rag.chunker import build_chunks
from backend.rag.embeddings import get_embedding_model
from backend.rag.models import Chunk, ChunkSummary, IndexSummary
from backend.rag.retriever import embed_chunks, index_vectors
from backend.repository.clone import CloneError, clone_repository
from backend.repository.scanner import RepositoryScan, scan_repository
from backend.security.engine import overview_of, run_security_scan
from backend.security.models import SecurityOverview

logger = logging.getLogger(__name__)


class AnalysisFailure(Exception):
    """A failure with a message that is safe and useful to show the user."""


class RepositoryInfo(BaseModel):
    name: str
    url: str


class AnalyzeResponse(BaseModel):
    """The persisted analysis overview (analysis/repository.json)."""

    session_id: str
    repository: RepositoryInfo
    scan: RepositoryScan
    # None only for sessions persisted before the respective stage existed.
    parse: ParseSummary | None = None
    chunks: ChunkSummary | None = None
    # index is None when embedding failed; index_error then says why (the rest
    # of the analysis is still usable - spec §42, optional component failure).
    index: IndexSummary | None = None
    index_error: str | None = None
    # security is None when no scanner could run at all; the scanner statuses
    # inside it say which tools ran, failed or are not installed.
    security: SecurityOverview | None = None
    security_error: str | None = None


def run_analysis(session_id: str, url: str, repo_dir, repo_name: str, tracker: StatusTracker) -> None:
    """Execute all stages for one session, updating the tracker as they happen."""
    try:
        tracker.start("cloning")
        clone_repository(url, repo_dir)
        tracker.complete("cloning")

        tracker.start("scanning")
        scan = scan_repository(repo_dir)
        if scan.summary.files_included == 0:
            raise AnalysisFailure("The repository contains no analyzable source files.")
        tracker.complete("scanning", f"{scan.summary.files_included} files")

        tracker.start("parsing")
        knowledge = build_knowledge_base(repo_dir, scan)
        tracker.complete("parsing", f"{sum(knowledge.summary.entities.values())} entities")

        tracker.start("chunking")
        chunk_list, chunk_summary = build_chunks(repo_dir, scan, knowledge.entities)
        tracker.complete("chunking", f"{chunk_summary.total} chunks")

        index, index_error = _embed_and_index(session_id, chunk_list, tracker)
        security, security_error = _scan_security(session_id, repo_dir, tracker)

        response = AnalyzeResponse(
            session_id=session_id,
            repository=RepositoryInfo(name=repo_name, url=url),
            scan=scan,
            parse=knowledge.summary,
            chunks=chunk_summary,
            index=index,
            index_error=index_error,
            security=security,
            security_error=security_error,
        )
        _persist(session_id, response, knowledge, chunk_list)
        tracker.finish()
    except (CloneError, AnalysisFailure) as exc:
        logger.warning("Analysis of session %s failed: %s", session_id, exc)
        tracker.fail(str(exc))
        _scrub_session_content(session_id)
    except Exception:
        logger.exception("Unexpected failure while analyzing session %s", session_id)
        tracker.fail("Repository analysis failed unexpectedly.")
        _scrub_session_content(session_id)


def _embed_and_index(
    session_id: str, chunk_list: list[Chunk], tracker: StatusTracker
) -> tuple[IndexSummary | None, str | None]:
    """The optional stages: their failure degrades search, never the analysis."""
    if not chunk_list:
        return None, None
    stage = "embedding"
    try:
        tracker.start("embedding", "loading embedding model")
        model = get_embedding_model()
        vectors = embed_chunks(
            chunk_list,
            model,
            progress=lambda done, total: tracker.progress("embedding", f"{done}/{total} chunks"),
        )
        tracker.complete("embedding")

        stage = "indexing"
        tracker.start("indexing")
        index = index_vectors(
            settings.session_dir(session_id) / "vectors", chunk_list, vectors, model.name
        )
        tracker.complete("indexing", f"{index.chunks_indexed} vectors")
        return index, None
    except Exception as exc:
        logger.warning("Embedding/indexing failed; analysis continues without search: %s", exc)
        tracker.stage_failed(stage, str(exc))
        return None, str(exc)


def _scan_security(
    session_id: str, repo_dir, tracker: StatusTracker
) -> tuple[SecurityOverview | None, str | None]:
    """Optional stage: missing or failing scanners degrade security, never the analysis."""
    try:
        tracker.start("security")
        report = run_security_scan(
            session_id, repo_dir, progress=lambda detail: tracker.progress("security", detail)
        )
    except Exception as exc:
        logger.warning("Security scan failed; analysis continues without it: %s", exc)
        tracker.stage_failed("security", "security scan failed")
        return None, "Security scan failed unexpectedly."

    ran = [s.name for s in report.scanners if s.ran]
    if not ran:
        reasons = "; ".join(f"{s.name}: {s.error}" for s in report.scanners if s.error)
        tracker.stage_failed("security", reasons or "no scanner available")
        return overview_of(report), reasons or "No security scanner is installed."
    skipped = [f"{s.name} {s.error}" for s in report.scanners if not s.ran and s.error]
    detail = f"{report.summary.total} findings ({', '.join(ran)})"
    if skipped:
        detail += f"; {'; '.join(skipped)}"
    tracker.complete("security", detail)
    return overview_of(report), None


def _persist(
    session_id: str, response: AnalyzeResponse, knowledge: KnowledgeBase, chunk_list: list[Chunk]
) -> None:
    """Store the analysis under the session: overview, knowledge base and chunks."""
    analysis_dir = settings.session_dir(session_id) / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    write_knowledge_base(analysis_dir, knowledge)
    write_chunks(analysis_dir, chunk_list)
    # Atomic write: a crash mid-write must not leave a truncated repository.json
    # behind (it would turn every later overview read into a 500).
    tmp_path = analysis_dir / "repository.json.tmp"
    tmp_path.write_text(response.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp_path, analysis_dir / "repository.json")


def _scrub_session_content(session_id: str) -> None:
    """Remove repository content after a failed run; keep status.json for the UI."""
    session_dir = settings.session_dir(session_id)
    for subdir in ("repository", "analysis", "vectors", "security"):
        shutil.rmtree(session_dir / subdir, ignore_errors=True)
