"""Shared Kotlin CST helpers — the leaf module the Kotlin front-end's readers import.

``kotlin_extractor``, ``kotlin_room`` and ``kotlin_http`` all need to read text,
type names and **annotations** off the same tree. Putting those here rather than
in the extractor keeps the import graph a DAG (extractor → room/http → names),
which is the PHP lesson: ``php_names.py`` exists for exactly this reason after a
cycle between the extractor and its ORM reader.

It is deliberately ``kotlin_names`` rather than the ``jvm_names`` the roadmap
floated. Nothing here is shareable with the Java front-end: Java's grammar spells
an annotation ``annotation``/``marker_annotation`` with a ``name`` field, while
Kotlin nests a ``user_type`` (no arguments) or a ``constructor_invocation`` (with
arguments) under ``annotation``, and adds a ``use_site_target`` for ``@field:``
and ``@get:``. A shared module would be two disjoint halves under one name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode


def text(node: TSNode | None, source: bytes) -> str:
    """The source text of a node, stripped; ``""`` for a missing node."""
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace").strip()


def field_text(node: TSNode, field: str, source: bytes) -> str:
    child = node.child_by_field_name(field)
    return text(child, source) if child is not None else ""


def bare_type(name: str) -> str:
    """``Flow<List<Topic>>`` → ``Flow``; a nullable ``Topic?`` → ``Topic``."""
    return name.split("<", 1)[0].strip().rstrip("?").strip()


def element_type(name: str) -> str:
    """The innermost type argument of a generic, else the type itself.

    Room DAO methods are written against collections and coroutine wrappers —
    ``List<TopicEntity>``, ``Flow<List<TopicEntity>>`` — but the *entity* is what
    the edge has to point at. Peeling to the innermost argument is what turns a
    declared return or parameter type into the table it touches.
    """
    current = name.strip().rstrip("?").strip()
    while "<" in current and current.endswith(">"):
        current = current[current.index("<") + 1 : -1].strip().rstrip("?").strip()
        # `Map<String, Topic>` — a wrapper with several arguments names no single
        # entity, so stop rather than pick one.
        if "," in _top_level_split(current):
            return ""
    return current


def _top_level_split(name: str) -> str:
    """``name`` with nested generic arguments removed, for comma detection."""
    out: list[str] = []
    depth = 0
    for ch in name:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


@dataclass(frozen=True)
class Annotation:
    """One annotation on a declaration, with its arguments still as CST nodes.

    ``name`` is the simple name (``Entity``, not ``androidx.room.Entity``) — the
    qualified form is resolved through the import map by the caller, because only
    the caller knows which package it is looking for. ``args`` keeps argument
    order and names: ``("tableName", <node>)`` for ``tableName = "topics"`` and
    ``("", <node>)`` for a positional one.
    """

    name: str
    args: tuple[tuple[str, TSNode], ...]
    line: int

    def arg(self, name: str, *, position: int = 0) -> TSNode | None:
        """The named argument, falling back to the one at ``position``.

        Kotlin lets the same annotation be written either way and real code uses
        both: aiandroid writes ``@GET(value = "topics")`` and ``@Query(value =
        "…")`` with the name, while most examples write them positionally. A
        reader that handles only one form silently sees nothing in half of them.
        """
        for arg_name, node in self.args:
            if arg_name == name:
                return node
        positional = [node for arg_name, node in self.args if not arg_name]
        return positional[position] if position < len(positional) else None


def annotations_of(node: TSNode, source: bytes) -> list[Annotation]:
    """Every annotation on a declaration, read off its ``modifiers``.

    Handles both grammar shapes: ``@Dao`` nests a bare ``user_type``, while
    ``@Entity(tableName = "topics")`` nests a ``constructor_invocation`` holding
    the type and a ``value_arguments`` list. A ``use_site_target`` (``@field:``,
    ``@get:``) is skipped over rather than treated as part of the name.
    """
    out: list[Annotation] = list(_stranded_annotations(node, source))
    modifiers = next((c for c in node.named_children if c.type == "modifiers"), None)
    if modifiers is not None:
        for child in modifiers.named_children:
            if child.type == "annotation":
                out.extend(_read_annotation(child, source))
    return out


def _stranded_annotations(node: TSNode, source: bytes) -> list[Annotation]:
    """Annotations the grammar parked *outside* the declaration they decorate.

    tree-sitter-kotlin 1.1.0 misreads an annotation whose arguments are
    parenthesised when it sits above a declaration: ``@Resource("/styles")`` before
    a class becomes a standalone ``annotated_expression`` — an ``annotation`` node
    with no arguments plus a ``parenthesized_expression`` holding them — and the
    declaration loses its ``modifiers`` entirely. The source is ordinary Kotlin; a
    top-level annotated *expression* is not even legal, so every one of these is a
    misparse. It is not rare either: 24 files in the validation app and 8 in the
    Ktor sample corpus hit it, including ``@AndroidEntryPoint class MainActivity``.

    Left alone it fails the worst way — silently and completely. Every framework
    reading (Room, Hilt, Retrofit, Spring) asks this one function for a
    declaration's annotations, so a class whose annotations were stranded looks
    like a plain class rather than a DAO, a module or a controller.

    Recovery is deliberately narrow: only an ``annotated_expression`` **immediately**
    before the declaration, with nothing but whitespace between them, and only its
    single parenthesised argument re-attached. Anything further would start
    guessing which expression belonged to which declaration.
    """
    previous = node.prev_named_sibling
    if previous is None or previous.type != "annotated_expression":
        return []
    if source[previous.end_byte : node.start_byte].strip():
        return []  # something sits between them, so it is not this declaration's
    return _unwrap_annotated(previous, source)


def _unwrap_annotated(node: TSNode, source: bytes) -> list[Annotation]:
    """Read a misparsed ``annotated_expression`` chain back into annotations.

    The chain nests one level per annotation, and an argument list that the
    grammar split off follows its annotation as a ``parenthesized_expression``.
    """
    out: list[Annotation] = []
    children = list(node.named_children)
    index = 0
    while index < len(children):
        child = children[index]
        if child.type == "annotation":
            args: tuple[tuple[str, TSNode], ...] = ()
            following = children[index + 1] if index + 1 < len(children) else None
            if following is not None and following.type == "parenthesized_expression":
                args = tuple(("", c) for c in following.named_children)
                index += 1
            for annotation in _read_annotation(child, source):
                out.append(Annotation(annotation.name, annotation.args or args, annotation.line))
        elif child.type == "annotated_expression":
            out.extend(_unwrap_annotated(child, source))
        index += 1
    return out


def _read_annotation(node: TSNode, source: bytes) -> list[Annotation]:
    """One ``annotation`` node → the annotations it carries.

    Usually one. Kotlin also allows ``@[A B]`` to put several in one node, so the
    return is a list rather than a single value.
    """
    line = node.start_point[0] + 1
    out: list[Annotation] = []
    for child in node.named_children:
        if child.type == "user_type":  # `@Dao` — no arguments
            out.append(Annotation(_simple_name(text(child, source)), (), line))
        elif child.type == "constructor_invocation":  # `@Entity(...)`
            name = _simple_name(text(_child_of(child, "user_type"), source))
            out.append(Annotation(name, _value_arguments(child, source), line))
    return out


def _child_of(node: TSNode, kind: str) -> TSNode | None:
    return next((c for c in node.named_children if c.type == kind), None)


def _value_arguments(invocation: TSNode, source: bytes) -> tuple[tuple[str, TSNode], ...]:
    """``(tableName = "topics")`` → ``(("tableName", <string_literal>),)``."""
    args = _child_of(invocation, "value_arguments")
    if args is None:
        return ()
    out: list[tuple[str, TSNode]] = []
    for arg in args.named_children:
        if arg.type != "value_argument":
            continue
        children = arg.named_children
        if not children:
            continue
        # `name = value` is two named children with an `=` token between them;
        # a positional argument is one.
        if len(children) >= 2 and children[0].type == "identifier" and _has_eq(arg):
            out.append((text(children[0], source), children[-1]))
        else:
            out.append(("", children[-1]))
    return tuple(out)


def _has_eq(arg: TSNode) -> bool:
    return any(c.type == "=" for c in arg.children)


def _simple_name(name: str) -> str:
    """``androidx.room.Entity`` → ``Entity``; generics dropped."""
    return bare_type(name).rsplit(".", 1)[-1]


def string_value(node: TSNode | None, source: bytes) -> str | None:
    """A string literal's content, or ``None`` when the node is not a literal.

    Both spellings matter and the second one is not optional in practice: Room
    ``@Query`` bodies in real code are **raw** strings (``multiline_string_literal``),
    not the ordinary ``string_literal`` a first pass would look for — 8 of the 18
    ``@Query`` annotations in the validation app are written that way. Returning
    ``None`` for anything else is what keeps a computed path or a constant
    reference from being mistaken for a literal one.
    """
    if node is None:
        return None
    if node.type not in ("string_literal", "multiline_string_literal"):
        return None
    parts = [text(c, source) for c in node.named_children if c.type == "string_content"]
    if parts:
        return "".join(
            source[c.start_byte : c.end_byte].decode("utf-8", "replace")
            for c in node.named_children
            if c.type == "string_content"
        )
    # An empty literal ("" or """""") has no string_content child.
    raw = text(node, source)
    return "" if raw in ('""', '""""""') else None


def collection_items(node: TSNode | None) -> list[TSNode]:
    """The elements of a ``["a", "b"]`` collection literal, else an empty list."""
    if node is None or node.type != "collection_literal":
        return []
    return list(node.named_children)


def class_reference(node: TSNode | None, source: bytes) -> str:
    """``TopicEntity::class`` → ``TopicEntity``; ``""`` when it is not one.

    Room names related entities this way (``entity = TopicEntity::class``), and
    it is the only form that resolves to a type without inference.
    """
    if node is None:
        return ""
    raw = text(node, source)
    if not raw.endswith("::class"):
        return ""
    return bare_type(raw[: -len("::class")].strip()).rsplit(".", 1)[-1]


__all__ = [
    "Annotation",
    "annotations_of",
    "bare_type",
    "class_reference",
    "collection_items",
    "element_type",
    "field_text",
    "string_value",
    "text",
]
