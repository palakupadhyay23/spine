"""Reverse-DNS area grouping (§9.4 of docs/specs/kotlin-support-roadmap.md).

An area is meant to be a component. Under a reverse-DNS namespace the first two segments
name the *organisation*, not a component, so grouping by them answers `com.google` for
every module in the application — measured on the Android validation app as **258 of 273
first-party types in a single area**. Dropping the prefix the modules all share puts them
back where they belong: `core.data`, `core.database`, `feature.topic`, 39 areas in all.

The rule has to stay a no-op for projects whose namespaces are already shallow. On this
repository the deepest prefix a majority shares is `orchestrator` at 48.1% — well under the
threshold — so nothing moves, which is correct: `orchestrator.pkg` already *is* the area.
"""

from __future__ import annotations

from orchestrator.knowledge.areas import AreaIndex, area_of_name, common_namespace_prefix
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.store import FactStore

_APP = "com.example.acme.mobile"
_MODULES = (
    f"{_APP}.core.data",
    f"{_APP}.core.database",
    f"{_APP}.core.model",
    f"{_APP}.feature.foryou",
    f"{_APP}.feature.topic",
)


def _module(name: str, *, external: bool = False) -> Node:
    return Node(
        f"java:{name}",
        NodeKind.MODULE,
        name,
        "kotlin",
        None if external else Provenance(f"{name.replace('.', '/')}/X.kt", 1),
        external=external,
    )


def _repo() -> FactStore:
    batch = FactBatch()
    for name in _MODULES:
        module = _module(name)
        batch.add_node(module)
        type_node = Node(
            f"{module.id}.Thing",
            NodeKind.TYPE,
            "Thing",
            "kotlin",
            Provenance(f"{name.replace('.', '/')}/Thing.kt", 1),
        )
        batch.add_node(type_node)
        batch.add_edge(Edge(module.id, type_node.id, EdgeKind.CONTAINS, type_node.provenance))
    return FactStore(batch)


def test_the_shared_vendor_prefix_is_found() -> None:
    assert common_namespace_prefix(_MODULES) == ("com", "example", "acme", "mobile")


def test_modules_group_by_component_not_by_vendor() -> None:
    """The snapshot §9.4 asks for: every module in one `com.example` blob before, real
    components after."""
    prefix = common_namespace_prefix(_MODULES)
    assert {area_of_name(m) for m in _MODULES} == {"com.example"}
    assert {area_of_name(m, prefix) for m in _MODULES} == {
        "core.data",
        "core.database",
        "core.model",
        "feature.foryou",
        "feature.topic",
    }


def test_the_area_index_resolves_the_prefix_from_the_store() -> None:
    store = _repo()
    index = AreaIndex(store)
    assert index.prefix == ("com", "example", "acme", "mobile")
    types = [n for n in store.nodes if n.kind is NodeKind.TYPE]
    assert {index.area_of(t) for t in types} == {
        "core.data",
        "core.database",
        "core.model",
        "feature.foryou",
        "feature.topic",
    }


def test_a_shallow_namespace_is_left_alone() -> None:
    """Python/Node shapes: `orchestrator.pkg` is already the component, and stripping
    `orchestrator` would make every *module* its own area."""
    names = [f"orchestrator.{p}.{m}" for p in ("pkg", "sdlc", "cli") for m in ("a", "b")]
    names += [f"tests.{p}.{m}" for p in ("pkg", "sdlc") for m in ("a", "b")]
    assert common_namespace_prefix(names) == ()
    assert area_of_name("orchestrator.pkg.facts", ()) == "orchestrator.pkg"


def test_one_module_in_a_third_party_namespace_does_not_destroy_the_prefix() -> None:
    """Found live: the validation app has exactly one first-party module declaring
    `package androidx.test.uiautomator`, and under a "shared by every module" rule that
    single file dragged the common prefix of all 71 down to nothing."""
    names = [*_MODULES, "androidx.test.uiautomator"]
    assert common_namespace_prefix(names) == ("com", "example", "acme", "mobile")


def test_a_module_outside_the_prefix_keeps_its_own_grouping() -> None:
    prefix = common_namespace_prefix([*_MODULES, "androidx.test.uiautomator"])
    assert area_of_name("androidx.test.uiautomator", prefix) == "androidx.test"


def test_a_module_that_is_exactly_the_prefix_still_gets_an_area() -> None:
    """It would otherwise strip to the empty string and group under `""`."""
    prefix = ("com", "example", "acme", "mobile")
    assert area_of_name(_APP, prefix) == "mobile"


def test_path_modules_are_untouched() -> None:
    """C/C++ translation units and Gradle module ids are paths, not namespaces."""
    prefix = common_namespace_prefix(_MODULES)
    assert area_of_name("src/smf/smf-sm.c", prefix) == "src/smf"
    assert area_of_name("core/data", prefix) == "core/data"


def test_a_single_module_project_has_no_prefix() -> None:
    """Nothing is "shared" by one name, and stripping it would leave no area at all."""
    assert common_namespace_prefix(["com.example.acme.mobile.core.data"]) == ()
