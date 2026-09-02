"""Semantic chunker: turns a scanned + parsed repository into retrieval chunks.

Policy (approved design, Phase 3a):
- One chunk per function/method, whole source including nested defs; the nested
  defs get no chunk of their own (that would duplicate their text in the index).
- Class chunks carry what the method chunks do not: header, docstring and
  class-level statements, as contiguous segments with real line ranges.
- Module chunks are the top-level code between definitions (imports, constants,
  main blocks) - again contiguous segments, never stitched-together holes.
- Markdown splits by heading; project/config files become one chunk; lockfiles
  are skipped as retrieval noise; other code languages chunk whole-file until
  they get a parser.
- Oversized chunks split on line boundaries with a small line overlap.

Every chunk's text is exactly the decoded content of its line range - the
invariant that makes retrieved chunks citable and validatable.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from backend.config import settings
from backend.parser.models import Entity
from backend.rag.models import Chunk, ChunkSummary, ChunkType
from backend.repository.scanner import FileInfo, RepositoryScan

# Lockfiles are machine-generated and enormous relative to their meaning.
LOCKFILE_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "pipfile.lock", "cargo.lock", "go.sum",
}

CONFIG_LANGUAGES = {"json", "yaml", "toml", "config", "docker", "make"}
PROSE_LANGUAGES = {"restructuredtext", "text"}

_HEADING = re.compile(r"^(#{1,6})\s+(.+)")


def build_chunks(repo_root: Path, scan: RepositoryScan, entities: list[Entity]) -> tuple[list[Chunk], ChunkSummary]:
    """Chunk every readable scanned file. Failures skip the file, never the run."""
    entity_map = {e.id: e for e in entities}
    by_file: dict[str, list[Entity]] = defaultdict(list)
    for entity in entities:
        if entity.type != "file":
            by_file[entity.file].append(entity)

    chunks: list[Chunk] = []
    files_chunked = 0
    for info in sorted(scan.files, key=lambda f: f.path):
        if info.language is None or info.line_count is None:
            continue  # no recognized language, unreadable, or content never read (oversized)
        lines = _read_lines(repo_root, info.path)
        if lines is None:
            continue
        file_chunks = _chunk_file(info, lines, by_file.get(info.path, []), entity_map)
        if file_chunks:
            files_chunked += 1
            chunks.extend(file_chunks)

    chunks, split_parts = _split_oversized(chunks)
    for index, chunk in enumerate(chunks, start=1):
        chunk.chunk_id = f"chunk_{index:05d}"

    summary = ChunkSummary(
        total=len(chunks),
        by_type=dict(Counter(c.type for c in chunks)),
        files_chunked=files_chunked,
        oversized_split=split_parts,
    )
    return chunks, summary


def _chunk_file(
    info: FileInfo, lines: list[str], file_entities: list[Entity], entity_map: dict[str, Entity]
) -> list[Chunk]:
    if info.language == "python":
        return _chunk_python(info, lines, file_entities, entity_map)
    if info.language == "markdown":
        return _chunk_markdown(info, lines)
    if info.name.lower() in LOCKFILE_NAMES:
        return []
    if info.is_project_file or info.language in CONFIG_LANGUAGES:
        return _whole_file(info, lines, "config")
    if info.language in PROSE_LANGUAGES:
        return _whole_file(info, lines, "documentation")
    # Other code languages (JS/TS/Go/...) chunk whole-file until they get a parser.
    return _whole_file(info, lines, "module")


def _chunk_python(
    info: FileInfo, lines: list[str], file_entities: list[Entity], entity_map: dict[str, Entity]
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for entity in sorted(file_entities, key=lambda e: e.start_line):
        if _nested_in_callable(entity, entity_map):
            continue  # its text already lives inside the enclosing callable's chunk
        symbol = entity.id.split("::", 1)[1]
        if entity.type in {"function", "method"}:
            chunks.append(
                _make(info, lines, entity.start_line, entity.end_line, entity.type, symbol, entity.id)
            )
        elif entity.type == "class":
            covered = [
                (child.start_line, child.end_line)
                for child in file_entities
                if child.parent == entity.id
            ]
            for start, end in _uncovered_segments(entity.start_line, entity.end_line, covered, lines):
                chunks.append(_make(info, lines, start, end, "class", symbol, entity.id))

    top_level = [
        (e.start_line, e.end_line) for e in file_entities if e.parent == info.path
    ]
    for start, end in _uncovered_segments(1, len(lines), top_level, lines):
        chunks.append(_make(info, lines, start, end, "module", None, info.path))
    chunks.sort(key=lambda c: c.start_line)
    return chunks


def _chunk_markdown(info: FileInfo, lines: list[str]) -> list[Chunk]:
    """One documentation chunk per heading section (plus any preamble)."""
    boundaries: list[tuple[int, str | None]] = []  # (1-based start line, heading text)
    for number, line in enumerate(lines, start=1):
        match = _HEADING.match(line)
        if match:
            boundaries.append((number, match.group(2).strip()))
    if not boundaries or boundaries[0][0] > 1:
        boundaries.insert(0, (1, None))

    chunks = []
    for (start, heading), (next_start, _) in zip(boundaries, boundaries[1:] + [(len(lines) + 1, None)]):
        end = next_start - 1
        if any(line.strip() for line in lines[start - 1 : end]):
            chunks.append(_make(info, lines, start, end, "documentation", heading, info.path))
    return chunks


def _whole_file(info: FileInfo, lines: list[str], chunk_type: ChunkType) -> list[Chunk]:
    if not any(line.strip() for line in lines):
        return []
    return [_make(info, lines, 1, len(lines), chunk_type, None, info.path)]


def _make(
    info: FileInfo,
    lines: list[str],
    start: int,
    end: int,
    chunk_type: ChunkType,
    symbol: str | None,
    entity_id: str | None,
) -> Chunk:
    return Chunk(
        chunk_id="",  # assigned after the split pass, when order is final
        file=info.path,
        symbol=symbol,
        entity_id=entity_id,
        type=chunk_type,
        start_line=start,
        end_line=end,
        text="\n".join(lines[start - 1 : end]),
    )


def _nested_in_callable(entity: Entity, entity_map: dict[str, Entity]) -> bool:
    """True when any ancestor scope is a function/method."""
    parent_id = entity.parent
    while parent_id is not None and "::" in parent_id:
        parent = entity_map.get(parent_id)
        if parent is None:
            return False
        if parent.type in {"function", "method"}:
            return True
        parent_id = parent.parent
    return False


def _uncovered_segments(
    start: int, end: int, covered: list[tuple[int, int]], lines: list[str]
) -> list[tuple[int, int]]:
    """Maximal contiguous line runs in [start, end] not covered by any range.

    Blank-only runs are dropped. Returned ranges are real, contiguous spans -
    a chunk must never claim a line range with holes in it.
    """
    is_covered = [False] * (end - start + 1)
    for cover_start, cover_end in covered:
        for line in range(max(cover_start, start), min(cover_end, end) + 1):
            is_covered[line - start] = True

    segments: list[tuple[int, int]] = []
    run_start: int | None = None
    for offset, covered_line in enumerate(is_covered + [True]):  # sentinel closes the last run
        line_number = start + offset
        if not covered_line and run_start is None:
            run_start = line_number
        elif covered_line and run_start is not None:
            if any(line.strip() for line in lines[run_start - 1 : line_number - 1]):
                segments.append((run_start, line_number - 1))
            run_start = None
    return segments


def _split_oversized(chunks: list[Chunk]) -> tuple[list[Chunk], int]:
    """Split chunks over the size limit on line boundaries, with a small overlap."""
    result: list[Chunk] = []
    split_parts = 0
    for chunk in chunks:
        if len(chunk.text) <= settings.chunk_max_chars:
            result.append(chunk)
            continue
        parts = _split_chunk(chunk)
        split_parts += len(parts)
        result.extend(parts)
    return result, split_parts


def _split_chunk(chunk: Chunk) -> list[Chunk]:
    lines = chunk.text.split("\n")
    parts: list[Chunk] = []
    index = 0
    while index < len(lines):
        size = 0
        end_index = index
        while end_index < len(lines) and size + len(lines[end_index]) + 1 <= settings.chunk_max_chars:
            size += len(lines[end_index]) + 1
            end_index += 1
        if end_index == index:  # single line over the limit: take it whole rather than loop
            end_index = index + 1
        parts.append(
            chunk.model_copy(
                update={
                    "start_line": chunk.start_line + index,
                    "end_line": chunk.start_line + end_index - 1,
                    "text": "\n".join(lines[index:end_index]),
                    "part": len(parts) + 1,
                }
            )
        )
        if end_index >= len(lines):
            break
        # Step back a few lines so split parts share context at the seam.
        index = max(end_index - settings.chunk_overlap_lines, index + 1)
    return parts


def _read_lines(repo_root: Path, rel_path: str) -> list[str] | None:
    try:
        return (repo_root / rel_path).read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return None
