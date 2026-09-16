"""Compose navigation → in-app routes as ``Endpoint``s with verb ``NAV``.

P4 of docs/specs/kotlin-support-roadmap.md, D14.

``composable("topic/{topicId}") { TopicRoute(…) }`` *declares* a route and
``navController.navigate("topic/$id")`` *consumes* one. That is the same shape as
an HTTP route with the app as both provider and consumer, so it reuses the same
vocabulary rather than adding a node kind: ``Endpoint`` named ``NAV topic/{topicId}``,
``EXPOSES`` to the screen it shows, ``CONSUMES`` from the function that navigates.
``NAV`` cannot collide with an HTTP verb, so an in-app route never joins to a real
one in ``pkg joins``.

**Routes are constants, not literals — which is the whole difficulty.** The
textbook form passes a string, but real navigation code names a route once and
imports it: the validation app writes ``composable(route = forYouNavigationRoute)``
and declares ``const val forYouNavigationRoute = "for_you_route"`` in *another
file*. A reader that only accepts string literals finds almost nothing. So route
constants are collected across the whole walk and resolved in ``finalize``, the
same two-stage shape the Room table join uses.

**Two paths match when they differ only in their parameters.** A declaration
writes ``"topic_route/{$topicIdArg}"`` and the call writes ``"topic_route/$encodedId"``.
Both normalise to ``topic_route/{}`` for matching, while the endpoint keeps the
readable resolved form (``topic_route/{topicId}``) as its name. Without that, the
declaration and the call would never pair and every in-app route would look unused.

**What it refuses.** A route built by concatenation or a function call yields
nothing. A ``composable`` lambda that calls no named screen, or several, gets an
``Endpoint`` but no ``EXPOSES`` — the closure rule, because picking one of several
would be a guess about which one is "the" screen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.kotlin_names import string_value, text

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_LANG = "kotlin"

#: The pseudo-verb that keeps in-app routes out of the HTTP join (D14).
NAV = "NAV"

#: `{anything}` or `$ident` — the parts of a route that vary per navigation.
_PARAM = re.compile(r"\{[^}]*\}|\$\w+")


@dataclass
class NavState:
    """Routes, declarations and navigations collected across the whole walk.

    Everything here waits for ``finalize`` because a route constant is almost
    always declared in a different file from the ``composable`` that uses it.
    """

    #: `const val` name → its literal value, from every file seen so far
    consts: dict[str, str] = field(default_factory=dict)
    #: (raw route expression, screen id or "", rel, line)
    declarations: list[tuple[str, str, str, int]] = field(default_factory=list)
    #: (raw route expression, calling function id, rel, line)
    navigations: list[tuple[str, str, str, int]] = field(default_factory=list)

    def clear(self) -> None:
        self.consts.clear()
        self.declarations.clear()
        self.navigations.clear()


def collect_consts(root: TSNode, source: bytes, state: NavState) -> None:
    """Record every top-level ``const val NAME = "literal"`` in this file."""
    for node in root.named_children:
        if node.type != "property_declaration":
            continue
        decl = next((c for c in node.named_children if c.type == "variable_declaration"), None)
        if decl is None:
            continue
        name = next((text(c, source) for c in decl.named_children if c.type == "identifier"), "")
        literal = next(
            (string_value(c, source) for c in node.named_children if c.type == "string_literal"),
            None,
        )
        if name and literal:
            state.consts[name] = literal


def scan_calls(body: TSNode, func_id: str, source: bytes, rel: str, state: NavState) -> None:
    """Collect ``composable(...)`` declarations and ``navigate(...)`` calls in a body."""
    for call in _walk(body):
        if call.type != "call_expression" or _is_inner_callee(call):
            continue
        # A trailing lambda wraps the call it decorates: `composable(route = r) { … }`
        # is an outer `call_expression` whose callee is the inner `composable(route = r)`.
        # The arguments live on the inner node and the lambda on the outer, so both
        # have to be read from the right one — reading only the node whose callee is
        # named `composable` finds the route and never the screen.
        inner = _arguments_holder(call)
        name = _callee_name(inner, source)
        if name == "composable":
            route = _argument_text(inner, source, named="route")
            if route:
                state.declarations.append((route, _single_screen(call, source), rel, call.start_point[0] + 1))
        elif name == "navigate":
            route = _argument_text(inner, source)
            if route:
                state.navigations.append((route, func_id, rel, call.start_point[0] + 1))


def emit(state: NavState, batch: FactBatch, resolve: Any) -> None:
    """Turn collected routes into ``Endpoint`` + ``EXPOSES`` + ``CONSUMES``.

    Runs once, in ``finalize``, when every route constant in the repository is known.
    """
    by_key: dict[str, str] = {}
    for raw, screen, rel, line in state.declarations:
        path = _resolve(raw, state.consts)
        if path is None:
            continue
        endpoint_id = f"java:endpoint:{NAV} {path}"
        provenance = Provenance(rel, line)
        batch.add_node(Node(endpoint_id, NodeKind.ENDPOINT, f"{NAV} {path}", _LANG, provenance))
        by_key.setdefault(_key(path), endpoint_id)
        target = resolve(screen) if screen else None
        if target:
            batch.add_edge(Edge(endpoint_id, target, EdgeKind.EXPOSES, provenance))

    for raw, caller, rel, line in state.navigations:
        path = _resolve(raw, state.consts)
        if path is None:
            continue
        declared = by_key.get(_key(path))
        if declared is None:
            # Navigating to a route nothing in this tree declares. Saying nothing is
            # the honest answer — inventing the endpoint would make `pkg verify`
            # report zero dangling for a destination that does not exist.
            continue
        batch.add_edge(Edge(caller, declared, EdgeKind.CONSUMES, Provenance(rel, line)))


def _resolve(raw: str, consts: dict[str, str]) -> str | None:
    """A route expression → its path, or ``None`` when it cannot be known.

    Handles the three spellings real navigation code uses: a plain literal, a bare
    constant reference, and a literal with ``$constant`` interpolated into it.
    """
    if raw.startswith('"'):
        inner = raw[1:-1]
        return _PARAM.sub(lambda m: _expand(m.group(0), consts), inner)
    literal = consts.get(raw)
    return literal if literal is not None else None


def _expand(token: str, consts: dict[str, str]) -> str:
    """``{$topicIdArg}`` → ``{topicId}`` when the constant is known, else unchanged."""
    inner = token.strip("{}")
    if inner.startswith("$"):
        value = consts.get(inner[1:])
        if value is not None:
            return f"{{{value}}}"
    return token


def _key(path: str) -> str:
    """One spelling for matching: every parameter segment collapses to ``{}``.

    ``topic_route/{topicId}`` from the declaration and ``topic_route/$encodedId``
    from the call are the same route; only this normalisation makes them pair.
    """
    return _PARAM.sub("{}", path)


def _is_inner_callee(call: TSNode) -> bool:
    """Whether this call is only the callee half of an enclosing trailing-lambda call."""
    parent = call.parent
    return (
        parent is not None
        and parent.type == "call_expression"
        and next(iter(parent.named_children), None) is call
    )


def _arguments_holder(call: TSNode) -> TSNode:
    """The node carrying ``value_arguments`` — the inner call when a lambda wraps it."""
    first = next(iter(call.named_children), None)
    return first if first is not None and first.type == "call_expression" else call


def _callee_name(call: TSNode, source: bytes) -> str:
    callee = next(iter(call.named_children), None)
    if callee is None:
        return ""
    if callee.type == "identifier":
        return text(callee, source)
    if callee.type == "navigation_expression":  # `this.navigate(...)`, `controller.navigate(...)`
        parts = callee.named_children
        if parts and parts[-1].type == "identifier":
            return text(parts[-1], source)
    return ""


def _argument_text(call: TSNode, source: bytes, *, named: str = "") -> str:
    """The raw text of the route argument — named when given, else the first one."""
    args = next((c for c in call.named_children if c.type == "value_arguments"), None)
    if args is None:
        return ""
    positional: list[str] = []
    for arg in args.named_children:
        if arg.type != "value_argument" or not arg.named_children:
            continue
        children = arg.named_children
        if named and len(children) >= 2 and children[0].type == "identifier":
            if text(children[0], source) == named:
                return text(children[-1], source)
            continue
        if len(children) == 1:
            positional.append(text(children[0], source))
    return positional[0] if positional else ""


def _single_screen(call: TSNode, source: bytes) -> str:
    """The one named screen a ``composable`` lambda shows, or ``""``.

    Compose names screens with a capitalised function, which is what makes this
    readable at all. Zero or several means no ``EXPOSES`` — the closure rule from
    D14: a lambda that shows two things does not have "the" screen, and picking
    one would be a guess.
    """
    lambda_node = next((c for c in call.named_children if c.type == "annotated_lambda"), None)
    if lambda_node is None:
        return ""
    names = []
    for inner in _walk(lambda_node):
        if inner.type != "call_expression":
            continue
        name = _callee_name(inner, source)
        if name[:1].isupper() and name not in names:
            names.append(name)
    return names[0] if len(names) == 1 else ""


def _walk(node: TSNode) -> list[TSNode]:
    out: list[TSNode] = []
    stack = [node]
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(current.named_children)
    return out


__all__ = ["NAV", "NavState", "collect_consts", "emit", "scan_calls"]
