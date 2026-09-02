"""Relationship extraction: structural containment and repository-internal imports.

Only edges we are confident about are emitted (ADR 0001): ``contains`` comes
straight from the parse tree, and ``imports`` only links files when the imported
module resolves to a file inside the repository. Imports of external packages
are simply not edges - the knowledge base never guesses.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Iterable

from backend.parser.models import Entity, ImportStatement, Relationship


def contains_relationships(entities: Iterable[Entity]) -> list[Relationship]:
    """One ``contains`` edge per entity nesting: file->class, class->method, ..."""
    return [
        Relationship(source=e.parent, relation="contains", target=e.id, line=e.start_line)
        for e in entities
        if e.parent is not None
    ]


def build_module_map(python_files: Iterable[str]) -> dict[str, str]:
    """Map dotted module names to repository file paths.

    ``app/auth.py`` -> ``app.auth``; ``app/__init__.py`` -> ``app``. The repo
    root is treated as the import root, which holds for the common layout where
    code is imported the way the fixture and demo repositories do it. When both
    ``x.py`` and ``x.pyi`` exist the real module wins over the stub.
    """
    module_map: dict[str, str] = {}
    for path in sorted(python_files, key=lambda p: p.endswith(".pyi")):  # .py first, .pyi last
        parts = list(PurePosixPath(path).parts)
        stem = PurePosixPath(parts[-1]).stem
        if stem == "__init__":
            module_parts = parts[:-1]
        else:
            module_parts = parts[:-1] + [stem]
        if not module_parts:  # a bare __init__.py at the repo root names no module
            continue
        module_map.setdefault(".".join(module_parts), path)
    return module_map


def import_relationships(
    file_path: str,
    imports: Iterable[ImportStatement],
    module_map: dict[str, str],
) -> list[Relationship]:
    """Resolve import statements of one file to ``imports`` edges.

    Both the imported module and any imported names that are themselves modules
    become edges (``from app import auth`` links to ``app/__init__.py`` AND
    ``app/auth.py`` - both genuinely execute on import). Unresolvable modules,
    including namespace packages without ``__init__.py``, yield no edge.
    """
    edges: list[Relationship] = []
    seen: set[str] = set()
    for statement in imports:
        base = _base_parts(file_path, statement)
        if base is None:  # relative import climbing above the repo root
            continue
        candidates = []
        if base:
            candidates.append(base)
        for name in statement.names:
            if name != "*":
                candidates.append(base + name.split("."))
        for candidate in candidates:
            target = module_map.get(".".join(candidate))
            if target is None or target == file_path or target in seen:
                continue
            seen.add(target)
            edges.append(
                Relationship(source=file_path, relation="imports", target=target, line=statement.line)
            )
    return edges


def _base_parts(file_path: str, statement: ImportStatement) -> list[str] | None:
    """The dotted-name parts the statement's module resolves against, or None."""
    module_parts = statement.module.split(".") if statement.module else []
    if statement.level == 0:
        return module_parts
    # Relative import: level 1 is the importing file's own package, each extra
    # dot climbs one package higher.
    package = list(PurePosixPath(file_path).parts[:-1])
    climb = statement.level - 1
    if climb > len(package):
        return None
    base = package[: len(package) - climb]
    return base + module_parts
