"""Blast-radius + unverified-reference annotation for the design stage (C1 + C9)."""

from __future__ import annotations

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc.design import produce_design, render_design_md
from orchestrator.sdlc.impact import blast_radius, render_md, to_dict, unverified_references


def _node(nid: str, kind: NodeKind, name: str, file: str, line: int = 1) -> Node:
    return Node(id=nid, kind=kind, name=name, language="python", provenance=Provenance(file, line))


def _graph() -> FactBatch:
    """report.py defines render/to_row; web.py imports report and calls render."""
    b = FactBatch()
    report = _node("py:report", NodeKind.MODULE, "report.py", "report.py")
    web = _node("py:web", NodeKind.MODULE, "web.py", "web.py")
    render = _node("py:report.render", NodeKind.FUNCTION, "render", "report.py", 10)
    to_row = _node("py:report.to_row", NodeKind.FUNCTION, "to_row", "report.py", 20)
    handler = _node("py:web.handler", NodeKind.FUNCTION, "handler", "web.py", 5)
    for n in (report, web, render, to_row, handler):
        b.add_node(n)
    b.add_edge(Edge("py:report", "py:report.render", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:report", "py:report.to_row", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:web", "py:web.handler", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:web", "py:report", EdgeKind.IMPORTS))  # web imports report
    b.add_edge(Edge("py:web.handler", "py:report.render", EdgeKind.CALLS, Provenance("web.py", 6)))
    b.add_edge(Edge("py:report.to_row", "py:report.render", EdgeKind.CALLS, Provenance("report.py", 22)))
    return b


def test_a_route_handler_is_not_scored_as_safe_to_change() -> None:
    """A handler has no in-language callers, so filtering hotspots on ``callers_of``
    dropped every endpoint out of the blast radius and scored public API as touching
    nothing. It must appear, name its endpoint, and outrank an equal-fan-in internal."""
    b = _graph()
    b.add_node(
        Node(
            id="py:endpoint:GET /rows",
            kind=NodeKind.ENDPOINT,
            name="GET /rows",
            language="python",
            provenance=Provenance("web.py", 4),
        )
    )
    b.add_edge(Edge("py:endpoint:GET /rows", "py:web.handler", EdgeKind.EXPOSES, Provenance("web.py", 4)))

    br = blast_radius(FactStore(b), ["web.py"])
    hotspots = {h.name: h for m in br.modules for h in m.hotspots}

    assert "handler" in hotspots
    assert hotspots["handler"].callers == 0  # still honest: no call sites
    assert hotspots["handler"].exposed == 1
    assert "serves 1 endpoint(s)" in render_md(to_dict(br))


# --------------------------------------------------------------------------- #
# C1 — blast radius
# --------------------------------------------------------------------------- #
def test_blast_radius_resolves_module_importers_and_hotspots() -> None:
    store = FactStore(_graph())
    br = blast_radius(store, ["report.py"])

    assert br.grounded and br.call_graph_available
    assert len(br.modules) == 1
    m = br.modules[0]
    assert m.ref == "report.py" and m.module == "report.py"
    assert m.importers == 1 and "web.py" in m.importer_names
    # render is called by handler + to_row → the top hotspot
    names = [s.name for s in m.hotspots]
    assert "render" in names
    top = next(s for s in m.hotspots if s.name == "render")
    assert top.callers == 2


def test_unresolved_reference_is_flagged_when_grounded() -> None:
    store = FactStore(_graph())
    br = blast_radius(store, ["report.py", "ghost.py"])
    assert "ghost.py" in br.unresolved
    assert unverified_references(br) == ["ghost.py"]  # C9: absent path surfaced


def test_greenfield_suppresses_unverified_references() -> None:
    """An ungrounded graph makes every path 'absent' — don't flag them all."""
    store = FactStore(FactBatch())
    br = blast_radius(store, ["anything.py"])
    assert br.grounded is False
    assert unverified_references(br) == []
    assert render_md(to_dict(br)) == ""  # nothing to render on greenfield


def test_no_call_graph_reports_module_impact_only() -> None:
    """A graph with IMPORTS but no CALLS (e.g. TS/Java) → module-level only, stated."""
    b = FactBatch()
    b.add_node(_node("py:a", NodeKind.MODULE, "a.py", "a.py"))
    b.add_node(_node("py:b", NodeKind.MODULE, "b.py", "b.py"))
    b.add_edge(Edge("py:b", "py:a", EdgeKind.IMPORTS))
    br = blast_radius(FactStore(b), ["a.py"])
    assert br.call_graph_available is False
    assert br.modules[0].hotspots == ()
    md = render_md(to_dict(br))
    assert "Call graph unavailable" in md


# --------------------------------------------------------------------------- #
# render + design integration
# --------------------------------------------------------------------------- #
def test_render_md_includes_sections() -> None:
    store = FactStore(_graph())
    md = render_md(to_dict(blast_radius(store, ["report.py", "ghost.py"])))
    assert "## Blast radius" in md
    assert "imported by 1 module(s): web.py" in md
    assert "high fan-in: `render`" in md
    assert "## ⚠ Unverified references" in md and "`ghost.py`" in md


async def test_produce_design_annotates_with_blast_radius() -> None:
    store = FactStore(_graph())
    overview = {"modules": [{"module": "report.py", "nodes": 3}]}
    spec = {"title": "Tweak render", "summary": "x", "acceptance_criteria": ["works"]}
    # heuristic (no LLM) design draws files_to_touch from the overview modules
    design = await produce_design(spec, overview=overview, store=store)
    bd = design["blast_radius"]
    assert bd["grounded"] is True
    assert any(m["ref"] == "report.py" for m in bd["modules"])
    # and it renders through the design markdown
    assert "## Blast radius" in render_design_md(spec, design)


async def test_produce_design_without_store_is_unannotated() -> None:
    design = await produce_design(
        {"title": "t", "acceptance_criteria": ["a"]}, overview={"modules": []}, store=None
    )
    assert "blast_radius" not in design
    assert "## Blast radius" not in render_design_md({"title": "t"}, design)


# --------------------------------------------------------------------------- #
# NSS-1209 — a namespace-keyed front-end (C#/Java/PHP; Go by directory) emits one
# MODULE node per namespace, so only the first file walked used to resolve and every
# sibling came back "absent from the knowledge graph".
# --------------------------------------------------------------------------- #
def _csharp_namespace_graph() -> FactBatch:
    """One MODULE for the namespace (provenance = ColumnKey.cs, the first file walked);
    three Types across three files; one other module importing the namespace."""
    b = FactBatch()
    ns = Node(
        id="csharp:Shared.Enums",
        kind=NodeKind.MODULE,
        name="Shared.Enums",
        language="csharp",
        provenance=Provenance("WebApp/Shared/Enums/ColumnKey.cs", 1),
    )
    b.add_node(ns)
    for name, file in (
        ("ColumnKey", "ColumnKey.cs"),
        ("ProductGroupHelper", "ProductGroup.cs"),
        ("ProductHistoryEventType", "ProductHistoryEventType.cs"),
    ):
        tid = f"csharp:Shared.Enums.{name}"
        b.add_node(
            Node(
                id=tid,
                kind=NodeKind.TYPE,
                name=name,
                language="csharp",
                provenance=Provenance(f"WebApp/Shared/Enums/{file}", 3),
            )
        )
        b.add_edge(Edge(ns.id, tid, EdgeKind.CONTAINS))
    grid = Node(
        id="csharp:Features.Grid",
        kind=NodeKind.MODULE,
        name="Features.Grid",
        language="csharp",
        provenance=Provenance("WebApp/Features/Grid.cs", 1),
    )
    b.add_node(grid)
    b.add_edge(Edge(grid.id, ns.id, EdgeKind.IMPORTS))
    return b


def test_nss_1209_a_file_in_a_shared_namespace_is_not_flagged_absent() -> None:
    """Field report NSS-1209: `ProductGroup.cs` and `ProductHistoryEventType.cs` were listed as
    "absent from the knowledge graph" two sections after their own symbols had been printed
    with line numbers. Every file a grounded node lives in must resolve; only a true ghost
    is absent. A bare basename — how a ticket usually writes it — resolves too."""
    store = FactStore(_csharp_namespace_graph())
    files = [
        "WebApp/Shared/Enums/ProductGroup.cs",
        "WebApp/Shared/Enums/ProductHistoryEventType.cs",
        "WebApp/Shared/Enums/ColumnKey.cs",
        "WebApp/Shared/Enums/Ghost.cs",
    ]
    br = blast_radius(store, files)
    assert unverified_references(br) == ["WebApp/Shared/Enums/Ghost.cs"]
    assert [m.module for m in br.modules] == ["Shared.Enums"] * 3
    assert blast_radius(store, ["ProductGroup.cs"]).unresolved == ()


def test_a_module_spanning_files_says_so_and_its_importers_are_counted_once() -> None:
    """The importer count belongs to the namespace, not to whichever file resolved. Say so in
    the design, and do not sum it once per file in the build document's reading."""
    from orchestrator.sdlc.builddoc import _blast_prose

    store = FactStore(_csharp_namespace_graph())
    br = blast_radius(store, ["WebApp/Shared/Enums/ProductGroup.cs", "WebApp/Shared/Enums/ColumnKey.cs"])
    assert all(m.spans == 3 and m.importers == 1 for m in br.modules)
    md = render_md(to_dict(br))
    assert "module `Shared.Enums` spans 3 file(s); imported by 1 module(s): Features.Grid" in md
    assert "2 module(s) change; 1 module(s) import them" in _blast_prose(to_dict(br))
