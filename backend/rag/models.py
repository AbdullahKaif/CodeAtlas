"""Data models for the retrieval layer.

A Chunk is the unit of embedding and retrieval (CONTEXT.md). Chunks carry their
entity metadata and exact line ranges so every retrieved chunk can be cited -
and validated - against the repository. A chunk's line range is always
contiguous and its text always matches those lines verbatim; that invariant is
what makes source-reference validation (spec §20) possible later.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ChunkType = Literal["function", "method", "class", "module", "documentation", "config"]


class Chunk(BaseModel):
    chunk_id: str  # sequential, stable within one analysis ("chunk_00042")
    file: str  # POSIX path relative to the repository root
    symbol: str | None = None  # qualified name for code, heading for documentation
    entity_id: str | None = None  # owning entity in the knowledge base, when one exists
    type: ChunkType
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    part: int | None = None  # 1-based index when an oversized chunk was split
    text: str


class ChunkSummary(BaseModel):
    """Chunking counters for the analysis response."""

    total: int
    by_type: dict[str, int] = Field(default_factory=dict)
    files_chunked: int
    oversized_split: int  # chunks that came from splitting an oversized region
