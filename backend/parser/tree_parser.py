"""Tree-sitter based structural parser for Python source files.

Repository contents are untrusted input: files are parsed as bytes, never
imported or executed. Tree-sitter is error-tolerant, so a file with syntax
errors still yields every entity it could understand - a broken file must never
sink the analysis of a whole repository.

Python only for now. Adding a language means adding a grammar and an extractor;
the models and IDs are language-agnostic by design (ADR 0001).
"""
from __future__ import annotations

import logging

import tree_sitter_python
from tree_sitter import Language, Node, Parser

from backend.parser.models import CallSite, Entity, ImportedName, ImportStatement, ParsedFile, entity_id

logger = logging.getLogger(__name__)

PYTHON_LANGUAGE = Language(tree_sitter_python.language())

# Scope-introducing node types. A definition directly inside a class body is a
# method; anywhere else (module, function body, conditional block) it is a function.
_DEFINITION_TYPES = {"class_definition", "function_definition"}


class FileParseError(Exception):
    """Raised when a file cannot be parsed at all (unreadable, parser failure)."""


def parse_python_file(file_bytes: bytes, file_path: str) -> ParsedFile:
    """Extract entities and import statements from one Python source file.

    ``file_path`` is the POSIX path relative to the repository root; it becomes
    the prefix of every entity ID extracted here.
    """
    try:
        parser = Parser(PYTHON_LANGUAGE)
        tree = parser.parse(file_bytes)
        root = tree.root_node
    except Exception as exc:  # tree-sitter failures are environmental, not user errors
        raise FileParseError(f"Tree-sitter could not parse {file_path}: {exc}") from exc

    entities: list[Entity] = []
    imports: list[ImportStatement] = []
    calls: list[CallSite] = []
    _walk(root, file_path, scope=[], entities=entities, imports=imports, calls=calls)
    return ParsedFile(
        entities=entities,
        imports=imports,
        calls=calls,
        module_docstring=_docstring_of_block(root),
        had_syntax_errors=root.has_error,
    )


def _walk(
    node: Node,
    file_path: str,
    scope: list[tuple[str, str]],  # (name, node_type) of enclosing definitions
    entities: list[Entity],
    imports: list[ImportStatement],
    calls: list[CallSite],
) -> None:
    for child in node.named_children:
        if child.type == "decorated_definition":
            definition = child.child_by_field_name("definition")
            if definition is not None and definition.type in _DEFINITION_TYPES:
                _extract_definition(definition, child, file_path, scope, entities, imports, calls)
            continue
        if child.type in _DEFINITION_TYPES:
            _extract_definition(child, child, file_path, scope, entities, imports, calls)
            continue
        if child.type == "import_statement":
            imports.extend(_extract_plain_import(child))
            continue
        if child.type == "import_from_statement":
            statement = _extract_from_import(child)
            if statement is not None:
                imports.append(statement)
            continue
        if child.type == "call":
            callee = _callee_text(child.child_by_field_name("function"))
            if callee is not None:
                caller = entity_id(file_path, ".".join(s for s, _ in scope) or None)
                calls.append(CallSite(caller=caller, callee=callee, line=child.start_point[0] + 1))
            # Fall through: arguments may contain further calls.
        # Recurse into compound statements (if/try/with/...) so guarded
        # definitions like `if TYPE_CHECKING:` blocks are still found. The
        # scope stack is untouched: blocks do not introduce named scopes.
        _walk(child, file_path, scope, entities, imports, calls)


def _extract_definition(
    definition: Node,
    outer: Node,  # decorated_definition when decorators exist, else == definition
    file_path: str,
    scope: list[tuple[str, str]],
    entities: list[Entity],
    imports: list[ImportStatement],
    calls: list[CallSite],
) -> None:
    name_node = definition.child_by_field_name("name")
    body = definition.child_by_field_name("body")
    if name_node is None or body is None:  # syntactically broken definition: skip it
        return

    name = _text(name_node)
    qualified = ".".join([s for s, _ in scope] + [name])
    is_method = definition.type == "function_definition" and bool(scope) and scope[-1][1] == "class_definition"

    entity = Entity(
        id=entity_id(file_path, qualified),
        type="class" if definition.type == "class_definition" else ("method" if is_method else "function"),
        name=name,
        file=file_path,
        language="python",
        start_line=outer.start_point[0] + 1,
        end_line=outer.end_point[0] + 1,
        parent=entity_id(file_path, ".".join(s for s, _ in scope) or None),
        parameters=_parameters(definition),
        signature=_signature(definition, body),
        docstring=_docstring_of_block(body),
        decorators=_decorators(outer),
        parent_classes=_superclasses(definition),
        source_code=_text(outer),
    )
    entities.append(entity)

    scope.append((name, definition.type))
    _walk(body, file_path, scope, entities, imports, calls)
    scope.pop()


