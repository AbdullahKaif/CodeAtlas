"""Tests for the Tree-sitter Python parser (entity + import extraction)."""
from __future__ import annotations

from backend.parser.tree_parser import parse_python_file

SOURCE = b'''"""Module docstring."""
from app.base import BaseService
import os, sys as system
from . import helpers
from ..pkg import tools
from app.auth import *


@register
class AuthService(BaseService, metaclass=Meta):
    """Handles auth."""

    def __init__(self, name: str = "auth") -> None:
        self.name = name

    @property
    def label(self):
        """Human readable label."""
        return self.name

    async def login(self, username, password="", *args, **kwargs) -> bool:
        def check():
            return True
        return check()


def top_level(x: int, y: int = 2) -> int:
    """Add two numbers."""
    return x + y


if True:
    def guarded():
        pass
'''


class TestEntityExtraction:
    def parse(self):
        return parse_python_file(SOURCE, "app/service.py")

    def test_ids_types_and_parents(self):
        result = self.parse()
        by_id = {e.id: e for e in result.entities}

        assert by_id["app/service.py::AuthService"].type == "class"
        assert by_id["app/service.py::AuthService.__init__"].type == "method"
        assert by_id["app/service.py::AuthService.login"].type == "method"
        # A function nested inside a method is a function, not a method:
        assert by_id["app/service.py::AuthService.login.check"].type == "function"
        assert by_id["app/service.py::top_level"].type == "function"
        # Definitions inside plain blocks (if/try/...) are still found:
        assert by_id["app/service.py::guarded"].type == "function"

        assert by_id["app/service.py::AuthService"].parent == "app/service.py"
        assert by_id["app/service.py::AuthService.login"].parent == "app/service.py::AuthService"
        assert by_id["app/service.py::AuthService.login.check"].parent == "app/service.py::AuthService.login"
        assert not result.had_syntax_errors

    def test_class_details(self):
        result = self.parse()
        cls = next(e for e in result.entities if e.type == "class")
        assert cls.name == "AuthService"
        assert cls.parent_classes == ["BaseService"]  # metaclass=... is not a base
        assert cls.decorators == ["register"]
        assert cls.docstring == "Handles auth."
        assert cls.start_line == 9  # the @register line - decorators belong to the entity
        assert cls.source_code is not None and cls.source_code.startswith("@register")

    def test_function_details(self):
        result = self.parse()
        by_id = {e.id: e for e in result.entities}

        login = by_id["app/service.py::AuthService.login"]
        assert login.parameters == ["self", "username", "password", "*args", "**kwargs"]
        assert login.signature == "async def login(self, username, password=\"\", *args, **kwargs) -> bool"
        assert login.docstring is None

        top = by_id["app/service.py::top_level"]
        assert top.parameters == ["x", "y"]
        assert top.docstring == "Add two numbers."
        assert top.start_line == 27 and top.end_line == 29

        label = by_id["app/service.py::AuthService.label"]
        assert label.decorators == ["property"]

    def test_module_docstring(self):
        assert self.parse().module_docstring == "Module docstring."


class TestImportExtraction:
    def test_all_import_forms(self):
        imports = parse_python_file(SOURCE, "app/service.py").imports
        as_tuples = {(i.module, tuple(i.names), i.level) for i in imports}

        assert ("app.base", ("BaseService",), 0) in as_tuples
        assert ("os", (), 0) in as_tuples
        assert ("sys", (), 0) in as_tuples  # aliased: original module name kept
        assert (None, ("helpers",), 1) in as_tuples  # from . import helpers
        assert ("pkg", ("tools",), 2) in as_tuples  # from ..pkg import tools
        assert ("app.auth", ("*",), 0) in as_tuples

    def test_import_lines_are_recorded(self):
        imports = parse_python_file(SOURCE, "app/service.py").imports
        base_import = next(i for i in imports if i.module == "app.base")
        assert base_import.line == 2


class TestBrokenInput:
    def test_syntax_errors_do_not_lose_valid_entities(self):
        source = b"def broken(:\n    pass\n\nclass StillHere:\n    pass\n"
        result = parse_python_file(source, "bad.py")
        assert result.had_syntax_errors
        assert any(e.id == "bad.py::StillHere" for e in result.entities)

    def test_empty_file(self):
        result = parse_python_file(b"", "empty.py")
        assert result.entities == []
        assert result.imports == []
        assert result.module_docstring is None
        assert not result.had_syntax_errors

    def test_non_utf8_bytes_do_not_crash(self):
        source = b"def f():\n    return '\xff\xfe garbage'\n"
        result = parse_python_file(source, "weird.py")
        assert any(e.name == "f" for e in result.entities)
