"""The plan → approve → gate seam, driven through the real CLI in a real git checkout.

Every gate test in `test_builddoc.py` builds its repository in a bare `tmp_path` — no git — so
`derived_at` answers ``unknown`` on both sides of the comparison and the commit stamp the body
carries can never differ. That is how four defects in this seam shipped at once (ledger rows
B15–B18): nothing ever ran `sdlc plan`, then `sdlc approve`, then the gate, in a checkout that
looks like the one an adopter — or `.github/workflows/spine-sdlc.yml`'s build job — actually has.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app

_SPEC = {
    "intent_id": "PROJ-42",
    "title": "Cart.total raises KeyError for an unknown sku",
    "summary": "Cart.total in shop/cart.py crashes when a sku has no price.",
    "acceptance_criteria": ["Cart.total skips skus without a price instead of raising KeyError"],
}


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A committed repository with no `.gitignore` — what a fresh adopter checkout looks like."""
    root = tmp_path / "repo"
    (root / "shop").mkdir(parents=True)
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "cart.py").write_text(
        "class Cart:\n"
        "    def __init__(self):\n"
        "        self.items = []\n\n"
        "    def total(self, prices):\n"
        "        return sum(prices[s] * q for s, q in self.items)\n",
        encoding="utf-8",
    )
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "init")
    return root


def _spec_file(tmp_path: Path, **over: object) -> Path:
    spec = {**_SPEC, **over}
    path = tmp_path / f"{spec['intent_id']}.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def _gate(spec_file: Path, root: Path) -> str:
    """What `autorun`'s plan gate says — the function it calls, with the spec it would load."""
    from orchestrator.sdlc.builddoc import PlanNotApprovedError, require_approved_plan
    from orchestrator.sdlc.spec_file import load_spec_file

    try:
        approval = asyncio.run(require_approved_plan(load_spec_file(spec_file), root=root))
    except PlanNotApprovedError as exc:
        return f"REFUSED: {exc}"
    return f"PASSED: {approval.decided_by}"


def _plan_and_approve(runner: CliRunner, spec_file: Path, root: Path, *extra: str) -> None:
    planned = runner.invoke(
        app, ["sdlc", "plan", "--spec", str(spec_file), "--path", str(root), "--quiet", *extra]
    )
    assert planned.exit_code == 0, planned.output
    approved = runner.invoke(app, ["sdlc", "approve", "PROJ-42", "--path", str(root), "--by", "reviewer"])
    assert approved.exit_code == 0, approved.output


def test_a_plan_approved_in_a_fresh_checkout_is_the_plan_the_gate_accepts(
    checkout: Path, tmp_path: Path
) -> None:
    """The build job of `spine-sdlc.yml`, step for step: plan, approve, gate — no `.gitignore`."""
    spec_file = _spec_file(tmp_path)
    _plan_and_approve(CliRunner(), spec_file, checkout)
    assert _gate(spec_file, checkout) == "PASSED: reviewer"


def test_writing_a_plan_leaves_the_tree_trusted(checkout: Path, tmp_path: Path) -> None:
    from orchestrator.pkg.persistence import repo_state

    result = CliRunner().invoke(
        app, ["sdlc", "plan", "--spec", str(_spec_file(tmp_path)), "--path", str(checkout), "--quiet"]
    )
    assert result.exit_code == 0, result.output
    assert (checkout / ".spine" / "plans" / "PROJ-42-build.md").is_file()
    assert repo_state(checkout)[1] is False


@pytest.mark.xfail(strict=True, reason="B15: `--spec` with `--source` reads an unbound `plan_result`")
def test_a_spec_with_its_ticket_plans_from_the_spec_and_keeps_the_ticket_text(
    checkout: Path, tmp_path: Path
) -> None:
    """D1: the spec is the requirements; the source only supplies the text §8 checks them against."""
    ticket = tmp_path / "PROJ-42.md"
    criterion = "Cart.total skips skus without a price instead of raising KeyError"
    ticket.write_text(f"# PROJ-42\n\n## Acceptance criteria\n- {criterion}\n", encoding="utf-8")
    spec_file = _spec_file(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "sdlc",
            "plan",
            "--spec",
            str(spec_file),
            "--source",
            f"file://{ticket}",
            "--path",
            str(checkout),
            "--quiet",
        ],
    )
    assert result.exit_code == 0, result.output
    from orchestrator.sdlc.builddoc import load_source_text

    assert "skips skus without a price" in load_source_text("PROJ-42", root=checkout)
    document = (checkout / ".spine" / "plans" / "PROJ-42-build.md").read_text(encoding="utf-8")
    assert "Cart.total raises KeyError for an unknown sku" in document  # the spec's title, not the ticket's


@pytest.mark.xfail(
    strict=True, reason="B17: `--out` writes an approval the gate never reads, and says nothing"
)
@pytest.mark.parametrize("command", ["plan", "approve"])
def test_out_says_the_plan_it_writes_cannot_be_built(command: str, checkout: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    elsewhere = tmp_path / "elsewhere"
    spec_file = _spec_file(tmp_path)
    planned = runner.invoke(
        app,
        [
            "sdlc",
            "plan",
            "--spec",
            str(spec_file),
            "--path",
            str(checkout),
            "--out",
            str(elsewhere),
            "--quiet",
        ],
    )
    assert planned.exit_code == 0, planned.output
    approved = runner.invoke(
        app, ["sdlc", "approve", "PROJ-42", "--path", str(checkout), "--by", "r", "--out", str(elsewhere)]
    )
    assert approved.exit_code == 0, approved.output
    said = planned.output if command == "plan" else approved.output
    assert "--out is deprecated" in said and "cannot be built" in said


@pytest.mark.xfail(
    strict=True, reason="B18: the gate re-derives the plan without the issue type it was planned with"
)
def test_a_bug_that_lands_nowhere_keeps_its_approval(checkout: Path, tmp_path: Path) -> None:
    """Typed `Bug`, §12's validity row reads UNLOCALIZED; re-derived untyped it reads PROCEED.

    `.spine/` is excluded through `.git/info/exclude` so this measures B18 alone, not B16.
    """
    (checkout / ".git" / "info" / "exclude").write_text(".spine/\n", encoding="utf-8")
    spec_file = _spec_file(
        tmp_path,
        title="Zqxv flurble wibbles",
        summary="The flurble wibbles zqxv.",
        acceptance_criteria=["No flurble wibbles."],
    )
    _plan_and_approve(CliRunner(), spec_file, checkout, "--issue-type", "Bug")
    document = (checkout / ".spine" / "plans" / "PROJ-42-build.md").read_text(encoding="utf-8")
    assert "**Validity:** UNLOCALIZED" in document  # the fixture reaches the diverging verdict
    assert _gate(spec_file, checkout) == "PASSED: reviewer"
