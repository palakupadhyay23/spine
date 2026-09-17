"""PKG: Gradle `.kts` build scripts as a module dependency graph (P5, D11).

The Gradle reader exists because a `.kts` is **not** Kotlin source — its
"functions" are build configuration — so these tests pin both halves of that: what
it reads (modules and their dependencies, which nothing else in a repository
states) and what it refuses (artifact coordinates, computed targets, and the
Kotlin-source reading that would invent a component per module).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind

pytest.importorskip("tree_sitter_kotlin", reason="install the 'kotlin' extra")

SETTINGS = """\
rootProject.name = "shop"

include(":core:model")
include(":core:data")
include(":feature:topic")
"""

DATA_BUILD = """\
plugins {
    id("shop.android.library")
}

dependencies {
    implementation(project(":core:model"))
    implementation(libs.kotlinx.datetime)
    testImplementation(project(":core:model"))
}
"""

FEATURE_BUILD = """\
val shared = ":core:data"

dependencies {
    api(project(":core:data"))
    implementation(project(shared))
}
"""


def _build(tmp_path: Path) -> FactBatch:
    (tmp_path / "settings.gradle.kts").write_text(SETTINGS, encoding="utf-8")
    for rel, body in (
        ("core/data/build.gradle.kts", DATA_BUILD),
        ("feature/topic/build.gradle.kts", FEATURE_BUILD),
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return RepoCodeExtractor().extract(tmp_path)


def _modules(batch: FactBatch) -> set[str]:
    return {n.id for n in batch.nodes if n.kind is NodeKind.MODULE and n.id.startswith("gradle:")}


def _imports(batch: FactBatch) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPORTS and e.src.startswith("gradle:")}


def test_settings_declares_the_modules(tmp_path: Path) -> None:
    """`include(":core:data")` is the only statement of what modules exist."""
    modules = _modules(_build(tmp_path))
    assert {"gradle:core/model", "gradle:core/data", "gradle:feature/topic"} <= modules


def test_the_id_is_the_directory_not_a_namespace(tmp_path: Path) -> None:
    """A Gradle module is a directory; several can publish the same package."""
    assert "gradle:core/data" in _modules(_build(tmp_path))


def test_project_dependencies_become_module_imports(tmp_path: Path) -> None:
    imports = _imports(_build(tmp_path))
    assert ("gradle:core/data", "gradle:core/model") in imports
    assert ("gradle:feature/topic", "gradle:core/data") in imports


def test_a_version_catalog_coordinate_produces_nothing(tmp_path: Path) -> None:
    """`libs.kotlinx.datetime` is a third-party artifact, not a module."""
    batch = _build(tmp_path)
    assert not any("libs" in mid for mid in _modules(batch))
    assert not any("libs" in dst for _, dst in _imports(batch))


def test_a_computed_project_target_produces_nothing(tmp_path: Path) -> None:
    """`project(shared)` names a variable — the dependency is real, the target unknowable."""
    targets = {dst for src, dst in _imports(_build(tmp_path)) if src == "gradle:feature/topic"}
    assert targets == {"gradle:core/data"}  # from `api(...)` only, not from the variable


def test_build_scripts_are_not_read_as_kotlin_source(tmp_path: Path) -> None:
    """D11's whole argument: parsed as source, every DSL block becomes a phantom.

    A `.kts` must contribute no Kotlin module, type or function — only the Gradle
    module it configures.
    """
    batch = _build(tmp_path)
    assert [n for n in batch.nodes if n.language == "kotlin"] == []
    assert [n for n in batch.nodes if n.kind is NodeKind.FUNCTION] == []


def test_a_nested_settings_file_is_a_separate_build(tmp_path: Path) -> None:
    """`includeBuild("build-logic")` has its own settings, whose paths are its own.

    Reading it as this repository's produced a phantom top-level module beside the
    real nested one.
    """
    (tmp_path / "settings.gradle.kts").write_text(SETTINGS, encoding="utf-8")
    nested = tmp_path / "build-logic"
    nested.mkdir()
    (nested / "settings.gradle.kts").write_text('include(":convention")\n', encoding="utf-8")
    modules = _modules(RepoCodeExtractor().extract(tmp_path))
    assert "gradle:convention" not in modules


def test_a_repo_with_no_gradle_gains_no_gradle_modules(tmp_path: Path) -> None:
    (tmp_path / "a.kt").write_text("package p\n\nclass A\n", encoding="utf-8")
    assert _modules(RepoCodeExtractor().extract(tmp_path)) == set()


# ---- §11 finding 8: a dependency belongs to the module it configures ----


def test_a_dependency_inside_a_project_block_belongs_to_that_project(tmp_path: Path) -> None:
    """A root script may configure other modules from inside itself.

    Walking the whole tree and crediting every `project(":x")` to the script's own module
    asserted a root -> `core/ui` edge that no file declares *and* lost the real
    `app` -> `core/ui` one: a false edge and a missing edge from a single read.
    """
    (tmp_path / "settings.gradle.kts").write_text('include(":app", ":core:ui")\n', encoding="utf-8")
    (tmp_path / "build.gradle.kts").write_text(
        'project(":app") {\n    dependencies { implementation(project(":core:ui")) }\n}\n',
        encoding="utf-8",
    )
    batch = RepoCodeExtractor().extract(tmp_path)
    imports = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPORTS}
    assert ("gradle:app", "gradle:core/ui") in imports
    assert ("gradle:<root>", "gradle:core/ui") not in imports


def test_a_subprojects_block_credits_no_single_module(tmp_path: Path) -> None:
    """`subprojects { }` applies to a set this file does not enumerate, so the honest
    reading is no edge — not one edge hung off the root."""
    (tmp_path / "settings.gradle.kts").write_text('include(":app")\n', encoding="utf-8")
    (tmp_path / "build.gradle.kts").write_text(
        'subprojects {\n    dependencies { implementation(project(":core:ghost")) }\n}\n',
        encoding="utf-8",
    )
    batch = RepoCodeExtractor().extract(tmp_path)
    assert not [e for e in batch.edges if e.kind is EdgeKind.IMPORTS]
    assert "gradle:core/ghost" not in {n.id for n in batch.nodes}


def test_a_computed_module_path_is_not_a_module(tmp_path: Path) -> None:
    """`include(":core:$it")` is a loop body, not a module named `core/$it`."""
    (tmp_path / "settings.gradle.kts").write_text(
        'listOf("data", "model").forEach { include(":core:$it") }\n', encoding="utf-8"
    )
    batch = RepoCodeExtractor().extract(tmp_path)
    assert "gradle:core/$it" not in {n.id for n in batch.nodes}
