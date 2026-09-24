"""Python re-exports: land a call made through a re-export on the symbol that defines it.

``from orchestrator.pkg import FactStore; FactStore(b)`` is resolved per file, from the import
*text*, to ``py:orchestrator.pkg.FactStore`` — an ``external`` placeholder, because the class is
defined in ``orchestrator.pkg.store`` and the package only re-binds it. Measured on this
repository before the fix: 141 in-repo symbols had such a phantom twin, and 1,271 ``CALLS`` edges
(5.5% of all) plus an ``IMPLEMENTS`` landed on them — so ``blast_radius FactStore`` missed 110
callers and called the class third-party. ``pkg verify`` passed; no corpus case had the shape.

**Why here, and not in** :func:`~orchestrator.pkg.import_link.link_imports`: that join sees only
the finished graph, and the graph has already lost what resolution needs. ``from .x import a as b``
records its IMPORTS edge under ``a`` (the name ``b`` a caller uses is gone), and ``from .x import
*`` records no names at all. The front-end has both while it reads each file, so it keeps a
per-module binding table (:func:`collect_exports`) and :func:`resolve_reexports` follows it in
``PythonExtractor.finalize``, once every module is known.

The rules are Python's own, applied precision-first:

- **Any module re-exports**, not only a package ``__init__``: every name a module binds at module
  level is importable from it. Chains are followed (``app`` → ``app.sub`` → ``app.sub.deep``),
  cycle-safe.
- **Only module-level bindings count.** An import inside a function binds a local. Statements
  under a module-level ``if``/``try``/``with`` do bind — including ``if TYPE_CHECKING:``.
- **``from x import *``** binds x's literal ``__all__`` when it has one, and otherwise every
  name x binds that does not start with an underscore. A non-literal ``__all__`` is unknowable,
  so the star binds nothing we will follow.
- **A name bound more than once resolves only when every binding agrees.** ``try: from .fast
  import f / except ImportError: from .slow import f`` depends on the environment; picking either
  would be a guess, so the call stays on the placeholder, where ``pkg verify``'s
  ``phantom-symbol`` warning counts it. A module-level ``__getattr__`` (PEP 562) binds nothing
  statically and is left alone for the same reason.
- **A member of a re-exported class resolves too** (``from app import Store; Store.get()`` →
  ``py:app.Store.get``): the class part is resolved, then the member must exist on it.

Every edge kind is repointed, IMPORTS included — an import through a re-export names the defining
symbol, exactly as a direct import already does — and the orphaned placeholders are dropped.
Iteration is sorted throughout: the same tree gives the same graph.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass, field

from orchestrator.pkg.facts import Edge, FactBatch

# `from x import *` with an `__all__` we cannot read binds a set we cannot know.
_UNKNOWABLE: tuple[str, ...] = ("<non-literal __all__>",)


@dataclass
class ModuleExports:
    """What one module binds at module level, for another module to import from it."""

    bindings: dict[str, set[str]] = field(default_factory=dict)  # name -> {"py:<target>"}
    stars: list[str] = field(default_factory=list)  # "py:<module>" of each `from m import *`
    all_names: tuple[str, ...] | None = None  # literal `__all__`, or None when absent
    defined: set[str] = field(default_factory=set)  # top-level def / class names

    def star_exposes(self, name: str) -> bool:
        """Would ``from <this module> import *`` bind ``name``?"""
        if self.all_names is _UNKNOWABLE:
            return False
        if self.all_names is not None:
            return name in self.all_names
        return not name.startswith("_") and (
            name in self.defined or name in self.bindings or bool(self.stars)
        )


def _module_level(body: list[ast.stmt]) -> list[ast.stmt]:
    """Statements that bind at module level: descend into if/try/with, never into def/class."""
    out: list[ast.stmt] = []
    for stmt in body:
        out.append(stmt)
        if isinstance(stmt, ast.If | ast.With | ast.AsyncWith | ast.Try | ast.TryStar):
            nested = [
                *getattr(stmt, "body", []),
                *getattr(stmt, "orelse", []),
                *getattr(stmt, "finalbody", []),
            ]
            for handler in getattr(stmt, "handlers", []) or []:
                nested.extend(handler.body)
            out.extend(_module_level(nested))
    return out


def _literal_all(value: ast.expr) -> tuple[str, ...]:
    if isinstance(value, ast.List | ast.Tuple) and all(
        isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts
    ):
        return tuple(str(e.value) for e in value.elts if isinstance(e, ast.Constant))
    return _UNKNOWABLE


def collect_exports(tree: ast.Module, import_base: Callable[[ast.ImportFrom], str]) -> ModuleExports:
    """One module's re-export table. ``import_base`` resolves a (possibly relative) ``from``."""
    out = ModuleExports()
    for stmt in _module_level(tree.body):
        if isinstance(stmt, ast.Import):
            for a in stmt.names:
                # `import a.b` binds `a` (the package); `import a.b as c` binds `c` to `a.b`.
                top = a.name.split(".")[0]
                out.bindings.setdefault(a.asname or top, set()).add(f"py:{a.name if a.asname else top}")
        elif isinstance(stmt, ast.ImportFrom):
            base = import_base(stmt)
            if base.startswith("."):
                continue  # climbed out of the scanned tree — never joinable
            for a in stmt.names:
                if a.name == "*":
                    out.stars.append(f"py:{base}")
                    continue
                target = f"py:{base}.{a.name}" if base else f"py:{a.name}"
                out.bindings.setdefault(a.asname or a.name, set()).add(target)
        elif isinstance(stmt, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            out.defined.add(stmt.name)
        elif isinstance(stmt, ast.Assign) and any(_is_all(t) for t in stmt.targets):
            out.all_names = _literal_all(stmt.value)
        elif isinstance(stmt, ast.AnnAssign) and _is_all(stmt.target) and stmt.value is not None:
            out.all_names = _literal_all(stmt.value)  # `__all__: list[str] = [...]`
        elif isinstance(stmt, ast.AugAssign) and _is_all(stmt.target):
            out.all_names = _UNKNOWABLE  # built up piecewise — not a literal we can trust
    return out


def _is_all(target: ast.expr) -> bool:
    return isinstance(target, ast.Name) and target.id == "__all__"


class _Resolver:
    def __init__(self, exports: dict[str, ModuleExports], grounded: set[str]) -> None:
        self._exports = exports
        self._grounded = grounded
        self._memo: dict[str, str | None] = {}

    def resolve(self, target: str) -> str | None:
        """The grounded id ``target`` denotes through re-exports, or None when it cannot be told."""
        if target not in self._memo:
            self._memo[target] = self._walk(target, frozenset())
        return self._memo[target]

    def _walk(self, target: str, seen: frozenset[str]) -> str | None:
        if target in self._grounded:
            return target
        if target in seen:
            return None  # a re-export cycle binds nothing
        seen = seen | {target}
        module, _, name = target.rpartition(".")
        if not module or not name:
            return None
        exports = self._exports.get(module)
        if exports is None:
            # Not a first-party module: perhaps a member of a re-exported symbol
            # (`py:app.Store.get`, where `py:app.Store` re-exports a class).
            owner = self._walk(module, seen)
            member = f"{owner}.{name}" if owner is not None else None
            return member if member in self._grounded else None
        candidates: set[str | None] = {self._walk(t, seen) for t in sorted(exports.bindings.get(name, ()))}
        for star in exports.stars:
            # A star from outside the tree is not followed: what it binds is unknowable here,
            # and a name nothing visible binds stays unresolved anyway.
            source = self._exports.get(star)
            if source is not None and source.star_exposes(name):
                candidates.add(self._walk(f"{star}.{name}", seen))
        if len(candidates) == 1:
            return next(iter(candidates))
        return None  # unbound, or bindings that disagree — never guessed


def resolve_reexports(batch: FactBatch, exports: dict[str, ModuleExports]) -> FactBatch:
    """Repoint every edge whose target is a Python re-export placeholder; drop the placeholders."""
    grounded = {n.id for n in batch.nodes if n.grounded}
    resolver = _Resolver(exports, grounded)
    alias: dict[str, str] = {}
    for node in sorted(batch.nodes, key=lambda n: n.id):
        if node.external and node.id.startswith("py:"):
            real = resolver.resolve(node.id)
            if real is not None and real != node.id:
                alias[node.id] = real
    if not alias:
        return batch
    result = FactBatch()
    for node in batch.nodes:
        if node.id not in alias:
            result.add_node(node)
    for edge in batch.edges:
        src, dst = alias.get(edge.src, edge.src), alias.get(edge.dst, edge.dst)
        result.add_edge(
            edge if (src, dst) == (edge.src, edge.dst) else Edge(src, dst, edge.kind, edge.provenance)
        )
    return result


__all__ = ["ModuleExports", "collect_exports", "resolve_reexports"]
