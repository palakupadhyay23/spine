"""Sequelize models in JavaScript source → ``Entity`` + ``Field`` nodes and ``REFERENCES`` edges.

The data layer of javascript-support-roadmap P4 (D10). The shape is Python's (``python_orm.py``):
an ``Entity`` a module ``CONTAINS``, its columns as ``Field`` nodes it ``CONTAINS``, and
entity→entity ``REFERENCES``. Ids are ``ts:entity:<model>`` — the ``ts:`` namespace the
JavaScript front-end shares with TypeScript, in the form ``ts:endpoint:`` already uses — and
deliberately not ``sql:``-prefixed, so :func:`~orchestrator.pkg.data_layer_link.link_data_layer`
reads them as ORM entities and collapses each onto a matching table in a ``.sql`` schema. It
matches by name alone, so nothing here is JavaScript-specific to it.

**No ORM marker, no Entity** — the rule every ORM reader in this package keeps, because a model
is not recognisable by its shape. The two markers read here, both statically:

- ``<x>.define('<name>', {…})`` in a file that imports the ``sequelize`` package. The official
  example app defines every model this way, and *inside a function* —
  ``module.exports = (sequelize) => { sequelize.define('user', …) }`` — with the connection
  handed in as a parameter. So the whole file is walked, not only its top level, and the
  receiver's name is not what marks it: the import is.
- ``class X extends Model`` where ``Model`` is **bound from ``sequelize``** — a base merely
  *named* ``Model`` is not enough, since Objection.js names its base the same — with the
  columns read from ``X.init({…}, …)``.

**``REFERENCES`` follows the foreign key**, so it agrees with the schema it is reconciled
against: ``A.belongsTo(B)`` puts the key on A (A references B); ``A.hasMany(B)`` and
``A.hasOne(B)`` put it on B (B references A). The redundant pair a real app writes —
``orchestra.hasMany(instrument)`` *and* ``instrument.belongsTo(orchestra)`` — therefore states
one edge twice, not two. Both ends are resolved by name: from a destructuring of the models
registry (``const { instrument } = sequelize.models``), a ``….models.name`` access, a Sequelize
model class in this file, or an import binding. A name is a claim, so the front-end's
``finalize`` drops any ``REFERENCES`` whose ends are not both entities something defined.

Left out, and declared in the corpus rather than guessed: ``belongsToMany`` (its keys live on a
join table the source may never declare as a model), a model name or attribute map that is not a
literal, and a file that reaches Sequelize only through a re-export of its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_LANG = "javascript"
_PACKAGE = "sequelize"
#: Association → whether the foreign key sits on the *receiver* (True) or the argument (False).
_ASSOCIATIONS = {"belongsTo": True, "hasMany": False, "hasOne": False}


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
    args = call.child_by_field_name("arguments")
    return list(args.named_children) if args is not None else []


def entity_id(name: str) -> str:
    return f"ts:entity:{name}"


def scan(
    root: TSNode, module_id: str, source: bytes, rel: str, batch: FactBatch, imports: dict[str, str]
) -> None:
    """Emit this file's Sequelize models and associations into ``batch``."""
    nodes = _walk(root)
    uses_sequelize = _PACKAGE in imports.values()
    model_base = {local for local, spec in imports.items() if spec == _PACKAGE and local == "Model"}

    classes = _model_classes(nodes, source, model_base)
    if uses_sequelize:
        for call in (n for n in nodes if n.type == "call_expression"):
            _define(call, module_id, source, rel, batch)
    for cls, name in classes.items():
        _init(nodes, cls, name, module_id, source, rel, batch)

    registry = _registry_names(nodes, source)
    for call in (n for n in nodes if n.type == "call_expression"):
        _association(call, source, rel, batch, registry, classes, imports)


def _model_classes(nodes: list[TSNode], source: bytes, model_base: set[str]) -> dict[str, str]:
    """``{class name: model name}`` for classes extending Sequelize's ``Model``."""
    out: dict[str, str] = {}
    if not model_base:
        return out
    from orchestrator.pkg.typescript_extractor import _supertypes

    for node in nodes:
        if node.type != "class_declaration" or not model_base & set(_supertypes(node, source)):
            continue
        name = _text(node.child_by_field_name("name"), source)
        if name:
            out[name] = name
    return out


