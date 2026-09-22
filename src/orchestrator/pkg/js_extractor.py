"""JavaScript front-end for the PKG extractor — the TypeScript front-end, taught CommonJS.

A subclass rather than a new parser (javascript-support-roadmap D1). Every TypeScript-only
construct the parent reads — `interface`, `type`, `enum`, generics, `implements`, `as` — is a
clean no-op on JavaScript, and the TSX grammar parses plain JavaScript and JSX alike, so what
JavaScript actually needs is narrow and listed below. It rides the ``typescript`` extra: it
needs `tree_sitter_typescript` and nothing else, exactly as the Gradle reader rides ``kotlin``.

**Ids are TypeScript's, the language tag is not** (D4). A JavaScript module mints
``ts:path/to/file``, the same namespace TypeScript uses, because the two share one module
resolution: a `.ts` file importing `./util` reaches `util.js` and the reverse, and a separate
prefix would make every such edge resolve to nothing. Nodes are tagged ``javascript`` all the
same — that string, not the prefix, is what `pkg accuracy` and the capability matrix count by.
Kotlin is the precedent: ``kotlin`` nodes, ``java:`` ids.

What JavaScript adds over the parent:

* **CommonJS imports.** The parent reads `import` statements only, so on a CommonJS repository
  every module was an island — no ``IMPORTS``, and with no imports no cross-file ``CALLS`` or
  ``IMPLEMENTS`` either. Read here: `const m = require('./m')` (a *namespace*: `m.f()` is the
  export `f`), `const {a} = require('./m')`, `const a = require('./m').a`, and a bare
  `require('./m')` for its side effects.
* **CommonJS exports.** `exports.f = …`, `module.exports.f = …`, `module.exports = {f, g() {}}`,
  a named `module.exports = function f() {}`, and `obj.f = …` where `obj` *is* the exports
  object (`var app = exports = module.exports = {}`, or `module.exports = res`) — the form the
  classic CommonJS library uses most (see `_export_aliases`). The parent sees none of these —
  they are assignments, not declarations — so a CommonJS module's public surface was empty.
* **Express handlers named as members** — `app.get('/', site.index)` — bound to the export, so
  the ``EXPOSES`` edge exists (see `_route_handler`). The route reader it shares with TypeScript
  also now reads `var app = module.exports = express()`, a chained router binding.
* **The data layer** — Sequelize models as ``Entity``/``Field`` nodes and ``REFERENCES`` edges,
  read by :mod:`orchestrator.pkg.js_orm`.
* **An existence check on what it resolved.** See :meth:`JavaScriptExtractor.finalize`.

Precision-first, as every front-end. Left out on purpose, and declared in the corpus's
``known_gaps`` rather than guessed at: calling a whole module (`m()` after `m = require(…)` —
what it reaches depends on what that module assigned), a renamed destructuring
(`{run: go} = require(…)` — resolution names the target by the local), an anonymous
`module.exports = function () {}`, class expressions, `require` inside a function, dynamic
`import()`, and member calls on a receiver not built with `new` in the same body (D8 —
JavaScript states no types for the parent's typed-receiver rule to read).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.typescript_extractor import (
    _FUNC_CONST_DECLS,
    TypeScriptExtractor,
    _field_text,
    _import_target,
    _relative_module,
    _text,
)

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_LANG = "javascript"
_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs")
_FUNCTION_VALUES = frozenset({"arrow_function", "function_expression", "function", "generator_function"})
#: Edge kinds this front-end resolves by name, and so the ones `finalize` checks landed.
_CHECKED = frozenset({EdgeKind.CALLS, EdgeKind.IMPLEMENTS, EdgeKind.EXPOSES, EdgeKind.REFERENCES})
#: A name a call site can actually spell as `m.name`. A string key like `'a-b'` cannot.
_IDENTIFIER = re.compile(r"^[A-Za-z_$][\w$]*$")


class JavaScriptExtractor(TypeScriptExtractor):
    """JavaScript front-end (tree-sitter, via the ``typescript`` extra)."""

    language: str = _LANG
    suffixes: tuple[str, ...] = _SUFFIXES

    def __init__(self) -> None:
        super().__init__()
        #: Top-level names bound to the module's exports object in the file being read. Set by
        #: the `_imports` pre-pass — the one per-file hook that sees every declaration before
        #: any is emitted — and read by `_emit_statement`.
        self._export_aliases: set[str] = set()

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch:
        return _retag(super().extract(path=path, module=module, rel=rel))

    def finalize(self, batch: FactBatch) -> FactBatch:
        """The parent's deferred member calls, then drop what this front-end could not land.

        JavaScript resolves more calls *by name* than TypeScript does — every `m.f()` through a
        `require` binding — and a name is a claim, not a fact. `m.f()` names `ts:m.f`, and the
        module may export `f` under another name (`module.exports = {f: helper}`) or not at all.
        Only the whole graph knows, so the check sits here: a ``CALLS`` or ``IMPLEMENTS`` edge
        drawn from a JavaScript file to a node nothing declares is dropped rather than left
        dangling. The same skip-rather-than-guess rule the parent applies to member calls,
        extended to the edges this front-end adds.
        """
        return _drop_unlanded(super().finalize(batch))

    def _imports(
        self, decls: list[TSNode | None], module_id: str, source: bytes, rel: str, batch: FactBatch
    ) -> tuple[dict[str, str], set[str]]:
        by_local, namespaces = super()._imports(decls, module_id, source, rel, batch)
        self._export_aliases = _export_aliases(decls, source)
        for node in decls:
            if node is None:
                continue
            if node.type in _FUNC_CONST_DECLS:
                for declarator in node.named_children:
                    if declarator.type != "variable_declarator":
                        continue
                    bound = _require_binding(declarator, source)
                    if bound is None:
                        continue
                    spec, locals_, is_namespace = bound
                    _import_edge(spec, module_id, rel, declarator.start_point[0] + 1, batch)
                    for local in locals_:
                        by_local[local] = spec
                    if is_namespace:
                        namespaces.update(locals_)
            elif node.type == "expression_statement" and node.named_children:
                side_effect = _require_spec(node.named_children[0], source)
                if side_effect is not None:  # `require('./polyfill')` — imported for its side effects
                    _import_edge(side_effect, module_id, rel, node.start_point[0] + 1, batch)
        return by_local, namespaces

    def _emit_module(
        self, root: TSNode, module_id: str, source: bytes, rel: str, batch: FactBatch, imports: dict[str, str]
    ) -> None:
        """The data layer: Sequelize models and their associations (see :mod:`js_orm`)."""
        from orchestrator.pkg.js_orm import scan

        scan(root, module_id, source, rel, batch, imports)

    def _route_handler(
        self, module_id: str, imports: dict[str, str], namespaces: set[str], source: bytes, rel: str
    ) -> Callable[[TSNode], str | None] | None:
        """Bind `app.get('/', site.index)` — the CommonJS way to route to another file's export.

        Two spellings: a member of a `require` namespace (`site.index`, where
        `site = require('./site')`), and a member of this module's own exports object
        (`exports.index`, or an alias of it). Either names its target by name, which is safe
        here and nowhere upstream: `finalize` drops an ``EXPOSES`` whose handler does not exist.
        """
        aliases = self._export_aliases

        def resolve(member: TSNode) -> str | None:
            owner = member.child_by_field_name("object")
            prop = _field_text(member, "property", source)
            if owner is None or not _IDENTIFIER.match(prop):
                return None
            name = _text(owner, source)
            if name in ("exports", "module.exports") or (owner.type == "identifier" and name in aliases):
                return f"{module_id}.{prop}"
            if owner.type == "identifier" and name in namespaces and name in imports:
                return _import_target(imports[name], prop, rel)
            return None

        return resolve

    def _emit_statement(
        self,
        node: TSNode,
        module_id: str,
        source: bytes,
        rel: str,
        batch: FactBatch,
        funcs: list[tuple[str, str | None, TSNode]],
        local_funcs: dict[str, str],
    ) -> None:
        """CommonJS exports — the assignments that are a CommonJS module's public surface."""
        if node.type != "expression_statement" or not node.named_children:
            return
        expr = node.named_children[0]
        if expr.type != "assignment_expression":
            return
        left = expr.child_by_field_name("left")
        right = expr.child_by_field_name("right")
        if left is None or right is None or left.type != "member_expression":
            return
        line = node.start_point[0] + 1
        if _text(left, source) == "module.exports":
            if right.type == "object":
                for member in right.named_children:
                    _emit_object_member(member, module_id, source, rel, batch, funcs)
            elif right.type in _FUNCTION_VALUES:
                # Named only: an anonymous default export has no name any call site uses.
                name = _field_text(right, "name", source)
                if name:
                    _emit_export(name, right, module_id, rel, line, batch, funcs)
            return
        owner = left.child_by_field_name("object")
        prop = left.child_by_field_name("property")
        if (
            owner is not None
            and prop is not None
            and (
                _text(owner, source) in ("exports", "module.exports")
                or (owner.type == "identifier" and _text(owner, source) in self._export_aliases)
            )
            and right.type in _FUNCTION_VALUES
        ):
            _emit_export(_text(prop, source), right, module_id, rel, line, batch, funcs)


