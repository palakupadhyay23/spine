"""Sequelize models in JavaScript source → ``Entity`` + ``Field`` nodes and ``REFERENCES`` edges.

The data layer of javascript-support-roadmap P4 (D10). The shape is Python's (``python_orm.py``):
an ``Entity`` a module ``CONTAINS``, its columns as ``Field`` nodes it ``CONTAINS``, and
entity→entity ``REFERENCES``. Ids are ``ts:entity:<model>`` — the ``ts:`` namespace the
JavaScript front-end shares with TypeScript, in the form ``ts:endpoint:`` already uses — and
deliberately not ``sql:``-prefixed, so :func:`~orchestrator.pkg.data_layer_link.link_data_layer`
reads them as ORM entities and collapses each onto a matching table in a ``.sql`` schema. It
matches by the entity's *name*, which is the declared ``tableName`` when there is one and the
model name otherwise, and it pluralizes naively — a trailing ``s`` — so ``user``/``users`` pair
and ``person``/``people`` do not, unless ``tableName`` says so.

**No ORM marker, no Entity** — the rule every ORM reader in this package keeps, because a model
is not recognisable by its shape. The two markers read here, both statically:

- ``<x>.define('<name>', {…})`` whose attribute map **uses a Sequelize type** — a value rooted at
  a binding from the ``sequelize`` package (``DataTypes.STRING``, ``Sequelize.DataTypes.X``, or
  ``{type: …}`` of one). ``define`` alone marks nothing: ``customElements.define``,
  ``ajv.define`` and factory-girl's ``factory.define`` all take a string and an object. The
  official example app defines every model *inside a function* —
  ``module.exports = (sequelize) => { sequelize.define('user', …) }`` — so the whole file is
  walked, not only its top level.
- ``class X extends Model`` where ``Model`` is **bound from ``sequelize``** (Objection.js names
  its base the same) **and** ``X.init({…})`` is in the file. A class with no ``init`` is an
  abstract base the real models extend, and has no table. ``modelName`` renames the model;
  ``tableName`` names its table.

**``REFERENCES`` follows the foreign key**, so it agrees with the schema it is reconciled
against: ``A.belongsTo(B)`` puts the key on A (A references B); ``A.hasMany(B)`` and
``A.hasOne(B)`` put it on B (B references A). The redundant pair a real app writes —
``orchestra.hasMany(instrument)`` *and* ``instrument.belongsTo(orchestra)`` — states one edge
twice, not two. An end is named by: a destructuring of the models registry
(``const { instrument } = sequelize.models``), a ``….models.name`` access, a Sequelize model
class, a ``const User = sequelize.define(…)`` binding, or an **import** — which names whatever
the imported module *defines*, not the local's spelling (``const Author = require('./user')``
is the ``user`` model). An import is settled by :func:`settle` once every file is read, and the
front-end's ``finalize`` then drops any ``REFERENCES`` whose ends are not both entities.

Left out, and declared rather than guessed: ``belongsToMany`` (its keys live on a join table,
often one Sequelize generates from a ``through`` string at run time), a self-association, a
model name or attribute map that is not a literal, ``class X extends Sequelize.Model``,
sequelize-cli's generated models (``module.exports = (sequelize, DataTypes) => …`` — no
``sequelize`` import, so no marker), its ``static associate(models)`` form, ``@sequelize/core``
v7, the column-level ``references: {model: …}`` form, and a *renamed* CommonJS destructure of
the package (``const { Model: Base } = require('sequelize')``) — the front-end binds no local for
a renamed key, so its base is unseen, where the ESM ``import { Model as Base }`` is read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.typescript_extractor import _relative_module, _supertypes

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tree_sitter import Node as TSNode

    from orchestrator.pkg.extractor import ExportMap

_LANG = "javascript"
_PACKAGE = "sequelize"
#: Association → whether the foreign key sits on the *receiver* (True) or the argument (False).
_ASSOCIATIONS = {"belongsTo": True, "hasMany": False, "hasOne": False}
#: Placeholder for "the entity module M defines", settled by :func:`settle` once every file is
#: read: ``ts:entity-of:<module id>#<imported name>``, or ``#*`` for a whole-module/default import.
ENTITY_OF = "ts:entity-of:"
_WHOLE = "*"
#: Sequelize's column types. A value rooted at a `sequelize` binding is not enough to mark a model:
#: `Op.is` and `Sequelize.NOW` are both rooted there and neither is a type, and `scopes.define(
#: 'active', { deletedAt: Op.is })` minted an entity. `NOW`, `UUIDV1` and `UUIDV4` are defaults.
#: `NUMERIC` is `DECIMAL`'s alias. A modifier (`UNSIGNED`, `ZEROFILL`, `BINARY`) is not listed: it
#: is never a type on its own, only a suffix on one — see `_sequelize_type`.
_TYPE_NAMES = frozenset(
    {
        "STRING",
        "CHAR",
        "TEXT",
        "CITEXT",
        "TSVECTOR",
        "TINYINT",
        "SMALLINT",
        "MEDIUMINT",
        "INTEGER",
        "BIGINT",
        "NUMBER",
        "FLOAT",
        "REAL",
        "DOUBLE",
        "DECIMAL",
        "NUMERIC",
        "BOOLEAN",
        "TIME",
        "DATE",
        "DATEONLY",
        "HSTORE",
        "JSON",
        "JSONB",
        "BLOB",
        "RANGE",
        "UUID",
        "VIRTUAL",
        "ENUM",
        "ARRAY",
        "GEOMETRY",
        "GEOGRAPHY",
        "CIDR",
        "INET",
        "MACADDR",
        "MACADDR8",
    }
)


def _text(node: TSNode | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace").strip()


def _string(node: TSNode | None, source: bytes) -> str | None:
    if node is None or node.type != "string":
        return None
    return _text(node, source).strip("\"'") or None


def _walk(root: TSNode) -> list[TSNode]:
    out: list[TSNode] = []
    stack = [root]
    while stack:
        node = stack.pop()
        out.append(node)
        stack.extend(reversed(node.named_children))
    return out


def _args(call: TSNode) -> list[TSNode]:
    """A call's arguments. Comments are named children too, and `define(/* c */ 'x', …)` is real."""
    args = call.child_by_field_name("arguments")
    return [a for a in args.named_children if a.type != "comment"] if args is not None else []