def _define(call: TSNode, module_id: str, source: bytes, rel: str, batch: FactBatch) -> None:
    """``sequelize.define('user', {id: …, username: …})``."""
    fn = call.child_by_field_name("function")
    if (
        fn is None
        or fn.type != "member_expression"
        or _text(fn.child_by_field_name("property"), source) != "define"
    ):
        return
    args = _args(call)
    name = _string(args[0], source) if args else None
    if name is None:
        return
    attributes = args[1] if len(args) > 1 and args[1].type == "object" else None
    _entity(name, attributes, module_id, source, rel, call.start_point[0] + 1, batch)


def _init(
    nodes: list[TSNode], cls: str, name: str, module_id: str, source: bytes, rel: str, batch: FactBatch
) -> None:
    """``class User extends Model {}`` + ``User.init({…}, {sequelize})``.

    The class is the marker, so the entity exists even without an ``init`` in this file; the
    columns come from ``init`` when it is here.
    """
    attributes: TSNode | None = None
    line = 0
    for node in nodes:
        if node.type != "call_expression":
            continue
        fn = node.child_by_field_name("function")
        if (
            fn is not None
            and fn.type == "member_expression"
            and _text(fn.child_by_field_name("object"), source) == cls
            and _text(fn.child_by_field_name("property"), source) == "init"
        ):
            args = _args(node)
            if args and args[0].type == "object":
                attributes, line = args[0], node.start_point[0] + 1
                break
    if not line:
        line = next(
            (
                n.start_point[0] + 1
                for n in nodes
                if n.type == "class_declaration" and _text(n.child_by_field_name("name"), source) == cls
            ),
            1,
        )
    _entity(name, attributes, module_id, source, rel, line, batch)


def _entity(
    name: str, attributes: TSNode | None, module_id: str, source: bytes, rel: str, line: int, batch: FactBatch
) -> None:
    eid = entity_id(name)
    batch.add_node(Node(eid, NodeKind.ENTITY, name, _LANG, Provenance(rel, line)))
    batch.add_edge(Edge(module_id, eid, EdgeKind.CONTAINS, Provenance(rel, line)))
    if attributes is None:
        return
    for pair in attributes.named_children:
        if pair.type != "pair":
            continue  # a spread or a shorthand names no column this pass can see
        key = pair.child_by_field_name("key")
        column = (
            _text(key, source).strip("\"'")
            if key is not None and key.type in ("property_identifier", "string")
            else ""
        )
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


def _model_name(
    node: TSNode | None, source: bytes, registry: set[str], classes: dict[str, str], imports: dict[str, str]
) -> str | None:
    """The model an association end names, or ``None`` when it cannot be read without guessing."""
    if node is None:
        return None
    if node.type == "member_expression":  # sequelize.models.orchestra
        owner = node.child_by_field_name("object")
        if owner is not None and _text(owner.child_by_field_name("property"), source) == "models":
            return _text(node.child_by_field_name("property"), source) or None
        return None
    if node.type != "identifier":
        return None
    name = _text(node, source)
    if name in registry or name in classes:
        return classes.get(name, name)
    if name in imports:  # `const { Post } = require('./post')` — landed or dropped in `finalize`
        return name
    return None


def _association(
    call: TSNode,
    source: bytes,
    rel: str,
    batch: FactBatch,
    registry: set[str],
    classes: dict[str, str],
    imports: dict[str, str],
) -> None:
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "member_expression":
        return
    kind = _text(fn.child_by_field_name("property"), source)
    if kind not in _ASSOCIATIONS:
        return
    args = _args(call)
    receiver = _model_name(fn.child_by_field_name("object"), source, registry, classes, imports)
    target = _model_name(args[0] if args else None, source, registry, classes, imports)
    if receiver is None or target is None:
        return
    holder, referenced = (receiver, target) if _ASSOCIATIONS[kind] else (target, receiver)
    batch.add_edge(
        Edge(
            entity_id(holder),
            entity_id(referenced),
            EdgeKind.REFERENCES,
            Provenance(rel, call.start_point[0] + 1),
        )
    )


__all__ = ["entity_id", "scan"]
