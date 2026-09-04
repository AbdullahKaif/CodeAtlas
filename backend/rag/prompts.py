"""Prompt construction for repository-grounded answers (spec §19).

The LLM only ever sees numbered context blocks - retrieved chunks with their
file path and exact line range - never the whole repository. It is instructed
to cite blocks in one fixed format so that citations can be parsed and
validated against the knowledge base afterwards (spec §20).
"""
from __future__ import annotations

from backend.config import settings
from backend.rag.models import RetrievedChunk

SYSTEM_PROMPT = """You are CodeAtlas, a code intelligence assistant answering questions about ONE specific repository.

You are given numbered context blocks: excerpts of the repository's source code and documentation, each labelled with its file path and line range. This context is the ONLY evidence you have about this repository.

Rules:
1. Answer using only the context. Never invent files, functions, classes, behaviour or configuration that the context does not show.
2. If the context does not contain enough evidence to answer, say so plainly (for example "The retrieved code does not show how X is configured") and explain what is missing. Do not guess.
3. Be precise and concise. Explain the code in terms of the actual names that appear in the context (files, classes, functions).
4. Static analysis is not runtime truth: describe what the code appears to do, not what definitely happens at runtime.
5. End your answer with a "Sources:" section listing every context block you relied on, one per line, exactly in this format:
   - <file path>: lines <start>-<end>
   Copy the file path and line range from the block header. List only blocks you actually used. If you used none, write "Sources: none".
"""

_LANGUAGE_FENCE = {
    "documentation": "markdown",
    "config": "",
}


def build_context(
    chunks: list[RetrievedChunk], max_chars: int | None = None
) -> tuple[str, list[RetrievedChunk]]:
    """Render retrieved chunks as numbered blocks within the character budget.

    Chunks are taken in retrieval order (best first); once the budget is
    exhausted the remaining chunks are dropped rather than truncated, so every
    block the model sees is a complete, citable line range. Returns the
    context text and the chunks that made it in.
    """
    budget = max_chars or settings.llm_context_max_chars
    blocks: list[str] = []
    used: list[RetrievedChunk] = []
    total = 0
    for chunk in chunks:
        block = _render_block(len(used) + 1, chunk)
        if used and total + len(block) > budget:
            continue  # smaller later chunks may still fit
        if not used and len(block) > budget:
            block = _render_block(1, chunk, truncate_to=budget)  # never send an empty context
        blocks.append(block)
        used.append(chunk)
        total += len(block)
    return "\n\n".join(blocks), used


def build_user_prompt(question: str, context: str) -> str:
    if not context:
        return f"No repository context could be retrieved.\n\nQuestion: {question}"
    return f"Repository context:\n\n{context}\n\nQuestion: {question}"


def _render_block(number: int, chunk: RetrievedChunk, truncate_to: int | None = None) -> str:
    label = f"{chunk.file}: lines {chunk.start_line}-{chunk.end_line}"
    if chunk.symbol:
        label += f" ({chunk.symbol}, {chunk.type})"
    fence = _LANGUAGE_FENCE.get(chunk.type, _fence_language(chunk.file))
    text = chunk.text
    if truncate_to is not None:
        overhead = len(label) + len(fence) + 16
        text = text[: max(truncate_to - overhead, 0)]
    return f"[{number}] {label}\n```{fence}\n{text}\n```"


def _fence_language(path: str) -> str:
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {"py": "python", "js": "javascript", "ts": "typescript", "go": "go", "rs": "rust",
            "java": "java", "rb": "ruby", "md": "markdown", "yml": "yaml", "yaml": "yaml",
            "json": "json", "toml": "toml", "sh": "bash"}.get(suffix, "")
