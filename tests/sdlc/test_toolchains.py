"""Cross-surface registry contracts that the language-specific codegen tests cannot cover."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.catalog.catalog import CapabilityCatalog
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


def test_convention_ids_name_existing_catalog_capabilities() -> None:
    catalog = CapabilityCatalog.from_sources()
    for row in TOOLCHAINS.values():
        if row.conventions_skill_id is not None:
            assert catalog.get(row.conventions_skill_id) is not None


def test_preflight_factory_preserves_interpreter_selection() -> None:
    python = make_preflight_runner("python", executable="/custom/python")
    php = make_preflight_runner("php", executable="/custom/php")
    assert isinstance(python, SubprocessPreflightRunner)
    assert python._python == "/custom/python"
    assert isinstance(php, PhpPreflightRunner)
    assert php._php == "/custom/php"
