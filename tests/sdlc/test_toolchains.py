"""Cross-surface registry contracts that the language-specific codegen tests cannot cover."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.catalog.catalog import CapabilityCatalog
from orchestrator.catalog.skills import NATIVE_SKILLS
from orchestrator.sdlc.feature_runner import _resolve_language
from orchestrator.sdlc.preflight import PhpPreflightRunner, SubprocessPreflightRunner, make_preflight_runner
from orchestrator.sdlc.toolchains import TOOLCHAINS


@pytest.mark.parametrize(
    ("languages", "expected"),
    [
        (set(), "python"),
        ({"sql"}, "python"),
        ({"perl"}, "perl"),
        ({"python", "java", "php"}, "python"),
        ({"java", "typescript"}, "java"),
        ({"typescript", "csharp"}, "typescript"),
        ({"csharp", "php"}, "csharp"),
        ({"php", "go"}, "php"),
        ({"go", "cpp"}, "go"),
        ({"cpp", "c"}, "cpp"),
        ({"c"}, "c"),
    ],
)
def test_auto_language_precedence_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, languages: set[str], expected: str
) -> None:
    monkeypatch.setattr(
        "orchestrator.catalog.profile.ProjectProfile.from_repo",
        lambda path: SimpleNamespace(languages=frozenset(languages)),
    )
    assert _resolve_language(tmp_path, "auto") == expected


def test_every_languages_conventions_capability_resolves_to_a_skill() -> None:
    """Replaces a check on `Toolchain.conventions_skill_id`, removed in P10 as dead code.

    The invariant it guarded is real and belongs on the path codegen actually uses: the
    planner selects by capability, so a `<language>-conventions` capability that resolves to
    nothing reaches a run as an id with no text behind it — guidance that silently is not
    there. Not every language has one (SQL ships without), so a missing capability is fine;
    a *registered* one that resolves to nothing is not.
    """
    catalog = CapabilityCatalog.from_sources()
    skills = {skill.id: skill for skill in NATIVE_SKILLS}
    checked: list[str] = []
    problems: list[str] = []
    for language in TOOLCHAINS:
        capability = catalog.get(f"{language}-conventions")
        if capability is None:
            continue  # not every language has one — SQL ships without, and that is fine
        checked.append(language)
        skill = skills.get(capability.id)
        if skill is None:
            problems.append(f"{capability.id}: selected by the planner, no skill behind the id")
        elif not skill.guidance.strip():
            problems.append(f"{capability.id}: a skill with no guidance is an empty prompt")
        if language not in (capability.selector.languages or frozenset()):
            problems.append(f"{capability.id}: selector does not name {language}, so it is never chosen")
    assert problems == []
    # Non-vacuity. The previous version of this test filtered the catalog by membership of
    # `_SEED` — and the catalog is *built* from `_SEED`, so `dangling` was unconditionally
    # empty and the body could have been deleted without failing anything.
    assert len(checked) >= len(TOOLCHAINS) - 1, f"only {checked} were examined"


def test_preflight_factory_preserves_interpreter_selection() -> None:
    python = make_preflight_runner("python", executable="/custom/python")
    php = make_preflight_runner("php", executable="/custom/php")
    assert isinstance(python, SubprocessPreflightRunner)
    assert python._python == "/custom/python"
    assert isinstance(php, PhpPreflightRunner)
    assert php._php == "/custom/php"
