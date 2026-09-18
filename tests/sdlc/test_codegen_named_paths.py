"""codegen reads the paths a ticket names through `sdlc.source_paths`, like the design does."""

from __future__ import annotations

from pathlib import Path

from orchestrator.sdlc.codegen import _claims_a_change, _paths_from


def test_a_change_claim_naming_a_non_python_file_counts() -> None:
    """An empty submission whose summary says it rewrote a `.cs` file is a claim, not a no-op.
    With the Python-only regex it was read as "nothing to change" and the loop stopped."""
    assert _claims_a_change("Rewrote EBSOrderApiClient.cs to send the bearer token")
    assert _claims_a_change("Updated src/orchestrator/cli.py")
    assert not _claims_a_change("No changes needed in cli.py")  # names a path, claims nothing
    assert not _claims_a_change("Rewrote the docstring")  # claims, names no file
    assert not _claims_a_change("Fixed per Fig. 2.c")  # a section reference is not a file


def test_paths_come_from_every_field_the_identifiers_survive_in(tmp_path: Path) -> None:
    (tmp_path / "FunctionsApp" / "Shared").mkdir(parents=True)
    (tmp_path / "FunctionsApp" / "Shared" / "EBSOrderApiClient.cs").write_text("//\n", encoding="utf-8")
    spec = {
        "summary": "generic prose naming nothing",
        "description": "Basic Auth lives in `EBSOrderApiClient.cs`.",
        "acceptance_criteria": [],
    }
    assert _paths_from(spec, "", tmp_path) == ["FunctionsApp/Shared/EBSOrderApiClient.cs"]
    assert _paths_from(spec, "") == ["EBSOrderApiClient.cs"]  # no root: as written


def test_the_design_s_paths_follow_the_spec_s(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    for name in ("a.py", "b.py"):
        (tmp_path / "src" / name).write_text("x = 1\n", encoding="utf-8")
    spec = {"summary": "fix src/a.py", "acceptance_criteria": []}
    assert _paths_from(spec, "## Files to touch\n- src/b.py\n- src/ghost.py\n", tmp_path) == [
        "src/a.py",
        "src/b.py",
    ]
