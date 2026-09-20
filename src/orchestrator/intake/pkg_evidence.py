"""What the code says about a drafted change — and, when it says nothing, why.

A drafted OpenSpec change mixes a model's prose with the graph's facts on one page, and a
reader cannot tell them apart by looking. If a ``file:line`` makes an unreliable requirement
*look* verified, grounding has made the draft worse than the blind one it replaces. So this
module's job is as much about **absence** as evidence: every way the graph can fail to answer
gets its own words, because a reader who cannot distinguish them will assume the flattering
one.

There are four states, and three of them are kinds of silence:

``ungrounded``
    No repository was given. The blind draft is legitimate — bootstrapping a greenfield repo
    has no graph — but a reader must not have to guess that is what happened.
``empty``
    A repository *was* read and the graph came back with nothing. This is not rare: Spine's
    front-ends do not cover every language, and a repo it cannot parse yields zero nodes while
    looking exactly like a repo with nothing to find. Rendered as "we looked and found
    nothing", never as "this ticket touches nothing".
``untrusted``
    The tree has uncommitted work, so every citation is real right now and unreproducible at a
    commit. The CLI already warns about this on stderr — which scrolls away, while a drafted
    change file is committed and read months later. So it is recorded *in the file*.
``grounded``
    The graph answered. Only here may a citation appear.

The distinction ``empty`` draws against ``ungrounded`` is the one worth the code: treating a
zero-node graph as "no repository given" would claim nothing was consulted when something was.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from orchestrator.pkg import FactStore

__all__ = ["Grounding", "GroundingState", "absence_section", "banner_sentence", "from_store", "ungrounded"]

GroundingState = Literal["ungrounded", "empty", "untrusted", "grounded"]


@dataclass(frozen=True)
class Grounding:
    """The standing of the code evidence behind one drafted change."""

    state: GroundingState
    #: The path or ``.spine/repos.yaml`` that was read. Empty only when ``ungrounded``.
    where: str = ""
    nodes: int = 0
    grounded_nodes: int = 0
    #: Languages the extractor actually emitted nodes for. Empty when the graph is empty —
    #: which is the finding, not a missing field.
    languages: tuple[str, ...] = ()
    #: Repository keys with uncommitted work, when the graph is merged.
    untrusted: tuple[str, ...] = ()

    @property
    def cites(self) -> bool:
        """May this draft carry a ``file:line`` at all?

        ``untrusted`` still cites: the evidence is true of the tree on disk, and suppressing it
        would lose a real finding to protect a reproducibility property the page now states
        for itself.
        """
        return self.state in ("grounded", "untrusted")


def ungrounded() -> Grounding:
    """No repository was given — the blind draft, said out loud."""
    return Grounding(state="ungrounded")


def from_store(store: FactStore, *, where: str, untrusted: tuple[str, ...] = ()) -> Grounding:
    """Classify what a store came back with. The only place a state is decided."""
    summary = store.summary()
    grounded_nodes = int(summary.get("grounded_nodes", 0))
    languages = tuple(sorted({n.language for n in store.nodes if n.language and n.grounded}))
    if grounded_nodes == 0:
        # Checked before trust: a graph with nothing in it has nothing to be untrusted about,
        # and reporting "uncommitted work" over "we read nothing" buries the actionable half.
        return Grounding(
            state="empty",
            where=where,
            nodes=int(summary.get("nodes", 0)),
            grounded_nodes=0,
            untrusted=untrusted,
        )
    return Grounding(
        state="untrusted" if untrusted else "grounded",
        where=where,
        nodes=int(summary.get("nodes", 0)),
        grounded_nodes=grounded_nodes,
        languages=languages,
        untrusted=untrusted,
    )


def banner_sentence(g: Grounding) -> str:
    """One line for the draft banner — what a reader who skims `proposal.md` must still see."""
    if g.state == "ungrounded":
        return "**Not grounded:** no repository was read, so nothing here is checked against code."
    if g.state == "empty":
        return f"**Grounded against `{g.where}`, which yielded no graph** — see *Grounding* below."
    if g.state == "untrusted":
        return f"**Grounded against `{g.where}`, uncommitted** — citations cannot be re-derived at a commit."
    return f"**Grounded against `{g.where}`** — cited lines are facts from the graph; the prose above is not."


def absence_section(g: Grounding) -> str:
    """The *Grounding* section body: what was read, and what that does and does not prove."""
    if g.state == "ungrounded":
        return (
            "No repository was read, so every requirement above is derived from the source "
            "document alone — unchecked against any code. That is a legitimate mode "
            "(a greenfield repo has no graph to check against), and it is stated here so a "
            "reader never has to infer it.\n\n"
            "Pass a repository path, or `--repos <.spine/repos.yaml>`, to ground the draft."
        )
    if g.state == "empty":
        walked = (
            f"{g.nodes} node(s) were read but none were grounded in this repository's own source"
            if g.nodes
            else "the graph came back empty"
        )
        return (
            f"`{g.where}` **was** read and {walked}. This says nothing about the ticket: a "
            "language Spine has no front-end for yields zero nodes and looks exactly like a "
            "repository with nothing to find.\n\n"
            "Read this as *“we looked and found nothing”*, never as *“this ticket "
            "touches nothing”*. Run `orchestrator pkg extract <path>` to see what the "
            "extractor emits for this repository."
        )
    langs = ", ".join(f"`{lang}`" for lang in g.languages) or "no language"
    read = f"`{g.where}` — {g.grounded_nodes} grounded node(s) across {langs}."
    if g.state == "untrusted":
        keys = ", ".join(f"`{k}`" for k in g.untrusted)
        return (
            f"{read}\n\n**NOT REPRODUCIBLE — {keys} has uncommitted work or is not a git "
            "repository.** The citations below are true of the tree that was on disk when this "
            "was drafted, and cannot be re-derived at a commit. Re-draft from a clean checkout "
            "before treating any line number as durable."
        )
    return (
        f"{read}\n\nCited lines are facts, re-derivable from the graph at this commit. "
        "The requirements and scenarios above are the model's prose and carry no citation — "
        "if a line has no `file:line`, nothing has checked it."
    )
