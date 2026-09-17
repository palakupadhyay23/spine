"""Kotlin/JVM codegen: registration, layout, scaffold and the Gradle runner (P8, D13).

The Gradle runner is where this phase's real risk sits, and it is not the DSL — it is
that `./gradlew` and `gradle` are two different ways to run the same build, one of which
a repository carries with it. A probe that only looks at PATH rejects most real Kotlin
projects; one that only looks for a wrapper rejects a fresh scaffold. Both paths are
pinned here, along with the case where neither exists, which must fail loudly rather than
report a green suite that never compiled anything.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from orchestrator.catalog.catalog import _SEED
from orchestrator.catalog.models import CapabilityKind
from orchestrator.catalog.skills import NATIVE_SKILLS
from orchestrator.sdlc.feature_runner import SUPPORTED_LANGUAGES, unsupported_language_error
from orchestrator.sdlc.language_guidance import kotlin_guidance
from orchestrator.sdlc.layout import (
    _resolve_kotlin_layout,
    detect_jvm_test_library,
    detect_kotlin_layout,
)
from orchestrator.sdlc.preflight import GradlePreflightRunner
from orchestrator.sdlc.scaffold import _kotlin_files
from orchestrator.sdlc.testenv import KotlinToolEnvironment, gradle_available
from orchestrator.sdlc.testrunner import GradleTestRunner
from orchestrator.sdlc.toolchains import TOOLCHAINS

# ---- registration -----------------------------------------------------------


def test_kotlin_is_a_supported_codegen_language() -> None:
    """Until P8 `--language kotlin` exited 2 rather than scaffolding Python (D13)."""
    assert "kotlin" in SUPPORTED_LANGUAGES
    assert unsupported_language_error("kotlin") is None


def test_a_catalog_skill_is_selectable_and_defined() -> None:
    """The **live** path: the planner selects by `CapabilitySelector`, not by toolchain.

    A capability the planner can select but that has no `Skill` behind it reaches codegen
    as an id with no text — the same silent nothing, one layer further along.
    """
    known = {skill.id for skill in NATIVE_SKILLS}
    undefined = [cap.id for cap in _SEED if cap.kind is CapabilityKind.SKILL and cap.id not in known]
    assert undefined == []
    kotlin_caps = [
        cap.id for cap in _SEED if "kotlin" in (getattr(cap.selector, "languages", None) or frozenset())
    ]
    assert kotlin_caps == ["kotlin-conventions"]


def test_codegen_does_not_require_a_conventions_skill() -> None:
    """SQL ships as a supported codegen language with no conventions skill at all.

    The skill is guidance the planner *may* add; what actually makes a Kotlin run correct
    is the layout guidance and the prompt set, which are unconditional. Pinning this stops
    a future change from quietly making the skill load-bearing.
    """
    languages_with_a_skill = {
        lang
        for cap in _SEED
        if cap.kind is CapabilityKind.SKILL
        for lang in (getattr(cap.selector, "languages", None) or frozenset())
    }
    assert "sql" in TOOLCHAINS and "sql" not in languages_with_a_skill


def test_kotlin_is_ordered_after_java_in_auto_detection() -> None:
    """A repo with both `.java` and `.kt` is a Java project that adopted Kotlin."""
    java = TOOLCHAINS["java"].auto_priority
    kotlin = TOOLCHAINS["kotlin"].auto_priority
    assert java is not None and kotlin is not None and kotlin > java


# ---- layout -----------------------------------------------------------------


def test_kotlin_sources_are_found_under_the_java_source_root(tmp_path: Path) -> None:
    """The validation app has 263 `.kt` files and none under `src/main/kotlin`.

    Android and every JVM project converted from Java keep Kotlin in the Java source
    root, so looking only in the obvious place finds nothing in the common case.
    """
    pkg = tmp_path / "src" / "main" / "java" / "com" / "demo"
    pkg.mkdir(parents=True)
    (pkg / "Cart.kt").write_text("package com.demo\n\nclass Cart\n", encoding="utf-8")
    assert detect_kotlin_layout(tmp_path) == (
        "com.demo",
        "src/main/java/com/demo",
        "src/test/java/com/demo",
    )


def test_a_kotlin_source_root_is_preferred_when_present(tmp_path: Path) -> None:
    pkg = tmp_path / "src" / "main" / "kotlin" / "com" / "demo"
    pkg.mkdir(parents=True)
    (pkg / "Cart.kt").write_text("package com.demo\n\nclass Cart\n", encoding="utf-8")
    package, source_dir, tests_dir = detect_kotlin_layout(tmp_path) or ("", "", "")
    assert (package, source_dir, tests_dir) == (
        "com.demo",
        "src/main/kotlin/com/demo",
        "src/test/kotlin/com/demo",
    )


def test_a_greenfield_layout_uses_the_kotlin_source_root(tmp_path: Path) -> None:
    layout = _resolve_kotlin_layout(tmp_path, mode="new", package_name=None, repo="demo-service")
    assert layout.source_dir == "src/main/kotlin/org/example/demoservice"
    assert layout.language == "kotlin"
    assert layout.build_tool == "gradle"


def test_an_existing_gradle_project_keeps_its_own_package(tmp_path: Path) -> None:
    (tmp_path / "build.gradle.kts").write_text('plugins { kotlin("jvm") }\n', encoding="utf-8")
    pkg = tmp_path / "src" / "main" / "kotlin" / "org" / "acme"
    pkg.mkdir(parents=True)
    (pkg / "Svc.kt").write_text("package org.acme\n\nclass Svc\n", encoding="utf-8")
    layout = _resolve_kotlin_layout(tmp_path, mode="auto", package_name=None, repo="anything")
    assert layout.package_name == "org.acme"
    assert layout.mode == "existing"


# ---- scaffold ---------------------------------------------------------------


def test_the_scaffold_is_a_buildable_gradle_project(tmp_path: Path) -> None:
    layout = _resolve_kotlin_layout(tmp_path, mode="new", package_name=None, repo="demo-service")
    files = _kotlin_files(layout)
    assert "settings.gradle.kts" in files and "build.gradle.kts" in files
    build = files["build.gradle.kts"]
    assert 'kotlin("jvm") version' in build
    assert 'testImplementation(kotlin("test"))' in build
    assert "useJUnitPlatform()" in build


def test_the_scaffold_pins_its_kotlin_version(tmp_path: Path) -> None:
    """A floating version builds differently on two days and is not reproducible."""
    layout = _resolve_kotlin_layout(tmp_path, mode="new", package_name=None, repo="demo")
    build = _kotlin_files(layout)["build.gradle.kts"]
    assert 'version "+"' not in build and "latest" not in build.lower()


def test_the_scaffold_writes_no_wrapper_script(tmp_path: Path) -> None:
    """`gradlew` without `gradle-wrapper.jar` fails like a broken project, not a missing
    tool. The runner reports the missing tool instead."""
    layout = _resolve_kotlin_layout(tmp_path, mode="new", package_name=None, repo="demo")
    assert "gradlew" not in _kotlin_files(layout)


# ---- the runner -------------------------------------------------------------


def test_the_wrapper_is_preferred_over_gradle_on_path(tmp_path: Path) -> None:
    """`./gradlew` pins the version the project was written against; a PATH `gradle` is
    whatever happens to be installed."""
    (tmp_path / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    invocation = GradleTestRunner()._invocation(tmp_path)
    assert invocation is not None and invocation[0].endswith("gradlew")


def test_no_wrapper_and_no_gradle_fails_with_a_hint(tmp_path: Path) -> None:
    """Never a silent pass: a green suite that never compiled is the worst outcome."""
    result = asyncio.run(GradleTestRunner(gradle="definitely-not-gradle").run(path=str(tmp_path)))
    assert result.passed is False
    assert "gradle" in result.output.lower() and "wrapper" in result.output.lower()


def test_gradle_is_available_through_a_committed_wrapper(tmp_path: Path) -> None:
    assert gradle_available(tmp_path) is False
    (tmp_path / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    assert gradle_available(tmp_path) is True


def test_changed_files_select_their_owning_module(tmp_path: Path) -> None:
    """A root `./gradlew test` on a 27-module Android build spends minutes on code the
    change never touched (the Go 4.5 lesson)."""
    module = tmp_path / "core" / "data"
    (module / "src").mkdir(parents=True)
    (module / "build.gradle.kts").write_text("", encoding="utf-8")
    from orchestrator.sdlc.testrunner import _nearest_gradle_module

    found = _nearest_gradle_module(module / "src" / "Repo.kt", tmp_path)
    assert found is not None and found.relative_to(tmp_path).as_posix() == "core/data"


# ---- environment + guidance -------------------------------------------------


def test_the_tool_environment_has_no_python_interpreter() -> None:
    env = KotlinToolEnvironment()
    assert "Gradle" in env.describe()
    try:
        _ = env.python
    except RuntimeError:
        return
    raise AssertionError("KotlinToolEnvironment.python should raise")


def test_guidance_names_kotlin_test_rather_than_junit(tmp_path: Path) -> None:
    """The scaffold depends on `kotlin("test")`; a JUnit import does not compile."""
    layout = _resolve_kotlin_layout(tmp_path, mode="new", package_name=None, repo="demo")
    text = kotlin_guidance(layout)
    assert "kotlin.test" in text
    assert "build.gradle.kts" in text


# ---- the repo's existing test library ---------------------------------------


def test_junit_only_project_is_detected(tmp_path: Path) -> None:
    """The Spring validation repository declares `junit-jupiter-api` and no `kotlin("test")`.

    Generated code that imports `kotlin.test` there does not compile — found by running
    the brownfield proof, not by reading the docs.
    """
    (tmp_path / "build.gradle.kts").write_text(
        'dependencies { testImplementation("org.junit.jupiter:junit-jupiter-api") }\n',
        encoding="utf-8",
    )
    assert detect_jvm_test_library(tmp_path) == "junit5"


def test_kotlin_test_wins_when_both_are_declared(tmp_path: Path) -> None:
    """`kotlin("test")` delegates to JUnit under the hood, so a project with both has
    `kotlin.test` on the classpath and it is the idiomatic choice."""
    (tmp_path / "build.gradle.kts").write_text(
        "dependencies {\n"
        '    testImplementation(kotlin("test"))\n'
        '    testImplementation("org.junit.jupiter:junit-jupiter-api")\n'
        "}\n",
        encoding="utf-8",
    )
    assert detect_jvm_test_library(tmp_path) == "kotlin.test"


def test_a_project_declaring_neither_gets_the_default(tmp_path: Path) -> None:
    (tmp_path / "build.gradle.kts").write_text('plugins { kotlin("jvm") }\n', encoding="utf-8")
    assert detect_jvm_test_library(tmp_path) == ""


def test_guidance_follows_the_repo_rather_than_the_language_default(tmp_path: Path) -> None:
    (tmp_path / "build.gradle.kts").write_text(
        'dependencies { testImplementation("org.junit.jupiter:junit-jupiter-api") }\n',
        encoding="utf-8",
    )
    pkg = tmp_path / "src" / "main" / "kotlin" / "org" / "acme"
    pkg.mkdir(parents=True)
    (pkg / "Svc.kt").write_text("package org.acme\n\nclass Svc\n", encoding="utf-8")
    layout = _resolve_kotlin_layout(tmp_path, mode="auto", package_name=None, repo="x")
    text = kotlin_guidance(layout)
    assert "org.junit.jupiter.api.Test" in text
    assert "import kotlin.test.Test" not in text


# ---- preflight --------------------------------------------------------------


def test_preflight_is_skipped_when_no_linter_is_configured(tmp_path: Path) -> None:
    """Without ktlint or detekt, `gradle check` is just `test` again — running it would
    double the build to re-report what the test stage already said."""
    (tmp_path / "build.gradle.kts").write_text('plugins { kotlin("jvm") }\n', encoding="utf-8")
    result = asyncio.run(GradlePreflightRunner().run(path=str(tmp_path)))
    assert result.passed is True
    assert "skipped" in result.output


def test_a_version_catalog_alias_still_names_its_linter(tmp_path: Path) -> None:
    """`alias(libs.plugins.detekt)` is how a modern build applies it — a plugin-id match
    alone would miss it, and the KMP validation repository applies ktlint exactly this way."""
    (tmp_path / "build.gradle.kts").write_text("plugins { alias(libs.plugins.detekt) }\n", encoding="utf-8")
    assert GradlePreflightRunner()._configured_linter(tmp_path) == "detekt"


def test_a_configured_linter_without_gradle_skips_rather_than_fails(tmp_path: Path) -> None:
    """A missing tool is not a lint finding; reporting it as one would fail the run for
    something the author did not write."""
    (tmp_path / "build.gradle.kts").write_text(
        'plugins { id("org.jlleitschuh.gradle.ktlint") }\n', encoding="utf-8"
    )
    runner = GradlePreflightRunner(gradle="definitely-not-gradle")
    result = asyncio.run(runner.run(path=str(tmp_path)))
    assert result.passed is True and "skipped" in result.output


def test_a_linter_in_a_subproject_script_is_found(tmp_path: Path) -> None:
    """A multi-project build configures linting per module, not only at the root."""
    module = tmp_path / "core" / "data"
    module.mkdir(parents=True)
    (tmp_path / "build.gradle.kts").write_text("", encoding="utf-8")
    (module / "build.gradle.kts").write_text(
        'plugins { id("io.gitlab.arturbosch.detekt") }\n', encoding="utf-8"
    )
    assert GradlePreflightRunner()._configured_linter(tmp_path) == "detekt"
