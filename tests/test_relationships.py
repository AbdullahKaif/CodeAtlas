"""Tests for relationship extraction: containment and import resolution."""
from __future__ import annotations

from backend.parser.models import Entity, ImportStatement
from backend.parser.relationships import (
    SymbolResolver,
    build_module_map,
    contains_relationships,
    import_relationships,
)

FILES = [
    "main.py",
    "app/__init__.py",
    "app/auth.py",
    "app/base.py",
    "app/sub/__init__.py",
    "app/sub/deep.py",
    "util.py",
    "util.pyi",
]


class TestModuleMap:
    def test_modules_and_packages(self):
        module_map = build_module_map(FILES)
        assert module_map["main"] == "main.py"
        assert module_map["app"] == "app/__init__.py"
        assert module_map["app.auth"] == "app/auth.py"
        assert module_map["app.sub"] == "app/sub/__init__.py"
        assert module_map["app.sub.deep"] == "app/sub/deep.py"

    def test_stub_files_never_shadow_real_modules(self):
        assert build_module_map(FILES)["util"] == "util.py"
        # A stub without a twin still resolves:
        assert build_module_map(["only_stub.pyi"])["only_stub"] == "only_stub.pyi"

    def test_root_init_names_no_module(self):
        assert build_module_map(["__init__.py"]) == {}


def statement(names: tuple[str, ...] = (), **kwargs) -> ImportStatement:
    return ImportStatement(line=1, names=[{"name": n} for n in names], **kwargs)


class TestImportResolution:
    def resolve(self, file_path: str, names: tuple[str, ...] = (), **kwargs) -> set[str]:
        module_map = build_module_map(FILES)
        edges = import_relationships(file_path, [statement(names, **kwargs)], module_map)
        assert all(e.relation == "imports" and e.source == file_path for e in edges)
        return {e.target for e in edges}

    def test_absolute_from_import(self):
        assert self.resolve("app/auth.py", module="app.base", names=["BaseService"]) == {"app/base.py"}

    def test_plain_package_import(self):
        assert self.resolve("main.py", module="app") == {"app/__init__.py"}

    def test_from_package_import_module_links_both(self):
        # `from app import auth` executes app/__init__.py AND app/auth.py.
        assert self.resolve("main.py", module="app", names=["auth"]) == {"app/__init__.py", "app/auth.py"}

    def test_relative_import_of_sibling_module(self):
        assert self.resolve("app/sub/deep.py", module=None, names=["deep2"], level=1) == {
            "app/sub/__init__.py"
        }

    def test_relative_import_two_levels_up(self):
        assert self.resolve("app/sub/deep.py", module="auth", names=["login"], level=2) == {"app/auth.py"}

    def test_relative_import_climbing_above_root_is_ignored(self):
        assert self.resolve("app/auth.py", module="x", names=[], level=4) == set()

    def test_external_imports_yield_no_edges(self):
        assert self.resolve("app/auth.py", module="os", names=[]) == set()
        assert self.resolve("app/auth.py", module="fastapi", names=["FastAPI"]) == set()

    def test_self_import_yields_no_edge(self):
        assert self.resolve("app/auth.py", module="app.auth", names=["x"]) == set()

    def test_wildcard_import_links_the_module(self):
        assert self.resolve("main.py", module="app.auth", names=["*"]) == {"app/auth.py"}

    def test_duplicate_targets_deduplicated(self):
        module_map = build_module_map(FILES)
        statements = [
            statement(("login",), module="app.auth"),
            statement(("logout",), module="app.auth"),
        ]
        edges = import_relationships("main.py", statements, module_map)
        assert len(edges) == 1


class TestSymbolResolver:
    ENTITY_TYPES = {
        "app/auth.py": "file",
        "app/auth.py::login": "function",
        "app/auth.py::AuthService": "class",
        "app/auth.py::AuthService.login": "method",
        "app/base.py::BaseService": "class",
        "util.py::helper": "function",
        "main.py::Local": "class",
    }

    def resolver(self, *imports: ImportStatement, entity_types=None) -> SymbolResolver:
        return SymbolResolver(
            "main.py", list(imports), build_module_map(FILES), entity_types or self.ENTITY_TYPES
        )

    def test_local_definition_wins(self):
        assert self.resolver().resolve("Local") == "main.py::Local"

    def test_imported_symbol(self):
        r = self.resolver(statement(("login",), module="app.auth"))
        assert r.resolve("login") == "app/auth.py::login"

    def test_imported_symbol_alias(self):
        stmt = ImportStatement(module="app.auth", names=[{"name": "login", "alias": "l"}], line=1)
        r = self.resolver(stmt)
        assert r.resolve("l") == "app/auth.py::login"
        assert r.resolve("login") is None  # the alias is the only binding

    def test_module_attribute_chain(self):
        r = self.resolver(statement(module="app.auth"))
        assert r.resolve("app.auth.login") == "app/auth.py::login"
        assert r.resolve("app.auth.AuthService") == "app/auth.py::AuthService"

    def test_aliased_module(self):
        r = self.resolver(statement(module="app.auth", alias="aa"))
        assert r.resolve("aa.login") == "app/auth.py::login"

    def test_from_package_import_module(self):
        # `from app import auth; auth.login()` - auth is a module, not a symbol.
        r = self.resolver(statement(("auth",), module="app"))
        assert r.resolve("auth.login") == "app/auth.py::login"

    def test_wildcard_resolves_only_when_unambiguous(self):
        r = self.resolver(statement(("*",), module="app.auth"), statement(("*",), module="util"))
        assert r.resolve("helper") == "util.py::helper"  # only util defines it
        ambiguous_types = {**self.ENTITY_TYPES, "util.py::login": "function"}
        r = self.resolver(
            statement(("*",), module="app.auth"),
            statement(("*",), module="util"),
            entity_types=ambiguous_types,
        )
        assert r.resolve("login") is None  # both modules define login: no guess

    def test_self_calls_resolve_within_the_class_only(self):
        r = self.resolver()
        assert r.resolve("self.login", enclosing_class="app/auth.py::AuthService") == (
            "app/auth.py::AuthService.login"
        )
        assert r.resolve("self.login") is None
        assert r.resolve("self.missing", enclosing_class="app/auth.py::AuthService") is None

    def test_unresolvable_names_yield_none(self):
        r = self.resolver(statement(module="os"))
        assert r.resolve("os.path.join") is None  # external module
        assert r.resolve("nonexistent") is None
        assert r.resolve("not-an-identifier!") is None


class TestContains:
    def test_edges_follow_parent_chain(self):
        entities = [
            Entity(id="a.py", type="file", name="a.py", file="a.py", start_line=1, end_line=10),
            Entity(id="a.py::C", type="class", name="C", file="a.py", start_line=2, end_line=9, parent="a.py"),
            Entity(id="a.py::C.m", type="method", name="m", file="a.py", start_line=3, end_line=5, parent="a.py::C"),
        ]
        edges = {(r.source, r.target) for r in contains_relationships(entities)}
        assert edges == {("a.py", "a.py::C"), ("a.py::C", "a.py::C.m")}