def entity_id(name: str) -> str:
    return f"ts:entity:{name}"


def scan(
    root: TSNode,
    module_id: str,
    source: bytes,
    rel: str,
    batch: FactBatch,
    imports: dict[str, str],
    import_names: dict[str, str | None] | None = None,
) -> dict[str, str]:
    """Emit this file's Sequelize models and associations into ``batch``; return its bindings.

    ``import_names`` maps each local to the name it was imported *as* — None for a whole-module
    or default import — so an association end can name the export it really is.

    The bindings are ``{name: model}`` for every name this file holds a model under: a variable
    (`const Booking = sequelize.define('gig', …)`), a model class, or `exports.X` for a model
    assigned straight onto the exports object. :func:`settle` needs them because an import names
    the *binding* it was exported as, which need not be the model's name in any case.
    """
    names = import_names if import_names is not None else {}
    nodes = _walk(root)
    sequelize = {local for local, spec in imports.items() if spec == _PACKAGE}
    #: The locals that are Sequelize's `Model` — `import { Model as SeqModel }` included.
    model_base = {local for local in sequelize if names.get(local, local) == "Model"}

    defined: dict[str, str] = {}  # `const User = sequelize.define('User', …)` → {User: User}
    exported: dict[str, str] = {}  # `exports.Post = sequelize.define('article', …)`
    if sequelize:
        for node in nodes:
            if node.type != "call_expression":
                continue
            model = _define(node, module_id, source, rel, batch, sequelize)
            parent = node.parent
            if model is None or parent is None:
                continue
            if parent.type == "variable_declarator":
                name = parent.child_by_field_name("name")
                if name is not None and name.type == "identifier":
                    defined[_text(name, source)] = model
            elif parent.type == "assignment_expression":  # exports.Post = sequelize.define(…)
                left = _text(parent.child_by_field_name("left"), source)
                for prefix in ("exports.", "module.exports."):
                    if left.startswith(prefix) and "." not in left[len(prefix) :]:
                        exported[f"exports.{left[len(prefix) :]}"] = model

    classes: dict[str, str] = {}  # {class name: model name}, for classes with an `init`
    if model_base:
        for cls in _model_classes(nodes, source, model_base):
            model = _init(nodes, cls, module_id, source, rel, batch)
            if model is not None:
                classes[cls] = model

    registry = _registry_names(nodes, source)
    for call in (n for n in nodes if n.type == "call_expression"):
        _association(call, source, rel, batch, registry, classes, defined, imports, names)
    return {**defined, **classes, **exported}