def _export_aliases(decls: list[TSNode | None], source: bytes) -> set[str]:
    """Top-level names that *are* the module's exports object.

    The classic CommonJS library does not write `exports.f = …` — it names the object once and
    augments it. Express's `lib/` spells 43 of its 49 exported functions this way:

        var app = exports = module.exports = {};     // a declaration, chained through
        app.init = function init() { … };

        var res = Object.create(http.ServerResponse.prototype);
        module.exports = res;                        // an assignment, naming a declared object
        res.send = function send(body) { … };

    Either way `require('./application').init` *is* `app.init`, so `app.init = …` is an export
    exactly as `exports.init = …` is. Only an exact binding counts: a name merely *assigned
    from* the exports object (`const e = module.exports`) is left out, since a later
    `module.exports = …` would leave it pointing at the old object.
    """
    aliases: set[str] = set()
    for node in decls:
        if node is None:
            continue
        if node.type in _FUNC_CONST_DECLS:
            for declarator in node.named_children:
                name = declarator.child_by_field_name("name")
                value = declarator.child_by_field_name("value")
                if name is None or value is None or name.type != "identifier":
                    continue
                if _assigns_exports(value, source):
                    aliases.add(_text(name, source))
        elif node.type == "expression_statement" and node.named_children:
            expr = node.named_children[0]
            while (
                expr.type == "assignment_expression"
            ):  # module.exports = res; exports = module.exports = res
                left = expr.child_by_field_name("left")
                right = expr.child_by_field_name("right")
                if left is None or right is None or _text(left, source) not in ("module.exports", "exports"):
                    break
                if right.type == "identifier":
                    aliases.add(_text(right, source))
                    break
                expr = right
    return aliases


