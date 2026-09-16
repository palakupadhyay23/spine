"""PKG: the Spring route semantics shared by the Kotlin and Java front-ends (P6, D16).

These tests exercise ``jvm_routes`` directly — no grammar, no tree-sitter — because
the whole point of the module is that the *meaning* of a Spring annotation is the
same in both languages and only the reading of it differs. The grammar halves are
tested in ``test_kotlin_routes.py`` and ``test_java_extractor``'s Spring cases.
"""

from __future__ import annotations

from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind
from orchestrator.pkg.jvm_routes import (
    RouteAnnotation,
    class_prefix,
    emit_endpoints,
    is_controller,
    join_path,
    resolves_into_spring,
    verbs_of,
)


def _emit(*annotations: RouteAnnotation, prefix: str = "") -> FactBatch:
    batch = FactBatch()
    emit_endpoints(
        list(annotations),
        method_id="java:svc.C.m",
        prefix=prefix,
        language="java",
        rel="C.java",
        batch=batch,
    )
    return batch


def _endpoints(batch: FactBatch) -> set[str]:
    return {n.name for n in batch.nodes if n.kind is NodeKind.ENDPOINT}


# ---- what makes a class a route source --------------------------------------


def test_rest_controller_is_a_controller() -> None:
    assert is_controller([RouteAnnotation("RestController")])


def test_plain_controller_is_also_a_controller() -> None:
    """`@RestController` is `@Controller` + `@ResponseBody`; both register mappings.

    The validation repository (spring-petclinic-kotlin) uses the plain one on every
    controller it has, so a reader that accepts only `@RestController` finds nothing
    there. D16's first draft named only `@RestController`.
    """
    assert is_controller([RouteAnnotation("Controller")])


def test_a_mapping_without_a_stereotype_is_not_a_controller() -> None:
    """The Feign case: the same annotations declare a route the service *calls*."""
    assert not is_controller([RouteAnnotation("GetMapping", "/remote")])


# ---- the class prefix -------------------------------------------------------


def test_absent_class_mapping_is_an_empty_prefix() -> None:
    assert class_prefix([RouteAnnotation("RestController")]) == ""


def test_literal_class_mapping_is_the_prefix() -> None:
    assert class_prefix([RouteAnnotation("RequestMapping", "/api")]) == "/api"


def test_non_literal_class_mapping_is_none_and_not_an_empty_prefix() -> None:
    """`None` silences the whole class; `""` would silently root every method."""
    assert class_prefix([RouteAnnotation("RequestMapping", None)]) is None


# ---- verbs ------------------------------------------------------------------


def test_shortcut_annotations_name_their_own_verb() -> None:
    assert verbs_of(RouteAnnotation("PostMapping", "/x")) == ("POST",)


def test_request_mapping_takes_its_verbs_from_the_method_argument() -> None:
    assert verbs_of(RouteAnnotation("RequestMapping", "/x", ("PUT", "PATCH"))) == ("PUT", "PATCH")


def test_a_verb_less_request_mapping_declares_nothing() -> None:
    """The no-`ANY` rule: a verb nobody wrote must not enter the graph."""
    assert verbs_of(RouteAnnotation("RequestMapping", "/x")) == ()


def test_a_non_mapping_annotation_declares_no_verb() -> None:
    assert verbs_of(RouteAnnotation("Valid")) == ()


def test_repeated_verbs_collapse() -> None:
    assert verbs_of(RouteAnnotation("RequestMapping", "/x", ("GET", "GET"))) == ("GET",)


# ---- path joining -----------------------------------------------------------


def test_join_normalises_a_method_path_without_a_leading_slash() -> None:
    """Spring allows it and petclinic writes it three times (`@GetMapping("vets.json")`)."""
    assert join_path("", "vets.json") == "/vets.json"


def test_join_composes_class_and_method_paths() -> None:
    assert join_path("/owners/{ownerId}", "/pets/new") == "/owners/{ownerId}/pets/new"


def test_join_of_nothing_is_the_root() -> None:
    assert join_path("", "") == "/"


# ---- emission ---------------------------------------------------------------


def test_one_verb_emits_one_endpoint_and_one_exposes() -> None:
    batch = _emit(RouteAnnotation("GetMapping", "/topics"), prefix="/api")
    assert _endpoints(batch) == {"GET /api/topics"}
    assert [(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.EXPOSES] == [
        ("java:endpoint:GET /api/topics", "java:svc.C.m")
    ]


def test_two_verbs_on_one_method_are_two_endpoints() -> None:
    """Spring really does register both, so the graph says both."""
    batch = _emit(RouteAnnotation("RequestMapping", "/t", ("PUT", "PATCH")), prefix="/api")
    assert _endpoints(batch) == {"PUT /api/t", "PATCH /api/t"}


def test_a_bare_mapping_serves_the_class_prefix() -> None:
    batch = _emit(RouteAnnotation("GetMapping"), prefix="/api")
    assert _endpoints(batch) == {"GET /api"}


def test_a_non_literal_method_path_emits_nothing() -> None:
    assert _endpoints(_emit(RouteAnnotation("GetMapping", None), prefix="/api")) == set()


def test_a_verb_less_mapping_emits_nothing() -> None:
    assert _endpoints(_emit(RouteAnnotation("RequestMapping", "/any"), prefix="/api")) == set()


def test_the_endpoint_id_shares_the_java_namespace() -> None:
    """D2: one `java:endpoint:` namespace, so a Kotlin provider joins a Java consumer."""
    batch = _emit(RouteAnnotation("GetMapping", "/t"))
    assert [n.id for n in batch.nodes if n.kind is NodeKind.ENDPOINT] == ["java:endpoint:GET /t"]


# ---- resolving an annotation to Spring --------------------------------------


def test_an_explicit_import_resolves_the_annotation() -> None:
    assert resolves_into_spring(
        "GetMapping",
        by_simple={"GetMapping": "org.springframework.web.bind.annotation.GetMapping"},
        wildcard_prefixes=set(),
    )


def test_a_wildcard_import_resolves_the_annotation() -> None:
    """petclinic writes `import org.springframework.web.bind.annotation.*` — not optional."""
    assert resolves_into_spring(
        "GetMapping",
        by_simple={},
        wildcard_prefixes={"org.springframework.web.bind.annotation"},
    )


def test_a_fully_qualified_name_answers_for_itself() -> None:
    assert resolves_into_spring(
        "org.springframework.stereotype.Controller", by_simple={}, wildcard_prefixes=set()
    )


def test_an_unimported_annotation_does_not_resolve() -> None:
    """`@GetMapping` is nobody's annotation until an import says whose it is."""
    assert not resolves_into_spring("GetMapping", by_simple={}, wildcard_prefixes=set())


def test_a_same_named_annotation_from_elsewhere_does_not_resolve() -> None:
    assert not resolves_into_spring(
        "GetMapping", by_simple={"GetMapping": "com.acme.web.GetMapping"}, wildcard_prefixes=set()
    )
