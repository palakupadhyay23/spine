"""PKG: Kotlin Multiplatform — source sets and ``expect``/``actual`` (P7, D17).

The problem this solves is invisible without a test: three declarations of one
package-qualified name collapse to one node, and the graph looks *fine* afterwards.
Most of these tests are therefore about ids existing at all, and about the three
different reasons a contract edge is withheld.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.knowledge.areas import area_of_file, build_module_paths, multiplatform_modules
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind
from orchestrator.pkg.kotlin_kmp import source_set_of

pytest.importorskip("tree_sitter_kotlin", reason="install the 'kotlin' extra")

COMMON_KT = """\
package app

expect class Clock() {
    fun now(): Long
}

expect fun platformName(): String

fun describe(): String = platformName()
"""

ANDROID_KT = """\
package app

actual class Clock actual constructor() {
    actual fun now(): Long = 0L
}

actual fun platformName(): String = "android"

actual fun orphanPlatform(): String = "none"

fun androidOnly(): String = "helper"
"""

IOS_KT = """\
package app

actual class Clock actual constructor() {
    actual fun now(): Long = 1L
}

actual fun platformName(): String = "ios"
"""


def _kmp(tmp_path: Path) -> FactBatch:
    for source_set, src in (("commonMain", COMMON_KT), ("androidMain", ANDROID_KT), ("iosMain", IOS_KT)):
        d = tmp_path / "shared" / "src" / source_set / "kotlin" / "app"
        d.mkdir(parents=True)
        (d / "Platform.kt").write_text(src, encoding="utf-8")
    return RepoCodeExtractor().extract(tmp_path)


def _ids(batch: FactBatch) -> set[str]:
    return {n.id for n in batch.nodes}


def _implements(batch: FactBatch) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}


# ---- reading the source set off a path --------------------------------------


def test_source_set_comes_from_the_gradle_layout() -> None:
    assert source_set_of("shared/src/androidMain/kotlin/app/Platform.kt") == "androidMain"


def test_a_file_outside_any_src_directory_has_no_source_set() -> None:
    assert source_set_of("build.gradle.kts") == ""


# ---- ids --------------------------------------------------------------------


def test_the_expect_keeps_the_plain_id(tmp_path: Path) -> None:
    """Common code is written against the contract, so the contract gets the plain name."""
    assert "java:app.Clock" in _ids(_kmp(tmp_path))


def test_each_actual_carries_its_source_set(tmp_path: Path) -> None:
    ids = _ids(_kmp(tmp_path))
    assert {"java:app.Clock@androidMain", "java:app.Clock@iosMain"} <= ids


def test_three_declarations_of_one_name_are_three_nodes(tmp_path: Path) -> None:
    """Without the suffix `FactBatch` keeps one and the other two vanish silently."""
    batch = _kmp(tmp_path)
    clocks = [n for n in batch.nodes if n.kind is NodeKind.TYPE and n.name == "Clock"]
    assert len(clocks) == 3
    assert len({n.provenance.file for n in clocks if n.provenance}) == 3


def test_a_top_level_actual_function_is_suffixed_too(tmp_path: Path) -> None:
    assert "java:app.platformName@iosMain" in _ids(_kmp(tmp_path))


def test_a_member_inherits_the_suffix_through_its_parent(tmp_path: Path) -> None:
    """One suffix per id — `…Clock@iosMain.now`, never `…Clock@iosMain.now@iosMain`."""
    assert "java:app.Clock@iosMain.now" in _ids(_kmp(tmp_path))


def test_a_plain_declaration_in_a_platform_source_set_is_not_suffixed(tmp_path: Path) -> None:
    """The suffix keys off the `actual` keyword, not the directory: suffixing by
    directory would rename declarations that never collided."""
    ids = _ids(_kmp(tmp_path))
    assert "java:app.androidOnly" in ids
    assert "java:app.androidOnly@androidMain" not in ids


# ---- the contract edge ------------------------------------------------------


def test_every_actual_implements_its_expect(tmp_path: Path) -> None:
    edges = _implements(_kmp(tmp_path))
    assert {
        ("java:app.Clock@androidMain", "java:app.Clock"),
        ("java:app.Clock@iosMain", "java:app.Clock"),
        ("java:app.platformName@androidMain", "java:app.platformName"),
        ("java:app.platformName@iosMain", "java:app.platformName"),
    } <= edges


def test_an_actual_with_no_expect_in_the_tree_gets_no_edge(tmp_path: Path) -> None:
    """The contract may be real; this run has not seen it, so it is not asserted."""
    edges = _implements(_kmp(tmp_path))
    assert not any(src.startswith("java:app.orphanPlatform") for src, _ in edges)


def test_a_member_of_an_actual_class_gets_no_contract_edge(tmp_path: Path) -> None:
    """Only the declaration the source marked `actual` at the top level fulfils it —
    Kotlin does not require `actual` on every member, and a platform class may add
    members the `expect` never declared."""
    edges = _implements(_kmp(tmp_path))
    assert ("java:app.Clock@iosMain.now", "java:app.Clock.now") not in edges


def test_common_code_calls_the_contract_not_an_implementation(tmp_path: Path) -> None:
    calls = {(e.src, e.dst) for e in _kmp(tmp_path).edges if e.kind is EdgeKind.CALLS}
    assert ("java:app.describe", "java:app.platformName") in calls
    assert not any(dst.startswith("java:app.platformName@") for _, dst in calls)


# ---- areas: a KMP module's components are its source sets -------------------


def test_a_multiplatform_module_is_split_by_source_set() -> None:
    paths = ("shared",)
    kmp = multiplatform_modules({"shared/src/commonMain/kotlin/app/A.kt"}, paths)
    assert area_of_file("shared/src/iosMain/kotlin/app/A.kt", paths, kmp) == "shared/iosMain"


def test_an_ordinary_module_stays_whole() -> None:
    """`main`/`test`/`androidTest` are a build convention, not a component split — and
    splitting them would fragment every Android repository's architecture diagram."""
    paths = ("core/data",)
    kmp = multiplatform_modules({"core/data/src/main/java/app/A.kt"}, paths)
    assert kmp == frozenset()
    assert area_of_file("core/data/src/main/java/app/A.kt", paths, kmp) == "core/data"


def test_multiplatform_is_detected_from_a_common_main_directory() -> None:
    files = {"shared/src/commonMain/kotlin/app/A.kt", "app/src/main/java/app/B.kt"}
    assert multiplatform_modules(files, ("shared", "app")) == frozenset({"shared"})


def test_build_module_paths_is_what_area_splitting_keys_on(tmp_path: Path) -> None:
    """Source-set areas ride the Gradle module graph (D11); no Gradle, no split."""
    assert build_module_paths([]) == ()