def _assigns_exports(value: TSNode, source: bytes) -> bool:
    """Whether a declarator's value is an assignment chain through `module.exports`/`exports`."""
    node: TSNode | None = value
    while node is not None and node.type == "assignment_expression":
        left = node.child_by_field_name("left")
        if left is not None and _text(left, source) in ("module.exports", "exports"):
            return True
        node = node.child_by_field_name("right")
    return False


def _require_spec(node: TSNode | None, source: bytes) -> str | None:
    """The specifier of a `require('…')` call, or ``None``.

    A literal string argument only. `require(path.join(dir, name))` names a module this pass
    cannot know, and a guessed specifier is an invented edge.
    """
    if node is None or node.type != "call_expression":
        return None
    fn = node.child_by_field_name("function")
    if fn is None or fn.type != "identifier" or _text(fn, source) != "require":
        return None
    args = node.child_by_field_name("arguments")
    named = list(args.named_children) if args is not None else []
    if len(named) != 1 or named[0].type != "string":
        return None
    return _text(named[0], source).strip("\"'") or None


def _require_binding(declarator: TSNode, source: bytes) -> tuple[str, list[str], bool] | None:
    """``(specifier, local names, is-namespace)`` for a declarator whose value is a `require`.

    Locals are bound only where resolution will name the right target. Resolution names an
    imported callee by its *local* name, so `{run: go} = require('./m')` would send `go()` to
    `ts:m.go` — a function the module need not have. Such a binding still records the import;
    it just binds nothing.
    """
    name = declarator.child_by_field_name("name")
    value = declarator.child_by_field_name("value")
    if name is None or value is None:
        return None
    spec = _require_spec(value, source)
    if spec is not None:
        if name.type == "identifier":  # const m = require('./m') — the whole module
            return spec, [_text(name, source)], True
        if name.type == "object_pattern":  # const {a, b} = require('./m')
            return spec, _destructured(name, source), False
        return spec, [], False
    if value.type == "member_expression":  # const a = require('./m').a
        spec = _require_spec(value.child_by_field_name("object"), source)
        if spec is None:
            return None
        local = _text(name, source) if name.type == "identifier" else ""
        member = _field_text(value, "property", source)
        return spec, ([local] if local and local == member else []), False
    return None


