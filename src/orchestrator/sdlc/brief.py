"""One section vocabulary for every brief Spine writes.

Five modules render markdown briefs, and each grew its own private vocabulary. The same
section is spelled ``## Where it lands`` in ``evidence.py`` and ``## Where it lands in the
code`` in ``investigate.py``; the closing section is spelled **three** ways across five
modules. Two of them — ``builddoc.py`` and ``evidence.py`` — re-emit the other modules'
labels by hand, so the coupling already exists and is simply unmanaged: a rename in one
desynchronises a document nobody edited.

**The design is not new.** ``builddoc.py`` already fixes its twelve sections by title and by
order (``docs/specs/build-document.md`` §3) and fingerprints them so drift fails the build.
This module points the same mechanism at the briefs that never got it.

**The tiers are the load-bearing part, not the strings.** ``investigate`` and ``rca`` are
deterministic and no-LLM — invariant 2, and the property that makes them quotable.
``design`` renders a model's dictionary. A shared template that let a verdict, a
recommendation or an options table drift into the deterministic pair would quietly destroy
that, so the vocabulary is split by **provenance** rather than by tidiness:

``EVIDENCE``
    Facts re-derivable from the graph. Anything here can be recomputed at a commit by
    anyone, and two runs at the same commit produce the same words.

``JUDGEMENT``
    Claims that require an author — human or model. A verdict is an *argument*; the most
    useful line in a brief is still one a deterministic surface must not invent.

A surface declares the highest tier it may render, and :meth:`Brief.add` refuses anything
above it. That refusal is the whole point: a comment saying "don't put a verdict here" is
a comment, and the first person in a hurry will ignore it.

**A section with nothing to say still renders.** ``render_investigation_md`` already behaves
this way — "honest when a section has nothing grounded" — and making it structural is most
of the value. A brief with no slot for its own limits produces false confidence, which is
why :data:`NOT_VERIFIED` exists and why it is not optional.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Tier(Enum):
    """Where a section's content comes from. Ordered: EVIDENCE is the narrower permission."""

    EVIDENCE = 1
    JUDGEMENT = 2


class BriefError(ValueError):
    """A surface tried to render a section it is not permitted to. Always names both."""


@dataclass(frozen=True)
class Section:
    """One section of a brief: its title, its tier, and what it says when it is empty.

    ``empty`` is product copy, not filler. "No committed ``episteme/`` found — run
    ``orchestrator understand .`` to build one" tells a reader what to do; a bare heading
    tells them the tool is broken, and no heading at all tells them there was nothing to
    find. Those are three different messages and only one of them is true.
    """

    key: str
    title: str
    tier: Tier
    empty: str

    @property
    def heading(self) -> str:
        return f"## {self.title}"


# ---- the Evidence tier: deterministic, graph-derived ------------------------

PROBLEM = Section(
    "problem",
    "Problem",
    Tier.EVIDENCE,
    "_No problem statement was supplied._",
)
LANDS = Section(
    "lands",
    "Where it lands in the code",
    Tier.EVIDENCE,
    "_No symbols matched the ticket's terms — it may name new behavior, or use words the code doesn't._",
)
KNOWLEDGE = Section(
    "knowledge",
    "Relevant project knowledge",
    Tier.EVIDENCE,
    "_No committed `episteme/` found — run `orchestrator understand .` to build one._",
)
PRIOR_ART = Section(
    "prior_art",
    "Prior art / related work",
    Tier.EVIDENCE,
    "_None surfaced (cross-run memory needs the registry DB; the CLI runs without it)._",
)
NOT_VERIFIED = Section(
    "not_verified",
    "Not verified",
    Tier.EVIDENCE,
    "_Nothing was deliberately left unchecked in this brief._",
)
NEXT_STEP = Section(
    "next_step",
    "Next step",
    Tier.EVIDENCE,
    "_No next step suggested._",
)

# ---- the Judgement tier: requires an author ---------------------------------

VERDICT = Section(
    "verdict",
    "Verdict",
    Tier.JUDGEMENT,
    "_No verdict — the evidence above has not been argued to a conclusion._",
)
OPTIONS = Section(
    "options",
    "Options and recommendation",
    Tier.JUDGEMENT,
    "_No alternatives were weighed._",
)
OPEN_QUESTIONS = Section(
    "open_questions",
    "Open questions",
    Tier.JUDGEMENT,
    "_None raised._",
)
DECOMPOSITION = Section(
    "decomposition",
    "Decomposition",
    Tier.JUDGEMENT,
    "_Not broken down._",
)

#: **Order is fixed here, not at the call site.** Two briefs of the same kind are worth
#: diffing against each other, and a section that moves because one author assembled it in a
#: different order makes that diff unreadable. `builddoc.py`'s twelve work the same way.
ORDER: tuple[Section, ...] = (
    PROBLEM,
    VERDICT,
    LANDS,
    KNOWLEDGE,
    PRIOR_ART,
    OPTIONS,
    OPEN_QUESTIONS,
    DECOMPOSITION,
    NOT_VERIFIED,
    NEXT_STEP,
)

#: Sections a brief of this tier must carry even when empty. `NOT_VERIFIED` is mandatory
#: because a document with no place to record its own limits reads as having none.
REQUIRED: tuple[Section, ...] = (NOT_VERIFIED,)


def sections_for(tier: Tier) -> tuple[Section, ...]:
    """Every section a surface of ``tier`` may render, in document order."""
    return tuple(s for s in ORDER if s.tier.value <= tier.value)


@dataclass
class Brief:
    """A brief under assembly, bounded by the tier of the surface writing it.

    ``tier`` is the *highest* provenance the surface is allowed to claim. `investigate` and
    `rca` pass ``Tier.EVIDENCE`` and therefore cannot render a verdict at all; `design` and
    `sdlc plan` pass ``Tier.JUDGEMENT`` and may render both.
    """

    title: str
    tier: Tier = Tier.EVIDENCE
    _bodies: dict[str, str] = field(default_factory=dict)

    def add(self, section: Section, body: str = "") -> Brief:
        """Set one section's body. Returns self so calls chain.

        Refuses a section above this brief's tier, naming both — the boundary D4 exists for.
        An empty body is not an error: the section renders its own honest copy.
        """
        if section.tier.value > self.tier.value:
            raise BriefError(
                f"{section.title!r} is a {section.tier.name} section and this brief is "
                f"{self.tier.name}. A deterministic surface must not render an argument — "
                "put it in `design`, which has an author behind it."
            )
        self._bodies[section.key] = body.strip()
        return self

    def render(self) -> str:
        """The markdown, sections in :data:`ORDER`, empties spelled out where required.

        A section that was never added and is not required is omitted entirely — a brief
        does not owe every heading. A section that *was* added, or is required, always
        renders, carrying its ``empty`` copy when it has no body.
        """
        out = [f"# {self.title}\n"] if self.title else []
        for section in sections_for(self.tier):
            body = self._bodies.get(section.key)
            if body is None and section not in REQUIRED:
                continue
            out.append(section.heading)
            out.append(body or section.empty)
            out.append("")
        return "\n".join(out).rstrip() + "\n"


__all__ = [
    "DECOMPOSITION",
    "KNOWLEDGE",
    "LANDS",
    "NEXT_STEP",
    "NOT_VERIFIED",
    "OPEN_QUESTIONS",
    "OPTIONS",
    "ORDER",
    "PRIOR_ART",
    "PROBLEM",
    "REQUIRED",
    "VERDICT",
    "Brief",
    "BriefError",
    "Section",
    "Tier",
    "sections_for",
]