def _model_classes(nodes: list[TSNode], source: bytes, model_base: set[str]) -> list[str]:
    """Classes that descend from Sequelize's ``Model`` in this file, directly or through a base.

    `class BaseModel extends Model {}` then `class Product extends BaseModel {}` is the usual way
    to share hooks between models: the base has no ``init`` and no table, the subclass is the
    model. Read only directly, the subclass was missed; read by shape, the base was minted. So
    descent is followed within the file, and ``init`` decides which of them is a table.
    """
    bases: dict[str, set[str]] = {}
    for node in nodes:
        if node.type == "class_declaration":
            name = _text(node.child_by_field_name("name"), source)
            if name:
                bases[name] = set(_supertypes(node, source))
    derived = set(model_base)  # the binding, not the word: a local class named `Model` is not it
    grew = True
    while grew:
        grew = False
        for name, supers in bases.items():
            if name not in derived and supers & derived:
                derived.add(name)
                grew = True
    return sorted(derived - model_base)


def _sequelize_type(node: TSNode | None, source: bytes, sequelize: set[str]) -> bool:
    """Whether a value is one of Sequelize's column types: `DataTypes.STRING`,
    `Sequelize.DataTypes.TEXT`, the parameterized forms `STRING(120)`, `DECIMAL(10, 2)`,
    `ENUM('new', 'paid')`, and the modified ones `INTEGER.UNSIGNED`, `INTEGER(11).UNSIGNED`,
    `BIGINT.UNSIGNED.ZEROFILL`.

    The type is *somewhere in* the chain, not at its end: reading only the last property missed
    every modifier, so a MySQL join model whose columns are all `INTEGER.UNSIGNED` keys was no
    model at all. Each call is unwrapped to what it parameterizes, and the chain must still end
    at a binding from `sequelize` — `Op.is` does, but names no type anywhere, and is refused.
    """
    typed = False
    while node is not None:
        if node.type == "call_expression":
            node = node.child_by_field_name("function")
        elif node.type == "member_expression":
            typed = typed or _text(node.child_by_field_name("property"), source) in _TYPE_NAMES
            node = node.child_by_field_name("object")
        else:
            break
    return typed and node is not None and node.type == "identifier" and _text(node, source) in sequelize


def _typed_attributes(attributes: TSNode, source: bytes, sequelize: set[str]) -> bool:
    """Whether an attribute map uses a Sequelize type — the marker `define` itself cannot give."""
    for pair in attributes.named_children:
        if pair.type != "pair":
            continue
        value = pair.child_by_field_name("value")
        if _sequelize_type(value, source, sequelize):
            return True
        if value is not None and value.type == "object":
            for inner in value.named_children:
                if (
                    inner.type == "pair"
                    and _text(inner.child_by_field_name("key"), source) == "type"
                    and _sequelize_type(inner.child_by_field_name("value"), source, sequelize)
                ):
                    return True
    return False


def _option(options: TSNode | None, key: str, source: bytes) -> str | None:
    """A literal ``key: '…'`` in an options object — `modelName`, `tableName`."""
    if options is None or options.type != "object":
        return None
    for pair in options.named_children:
        if pair.type == "pair" and _text(pair.child_by_field_name("key"), source) == key:
            return _string(pair.child_by_field_name("value"), source)
    return None


def _define(
    call: TSNode, module_id: str, source: bytes, rel: str, batch: FactBatch, sequelize: set[str]
) -> str | None:
    """``sequelize.define('user', {…}, {tableName: 'users'})`` → the model name, when it is one."""
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "member_expression":
        return None
    if _text(fn.child_by_field_name("property"), source) != "define":
        return None
    args = _args(call)
    name = _string(args[0], source) if args else None
    attributes = args[1] if len(args) > 1 and args[1].type == "object" else None
    if name is None or attributes is None or not _typed_attributes(attributes, source, sequelize):
        return None
    table = _option(args[2] if len(args) > 2 else None, "tableName", source)
    _entity(name, table, attributes, module_id, source, rel, call.start_point[0] + 1, batch)
    return name


