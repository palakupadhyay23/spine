#!/usr/bin/env python3
"""Fail the build when a brief renders a section the vocabulary does not own.

Five modules each grew a private section vocabulary and drifted to **two** spellings of
"where it lands" and **three** of "next step". `sdlc/brief.py` gave them one. Nothing stops
them drifting apart again — the five spellings all started as one — so this is the
countermeasure, and it is the same mechanism `builddoc.py` already applies to its twelve
sections: fixed titles, fixed order, checked.

**It checks the rendered output, not the source.** A grep for `"## "` proves only that no
literal was typed; it says nothing about what a reader actually receives, and the whole
defect was that two documents *rendered* different titles for one section. So each brief is
rendered from a fixture and its headings compared against the vocabulary.

**Order is checked, not just membership.** Two briefs of the same kind are worth diffing
against each other, which stops being true the moment a section can move.

    python scripts/brief-sections.py          # print each renderer's sections
    python scripts/brief-sections.py --check  # non-zero if any disagree

A new section is meant to fail this until it is added to `brief.ORDER`. That is the point:
the gate is what makes the vocabulary a contract rather than a suggestion.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from orchestrator.sdlc import brief  # noqa: E402
from orchestrator.sdlc.investigate import (  # noqa: E402
    Investigation,
    Landing,
    render_investigation_md,
)
from orchestrator.sdlc.rca import Hypothesis, RCAReport, render_rca_md  # noqa: E402

_HEADING = re.compile(r"^## (.+)$", re.MULTILINE)


def _investigation() -> str:
    """A brief with every section populated — an empty one exercises fewer of them."""
    inv = Investigation(
        title="fixture",
        problem="x",
        landing=[
            Landing(
                name="helper",
                kind="Function",
                callers=1,
                module="pkg.mod",
                where="src/a.py:1",
                repo="web",
                cross_repo=1,
                intents=["TCK-1"],
            )
        ],
        areas=["pkg.mod"],
        repos=["web", "api"],
        elided=2,
        knowledge="some knowledge",
        prior_notes=["a prior run"],
        grounded=True,
    )
    return render_investigation_md(inv)


def _rca() -> str:
    return render_rca_md(
        RCAReport(
            llm=True,
            fault_site="src/a.py:1",
            fault_module="pkg.mod",
            hypotheses=[Hypothesis(claim="c", confidence="high", evidence=["e"])],
            regression_surface=["s"],
            callers=["c"],
            fix_approach="do the thing",
        )
    )


#: Renderer name -> the markdown it produces for a fully-populated fixture. Add a row when a
#: surface adopts the vocabulary; a surface absent from here is unchecked, which is exactly
#: the state this gate exists to end.
RENDERERS: dict[str, str] = {
    "sdlc/investigate.py::render_investigation_md": _investigation(),
    "sdlc/rca.py::render_rca_md": _rca(),
}


def main() -> int:
    check = "--check" in sys.argv
    known = {s.title: i for i, s in enumerate(brief.ORDER)}
    problems: list[str] = []

    for name, markdown in RENDERERS.items():
        found = _HEADING.findall(markdown)
        if not check:
            print(f"\n{name}")
            for title in found:
                print(f"  {title}")

        unknown = [t for t in found if t not in known]
        for title in unknown:
            problems.append(
                f"[UNKNOWN] {name} renders '## {title}', which is not in `brief.ORDER`. "
                "Add it to the vocabulary, or spell it from the constant that already exists."
            )

        positions = [known[t] for t in found if t in known]
        if positions != sorted(positions):
            out_of_order = [t for t in found if t in known]
            problems.append(
                f"[ORDER] {name} renders its sections in a different order than "
                f"`brief.ORDER` declares: {out_of_order}. Two briefs of one kind must stay "
                "diffable against each other."
            )

    if problems:
        print()
        for problem in problems:
            print(problem)
        print(f"\nbrief-sections --check: FAILED — {len(problems)} problem(s).")
        return 1

    total = sum(len(_HEADING.findall(md)) for md in RENDERERS.values())
    print(
        f"\nbrief-sections --check: OK — {len(RENDERERS)} renderer(s), "
        f"{total} section(s), all named and ordered by `brief.ORDER`."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
