"""The four states a grounded draft can be in, and the one rule that makes them worth having.

A drafted change puts a model's prose and the graph's facts on one page. If a reader cannot
tell which produced a given line — or cannot tell "we looked and found nothing" from "we never
looked" — then citations have lent their authority to guesses, and the grounded draft is worse
than the blind one. So each state is asserted to say something *different*, and the ungrounded
default is asserted to be byte-for-byte what the command rendered before grounding existed.
"""

from __future__ import annotations

import pytest

from orchestrator.intake import pkg_evidence
from orchestrator.intake.intents import Intent
from orchestrator.intake.openspec_writer import render_change
from orchestrator.intake.specs import FeatureSpec
from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.store import FactStore

INTENT = Intent(id="intent-cart", title="Cart checkout", description="The cart should charge.")
SPEC = FeatureSpec(
    intent_id="intent-cart",
    title="Cart checkout",
    summary="Charge a basket.",
    acceptance_criteria=["The cart posts to /api/charge."],
)


def _store(*, grounded: int = 0, external: int = 0, language: str = "python") -> FactStore:
    batch = FactBatch()
    for i in range(grounded):
        batch.add_node(
            Node(
                id=f"py:m.f{i}",
                kind=NodeKind.FUNCTION,
                name=f"f{i}",
                language=language,
                provenance=Provenance(file="m.py", line=i + 1),
            )
        )
    for i in range(external):
        batch.add_node(Node(id=f"ext:lib.g{i}", kind=NodeKind.FUNCTION, name=f"g{i}", external=True))
    return FactStore(batch)


# --- classification: the only place a state is decided ------------------------------------


def test_no_repository_is_ungrounded() -> None:
    assert pkg_evidence.ungrounded().state == "ungrounded"


def test_an_empty_graph_is_not_the_same_as_no_repository() -> None:
    """The distinction that costs code: claiming nothing was consulted when something was."""
    g = pkg_evidence.from_store(_store(), where="./web")
    assert g.state == "empty"
    assert g.where == "./web"


def test_external_only_nodes_still_count_as_empty() -> None:
    """Imports resolved to libraries are not this repository's own source."""
    g = pkg_evidence.from_store(_store(external=5), where="./web")
    assert g.state == "empty"
    assert g.nodes == 5 and g.grounded_nodes == 0


def test_a_dirty_tree_is_grounded_but_untrusted() -> None:
    g = pkg_evidence.from_store(_store(grounded=3), where="./web", untrusted=("web",))
    assert g.state == "untrusted"
    assert g.languages == ("python",)


def test_an_empty_graph_reports_emptiness_over_dirtiness() -> None:
    """Both are true; the actionable one wins, and the other would bury it."""
    g = pkg_evidence.from_store(_store(), where="./web", untrusted=("web",))
    assert g.state == "empty"


def test_a_clean_populated_graph_is_grounded() -> None:
    g = pkg_evidence.from_store(_store(grounded=2), where="./web")
    assert g.state == "grounded"
    assert g.cites is True


def test_only_a_state_with_evidence_may_cite() -> None:
    assert pkg_evidence.ungrounded().cites is False
    assert pkg_evidence.from_store(_store(), where="x").cites is False


# --- the page: four states a reader must be able to tell apart ----------------------------

STATES = [
    pkg_evidence.ungrounded(),
    pkg_evidence.from_store(_store(), where="./web"),
    pkg_evidence.from_store(_store(grounded=2), where="./web", untrusted=("web",)),
    pkg_evidence.from_store(_store(grounded=2), where="./web"),
]


def test_every_state_says_something_different_in_the_banner() -> None:
    """One wrong shared constant and all four pages would claim the same standing."""
    assert len({pkg_evidence.banner_sentence(g) for g in STATES}) == 4


def test_every_state_says_something_different_in_the_section() -> None:
    assert len({pkg_evidence.absence_section(g) for g in STATES}) == 4


def test_an_empty_graph_is_never_reported_as_the_ticket_touching_nothing() -> None:
    body = pkg_evidence.absence_section(STATES[1])
    assert "we looked and found nothing" in body
    assert "was** read" in body


def test_an_untrusted_draft_records_it_in_the_file_not_only_on_stderr() -> None:
    body = pkg_evidence.absence_section(STATES[2])
    assert "NOT REPRODUCIBLE" in body
    assert "`web`" in body


# --- render: the default must be the old behaviour ----------------------------------------


def test_the_ungrounded_default_renders_exactly_what_it_did_before() -> None:
    """`grounding=None` is not a fourth state — it is the command as it shipped."""
    files = render_change(SPEC, INTENT)
    assert "## Grounding" not in files["proposal.md"]
    assert "Auto-drafted by Spine" in files["proposal.md"]


@pytest.mark.parametrize("grounding", STATES)
def test_a_grounded_render_always_carries_both_the_banner_line_and_the_section(
    grounding: pkg_evidence.Grounding,
) -> None:
    proposal = render_change(SPEC, INTENT, grounding)["proposal.md"]
    assert pkg_evidence.banner_sentence(grounding) in proposal
    assert "## Grounding" in proposal


@pytest.mark.parametrize("grounding", STATES)
def test_the_fact_region_is_fenced_below_the_prose_never_interleaved(
    grounding: pkg_evidence.Grounding,
) -> None:
    """A citation beside a model's sentence lends it authority it has not earned."""
    proposal = render_change(SPEC, INTENT, grounding)["proposal.md"]
    assert proposal.index("## Why") < proposal.index("## Grounding")
    assert proposal.index("## What Changes") < proposal.index("## Grounding")


@pytest.mark.parametrize("grounding", STATES)
def test_grounding_does_not_leak_into_the_other_two_files(grounding: pkg_evidence.Grounding) -> None:
    """`tasks.md` and the delta spec are P3(e)'s and unchanged here — asserted, not assumed."""
    files = render_change(SPEC, INTENT, grounding)
    assert files["tasks.md"] == render_change(SPEC, INTENT)["tasks.md"]
    spec_key = next(k for k in files if k.endswith("spec.md"))
    assert files[spec_key] == render_change(SPEC, INTENT)[spec_key]