def _destructured(pattern: TSNode, source: bytes) -> list[str]:
    """Locals a `{…} = require(…)` binds under the export's own name."""
    out: list[str] = []
    for child in pattern.named_children:
        if child.type == "shorthand_property_identifier_pattern":  # {a}
            out.append(_text(child, source))
        elif child.type == "pair_pattern":  # {a: a} is fine; {a: b} is not
            key = _field_text(child, "key", source)
            value = child.child_by_field_name("value")
            if value is not None and value.type == "identifier" and _text(value, source) == key:
                out.append(key)
        elif child.type in ("object_assignment_pattern", "assignment_pattern"):  # {a = fallback}
            left = child.child_by_field_name("left")
            if left is not None and left.type == "shorthand_property_identifier_pattern":
                out.append(_text(left, source))
    return [n for n in out if n]


def _import_edge(spec: str, module_id: str, rel: str, line: int, batch: FactBatch) -> None:
    """The module node and ``IMPORTS`` edge a `require` implies — the parent's shape exactly."""
    resolved = _relative_module(spec, rel)
    mid = f"ts:{resolved}" if resolved is not None else f"ts:{spec}"
    batch.add_node(Node(mid, NodeKind.MODULE, resolved or spec, _LANG, external=resolved is None))
    batch.add_edge(Edge(module_id, mid, EdgeKind.IMPORTS, Provenance(rel, line)))


def _emit_object_member(
    member: TSNode,
    module_id: str,
    source: bytes,
    rel: str,
    batch: FactBatch,
    funcs: list[tuple[str, str | None, TSNode]],
) -> None:
    """One member of `module.exports = {…}`.

    A shorthand `{helper}` adds nothing: `helper` is a local function, already a node under the
    id a caller's `m.helper()` resolves to. A renamed `{run: helper}` is left alone for the
    opposite reason — `m.run()` names `ts:m.run`, which no declaration carries, and `finalize`
    drops the edge rather than inventing the node.
    """
    line = member.start_point[0] + 1
    if member.type == "pair":
        value = member.child_by_field_name("value")
        if value is not None and value.type in _FUNCTION_VALUES:
            key = _field_text(member, "key", source).strip("\"'")
            _emit_export(key, value, module_id, rel, line, batch, funcs)
    elif member.type == "method_definition":  # {run() {…}}
        _emit_export(_field_text(member, "name", source), member, module_id, rel, line, batch, funcs)


def _emit_export(
    name: str,
    fn: TSNode,
    module_id: str,
    rel: str,
    line: int,
    batch: FactBatch,
    funcs: list[tuple[str, str | None, TSNode]],
) -> None:
    if not _IDENTIFIER.match(name):
        return
    fid = f"{module_id}.{name}"
    batch.add_node(Node(fid, NodeKind.FUNCTION, name, _LANG, Provenance(rel, line)))
    batch.add_edge(Edge(module_id, fid, EdgeKind.CONTAINS, Provenance(rel, line)))
    body = fn.child_by_field_name("body")
    if body is not None:
        funcs.append((fid, None, body))


def _retag(batch: FactBatch) -> FactBatch:
    """Tag every node this file produced ``javascript`` — externals included, as Kotlin does."""
    out = FactBatch()
    for node in batch.nodes:
        out.add_node(node if node.language == _LANG else replace(node, language=_LANG))
    for edge in batch.edges:
        out.add_edge(edge)
    return out


def _drop_unlanded(batch: FactBatch) -> FactBatch:
    known = {n.id for n in batch.nodes}

    def unlanded(edge: Edge) -> bool:
        # Both ends: a CALLS or EXPOSES source is always a node this file emitted, but a
        # REFERENCES source is a model *named* in an association (`foo.hasMany(bar)`), and a
        # name is no more a fact at the source end than at the destination.
        return (
            edge.kind in _CHECKED
            and (edge.dst not in known or edge.src not in known)
            and edge.provenance is not None
            and edge.provenance.file.endswith(_SUFFIXES)
        )

    edges = list(batch.edges)
    kept = [e for e in edges if not unlanded(e)]
    if len(kept) == len(edges):
        return batch
    out = FactBatch()
    for node in batch.nodes:
        out.add_node(node)
    for edge in kept:
        out.add_edge(edge)
    return out


__all__ = ["JavaScriptExtractor"]
