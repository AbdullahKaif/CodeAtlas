"""End-to-end RAG: question -> retrieve -> context -> local LLM -> validated answer (spec §19).

Every answer carries two kinds of evidence: the validated source references the
model cited, and the retrieved chunks that were shown to it. The repository is
never sent whole; only the top-k chunks within the context budget are.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from pydantic import BaseModel, Field

from backend.config import settings
from backend.knowledge.serializer import load_entities
from backend.llm.ollama_client import ChatMessage, LLMClient, get_llm_client
from backend.rag.embeddings import EmbeddingModel
from backend.rag.models import RetrievedChunk
from backend.rag.prompts import SYSTEM_PROMPT, build_context, build_user_prompt
from backend.rag.retriever import retrieve
from backend.rag.sources import SourceReference, validate_answer

logger = logging.getLogger(__name__)

NO_EVIDENCE_ANSWER = (
    "No relevant code could be retrieved for this question, so there is no evidence to "
    "answer from. Try naming a file, class or function, or rephrase the question."
)


class ChatAnswer(BaseModel):
    session_id: str
    question: str
    answer: str
    sources: list[SourceReference] = Field(default_factory=list)  # validated citations
    context: list[RetrievedChunk] = Field(default_factory=list)  # what the model was shown
    references_removed: int = 0  # citations that failed validation
    model: str
    duration_seconds: float


def answer_question(
    session_id: str,
    session_dir: Path,
    question: str,
    history: list[ChatMessage] | None = None,
    top_k: int | None = None,
    llm: LLMClient | None = None,
    embedding_model: EmbeddingModel | None = None,
) -> ChatAnswer:
    """Answer one question about one analyzed session.

    Raises VectorStoreError / EmbeddingError when retrieval is impossible and
    LLMError subclasses when the local model cannot answer; the API maps each
    to a status code with the error's own user-facing message.
    """
    started = time.monotonic()
    llm = llm or get_llm_client()
    retrieved = retrieve(session_dir, question, top_k, model=embedding_model)
    context_text, context = build_context(retrieved)

    if not context:
        return ChatAnswer(
            session_id=session_id,
            question=question,
            answer=NO_EVIDENCE_ANSWER,
            model=llm.name,
            duration_seconds=time.monotonic() - started,
        )

    turns = (history or [])[-settings.chat_history_turns :]
    raw_answer = llm.generate(
        build_user_prompt(question, context_text), system=SYSTEM_PROMPT, history=turns
    )
    validated = validate_answer(raw_answer, context, load_entities(session_dir / "analysis"))
    if validated.references_removed:
        logger.info(
            "Session %s: removed %d unverifiable source reference(s)",
            session_id,
            validated.references_removed,
        )
    return ChatAnswer(
        session_id=session_id,
        question=question,
        answer=validated.answer or raw_answer.strip(),
        sources=validated.sources,
        context=context,
        references_removed=validated.references_removed,
        model=llm.name,
        duration_seconds=time.monotonic() - started,
    )