def _init(
    nodes: list[TSNode], cls: str, module_id: str, source: bytes, rel: str, batch: FactBatch
) -> str | None:
    """``User.init({…}, {modelName, tableName})`` for a Sequelize class → its model name."""
    for node in nodes:
        if node.type != "call_expression":
            continue
        fn = node.child_by_field_name("function")
        if (
            fn is None
            or fn.type != "member_expression"
            or _text(fn.child_by_field_name("object"), source) != cls
            or _text(fn.child_by_field_name("property"), source) != "init"
        ):
            continue
        args = _args(node)
        if not args or args[0].type != "object":
            continue
        options = args[1] if len(args) > 1 else None
        model = _option(options, "modelName", source) or cls
        table = _option(options, "tableName", source)
        _entity(model, table, args[0], module_id, source, rel, node.start_point[0] + 1, batch)
        return model
    return None


def _entity(
    name: str,
    table: str | None,
    attributes: TSNode | None,
    module_id: str,
    source: bytes,
    rel: str,
    line: int,
    batch: FactBatch,
) -> None:
    """The id is the *model* name, which associations name; the node's name is the table."""
    eid = entity_id(name)
    batch.add_node(Node(eid, NodeKind.ENTITY, table or name, _LANG, Provenance(rel, line)))
    batch.add_edge(Edge(module_id, eid, EdgeKind.CONTAINS, Provenance(rel, line)))
    if attributes is None:
        return
    for pair in attributes.named_children:
        if pair.type != "pair":
            continue  # a spread or a shorthand names no column this pass can see
        key = pair.child_by_field_name("key")
        if key is None or key.type not in ("property_identifier", "string"):
            continue
        column = _text(key, source).strip("\"'")
        if not column:
            continue
        fline = pair.start_point[0] + 1
        fid = f"{eid}.{column}"
        batch.add_node(Node(fid, NodeKind.FIELD, column, _LANG, Provenance(rel, fline)))
        batch.add_edge(Edge(eid, fid, EdgeKind.CONTAINS, Provenance(rel, fline)))


def _registry_names(nodes: list[TSNode], source: bytes) -> set[str]:
    """Locals destructured from the models registry: ``const { instrument } = sequelize.models``."""
    out: set[str] = set()
    for node in nodes:
        if node.type != "variable_declarator":
            continue
        pattern = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if (
            pattern is None
            or value is None
            or pattern.type != "object_pattern"
            or value.type != "member_expression"
            or _text(value.child_by_field_name("property"), source) != "models"
        ):
            continue
        for child in pattern.named_children:
            if child.type == "shorthand_property_identifier_pattern":
                out.add(_text(child, source))
    return out


def _model_end(
    node: TSNode | None,
    source: bytes,
    rel: str,
    registry: set[str],
    classes: dict[str, str],
    defined: dict[str, str],
    imports: dict[str, str],
    names: dict[str, str | None],
) -> str | None:
    """The entity id an association end names, a placeholder :func:`settle` resolves, or ``None``."""
    if node is None:
        return None
    if node.type == "member_expression":  # sequelize.models.orchestra
        owner = node.child_by_field_name("object")
        if owner is not None and _text(owner.child_by_field_name("property"), source) == "models":
            name = _text(node.child_by_field_name("property"), source)
            return entity_id(name) if name else None
        return None
    if node.type != "identifier":
        return None
    name = _text(node, source)
    if name in classes:
        return entity_id(classes[name])
    if name in defined:
        return entity_id(defined[name])
    if name in registry:
        return entity_id(name)
    if name in imports:
        # `const Author = require('./models/user')`: the local is not the model's name. It names
        # the export it was imported *as* — the whole module, or a named export — known only once
        # that module has been read.
        module = _relative_module(imports[name], rel)
        imported = names.get(name, name)
        return f"{ENTITY_OF}ts:{module}#{imported or _WHOLE}" if module is not None else None
    return None


