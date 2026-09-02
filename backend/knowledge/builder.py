"""Knowledge base construction: scan results + parsed source -> entities and edges.

The knowledge base is a derived analysis artifact - the cloned repository stays
the source of truth. Per-file parse failures are recorded and skipped; one
unreadable or hostile file must never sink the analysis of the whole repository.
"""
from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from backend.config import settings
from backend.parser.models import Entity, ParsedFile, ParseSummary, Relationship
from backend.parser.relationships import (
    build_module_map,
    contains_relationships,
    import_relationships,
)
from backend.parser.tree_parser import FileParseError, parse_python_file
from backend.repository.scanner import RepositoryScan

logger = logging.getLogger(__name__)


class KnowledgeBase(BaseModel):
    entities: list[Entity] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    summary: ParseSummary


def build_knowledge_base(repo_root: Path, scan: RepositoryScan) -> KnowledgeBase:
    """Parse the scanned repository into entities and relationships.

    Every scanned file with a recognized language becomes a ``file`` entity (so
    later phases can chunk documentation and configuration too); Python files
    are additionally parsed into code entities and edges.
    """
    # Entities keyed by ID. Python allows the same name to be defined twice in
    # one scope (overloads, if/else definitions); the LAST definition wins, the
    # same way the Python runtime resolves it.
    entities: dict[str, Entity] = {}
    relationships: list[Relationship] = []

    python_files = [f for f in scan.files if f.language == "python"]
    module_map = build_module_map(f.path for f in python_files)

    parsed = failed = skipped_large = with_errors = 0
    failed_files: list[str] = []

    for info in scan.files:
        if info.language is None:
            continue
        entities[info.path] = Entity(
            id=info.path,
            type="file",
            name=info.name,
            file=info.path,
            language=info.language,
            start_line=1,
            end_line=max(info.line_count or 1, 1),
        )

    for info in python_files:
        if info.size_bytes > settings.max_file_size_bytes:
            # Consistent with the scanner: oversized files are inventoried but
            # their content is never read.
            skipped_large += 1
            continue
        result = _parse_one(repo_root, info.path)
        if result is None:
            failed += 1
            failed_files.append(info.path)
            continue
        parsed += 1
        if result.had_syntax_errors:
            with_errors += 1
        if result.module_docstring and info.path in entities:
            entities[info.path] = entities[info.path].model_copy(
                update={"docstring": result.module_docstring}
            )
        for entity in result.entities:
            entities[entity.id] = entity
        relationships.extend(import_relationships(info.path, result.imports, module_map))

    # Containment is derived from the final entity set, so a duplicate
    # definition never produces two edges to the same ID.
    entity_list = list(entities.values())
    relationships = contains_relationships(entity_list) + relationships

    summary = ParseSummary(
        python_files=len(python_files),
        files_parsed=parsed,
        files_failed=failed,
        files_skipped_large=skipped_large,
        files_with_syntax_errors=with_errors,
        failed_files=failed_files,
        entities=dict(Counter(e.type for e in entity_list)),
        relationships=dict(Counter(r.relation for r in relationships)),
    )
    return KnowledgeBase(entities=entity_list, relationships=relationships, summary=summary)


def _parse_one(repo_root: Path, rel_path: str) -> ParsedFile | None:
    """Parse one file, returning None on any per-file failure."""
    try:
        file_bytes = (repo_root / rel_path).read_bytes()
    except OSError as exc:
        logger.warning("Could not read %s for parsing: %s", rel_path, exc)
        return None
    try:
        return parse_python_file(file_bytes, rel_path)
    except FileParseError as exc:
        logger.warning("%s", exc)
        return None
    except Exception:
        # Defensive: no single hostile or bizarre file may abort the analysis.
        logger.exception("Unexpected parser failure on %s", rel_path)
        return None
