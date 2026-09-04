"""Source-reference extraction and validation (spec §20).

LLM citations are never trusted blindly. Every reference the model writes is
checked against CodeAtlas metadata: the file must be one the model actually
saw in its context, a cited line range must overlap a retrieved chunk of that
file, and a cited symbol must be an entity of that file. Anything that fails
is removed - never repaired - and the caller is told how many were dropped so
the UI can show that uncertainty instead of hiding it.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from backend.parser.models import Entity
from backend.rag.models import RetrievedChunk

# A repository-relative path with an extension: app/auth.py, docs/README.md.
_PATH = r"(?P<file>[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*\.[A-Za-z0-9]{1,10})"
# Optional "::Qualified.name" symbol suffix (the knowledge base's own ID format).
_SYMBOL = r"(?:::(?P<symbol>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*))?"
# "lines 20-45", "line 20", "L20-L45", ":20-45", "(lines 20–45)".
_LINES = (
    r"(?:\s*[:,]?\s*\(?\s*(?:lines?\s*|L)(?P<start>\d+)(?:\s*(?:-|–|—|to)\s*L?(?P<end>\d+))?\s*\)?"
    r"|:(?P<start2>\d+)(?:\s*[-–—]\s*(?P<end2>\d+))?)"
)
_CITATION = re.compile(_PATH + _SYMBOL + _LINES)
_BARE_PATH = re.compile(_PATH + _SYMBOL)
_SOURCES_HEADER = re.compile(r"^\s*\**\s*sources?\s*:?\s*\**\s*:?\s*$", re.IGNORECASE | re.MULTILINE)
_SOURCES_INLINE = re.compile(r"^\s*\**\s*sources?\s*:\s*\**", re.IGNORECASE | re.MULTILINE)
_EMPTY_PARENS = re.compile(r"\(\s*\)")
_DOUBLE_SPACE = re.compile(r"[ \t]{2,}")


class SourceReference(BaseModel):
    """A citation that survived validation. Line ranges are the model's, clamped to evidence."""

    file: str
    start_line: int | None = None
    end_line: int | None = None
    symbol: str | None = None
    chunk_id: str | None = None  # the retrieved chunk that backs this reference


class RawReference(BaseModel):
    file: str
    symbol: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    span: tuple[int, int]  # character span in the text it was parsed from


class ValidatedAnswer(BaseModel):
    answer: str  # body without the Sources section and without invalid inline citations
    sources: list[SourceReference]
    references_removed: int


def split_sources_section(answer: str) -> tuple[str, str]:
    """Separate the trailing ``Sources:`` section from the answer body."""
    matches = list(_SOURCES_HEADER.finditer(answer)) or list(_SOURCES_INLINE.finditer(answer))
    if not matches:
        return answer.strip(), ""
    last = matches[-1]
    return answer[: last.start()].rstrip(), answer[last.end():].strip()


def extract_references(text: str, *, bare_paths: bool = False) -> list[RawReference]:
    """Citations found in ``text``. Bare paths (no lines) only count when asked."""
    refs: list[RawReference] = []
    covered: list[tuple[int, int]] = []
    for match in _CITATION.finditer(text):
        start = match.group("start") or match.group("start2")
        end = match.group("end") or match.group("end2") or start
        refs.append(
            RawReference(
                file=match.group("file"),
                symbol=match.group("symbol"),
                start_line=int(start),
                end_line=int(end),
                span=match.span(),
            )
        )
        covered.append(match.span())
    if bare_paths:
        for match in _BARE_PATH.finditer(text):
            if any(s <= match.start() < e for s, e in covered):
                continue
            refs.append(
                RawReference(file=match.group("file"), symbol=match.group("symbol"), span=match.span())
            )
    refs.sort(key=lambda r: r.span)
    return refs


def validate_answer(
    answer: str, context: list[RetrievedChunk], entities: list[Entity]
) -> ValidatedAnswer:
    """Validate every citation in the answer against the evidence the model saw."""
    body, sources_text = split_sources_section(answer)
    chunks_by_file: dict[str, list[RetrievedChunk]] = {}
    for chunk in context:
        chunks_by_file.setdefault(chunk.file, []).append(chunk)
    symbols_by_file: dict[str, set[str]] = {}
    for entity in entities:
        if entity.type != "file":
            symbols_by_file.setdefault(entity.file, set()).add(entity.id.split("::", 1)[1])

    valid: list[SourceReference] = []
    seen: set[tuple[str, int | None, int | None, str | None]] = set()
    removed = 0

    def _accept(raw: RawReference) -> SourceReference | None:
        result = _validate_one(raw, chunks_by_file, symbols_by_file)
        if result is None:
            return None
        key = (result.file, result.start_line, result.end_line, result.symbol)
        if key in seen:
            return result  # already listed; still a valid mention
        seen.add(key)
        valid.append(result)
        return result

    # Inline citations in the body: invalid ones are cut out of the text.
    pieces: list[str] = []
    cursor = 0
    for raw in extract_references(body):
        if _accept(raw) is None:
            removed += 1
            pieces.append(body[cursor : raw.span[0]])
            cursor = raw.span[1]
    pieces.append(body[cursor:])
    cleaned = _tidy("".join(pieces))

    for raw in extract_references(sources_text, bare_paths=True):
        if _accept(raw) is None:
            removed += 1

    return ValidatedAnswer(answer=cleaned, sources=valid, references_removed=removed)


def _validate_one(
    raw: RawReference,
    chunks_by_file: dict[str, list[RetrievedChunk]],
    symbols_by_file: dict[str, set[str]],
) -> SourceReference | None:
    chunks = chunks_by_file.get(raw.file)
    if not chunks:
        return None  # the model never saw this file: no evidence for the claim
    if raw.symbol is not None and raw.symbol not in symbols_by_file.get(raw.file, set()):
        return None
    if raw.start_line is None:
        backing = chunks[0]
        return SourceReference(file=raw.file, symbol=raw.symbol, chunk_id=backing.chunk_id)
    start, end = sorted((raw.start_line, raw.end_line or raw.start_line))
    backing = next(
        (c for c in chunks if c.start_line <= end and start <= c.end_line), None
    )
    if backing is None:
        return None  # cited lines were not in the context: unverifiable
    return SourceReference(
        file=raw.file,
        # Clamp to the evidence: the reference may not claim lines the model never saw.
        start_line=max(start, backing.start_line),
        end_line=min(end, backing.end_line),
        symbol=raw.symbol or backing.symbol,
        chunk_id=backing.chunk_id,
    )


def _tidy(text: str) -> str:
    text = _EMPTY_PARENS.sub("", text)
    text = _DOUBLE_SPACE.sub(" ", text)
    text = re.sub(r" +([,.;:])", r"\1", text)
    return text.strip()