def _extract_plain_import(node: Node) -> list[ImportStatement]:
    """``import a.b, c as d`` - one statement per imported module."""
    statements = []
    for child in node.named_children:
        if child.type == "dotted_name":
            statements.append(ImportStatement(module=_text(child), line=child.start_point[0] + 1))
        elif child.type == "aliased_import":
            target = child.child_by_field_name("name")
            alias = child.child_by_field_name("alias")
            if target is not None:
                statements.append(
                    ImportStatement(
                        module=_text(target),
                        alias=_text(alias) if alias is not None else None,
                        line=child.start_point[0] + 1,
                    )
                )
    return statements


def _extract_from_import(node: Node) -> ImportStatement | None:
    """``from .base import BaseService as B`` -> module=".base" split into level+module."""
    module_node = node.child_by_field_name("module_name")
    if module_node is None:
        return None

    level = 0
    module: str | None = None
    if module_node.type == "relative_import":
        for part in module_node.children:
            if part.type == "import_prefix":
                level = len(_text(part))
            elif part.type == "dotted_name":
                module = _text(part)
    else:
        module = _text(module_node)

    names: list[ImportedName] = []
    for child in node.named_children:
        # Compare node ids: py-tree-sitter returns a fresh wrapper object per
        # access, so `is` would never match the module_name field node.
        if child.id == module_node.id:
            continue
        if child.type == "dotted_name":
            names.append(ImportedName(name=_text(child)))
        elif child.type == "aliased_import":
            original = child.child_by_field_name("name")
            alias = child.child_by_field_name("alias")
            if original is not None:
                names.append(
                    ImportedName(name=_text(original), alias=_text(alias) if alias is not None else None)
                )
        elif child.type == "wildcard_import":
            names.append(ImportedName(name="*"))
    return ImportStatement(module=module, names=names, level=level, line=node.start_point[0] + 1)


def _parameters(definition: Node) -> list[str]:
    """Parameter names in order; separators (`*`, `/`) are syntax, not parameters."""
    params_node = definition.child_by_field_name("parameters")
    if params_node is None:
        return []
    names = []
    for param in params_node.named_children:
        if param.type == "identifier":
            names.append(_text(param))
        elif param.type in {"typed_parameter", "list_splat_pattern", "dictionary_splat_pattern"}:
            # `x: int` / `*args` / `**kwargs` - the identifier is nested one level down,
            # and for splats the leading `*`/`**` is part of the name developers expect.
            inner = next((c for c in param.children if c.type == "identifier"), None)
            if inner is None:
                continue
            prefix = {"list_splat_pattern": "*", "dictionary_splat_pattern": "**"}.get(param.type, "")
            names.append(prefix + _text(inner))
        elif param.type in {"default_parameter", "typed_default_parameter"}:
            name_node = param.child_by_field_name("name")
            if name_node is not None:
                names.append(_text(name_node))
    return names


def _signature(definition: Node, body: Node) -> str:
    """The header as written, decorators excluded: ``def login(self, ...) -> bool``."""
    if definition.text is None or body.start_byte <= definition.start_byte:
        return _text(definition).splitlines()[0].rstrip(": ")
    header = definition.text[: body.start_byte - definition.start_byte]
    return header.decode("utf-8", errors="replace").rstrip().rstrip(":").rstrip()


def _decorators(outer: Node) -> list[str]:
    """Decorator expressions without the leading ``@``."""
    if outer.type != "decorated_definition":
        return []
    return [_text(d).lstrip("@") for d in outer.named_children if d.type == "decorator"]


def _superclasses(definition: Node) -> list[str]:
    """Base classes as written (``BaseService``, ``Generic[T]``); keyword args excluded."""
    if definition.type != "class_definition":
        return []
    superclasses = definition.child_by_field_name("superclasses")
    if superclasses is None:
        return []
    return [
        _text(c)
        for c in superclasses.named_children
        if c.type not in {"keyword_argument", "comment"}  # metaclass=... is not a base
    ]


def _docstring_of_block(block: Node) -> str | None:
    """The docstring of a module or definition body, unquoted, or None."""
    first = block.named_children[0] if block.named_children else None
    if first is None or first.type != "expression_statement":
        return None
    string_node = first.named_children[0] if first.named_children else None
    if string_node is None or string_node.type != "string":
        return None
    content = "".join(
        _text(part) for part in string_node.children if part.type in {"string_content", "escape_sequence"}
    )
    return content.strip() or None


def _callee_text(function_node: Node | None) -> str | None:
    """The callee as a dotted name, or None when it is a computed expression.

    ``login`` and ``self.auth.login`` are readable; ``handlers[0]()`` or
    ``get_service()()`` are not statically resolvable and yield None.
    """
    if function_node is None:
        return None
    if function_node.type == "identifier":
        return _text(function_node)
    if function_node.type == "attribute":
        obj = _callee_text(function_node.child_by_field_name("object"))
        attr = function_node.child_by_field_name("attribute")
        if obj is None or attr is None:
            return None
        return f"{obj}.{_text(attr)}"
    return None


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")
