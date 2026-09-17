"""Spring MVC routes, for the Kotlin and Java front-ends alike.

P6 of docs/specs/kotlin-support-roadmap.md, D16.

``@RestController @RequestMapping("/api") class C { @GetMapping("/topics") fun list() }``
declares ``GET /api/topics`` and says which function serves it. That reading is
identical in Kotlin and Java; only the *grammar* differs — Java spells an
annotation ``annotation``/``marker_annotation`` with a ``name`` field, Kotlin nests
a ``user_type`` or a ``constructor_invocation``. So each front-end reads its own
tree into :class:`RouteAnnotation` and the route semantics live here, once.

**Why this module exists at all.** The Java front-end has always read JAX-RS and
never Spring, which is the framework most Java services actually use. Adding it
for Kotlin without adding it for Java would have left the same blind spot in the
older front-end while claiming the graph understands JVM web services. One reader,
both languages, is the only version of that claim that is true.

**The class stereotype is required, and that is a precision rule, not ceremony.**
``@GetMapping`` on its own means nothing: Spring Cloud OpenFeign puts the *same*
annotations on an interface to declare an HTTP **client**. Reading those as
endpoints would make every consumer look like a provider and quietly corrupt
``pkg joins``. Only a class annotated ``@Controller`` or ``@RestController``
registers handler mappings, so only such a class is read. (``@RestController`` is
``@Controller`` plus ``@ResponseBody``; both route, and the validation repository
uses the plain one throughout — an earlier draft of D16 named only
``@RestController`` and would have found nothing there.)

**The no-``ANY`` rule.** ``@RequestMapping("/x")`` with no ``method`` matches every
verb. There is no honest ``Endpoint`` for that: inventing ``ANY`` would put a verb
in the graph that appears nowhere in the source, and expanding it to seven
endpoints would invent six routes nobody wrote. On a method it yields nothing. On
a *class* it is still a perfectly good path prefix, which is what it is used for.

**What silences a reading.** A path argument that is not a string literal — a
constant, a concatenation, a ``@Value`` placeholder — yields ``None`` rather than a
guess, and ``None`` at class level silences every method in the class, because a
method path without its real prefix is a route that does not exist.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

#: Packages an annotation must resolve into to be Spring's. ``stereotype`` carries
#: ``@Controller``, ``web.bind.annotation`` carries ``@RestController`` and every
#: mapping annotation — both are needed, and neither front-end may assume either.
SPRING_PACKAGES = frozenset({"org.springframework.web.bind.annotation", "org.springframework.stereotype"})

#: Class annotations that make a class a request-mapping handler (see module docstring).
CONTROLLER_ANNOTATIONS = frozenset({"Controller", "RestController"})

#: A Spring property placeholder (``${api.base}``) or a SpEL expression (``#{...}``).
#: Either one means the path is resolved from configuration at boot, so the source does
#: not say what it is.
_PLACEHOLDER = re.compile(r"[$#]\{")


def literal_path(value: str | None) -> str | None:
    """A route path that is genuinely constant, or ``None``.

    The module docstring's rule — "a path argument that is not a string literal … a
    ``@Value`` placeholder — yields ``None`` rather than a guess" — needs enforcing
    *after* the literal is read, not only by refusing non-literal nodes. Found in
    review: ``@GetMapping("${api.base}/topics")`` is a perfectly ordinary Java
    ``string_literal``, so it sailed through the node-type test and produced the
    endpoint ``GET /${api.base}/topics``. Kotlin has the same hole by a different
    spelling: an interpolation is refused by the grammar, but the escaped form
    ``"\\${api.base}/topics"`` decodes to exactly the same literal text.

    An empty string stays a valid path (it is how a class-level prefix says "no
    prefix"), so this tests for the placeholder, not for emptiness.
    """
    if value is None:
        return None
    return None if _PLACEHOLDER.search(value) else value


#: The verb each shortcut mapping annotation stands for.
VERB_BY_MAPPING = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}

#: The generic annotation, whose verb comes from its ``method`` argument instead.
REQUEST_MAPPING = "RequestMapping"

#: Argument names that hold the path. Spring accepts either, and real code uses both.
PATH_ARGUMENTS = ("value", "path")

#: The argument naming the verbs of a generic ``@RequestMapping``.
METHOD_ARGUMENT = "method"

#: Every annotation this module reads — front-ends use it to skip the rest cheaply.
MAPPING_ANNOTATIONS = frozenset(VERB_BY_MAPPING) | {REQUEST_MAPPING}


@dataclass(frozen=True)
class RouteAnnotation:
    """One Spring annotation, already read out of its language's grammar.

    ``path`` distinguishes three states that must not be conflated: ``""`` means
    the annotation carries no path (``@GetMapping`` alone, which maps the class
    prefix), a string means a literal one, and ``None`` means *there is a path
    argument but it is not a literal* — the case that has to silence the reading
    rather than fall back to the prefix.

    ``methods`` holds the simple verb names of a ``method = [...]`` argument
    (``RequestMethod.GET`` → ``"GET"``), empty for the shortcut annotations that
    name their verb in the annotation itself.
    """

    name: str
    path: str | None = ""
    methods: tuple[str, ...] = ()
    line: int = 0


def resolves_into_spring(
    name: str, *, by_simple: Mapping[str, str], wildcard_prefixes: AbstractSet[str]
) -> bool:
    """Whether an annotation written as ``name`` is one of Spring's, here.

    Both front-ends need exactly this and neither may assume it: ``@GetMapping``
    could be anybody's annotation until an import says otherwise. A fully
    qualified name answers for itself; a bare one resolves through an explicit
    import, and failing that through a wildcard — which the validation repository
    needs, since it writes ``import org.springframework.web.bind.annotation.*``.
    """
    simple = name.rsplit(".", 1)[-1]
    if "." in name:
        return name.rsplit(".", 1)[0] in SPRING_PACKAGES
    imported = by_simple.get(simple)
    if imported is not None:
        return imported.rsplit(".", 1)[0] in SPRING_PACKAGES
    return bool(set(wildcard_prefixes) & SPRING_PACKAGES)


def is_controller(annotations: list[RouteAnnotation]) -> bool:
    """Whether these class annotations make it a Spring request handler."""
    return any(a.name in CONTROLLER_ANNOTATIONS for a in annotations)


def class_prefix(annotations: list[RouteAnnotation]) -> str | None:
    """The class-level path prefix: ``""`` when absent, ``None`` when non-literal.

    A class may carry ``@RequestMapping`` with no verb — that is its normal use, and
    the no-``ANY`` rule does not apply here because a class declares no endpoint of
    its own either way.
    """
    for a in annotations:
        if a.name == REQUEST_MAPPING:
            return a.path
    return ""


def verbs_of(annotation: RouteAnnotation) -> tuple[str, ...]:
    """The HTTP verbs one method annotation declares — empty when it declares none.

    Empty is the answer for a verb-less ``@RequestMapping`` (the no-``ANY`` rule)
    and for any annotation that is not a mapping at all.
    """
    shortcut = VERB_BY_MAPPING.get(annotation.name)
    if shortcut is not None:
        return (shortcut,)
    if annotation.name != REQUEST_MAPPING:
        return ()
    return tuple(dict.fromkeys(annotation.methods))


def join_path(prefix: str, suffix: str) -> str:
    """Join a class prefix and a method path into one normalised absolute path.

    Spring lets a method path omit its leading slash (``@GetMapping("vets.json")``
    appears three times in the validation repository), so the slashes are
    normalised rather than concatenated.
    """
    parts = [part.strip("/") for part in (prefix, suffix) if part and part.strip("/")]
    return "/" + "/".join(parts) if parts else "/"


def emit_endpoints(
    annotations: list[RouteAnnotation],
    *,
    method_id: str,
    prefix: str,
    language: str,
    rel: str,
    batch: FactBatch,
) -> tuple[str, ...]:
    """Emit ``Endpoint`` + ``EXPOSES`` for one handler method. Returns the paths.

    ``prefix`` is the class prefix from :func:`class_prefix`; the caller is
    responsible for having skipped the class entirely when that was ``None``.
    """
    emitted: list[str] = []
    for annotation in annotations:
        verbs = verbs_of(annotation)
        if not verbs or annotation.path is None:
            continue  # no verb (the no-`ANY` rule), or a path that is not a literal
        path = join_path(prefix, annotation.path)
        provenance = Provenance(rel, annotation.line)
        for verb in verbs:
            endpoint_id = f"java:endpoint:{verb} {path}"
            batch.add_node(Node(endpoint_id, NodeKind.ENDPOINT, f"{verb} {path}", language, provenance))
            batch.add_edge(Edge(endpoint_id, method_id, EdgeKind.EXPOSES, provenance))
            emitted.append(f"{verb} {path}")
    return tuple(emitted)


__all__ = [
    "CONTROLLER_ANNOTATIONS",
    "MAPPING_ANNOTATIONS",
    "METHOD_ARGUMENT",
    "PATH_ARGUMENTS",
    "REQUEST_MAPPING",
    "SPRING_PACKAGES",
    "VERB_BY_MAPPING",
    "RouteAnnotation",
    "class_prefix",
    "emit_endpoints",
    "is_controller",
    "join_path",
    "literal_path",
    "resolves_into_spring",
    "verbs_of",
]
