"""Python re-exports land on the defining symbol, never on an import-text placeholder (B20).

Before this, ``from app import Store; Store()`` produced ``CALLS -> py:app.Store`` — an external
node — while the class was grounded as ``py:app.store.Store``: 1,271 CALLS edges on this
repository alone. The corpus cases ``python/package_reexport`` and ``python/reexport_refusals``
carry the shapes end to end; these pin each rule of ``python_reexport`` on its own.
"""

from __future__ import annotations

import ast
from pathlib import Path

from orchestrator.pkg import EdgeKind, FactBatch, RepoCodeExtractor
from orchestrator.pkg.facts import Edge, Node, NodeKind, Provenance
from orchestrator.pkg.python_reexport import ModuleExports, collect_exports, resolve_reexports


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _pairs(batch: FactBatch, kind: EdgeKind) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is kind}


def _exports(source: str, base: str = "pkg") -> ModuleExports:
    def import_base(stmt: ast.ImportFrom) -> str:
        return f"{base}.{stmt.module}" if stmt.level else (stmt.module or "")

    return collect_exports(ast.parse(source), import_base)


# ---- the binding table ---------------------------------------------------------------------


def test_only_module_level_imports_bind() -> None:
    table = _exports(
        "from .a import kept\n"
        "if TYPE_CHECKING:\n    from .b import typed\n"
        "def __getattr__(name):\n    from .c import lazy\n    return lazy\n"
        "class K:\n    from .d import inner\n"
    )
    assert table.bindings == {"kept": {"py:pkg.a.kept"}, "typed": {"py:pkg.b.typed"}}
    assert table.defined == {"__getattr__", "K"}


def test_a_name_bound_twice_keeps_both_targets() -> None:
    table = _exports("try:\n    from .fast import f\nexcept ImportError:\n    from .slow import f\n")
    assert table.bindings == {"f": {"py:pkg.fast.f", "py:pkg.slow.f"}}


def test_renames_plain_imports_and_stars() -> None:
    table = _exports("from .util import helper as assist\nimport a.b\nimport c.d as e\nfrom .m import *\n")
    assert table.bindings == {"assist": {"py:pkg.util.helper"}, "a": {"py:a"}, "e": {"py:c.d"}}
    assert table.stars == ["py:pkg.m"]


def test_all_is_read_only_when_literal() -> None:
    assert _exports('__all__ = ["A", "B"]\n').all_names == ("A", "B")
    assert _exports('__all__: list[str] = ["A"]\n').all_names == ("A",)
    assert not _exports("__all__ = names()\n").star_exposes("A")
    assert not _exports('__all__ = ["A"]\n__all__ += ["B"]\n').star_exposes("A")


def test_star_exposure_follows_all_then_public_names() -> None:
    listed = _exports('__all__ = ["Order"]\nclass Order: ...\nclass Draft: ...\n')
    assert listed.star_exposes("Order") and not listed.star_exposes("Draft")
    unlisted = _exports("def area(): ...\ndef _private(): ...\n")
    assert unlisted.star_exposes("area") and not unlisted.star_exposes("_private")


# ---- resolution, end to end through the extractor ------------------------------------------


def test_a_package_reexport_lands_calls_imports_and_bases_on_the_definition(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "app/__init__.py": "from .store import Store\nfrom .base import Base\n",
            "app/store.py": "class Store:\n    def get(self):\n        return 1\n",
            "app/base.py": "class Base:\n    pass\n",
            "client.py": (
                "from app import Base, Store\n"
                "class Special(Base):\n    pass\n"
                "def make():\n    return Store()\n"
                "def member():\n    return Store.get(Store())\n"
            ),
        },
    )
    batch = RepoCodeExtractor().extract(tmp_path)
    calls = _pairs(batch, EdgeKind.CALLS)
    assert ("py:client.make", "py:app.store.Store") in calls
    assert ("py:client.member", "py:app.store.Store.get") in calls
    assert ("py:client.Special", "py:app.base.Base") in _pairs(batch, EdgeKind.IMPLEMENTS)
    # D4: the import names the defining symbol, as a direct import would — not the package.
    assert ("py:client", "py:app.store.Store") in _pairs(batch, EdgeKind.IMPORTS)
    ids = {n.id for n in batch.nodes}
    assert not {"py:app.Store", "py:app.Base", "py:app.Store.get"} & ids  # placeholders dropped


