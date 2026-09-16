"""Retrofit → HTTP **client** calls, the other half of ``EXPOSES``.

P3 of docs/specs/kotlin-support-roadmap.md, D10/§3.3.

**An Android app is a consumer, not a provider — and that inversion is the point.**
Every other front-end's HTTP reader looks for routes a codebase *serves*: JAX-RS in
Java, Laravel in PHP, FastAPI in Python. Nothing in an Android app exposes a route;
it calls one. So a Retrofit ``@GET("topics")`` is not an ``Endpoint`` — asserting
one would claim this repository serves a path it merely calls — it is a
**``CONSUMES`` candidate**, and it goes through the same ``PendingCall``
side-channel :mod:`orchestrator.pkg.python_client` already uses.

That makes a Kotlin app the first **mobile consumer** in the multi-repo join:
``pkg joins`` matches these calls against another declared repository's
``Endpoint``s by verb and path, so "which screens break if this Go service changes
`GET /topics`" becomes answerable. No other front-end can be that today.

Only the *scanner* here is Kotlin-specific. ``ClientState``, ``PendingCall`` and
``emit`` are reused verbatim from ``python_client``, including its rule that an
unmatched call stays **out of the graph** and lives on ``unresolved_calls``
instead — emitting an edge to an endpoint nothing in scope serves is the
self-consistent invention this project has removed twice. Generalising the shared
half into a ``pkg/http_clients.py`` with per-language scanners is §9.6, deliberately
left to the generic-work phase rather than done speculatively here.

**What it refuses to read.** A non-literal path (``@GET(PATH_CONST)``) yields
nothing. A non-literal ``baseUrl(...)`` — which is what the validation app has,
``baseUrl(NiaBaseUrl)`` where the constant comes from ``BuildConfig`` — leaves the
call path-only, which is the documented fallback and is also the right answer: the
host is build configuration, not a fact about the code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from orchestrator.pkg.facts import EdgeKind, FactBatch, Provenance
from orchestrator.pkg.kotlin_names import annotations_of, field_text, string_value
from orchestrator.pkg.python_client import ClientState, PendingCall
from orchestrator.pkg.python_client import emit as _emit_matched

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

#: Retrofit's method annotations. `HTTP` (the generic escape hatch) is excluded:
#: its verb is an argument, and a verb-less call cannot join to anything.
_VERBS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"})


def scan_type(
    node: TSNode,
    type_id: str,
    source: bytes,
    rel: str,
    state: ClientState,
    *,
    base_path: str = "",
) -> None:
    """Collect this type's Retrofit calls into ``state``. No emission here.

    Endpoints may be declared in any file — or, for the cross-repo case, in another
    repository entirely — so the join waits until every endpoint is known, exactly
    as the Python scanner does.
    """
    for body in (c for c in node.named_children if c.type in ("class_body", "enum_class_body")):
        for member in body.named_children:
            if member.type != "function_declaration":
                continue
            name = field_text(member, "name", source)
            if not name:
                continue
            _scan_method(member, f"{type_id}.{name}", source, rel, state, base_path)


def _scan_method(
    method: TSNode,
    func_id: str,
    source: bytes,
    rel: str,
    state: ClientState,
    base_path: str,
) -> None:
    for annotation in annotations_of(method, source):
        if annotation.name not in _VERBS:
            continue
        # Real Retrofit code writes both `@GET("topics")` and `@GET(value = "topics")`;
        # the validation app uses the named form throughout.
        path = string_value(annotation.arg("value"), source)
        if path is None:
            continue  # a computed path names no route — say nothing
        state.calls.append(
            PendingCall(
                verb=annotation.name,
                path=_join(base_path, path),
                caller_id=func_id,
                provenance=Provenance(rel, annotation.line),
            )
        )


def _join(base_path: str, path: str) -> str:
    """Join the builder's base path to an annotation path, exactly one slash between.

    Retrofit writes the base with a trailing slash (``https://host/v1/``) and the
    annotation without a leading one (``"topics"``), so concatenating them
    directly yields ``/v1topics`` — a path that matches no endpoint and fails the
    cross-repo join silently, which is the only way this could go wrong.
    """
    if not base_path:
        return _norm(path)
    return _norm(f"{base_path.rstrip('/')}/{path.lstrip('/')}")


def join_to_endpoints(state: ClientState, batch: FactBatch) -> tuple[list[PendingCall], int]:
    """Join collected calls to endpoints in ``batch``.

    Returns ``(calls that matched nothing, number of CONSUMES edges added)``. The
    count is what distinguishes "this repo serves what it calls" from "these are
    cross-repo candidates", and it is the number `tests/pkg/test_kotlin_http.py`
    asserts when it checks that a monorepo really does join.

    A thin wrapper over ``python_client.emit`` so the Kotlin front-end owns its own
    client lifecycle. That is not ceremony: the capability matrix in
    ``KNOWLEDGE_GRAPH.md`` is *derived* by reading which ``NodeKind``/``EdgeKind``
    members a front-end and its direct delegates name, and importing ``emit``
    into the extractor made Kotlin claim it could emit ``Endpoint`` nodes —
    because ``emit`` mentions ``NodeKind.ENDPOINT`` to *look endpoints up*. For a
    consumer-only front-end that claim is precisely backwards (D10), so the
    import stops here, one level further out, where the matrix does not inherit it.

    ``CONSUMES`` **is** claimed, and correctly: a monorepo holding this Android
    app beside a Java service really does get the edge.
    """
    before = sum(1 for e in batch.edges if e.kind is EdgeKind.CONSUMES)
    _emit_matched(state, batch)
    joined = sum(1 for e in batch.edges if e.kind is EdgeKind.CONSUMES) - before
    unmatched = list(state.unmatched)
    state.clear()
    return unmatched, joined


def base_url_path(root: TSNode, source: bytes) -> str:
    """The path component of a literal ``baseUrl("https://…/v1/")``, else ``""``.

    Retrofit puts the host in the builder rather than the annotations, so a call's
    full path is only knowable when the builder argument is a literal. When it is
    not — ``baseUrl(NiaBaseUrl)`` in the validation app — this returns ``""`` and
    the call stays path-only rather than acquiring a host that was guessed.
    """
    for node in _walk(root):
        if node.type != "call_expression":
            continue
        callee = next(iter(node.named_children), None)
        if callee is None:
            continue
        name = callee.type == "navigation_expression" and _trailing_name(callee, source)
        if name != "baseUrl":
            continue
        args = next((c for c in node.named_children if c.type == "value_arguments"), None)
        if args is None:
            continue
        for arg in args.named_children:
            if arg.type != "value_argument" or not arg.named_children:
                continue
            literal = string_value(arg.named_children[-1], source)
            if literal:
                return urlsplit(literal).path.rstrip("/")
    return ""


def _trailing_name(nav: TSNode, source: bytes) -> str:
    parts = nav.named_children
    if not parts or parts[-1].type != "identifier":
        return ""
    return source[parts[-1].start_byte : parts[-1].end_byte].decode("utf-8", "replace")


def _norm(path: str) -> str:
    """One spelling for a path, so both sides of the join agree.

    Deliberately identical to ``python_client._norm``: the whole value of this
    reader is that its output can be compared against endpoints emitted by another
    front-end, and two normalizers that disagree by a trailing slash would make
    every join silently miss.
    """
    if "://" in path:
        path = urlsplit(path).path or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


def _walk(node: TSNode) -> list[TSNode]:
    out: list[TSNode] = []
    stack = [node]
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(current.named_children)
    return out


__all__ = [
    "ClientState",
    "PendingCall",
    "base_url_path",
    "join_to_endpoints",
    "scan_type",
]
