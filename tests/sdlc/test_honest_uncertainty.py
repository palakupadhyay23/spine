"""Spine says what its evidence is, and says nothing rather than guess (NSS-1231, P8).

Retrieval's score used to be discarded on the way out, so nothing downstream could tell a hit
on `oauth2` from a hit on `client`; five such hits became "Files to touch", the gate said
"nothing contradicts the code", and section 1 labelled a model's paraphrase as a quote. Now a
hit carries its evidence, a weak one is marked in the brief and dropped by the design, an
all-weak Story proceeds with a finding that says so, and section 1 says what its text is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.retrieval import GroundedRetriever
from orchestrator.sdlc.design import _fallback_design
from orchestrator.sdlc.investigate import build_investigation, render_investigation_md
from orchestrator.sdlc.validity import Verdict, assess

_PARAPHRASE = (
    "Implement OAuth2 client-credentials authentication for OIC integration to ensure secure, "
    "standards-based authentication. This will allow the system to authenticate using client credentials."
)


def _add(b: FactBatch, module: str, kind: NodeKind, name: str, file: str, line: int) -> None:
    b.add_node(Node(module, NodeKind.MODULE, module.split(":")[1], "csharp", Provenance(file, 1)))
    nid = f"{module}.{name}"
    b.add_node(Node(nid, kind, name, "csharp", Provenance(file, line)))
    b.add_edge(Edge(module, nid, EdgeKind.CONTAINS))


def _nss_1231_store() -> FactStore:
    """The shape of the field report: `client` across four files, `system` across two."""
    b = FactBatch()
    _add(
        b,
        "csharp:Utils",
        NodeKind.FIELD,
        "_client",
        "FunctionsApp/Shared/Utils/AzureServiceBusScheduler.cs",
        7,
    )
    _add(
        b,
        "csharp:Utils",
        NodeKind.TYPE,
        "EBSOrderApiClient",
        "FunctionsApp/Shared/Utils/EBSOrderApiClient.cs",
        11,
    )
    _add(
        b,
        "csharp:Utils",
        NodeKind.TYPE,
        "PsiHoldApiClient",
        "FunctionsApp/Shared/Utils/PsiHoldApiClient.cs",
        7,
    )
    _add(
        b,
        "csharp:Services",
        NodeKind.FIELD,
        "_blobServiceClient",
        "FunctionsApp/Services/AzureBlobFileService.cs",
        9,
    )
    _add(b, "csharp:ApiModels", NodeKind.FIELD, "SourceSystemId", "FunctionsApp/ApiModels/Mill.cs", 13)
    _add(b, "csharp:DbModels", NodeKind.FIELD, "SourceSystemId", "FunctionsApp/DbModels/MillsEntity.cs", 15)
    return FactStore(b)


def _one_named_thing_store() -> FactStore:
    b = FactBatch()
    b.add_node(
        Node(
            "py:src.exporter", NodeKind.MODULE, "src/exporter.py", "python", Provenance("src/exporter.py", 1)
        )
    )
    b.add_node(
        Node(
            "py:src.exporter.exporter_handler",
            NodeKind.FUNCTION,
            "exporter_handler",
            "python",
            Provenance("src/exporter.py", 5),
        )
    )
    b.add_edge(Edge("py:src.exporter", "py:src.exporter.exporter_handler", EdgeKind.CONTAINS))
    return FactStore(b)


# ---- retrieval carries its evidence ---------------------------------------------------------


def test_one_shared_word_across_files_is_weak_and_one_named_thing_is_not() -> None:
    hits = GroundedRetriever(_nss_1231_store()).scored_symbols("client credentials for OIC")
    assert hits and all(h.matched == ("client",) and h.weak for h in hits)

    hits = GroundedRetriever(_one_named_thing_store()).scored_symbols("fix the exporter")
    # The module and the function inside it both carry `exporter`: one file, one thing named.
    assert hits and all(h.matched == ("exporter",) and not h.weak for h in hits)


def test_one_file_specific_word_makes_a_hit_strong_and_shared_words_alone_do_not() -> None:
    hits = GroundedRetriever(_nss_1231_store()).scored_symbols("the EBSOrderApiClient class")
    top = hits[0]
    psi = next(h for h in hits if h.node.name == "PsiHoldApiClient")
    assert psi.matched == ("api", "client") and psi.weak  # two words, both generic
    assert (
        top.node.name == "EBSOrderApiClient" and top.matched == ("api", "client", "ebsorder") and not top.weak
    )


# ---- the brief says so ----------------------------------------------------------------------


def test_the_brief_marks_weak_hits_and_says_when_every_hit_is_weak() -> None:
    inv = build_investigation(
        "Implement OAuth2 client-credentials auth", _PARAPHRASE, store=_nss_1231_store()
    )
    md = render_investigation_md(inv)
    assert all(land.weak for land in inv.landing)
    assert "weak: only `client`, which other files use too" in md
    assert "Every match rests only on words other files use too" in md


# ---- the design proposes nothing rather than five wrong files -------------------------------


def test_nss_1231_the_paraphrase_alone_proposes_no_files_and_says_so() -> None:
    spec: dict[str, Any] = {"title": "Implement OAuth2 client-credentials auth", "summary": _PARAPHRASE}
    design = _fallback_design(spec, None, store=_nss_1231_store())
    assert design["files_to_touch"] == []
    assert design["grounded"] is False
    assert any("no files are proposed" in risk for risk in design["risks"])


def test_with_the_ticket_s_own_words_the_design_proposes_exactly_the_named_file() -> None:
    spec: dict[str, Any] = {
        "title": "Implement OAuth2 client-credentials auth",
        "summary": _PARAPHRASE,
        "description": "Replace Basic Auth in the EBSOrderApiClient class with OAuth2 client credentials.",
    }
    design = _fallback_design(spec, None, store=_nss_1231_store())
    assert design["files_to_touch"] == ["FunctionsApp/Shared/Utils/EBSOrderApiClient.cs"]


# ---- the gate proceeds, and says what it could not establish --------------------------------


def test_a_story_that_lands_nowhere_on_a_grounded_graph_proceeds_with_a_finding() -> None:
    spec = {"title": "Implement OAuth2 auth", "summary": _PARAPHRASE, "acceptance_criteria": []}
    assessment = assess(spec, store=_nss_1231_store(), landing=[], issue_type="Story")
    assert assessment.verdict is Verdict.PROCEED
    localization = [f for f in assessment.findings if f.check == "localization"]
    assert localization and "names no file" in localization[0].detail


def test_a_greenfield_story_and_a_located_story_get_no_such_finding() -> None:
    spec = {"title": "Add a brand new subsystem", "summary": "from scratch", "acceptance_criteria": []}
    greenfield = assess(spec, store=FactStore(FactBatch()), landing=[], issue_type="Story")
    located = assess(
        spec, store=_nss_1231_store(), landing=["FunctionsApp/ApiModels/Mill.cs"], issue_type="Story"
    )
    for assessment in (greenfield, located):
        assert assessment.verdict is Verdict.PROCEED
        assert not [f for f in assessment.findings if f.check == "localization"]


def test_a_bug_that_lands_nowhere_is_still_refused() -> None:
    spec = {"title": "Crash on submit", "summary": "it crashes", "acceptance_criteria": []}
    assert assess(spec, store=_nss_1231_store(), landing=[], issue_type="Bug").verdict is Verdict.UNLOCALIZED


# ---- section 1 says what its text is --------------------------------------------------------


async def test_section_one_labels_the_description_as_carried_and_the_summary_as_a_paraphrase(
    tmp_path: Path,
) -> None:
    from orchestrator.sdlc.builddoc import build_plan

    carried = await build_plan(
        {
            "title": "T",
            "summary": _PARAPHRASE,
            "description": "the ticket's own words",
            "acceptance_criteria": [],
        },
        root=tmp_path,
    )
    assert "derived · model — the intent's description" in carried
    assert "the ticket's own words" in carried
    assert "stated — the ticket body, quoted" not in carried

    paraphrased = await build_plan(
        {"title": "T", "summary": _PARAPHRASE, "acceptance_criteria": []}, root=tmp_path
    )
    assert "the spec writer's summary; the ticket's own words were not carried" in paraphrased