def test_a_chain_through_a_subpackage_and_an_ordinary_module(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "app/__init__.py": "from .sub import Engine\n",
            "app/sub/__init__.py": "from .deep import Engine\n",
            "app/sub/deep.py": "class Engine:\n    pass\n",
            "app/util.py": "def helper():\n    return 2\n",
            "app/relay.py": "from app.util import helper\n",
            "client.py": (
                "from app import Engine\nfrom app.relay import helper\n"
                "def chain():\n    return Engine()\n"
                "def relay():\n    return helper()\n"
            ),
        },
    )
    calls = _pairs(RepoCodeExtractor().extract(tmp_path), EdgeKind.CALLS)
    assert ("py:client.chain", "py:app.sub.deep.Engine") in calls
    assert ("py:client.relay", "py:app.util.helper") in calls


def test_disagreeing_bindings_and_lazy_getattr_stay_on_the_placeholder(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "lib/__init__.py": (
                "try:\n    from .fast import compute\nexcept ImportError:\n    from .slow import compute\n"
                "def __getattr__(name):\n    from .lazy import Lazy\n    return Lazy\n"
            ),
            "lib/fast.py": "def compute():\n    return 1\n",
            "lib/slow.py": "def compute():\n    return 1\n",
            "lib/lazy.py": "class Lazy:\n    pass\n",
            "main.py": (
                "from lib import Lazy, compute\ndef a():\n    return compute()\ndef b():\n    return Lazy()\n"
            ),
        },
    )
    calls = _pairs(RepoCodeExtractor().extract(tmp_path), EdgeKind.CALLS)
    assert calls >= {("py:main.a", "py:lib.compute"), ("py:main.b", "py:lib.Lazy")}
    assert not calls & {
        ("py:main.a", "py:lib.fast.compute"),
        ("py:main.a", "py:lib.slow.compute"),
        ("py:main.b", "py:lib.lazy.Lazy"),
    }


def test_a_reexport_cycle_terminates_and_resolves_nothing(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "a.py": "from b import thing\n",
            "b.py": "from a import thing\n",
            "main.py": "from a import thing\ndef go():\n    return thing()\n",
        },
    )
    assert ("py:main.go", "py:a.thing") in _pairs(RepoCodeExtractor().extract(tmp_path), EdgeKind.CALLS)


def test_a_third_party_star_does_not_block_an_explicit_reexport(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "app/__init__.py": "from numpy import *\nfrom .store import Store\n",
            "app/store.py": "class Store:\n    pass\n",
            "client.py": "from app import Store\ndef go():\n    return Store()\n",
        },
    )
    calls = _pairs(RepoCodeExtractor().extract(tmp_path), EdgeKind.CALLS)
    assert ("py:client.go", "py:app.store.Store") in calls


def test_resolution_is_deterministic(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "app/__init__.py": "from .a import *\nfrom .b import *\nfrom .util import helper as assist\n",
            "app/a.py": "def one():\n    return 1\n",
            "app/b.py": "def two():\n    return 2\n",
            "app/util.py": "def helper():\n    return 3\n",
            "client.py": "from app import assist, one, two\ndef go():\n    one()\n    two()\n    assist()\n",
        },
    )

    def snapshot() -> list[tuple[str, str, str]]:
        return sorted((e.src, e.dst, e.kind.value) for e in RepoCodeExtractor().extract(tmp_path).edges)

    assert snapshot() == snapshot()
    assert ("py:client.go", "py:app.a.one", "CALLS") in snapshot()


def test_nothing_to_resolve_returns_the_same_batch() -> None:
    batch = FactBatch()
    batch.add_node(Node("py:m", NodeKind.MODULE, "m", "python", Provenance("m.py", 1)))
    batch.add_node(Node("py:os.path.join", NodeKind.FUNCTION, "join", "python", external=True))
    batch.add_edge(Edge("py:m", "py:os.path.join", EdgeKind.CALLS))
    assert resolve_reexports(batch, {"py:m": ModuleExports()}) is batch
