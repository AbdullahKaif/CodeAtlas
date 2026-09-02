"""Relationship extraction: containment, imports, inheritance and calls.

Only edges we are confident about are emitted (ADR 0001): ``contains`` comes
straight from the parse tree, ``imports`` only links files when the imported
module resolves to a file inside the repository, and ``inherits``/``calls``
only link names that resolve to a known entity through local definitions or
imported symbols. Everything else is simply not an edge - the knowledge base
never guesses.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Iterable

from backend.parser.models import CallSite, Entity, ImportStatement, Relationship, entity_id

# A syntactically plain dotted name: the only shape of base class / callee we
# attempt to resolve. Anything else (subscripts survive stripping, lambdas,
# call results) is not statically resolvable with confidence.
_DOTTED_NAME = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$")


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
        for imported in statement.names:
            if imported.name != "*":
                candidates.append(base + imported.name.split("."))
        for candidate in candidates:
            target = module_map.get(".".join(candidate))
            if target is None or target == file_path or target in seen:
                continue
            seen.add(target)
            edges.append(
                Relationship(source=file_path, relation="imports", target=target, line=statement.line)
            )
    return edges


class SymbolResolver:
    """Resolves names as written in one file to entity IDs in the knowledge base.

    Resolution order mirrors how a reader (not the runtime) finds a name: the
    file's own definitions first, then explicitly imported symbols, then module
    attribute chains, then (only if unambiguous) wildcard imports. A name that
    resolves nowhere yields None and produces no edge.
    """

    def __init__(
        self,
        file_path: str,
        imports: Iterable[ImportStatement],
        module_map: dict[str, str],
        entity_types: dict[str, str],  # entity ID -> entity type, for the whole repo
    ) -> None:
        self.file_path = file_path
        self.module_map = module_map
        self.entity_types = entity_types
        self.module_bindings: dict[str, str] = {}  # local name -> dotted module
        self.symbol_bindings: dict[str, tuple[str, str]] = {}  # local -> (module, original)
        self.wildcard_modules: list[str] = []

        for statement in imports:
            parts = _base_parts(file_path, statement)
            if parts is None:
                continue
            module = ".".join(parts)
            if not statement.names:  # plain "import app.auth [as aa]"
                if module:
                    self.module_bindings[statement.alias or module] = module
                continue
            for imported in statement.names:
                if imported.name == "*":
                    if module:
                        self.wildcard_modules.append(module)
                else:
                    self.symbol_bindings[imported.alias or imported.name] = (module, imported.name)

    def resolve(self, dotted: str, enclosing_class: str | None = None) -> str | None:
        """Entity ID for a dotted name as written, or None."""
        if not _DOTTED_NAME.match(dotted):
            return None
        parts = dotted.split(".")

        if parts[0] == "self":
            # Only same-class attribute calls; inherited methods would need MRO
            # resolution we deliberately do not attempt.
            if enclosing_class is not None and len(parts) == 2:
                return self._existing(f"{enclosing_class}.{parts[1]}")
            return None

        local = self._existing(entity_id(self.file_path, dotted))
        if local is not None:
            return local

        if parts[0] in self.symbol_bindings:
            module, original = self.symbol_bindings[parts[0]]
            resolved = self._in_module(module, [original] + parts[1:])
            if resolved is not None:
                return resolved
            # "from app import auth" imports a MODULE: retry with auth's file.
            return self._in_module(f"{module}.{original}" if module else original, parts[1:])

        prefix = self._longest_module_prefix(parts)
        if prefix is not None:
            bound, remainder = prefix
            return self._in_module(self.module_bindings[bound], remainder)

        # A bare name may come from a wildcard import - but only when exactly
        # one wildcard module defines it is the resolution unambiguous.
        candidates = [
            resolved
            for module in self.wildcard_modules
            if (resolved := self._in_module(module, parts)) is not None
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _in_module(self, module: str, name_parts: list[str]) -> str | None:
        """Entity ID of ``name_parts`` inside ``module``, if both exist."""
        target_file = self.module_map.get(module)
        if target_file is None or not name_parts:
            return None
        return self._existing(entity_id(target_file, ".".join(name_parts)))

    def _longest_module_prefix(self, parts: list[str]) -> tuple[str, list[str]] | None:
        """Match ``app.auth.login`` against a binding for ``app.auth`` (longest wins)."""
        for end in range(len(parts) - 1, 0, -1):
            bound = ".".join(parts[:end])
            if bound in self.module_bindings:
                return bound, parts[end:]
        return None

    def _existing(self, candidate: str) -> str | None:
        return candidate if candidate in self.entity_types else None


def inherits_relationships(resolver: SymbolResolver, class_entities: Iterable[Entity]) -> list[Relationship]:
    """One ``inherits`` edge per base class that resolves to a known class entity.

    Generic subscripts are stripped (``Base[T]`` inherits ``Base``); keyword
    arguments like ``metaclass=`` never reach here (parser excludes them).
    """
    edges: list[Relationship] = []
    seen: set[tuple[str, str]] = set()
    for entity in class_entities:
        for base in entity.parent_classes:
            base_name = base.split("[", 1)[0].strip()
            target = resolver.resolve(base_name)
            if target is None or resolver.entity_types.get(target) != "class":
                continue
            if target == entity.id or (entity.id, target) in seen:
                continue
            seen.add((entity.id, target))
            edges.append(
                Relationship(source=entity.id, relation="inherits", target=target, line=entity.start_line)
            )
    return edges


def call_relationships(resolver: SymbolResolver, calls: Iterable[CallSite]) -> list[Relationship]:
    """One ``calls`` edge per call site whose callee resolves to a known entity.

    Calling a class (instantiation) is an edge to the class. ``self.x()`` only
    resolves when the caller is a method of the class defining ``x``.
    """
    edges: list[Relationship] = []
    seen: set[tuple[str, str]] = set()
    for site in calls:
        target = resolver.resolve(site.callee, enclosing_class=_enclosing_class(resolver, site.caller))
        if target is None or resolver.entity_types.get(target) not in {"class", "function", "method"}:
            continue
        if (site.caller, target) in seen:
            continue
        seen.add((site.caller, target))
        edges.append(Relationship(source=site.caller, relation="calls", target=target, line=site.line))
    return edges


def _enclosing_class(resolver: SymbolResolver, caller_id: str) -> str | None:
    """The class an entity's ``self`` refers to: its direct parent, if a class."""
    if "::" not in caller_id or "." not in caller_id.split("::", 1)[1]:
        return None
    candidate = caller_id.rsplit(".", 1)[0]
    return candidate if resolver.entity_types.get(candidate) == "class" else None


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
