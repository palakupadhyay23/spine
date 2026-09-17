"""The shared section vocabulary: one spelling, fixed order, and a tier that is enforced."""

from __future__ import annotations

import pytest

from orchestrator.sdlc.brief import (
    LANDS,
    NOT_VERIFIED,
    ORDER,
    PROBLEM,
    VERDICT,
    Brief,
    BriefError,
    Tier,
    sections_for,
)


def test_a_deterministic_surface_cannot_render_a_verdict() -> None:
    """D4, made structural.

    A verdict is an argument, and `investigate` must stay re-derivable from the graph alone.
    A comment saying so is a comment; the first author in a hurry ignores it.
    """
    brief = Brief("Investigation", tier=Tier.EVIDENCE)
    with pytest.raises(BriefError) as exc:
        brief.add(VERDICT, "Feasible.")
    # The message must name both sides, or the next author does not know which to change.
    assert "JUDGEMENT" in str(exc.value) and "EVIDENCE" in str(exc.value)


def test_a_judgement_surface_may_render_both_tiers() -> None:
    brief = Brief("Design", tier=Tier.JUDGEMENT)
    brief.add(PROBLEM, "It is slow.").add(VERDICT, "Cache it.")
    md = brief.render()
    assert "## Problem" in md and "## Verdict" in md


def test_order_is_the_vocabulary_not_the_call_site() -> None:
    """Two briefs of one kind are worth diffing; a section that moves ruins that."""
    forwards = Brief("B", tier=Tier.JUDGEMENT).add(PROBLEM, "p").add(VERDICT, "v").render()
    backwards = Brief("B", tier=Tier.JUDGEMENT).add(VERDICT, "v").add(PROBLEM, "p").render()
    assert forwards == backwards
    assert forwards.index("## Problem") < forwards.index("## Verdict")


def test_a_required_section_renders_even_when_nobody_added_it() -> None:
    """A brief with no place to record its limits reads as a brief that has none."""
    md = Brief("Investigation").render()
    assert "## Not verified" in md
    assert "Nothing was deliberately left unchecked" in md


def test_an_empty_section_says_something_true_rather_than_nothing() -> None:
    """Three different messages — "nothing found", "not run", "broken" — only one is true."""
    md = Brief("Investigation").add(LANDS, "").render()
    assert "## Where it lands in the code" in md
    assert "may name new behavior" in md


def test_an_unused_optional_section_is_omitted_entirely() -> None:
    """A brief does not owe every heading; an empty one it never tried to fill is noise."""
    assert "## Prior art" not in Brief("Investigation").add(PROBLEM, "p").render()


def test_every_section_title_is_unique() -> None:
    """The defect this module exists for: two spellings of one section, in two modules."""
    titles = [s.title for s in ORDER]
    assert len(titles) == len(set(titles))


def test_evidence_tier_sees_only_evidence_sections() -> None:
    assert all(s.tier is Tier.EVIDENCE for s in sections_for(Tier.EVIDENCE))
    assert NOT_VERIFIED in sections_for(Tier.EVIDENCE)
    assert VERDICT not in sections_for(Tier.EVIDENCE)
    assert VERDICT in sections_for(Tier.JUDGEMENT)
