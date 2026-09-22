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

Precision-first, as every front-end. Two left-out shapes are declared as corpus ``known_gaps``
and measured — a renamed destructuring (`{run: go} = require(…)`, since resolution names the
target by the local) and a call through an untyped receiver (D8: JavaScript states no types for
the parent's typed-receiver rule to read). The rest are refused or excluded rather than guessed,
and are not measured by the corpus: calling a whole module (`m()` after `m = require(…)`), an
anonymous `module.exports = function () {}`, class expressions, `require` inside a function,
dynamic `import()`, routes registered inside a function body, and an export map the file itself
makes ambiguous (see `_export_surface`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.typescript_extractor import (
    _FUNC_CONST_DECLS,
    TypeScriptExtractor,
    _field_text,
    _import_target,
    _PendingCall,
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
#: Edges that name a module *member*, and so are routed through a CommonJS module's export map.
_THROUGH_EXPORTS = frozenset({EdgeKind.CALLS, EdgeKind.IMPLEMENTS, EdgeKind.EXPOSES})
#: A name a call site can actually spell as `m.name`. A string key like `'a-b'` cannot.
_IDENTIFIER = re.compile(r"^[A-Za-z_$][\w$]*$")


class JavaScriptExtractor(TypeScriptExtractor):
    """JavaScript front-end (tree-sitter, via the ``typescript`` extra)."""

    language: str = _LANG
    suffixes: tuple[str, ...] = _SUFFIXES

    def __init__(self) -> None:
        super().__init__()
        #: The file being read: what `module.exports` is, and which names are it. Set by the
        #: `_imports` pre-pass — the one per-file hook that sees every declaration before any is
        #: emitted — and read by `_emit_statement`, `_route_handler` and `_emit_module`.
        self._surface = _Surface()
        #: The file being read: ``{exported name: the node it is, or None}`` — what `require`
        #: returns, as far as the file says.
        self._file_exports: dict[str, str | None] = {}
        #: The file being read: every ``<exports object>.name = …`` it makes, exported or not. A
        #: same-file read of `exports.show` is the function, even after the export was undone.
        self._file_members: dict[str, str | None] = {}
        #: The file being read: ``{local: imported name}``, None for a whole-module or default
        #: import. Handed to the data-layer reader, which must know *which* export a local is.
        self._import_names: dict[str, str | None] = {}
        #: Every readable CommonJS module read so far: ``module id -> (file, export map)``.
        #: Drained by `finalize`, which routes cross-file edges through it.
        self._cjs: dict[str, tuple[str, dict[str, str | None]]] = {}
        #: Every module's Sequelize model bindings: ``module id -> {name: model}``, where a name
        #: is a variable (`const Booking = sequelize.define('gig', …)`) or an `exports.X` it was
        #: assigned to. Drained by `finalize`, which settles imported association ends with it.
        self._models: dict[str, dict[str, str]] = {}

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch:
        if any(path.with_suffix(s).is_file() for s in (".ts", ".tsx")):
            # `foo.js` beside `foo.ts` is `tsc`'s output. Both map to `ts:foo`, the walk reaches
            # the `.js` first, and the first grounded node wins — so read, it took over the
            # TypeScript module's nodes and provenance, and `explain_symbol` pointed at the build.
            # Generated code is skipped by name everywhere else; this is the same rule, by sibling,
            # and it covers `.mjs`/`.cjs` too: whatever wrote them, they collide on the same id.
            return FactBatch()
        return _retag(super().extract(path=path, module=module, rel=rel))

    def finalize(self, batch: FactBatch) -> FactBatch:
        """The parent's deferred member calls, then two checks on what was resolved by name.

        JavaScript resolves more calls *by name* than TypeScript does — every `m.f()` through a
        `require` binding, every `app.get('/', site.index)` — and a name is a claim, not a fact.

        1. **Through the export map**, for a module whose exports the source states in a form this
           pass reads in full (see `_export_surface`). A cross-file edge into it names
           `ts:m.<name>…`, and what the module exports under that name is known only from its own
           file: `module.exports = { run: helper }` makes `m.run` *be* `helper`, whatever else is
           called `run` there. So the edge is rewritten to the real target, or dropped when the
           module exports no such name. A module it cannot read is left to step 2 alone —
           enforcing a map it only half read dropped true edges into `Object.assign` exports.
        2. **Existence.** A ``CALLS``, ``IMPLEMENTS``, ``EXPOSES`` or ``REFERENCES`` edge from a
           JavaScript file whose source or target no one declares is dropped rather than left
           dangling — the parent's skip-rather-than-guess rule, extended to what this front-end
           adds.

        Routing runs **before** the parent's own existence check on deferred member calls, not
        after it: `new Handler().run()` names `ts:m.Handler.run`, and under
        `module.exports = { Handler: Impl }` no such node exists — checked first, both the method
        and the constructor edge were gone before the export map could say `Handler` is `Impl`.
        """
        from orchestrator.pkg.js_orm import settle

        cjs, self._cjs = self._cjs, {}
        models, self._models = self._models, {}
        route = _export_router(batch, cjs)
        routed = _through_exports(batch, route)
        pending: list[_PendingCall] = []
        for call in self._pending_calls:
            type_id = route(call.type_id, call.rel)
            if type_id is not None:
                pending.append(call if type_id == call.type_id else replace(call, type_id=type_id))
        self._pending_calls = pending
        return _drop_unlanded(settle(super().finalize(routed), cjs, models))

    def _imports(
        self, decls: list[TSNode | None], module_id: str, source: bytes, rel: str, batch: FactBatch
    ) -> tuple[dict[str, str], set[str]]:
        by_local, namespaces = super()._imports(decls, module_id, source, rel, batch)
        self._file_exports, self._file_members = {}, {}
        self._import_names = _esm_import_names(decls, source)
        whole_modules: set[str] = set()
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
                        self._import_names[local] = None if is_namespace else local
                    if is_namespace:
                        namespaces.update(locals_)
                        whole_modules.update(locals_)
            elif node.type == "expression_statement" and node.named_children:
                side_effect = _require_spec(node.named_children[0], source)
                if side_effect is not None:  # `require('./polyfill')` — imported for its side effects
                    _import_edge(side_effect, module_id, rel, node.start_point[0] + 1, batch)
        # A whole-module `require` binding is a namespace for `m.f()` and is not callable as one:
        # the parent reads this, per file, and only this front-end ever fills it.
        self._uncallable = frozenset(whole_modules)
        self._surface = _export_surface(decls, source, frozenset(by_local))
        return by_local, namespaces

    def _emit_module(
        self, root: TSNode, module_id: str, source: bytes, rel: str, batch: FactBatch, imports: dict[str, str]
    ) -> None:
        """The data layer (see :mod:`js_orm`), then this file's export map for `finalize`."""
        from orchestrator.pkg.js_orm import scan

        bindings = scan(root, module_id, source, rel, batch, imports, self._import_names)
        if bindings:
            self._models[module_id] = bindings
        surface = self._surface
        if not surface.cjs:
            return
        exported = self._file_exports
        if surface.alias_object_span is not None:  # `const api = {find, create}; module.exports = api`
            obj = root.descendant_for_byte_range(*surface.alias_object_span)
            if obj is not None and obj.type == "object":
                for member in obj.named_children:
                    _emit_object_member(member, module_id, source, rel, batch, [], exported)
        if surface.default is not None:  # `class Base {}; module.exports = Base`
            exported.setdefault(surface.default, f"{module_id}.{surface.default}")
        # An ESM `export` beside CommonJS exports is a surface this pass does not merge.
        mixed = any(child.type == "export_statement" for child in root.named_children)
        if surface.readable and not mixed:
            self._cjs[module_id] = (rel, dict(exported))

    def _route_handler(
        self, module_id: str, imports: dict[str, str], namespaces: set[str], source: bytes, rel: str
    ) -> Callable[[TSNode], str | None] | None:
        """Bind `app.get('/', site.index)` — the CommonJS way to route to another file's export.

        Two spellings: a member of a `require` namespace (`site.index`, where
        `site = require('./site')`), routed through that module's export map in `finalize`; and a
        member of this module's own exports object (`exports.index`, or an alias of it), which is
        whatever this file assigned there — read as it stands, so an export undone later is still
        the function this line refers to.
        """
        surface, members = self._surface, self._file_members

        def resolve(member: TSNode) -> str | None:
            owner = member.child_by_field_name("object")
            prop = _field_text(member, "property", source)
            if owner is None or not _IDENTIFIER.match(prop):
                return None
            name = _text(owner, source)
            if surface.is_exports_object(name, owner.type):
                return members.get(prop)
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
        """CommonJS exports — the assignments that are a CommonJS module's public surface.

        What *counts* as exported is decided by `_export_surface`; every assignment onto the
        exports object still gets its node, because the function it assigns is real whether or
        not a later line undoes the export. `exports.a = exports.b = f` exports both names.
        """
        if node.type != "expression_statement" or not node.named_children:
            return
        expr = node.named_children[0]
        if expr.type != "assignment_expression":
            return
        lefts, right = _chain_nodes(expr)
        if right is None:
            return
        line = node.start_point[0] + 1
        surface = self._surface
        emitted = False
        for left in lefts:
            if left.type != "member_expression":
                continue
            if _text(left, source) == "module.exports":
                if surface.is_object(right):
                    for member in right.named_children:
                        _emit_object_member(member, module_id, source, rel, batch, funcs, self._file_exports)
                elif right.type in _FUNCTION_VALUES and surface.single:
                    # The default export. Its function is real and gets a node; it is not a
                    # *member* a caller can name as `m.build`, so it joins no export map.
                    name = _field_text(right, "name", source)
                    if name and not emitted:
                        _emit_export(name, right, module_id, rel, line, batch, funcs)
                        emitted = True
                continue
            owner = left.child_by_field_name("object")
            prop = _field_text(left, "property", source)
            if owner is None or not _IDENTIFIER.match(prop):
                continue
            owner_text = _text(owner, source)
            if not surface.is_exports_object(owner_text, owner.type):
                continue
            if right.type in _FUNCTION_VALUES:
                target: str | None = f"{module_id}.{prop}"
                _emit_export(prop, right, module_id, rel, line, batch, funcs)
            else:
                target = _value_target(right, module_id, source)
            self._file_members[prop] = target
            if surface.names_exports(owner_text, owner.type, node.start_byte):
                self._file_exports[prop] = target


def _esm_import_names(decls: list[TSNode | None], source: bytes) -> dict[str, str | None]:
    """``{local: imported name}`` for ESM imports; None for a default or namespace import."""
    out: dict[str, str | None] = {}
    for node in decls:
        if node is None or node.type != "import_statement":
            continue
        for clause in node.named_children:
            if clause.type != "import_clause":
                continue
            for child in clause.named_children:
                if child.type == "identifier":  # default import
                    out[_text(child, source)] = None
                elif child.type == "namespace_import" and child.named_children:
                    out[_text(child.named_children[-1], source)] = None
                elif child.type == "named_imports":
                    for spec in child.named_children:
                        if spec.type != "import_specifier":
                            continue
                        imported = _field_text(spec, "name", source)
                        local = _field_text(spec, "alias", source) or imported
                        if local:
                            out[local] = imported
    return out


@dataclass(frozen=True)
class _Surface:
    """What a CommonJS file's `module.exports` is, as far as the source leaves no doubt."""

    #: Top-level names that *are* the exports object (`var app = … = module.exports = {}`).
    aliases: frozenset[str] = frozenset()
    #: Whether `exports.f = …` still reaches `module.exports`.
    exports_ok: bool = True
    #: Whether `module.exports.f = …` / `alias.f = …` names the object that is exported.
    members_ok: bool = True
    #: The byte span of the object literal `module.exports` was set to, when it was. A span and
    #: not the node: tree-sitter returns a fresh wrapper each time a node is reached.
    object_span: tuple[int, int] | None = None
    #: The span of the object literal an *alias* was declared as (`const api = {…}`).
    alias_object_span: tuple[int, int] | None = None
    #: A declared class or function that *is* `module.exports` (`module.exports = Base`).
    default: str | None = None
    #: Where the single `module.exports = …` sits, or -1 when there is none.
    offset: int = -1
    #: Whether `module.exports` is assigned exactly once.
    single: bool = False
    #: Whether the file exports anything the CommonJS way at all.
    cjs: bool = False
    #: Whether this pass reads the surface in full, and so may *enforce* it on callers.
    readable: bool = True

    def is_object(self, node: TSNode) -> bool:
        return self.object_span == (node.start_byte, node.end_byte)

    def is_exports_object(self, owner: str, owner_type: str) -> bool:
        """Whether ``owner`` is lexically the exports object — exported or not, as it turns out."""
        return owner in ("exports", "module.exports") or (
            owner_type == "identifier" and owner in self.aliases
        )

    def names_exports(self, owner: str, owner_type: str, at: int) -> bool:
        """Whether `<owner>.f = …` at byte ``at`` puts `f` on the object `require` returns.

        Order matters for `exports` and `module.exports`: a member set *before* `module.exports`
        is replaced was set on the object that got replaced. An alias is order-free — it is the
        exported object itself, whenever it is augmented.
        """
        if owner == "exports":
            return self.exports_ok and at > self.offset
        if owner == "module.exports":
            return self.members_ok and at > self.offset
        return owner_type == "identifier" and owner in self.aliases and self.members_ok


def _chain(node: TSNode, source: bytes) -> tuple[list[str], TSNode | None]:
    """``a = b = c = V`` → (``["a", "b", "c"]``, ``V``)."""
    lefts, final = _chain_nodes(node)
    return [_text(left, source) for left in lefts], final


def _chain_nodes(node: TSNode) -> tuple[list[TSNode], TSNode | None]:
    lefts: list[TSNode] = []
    current: TSNode | None = node
    while current is not None and current.type == "assignment_expression":
        left = current.child_by_field_name("left")
        if left is None:
            return lefts, None
        lefts.append(left)
        current = current.child_by_field_name("right")
    return lefts, current


def _walk(node: TSNode) -> list[TSNode]:
    out: list[TSNode] = []
    stack = [node]
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(current.named_children)
    return out


def _merge_target(expr: TSNode, source: bytes) -> str | None:
    """What `Object.assign(x, …)` / `Object.defineProperty(x, 'f', …)` writes members onto.

    Babel marks every file it emits with `Object.defineProperty(exports, "__esModule", …)`, which
    adds no member anyone calls, so that one is not a merge.
    """
    if expr.type != "call_expression":
        return None
    fn = _text(expr.child_by_field_name("function"), source)
    if fn not in ("Object.assign", "Object.defineProperty", "Object.defineProperties"):
        return None
    args = expr.child_by_field_name("arguments")
    named = [a for a in args.named_children if a.type != "comment"] if args is not None else []
    if not named:
        return None
    if fn == "Object.defineProperty" and len(named) > 1 and "__esModule" in _text(named[1], source):
        return None
    return _text(named[0], source)


#: What lies between a top-level statement and an assignment *it* makes — `a = b = c`, or
#: `var x = a = b`. Anything else between them (a function, a branch, a loop) makes it nested.
_STATEMENT_CHAIN = frozenset(
    {
        "expression_statement",
        "assignment_expression",
        "variable_declarator",
        "lexical_declaration",
        "variable_declaration",
    }
)


def _nested(node: TSNode, top: TSNode) -> bool:
    """Whether ``node`` sits inside something other than the top-level statement's own chain."""
    current = node.parent
    while current is not None and (current.start_byte, current.end_byte) != (top.start_byte, top.end_byte):
        if current.type not in _STATEMENT_CHAIN:
            return True
        current = current.parent
    return False


def _plain_object(obj: TSNode) -> bool:
    """An object literal whose every member this pass names: no spread, no computed key."""
    for child in obj.named_children:
        if child.type in ("shorthand_property_identifier", "comment"):
            continue
        if child.type not in ("pair", "method_definition"):
            return False  # a spread, or anything this pass does not read
        key = child.child_by_field_name("key" if child.type == "pair" else "name")
        if key is None or key.type == "computed_property_name":
            return False
    return True


def _object_create(node: TSNode | None, source: bytes) -> bool:
    """`Object.create(proto)` — a fresh object, whose own members are the ones the file assigns."""
    return (
        node is not None
        and node.type == "call_expression"
        and _text(node.child_by_field_name("function"), source) == "Object.create"
    )


def _export_surface(
    decls: list[TSNode | None], source: bytes, imported: frozenset[str] = frozenset()
) -> _Surface:
    """Which object `module.exports` is, which top-level names are it — and whether it is readable.

    The classic CommonJS library names the exports object once and augments it; express's `lib/`
    spells 43 of its 49 exported functions this way:

        var app = exports = module.exports = {};     // a declaration, chained through
        app.init = function init() { … };

        var res = Object.create(http.ServerResponse.prototype);
        module.exports = res;                        // an assignment, naming a declared object
        res.send = function send(body) { … };

    Either way `require('./application').init` *is* `app.init`. But every one of these facts can
    be undone by the same file, and each undoing was an invented export before this function
    looked for it:

    - **`module.exports` assigned twice**, or once inside a branch (UMD). Which object is exported
      depends on execution, so nothing is claimed.
    - **`module.exports` replaced without rebinding `exports`.** `exports` still points at the
      old object, so `exports.f = …` exports nothing. (`exports = module.exports = {}` rebinds
      both, which is why express writes it that way.)
    - **`exports` rebound on its own** — `exports = o` never changes what `require` returns.
    - **An alias reassigned** — `app = {}` after `var app = module.exports = {}`.

    **Readable** means the file states its exports in forms this pass reads in full, so the export
    map may be *enforced* on callers — a name it does not list is not exported. It is an
    allowlist, because a blocklist let every shape nobody had thought of through as readable:
    `module.exports` set to a plain object literal, a declared function or class, a function
    value, `Object.create(…)`, or a name declared as one of those; every member written as a
    top-level `<exports object>.f = …`. Anything else — `Object.freeze({…})`, `make()`, an alias
    assigned after its declaration, `exports['f'] = …`, a member written inside a function, an
    `Object.assign` or `defineProperty` onto the exports object or an alias of it — and the map
    is not enforced: calls into the module are resolved by name and kept if their target exists.
    Enforcing a map this pass only half read dropped true edges; a reader that cannot tell must
    not decide.
    """
    chains: list[tuple[list[str], TSNode | None, str | None, int]] = []
    bare_rebind = False
    reassigned: set[str] = set()
    member_exports = False
    declared: dict[str, TSNode] = {}  # top-level `const x = <value>`
    callables: set[str] = set()  # top-level `class X` / `function X`
    for node in decls:
        if node is None:
            continue
        if node.type in ("class_declaration", "function_declaration"):
            name = node.child_by_field_name("name")
            if name is not None:
                callables.add(_text(name, source))
        elif node.type in _FUNC_CONST_DECLS:
            for declarator in node.named_children:
                name = declarator.child_by_field_name("name")
                value = declarator.child_by_field_name("value")
                if name is None or value is None or name.type != "identifier":
                    continue
                declared[_text(name, source)] = value
                targets, final = _chain(value, source)
                if "module.exports" in targets:
                    chains.append((targets, final, _text(name, source), declarator.end_byte))
                elif "exports" in targets:
                    bare_rebind = True
        elif node.type == "expression_statement" and node.named_children:
            expr = node.named_children[0]
            if expr.type != "assignment_expression":
                continue
            targets, final = _chain(expr, source)
            if "module.exports" in targets:
                chains.append((targets, final, None, expr.end_byte))
            elif "exports" in targets:
                bare_rebind = True
            else:
                reassigned.update(t for t in targets if _IDENTIFIER.match(t))
                member_exports = member_exports or any(
                    t.startswith(("exports.", "module.exports.")) for t in targets
                )
    # One walk over the whole file for what the top level alone does not show. A `module.exports
    # = …` below the top level — UMD's `if (typeof module …)` — makes the exported object a matter
    # of which branch ran. The rest are objects written in a way the export map cannot list.
    assigned = 0
    unlisted: set[str] = set()  # objects written by merge, by subscript, or from inside a function
    for node in decls:
        if node is None:
            continue
        for sub in _walk(node):
            merged = _merge_target(sub, source)
            if merged is not None:
                unlisted.add(merged)
            if sub.type != "assignment_expression":
                continue
            left = sub.child_by_field_name("left")
            if left is None:
                continue
            if _text(left, source) == "module.exports":
                assigned += 1
            elif left.type == "subscript_expression" or (
                left.type == "member_expression" and _nested(sub, node)
            ):
                unlisted.add(_text(left.child_by_field_name("object"), source))
    if len(chains) > 1 or assigned > len(chains):
        return _Surface(exports_ok=False, members_ok=False, cjs=True)
    if not chains:
        readable = not unlisted & {"exports", "module.exports"}
        return _Surface(exports_ok=not bare_rebind, cjs=member_exports, readable=readable)
    targets, final, alias, offset = chains[0]
    aliases = {alias} if alias else set()
    alias_object_span: tuple[int, int] | None = None
    default: str | None = None
    readable = False
    if final is None:
        pass
    elif final.type == "identifier":
        named = _text(final, source)
        aliases.add(named)
        value = declared.get(named)
        if named in imported:
            pass  # `module.exports = require('./x')` in two steps: a re-export
        elif named in callables:
            default, readable = named, True
        elif value is not None and value.type == "object":
            if _plain_object(value):
                alias_object_span, readable = (value.start_byte, value.end_byte), True
        elif value is not None:
            readable = value.type in _FUNCTION_VALUES or _object_create(value, source)
    elif final.type == "object":
        readable = _plain_object(final)
    else:
        readable = final.type in _FUNCTION_VALUES or _object_create(final, source)
    if unlisted & ({"exports", "module.exports"} | aliases):
        readable = False
    return _Surface(
        aliases=frozenset(aliases - reassigned),
        exports_ok="exports" in targets and not bare_rebind,
        members_ok=True,
        object_span=(final.start_byte, final.end_byte)
        if final is not None and final.type == "object"
        else None,
        alias_object_span=alias_object_span,
        default=default,
        offset=offset,
        single=True,
        cjs=True,
        readable=readable,
    )


def _value_target(value: TSNode, module_id: str, source: bytes) -> str | None:
    """The node an exported *value* is: `exports.run = helper` → `ts:m.helper`; anything else unknown.

    A module-level function or class is minted `<module>.<name>`, so an identifier's id is known
    without looking it up — and `finalize`'s existence check drops it if the name is a local
    variable rather than a declaration.
    """
    if value.type == "identifier" and _IDENTIFIER.match(_text(value, source)):
        return f"{module_id}.{_text(value, source)}"
    return None


def _export_router(
    batch: FactBatch, cjs: dict[str, tuple[str, dict[str, str | None]]]
) -> Callable[[str, str | None], str | None]:
    """``route(target id, file it is named from)`` → the id it really is, or None for no such export.

    At any depth: `ts:m.Handler.run` is the `run` of whatever `m` exports as `Handler`, so the
    exported name is rewritten wherever it heads the id — `module.exports = { Handler: Impl }`
    makes it `ts:m.Impl.run`. Rewriting only the direct member left a method call on the
    renamed class landing on the unexported one while its constructor edge was corrected.

    **The module is the longest prefix that is a module** — any module, not only a CommonJS one.
    Ids are dotted paths, so `ts:models/user.model.find` also reads as member `model` of
    `ts:models/user`; searching only the CommonJS modules walked straight past `user.model`
    (TypeScript) to `user.js`, found no export called `model`, and dropped every edge into it.
    Only then is the map consulted, and only if that module has one. A target named from the
    module's own file is left alone: a file reaches its own members whether exported or not.
    """
    modules = {n.id for n in batch.nodes if n.kind is NodeKind.MODULE and not n.external}

    def route(target: str, file: str | None) -> str | None:
        if not cjs:
            return target
        module = target
        while module not in modules and "." in module:
            module = module.rpartition(".")[0]
        entry = cjs.get(module)
        if entry is None or module == target or file == entry[0]:
            return target
        head, _, tail = target[len(module) + 1 :].partition(".")
        exported = entry[1].get(head)
        if exported is None:
            return None  # not exported under that name: the call reaches nothing we can see
        return f"{exported}.{tail}" if tail else exported

    return route


def _through_exports(batch: FactBatch, route: Callable[[str, str | None], str | None]) -> FactBatch:
    """Route cross-file edges into readable CommonJS modules through each module's export map."""
    out = FactBatch()
    for node in batch.nodes:
        out.add_node(node)
    for edge in batch.edges:
        if edge.kind in _THROUGH_EXPORTS:
            target = route(edge.dst, edge.provenance.file if edge.provenance is not None else None)
            if target is None:
                continue
            if target != edge.dst:
                edge = Edge(edge.src, target, edge.kind, edge.provenance)
        out.add_edge(edge)
    return out


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
    named = [a for a in args.named_children if a.type != "comment"] if args is not None else []
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
    exported: dict[str, str | None],
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
        key = _field_text(member, "key", source).strip("\"'")
        if value is None or not _IDENTIFIER.match(key):
            return
        if value.type in _FUNCTION_VALUES:
            _emit_export(key, value, module_id, rel, line, batch, funcs)
            exported[key] = f"{module_id}.{key}"
        else:
            exported[key] = _value_target(value, module_id, source)  # {run: helper} → helper
    elif member.type == "method_definition":  # {run() {…}}
        name = _field_text(member, "name", source)
        if _IDENTIFIER.match(name):
            _emit_export(name, member, module_id, rel, line, batch, funcs)
            exported[name] = f"{module_id}.{name}"
    elif member.type == "shorthand_property_identifier":  # {helper}
        name = _text(member, source)
        exported[name] = f"{module_id}.{name}"


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