def _association(
    call: TSNode,
    source: bytes,
    rel: str,
    batch: FactBatch,
    registry: set[str],
    classes: dict[str, str],
    defined: dict[str, str],
    imports: dict[str, str],
    names: dict[str, str | None],
) -> None:
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "member_expression":
        return
    kind = _text(fn.child_by_field_name("property"), source)
    if kind not in _ASSOCIATIONS:
        return
    args = _args(call)
    ends = (registry, classes, defined, imports, names)
    receiver = _model_end(fn.child_by_field_name("object"), source, rel, *ends)
    target = _model_end(args[0] if args else None, source, rel, *ends)
    if receiver is None or target is None or receiver == target:
        return  # a self-association keys a row to its own table; Python's reader skips it too
    holder, referenced = (receiver, target) if _ASSOCIATIONS[kind] else (target, receiver)
    batch.add_edge(Edge(holder, referenced, EdgeKind.REFERENCES, Provenance(rel, call.start_point[0] + 1)))


def settle(
    batch: FactBatch,
    cjs: Mapping[str, ExportMap] | None = None,
    models: dict[str, dict[str, str]] | None = None,
) -> FactBatch:
    """Replace each ``ts:entity-of:<module>#<name>`` end with the entity that module defines.

    A **whole-module or default** import names the model the module defines, so one entity there
    is that one and several are ambiguous. A **named** import names an *export*, and is matched,
    in order, against:

    1. the module's export map (``cjs``, from the front-end), when it has one — and then only
       it: `{ Booking }` exports the variable `Booking`, which holds the model `gig`, and
       `exports.Post = sequelize.define(…)` exports that model as `Post`. A name the map does not
       list, or lists as something other than a model binding (a re-export), resolves to nothing;
    2. otherwise, the same two bindings read from the file (``models``, from :func:`scan`) — a
       model assigned onto the exports object, or a variable or class of that name;
    3. then a model of exactly that name, then of that name case aside — `const { Post }` is the
       `post` model — when exactly one matches.

    Nothing else: "the one model in the module" had picked `user` for `const { Post } =
    require('./user')` when `user.js` defined `user` and re-exported `Post`. Matching the model
    name alone lost `{ Booking }` for `define('gig')`; the binding is what the import names.
    Unresolved, the edge is dropped. A self-association only resolution reveals is dropped too.
    Run by the JavaScript front-end's `finalize`, before its existence check.
    """
    if not any(e.src.startswith(ENTITY_OF) or e.dst.startswith(ENTITY_OF) for e in batch.edges):
        return batch
    maps = cjs or {}
    bindings = models or {}
    by_module: dict[str, set[str]] = {}
    for edge in batch.edges:
        if edge.kind is EdgeKind.CONTAINS and edge.dst.startswith("ts:entity:") and "." not in edge.dst:
            by_module.setdefault(edge.src, set()).add(edge.dst)

    def named(module: str, name: str, candidates: set[str]) -> str | None:
        held = bindings.get(module, {})
        direct = held.get(f"exports.{name}")
        entry = maps.get(module)
        if entry is not None and name in entry.dead:
            return None  # written onto an object the module no longer exports
        if entry is not None and entry.tier != "opaque":  # the names the module may export decide
            if name not in entry.names:
                return None  # not exported under that name
            target = entry.names[name]
            if target is None and entry.tier == "readable":
                # a value, not a declaration: `exports.Post = sequelize.define(…)`
                return entity_id(direct) if direct is not None else None
            if target is not None:
                local = target[len(module) + 1 :] if target.startswith(f"{module}.") else ""
                model = held.get(local) if local and "." not in local else None
                return entity_id(model) if model is not None else None
        model = direct or held.get(name)
        if model is not None:
            return entity_id(model)
        exact = entity_id(name)
        if exact in candidates:
            return exact
        folded = [c for c in candidates if c.lower() == exact.lower()]
        return folded[0] if len(folded) == 1 else None

    def resolve(end: str) -> str | None:
        if not end.startswith(ENTITY_OF):
            return end
        module, _, imported = end[len(ENTITY_OF) :].rpartition("#")
        candidates = by_module.get(module, set())
        if imported == _WHOLE:
            return next(iter(candidates)) if len(candidates) == 1 else None
        found = named(module, imported, candidates)
        return found if found in candidates else None

    out = FactBatch()
    for node in batch.nodes:
        out.add_node(node)
    for edge in batch.edges:
        if edge.kind is EdgeKind.REFERENCES:
            src, dst = resolve(edge.src), resolve(edge.dst)
            if src is None or dst is None or src == dst:
                continue
            edge = Edge(src, dst, edge.kind, edge.provenance)
        out.add_edge(edge)
    return out


__all__ = ["ENTITY_OF", "entity_id", "scan", "settle"]
