"""Data models for the parsing layer.

The vocabulary here is canonical (see CONTEXT.md and docs/adr/0001): exactly four
entity types and four relation kinds exist. Every later subsystem - chunking,
vector metadata, the architecture graph, impact analysis, citation validation -
keys on the entity ID format defined by these models, so changes here are
breaking changes for the whole knowledge base.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EntityType = Literal["file", "class", "function", "method"]

# "defines" is deliberately absent: it would be a synonym of "contains".
# "inherits" and "calls" are introduced in Phase 2 PR 2.
RelationType = Literal["contains", "imports", "inherits", "calls"]


def entity_id(file_path: str, qualified_name: str | None = None) -> str:
    """Canonical entity ID: ``app/auth.py::AuthService.login``.

    A file's ID is its POSIX path alone; code entities append ``::`` and the
    dotted qualified name of their enclosing named scopes.
    """
    if not qualified_name:
        return file_path
    return f"{file_path}::{qualified_name}"


class Entity(BaseModel):
    """A named structural element extracted from source code.

    In Python a file IS a module, so there is no separate "module" type - the
    ``file`` entity carries module-level facts (e.g. the module docstring).
    """

    id: str
    type: EntityType
    name: str
    file: str  # POSIX path relative to the repository root
    language: str | None = None
    start_line: int  # 1-based, inclusive; includes decorators for decorated defs
    end_line: int  # 1-based, inclusive
    parent: str | None = None  # entity ID of the containing scope; None for files
    parameters: list[str] = Field(default_factory=list)
    signature: str | None = None
    docstring: str | None = None
    decorators: list[str] = Field(default_factory=list)
    parent_classes: list[str] = Field(default_factory=list)  # base classes as written
    # Files carry no source_code: the repository on disk is the source of truth
    # and duplicating every file into entities.json would double session storage.
    source_code: str | None = None


class Relationship(BaseModel):
    """A directed edge between two entity IDs, statically extracted.

    ``target`` always names an entity present in the knowledge base - edges to
    unresolvable targets (external packages, dynamic imports) are omitted, never
    guessed.
    """

    source: str
    relation: RelationType
    target: str
    line: int | None = None  # where the edge originates (e.g. the import statement)


class ImportStatement(BaseModel):
    """One import statement as written, before resolution to repository files.

    Kept separate from Relationship because most imports point outside the
    repository; the resolver decides which become ``imports`` edges.
    """

    module: str | None  # dotted module ("app.auth"); None for pure-relative "from . import x"
    names: list[str] = Field(default_factory=list)  # imported names; empty for "import m"
    level: int = 0  # leading dots of a relative import
    line: int


class ParsedFile(BaseModel):
    """Everything extracted from a single source file."""

    entities: list[Entity]
    imports: list[ImportStatement]
    module_docstring: str | None = None
    had_syntax_errors: bool = False


class ParseSummary(BaseModel):
    """Parsing counters.

    Invariant: python_files == files_parsed + files_failed + files_skipped_large.
    files_with_syntax_errors counts files that ARE parsed (tree-sitter recovers
    what it can) but contained regions it could not understand.
    """

    python_files: int
    files_parsed: int
    files_failed: int
    files_skipped_large: int
    files_with_syntax_errors: int
    failed_files: list[str] = Field(default_factory=list)
    entities: dict[str, int] = Field(default_factory=dict)  # count per entity type
    relationships: dict[str, int] = Field(default_factory=dict)  # count per relation
