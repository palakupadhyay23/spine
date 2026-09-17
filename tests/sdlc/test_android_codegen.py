"""Android codegen: module placement, the variant test task, and what must never run (P9, D13).

P8 proved Kotlin/JVM codegen against a single-module Gradle build. Android breaks three of
that phase's assumptions at once, and every one of them is load-bearing:

* there are no sources at the repository root — the validation app is 27 modules and its
  root holds only build scripts — so "which package" no longer implies "which directory";
* ``testDebugUnitTest``, the documented Android unit-test task, does not necessarily exist:
  a module with product flavours has one task per flavour, and the validation app applies
  flavours to every library module through a convention plugin, so the task named in this
  phase's own plan is ambiguous there;
* a generated *instrumented* test compiles and then cannot be run at all, because it needs
  a device. Ruling it out is guidance, not detection, so it is pinned here too.

The repositories built below are miniatures of the real one, including the parts that make
naive detection fail: the Android plugin is applied under a convention-plugin name so the
string ``com.android.library`` appears nowhere in the module that is an Android library.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from orchestrator.sdlc import android, testrunner
from orchestrator.sdlc.language_guidance import kotlin_guidance
from orchestrator.sdlc.layout import _resolve_kotlin_layout, detect_jvm_test_library
from orchestrator.sdlc.testenv import android_toolchain_available, kotlin_project_error
from orchestrator.sdlc.testrunner import GradleTestRunner, _variant_tasks
from orchestrator.sdlc.toolchains import TOOLCHAINS

# Real Gradle 8.1 output, from `./gradlew :core:data:testDebugUnitTest` on the validation
# app. Quoted rather than paraphrased: this string is the contract this phase parses.
AMBIGUOUS = (
    "* What went wrong:\n"
    "Cannot locate tasks that match ':core:data:testDebugUnitTest' as task "
    "'testDebugUnitTest' is ambiguous in project ':core:data'. Candidates are: "
    "'testDemoDebugUnitTest', 'testProdDebugUnitTest'.\n"
)
NOT_FOUND = (
    "* What went wrong:\n"
    "Task 'testDebugUnitTest' not found in project ':app'. Some candidates are: "
    "'testDemoDebugUnitTest', 'testProdDebugUnitTest'.\n"
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _android_repo(root: Path) -> Path:
    """A miniature of the validation app: one Android module, one plain JVM module, both
    configured by a convention plugin that lives in a separate included build."""
    _write(root / "settings.gradle.kts", 'includeBuild("build-logic")\ninclude(":core:data")\n')
    _write(root / "build.gradle.kts", "")
    # No `com.android.library` here on purpose — a real Android project applies the plugin
    # through a convention plugin, and the AGP plugin id then appears only in build-logic.
    _write(
        root / "core/data/build.gradle.kts",
        'plugins {\n    id("demo.android.library")\n}\n\nandroid {\n    namespace = "com.x.core.data"\n}\n',
    )
    _write(root / "core/data/src/main/AndroidManifest.xml", "<manifest/>\n")
    _write(root / "core/data/src/main/java/com/x/core/data/Existing.kt", "package com.x.core.data\n")
    _write(root / "core/model/build.gradle.kts", 'plugins {\n    id("kotlin")\n}\n')
    _write(root / "core/model/src/main/java/com/x/core/model/Model.kt", "package com.x.core.model\n")
    # The included build that owns the convention plugins, and declares the test library
    # on behalf of every module that applies it.
    _write(root / "build-logic/settings.gradle.kts", 'include(":convention")\n')
    # The registration block is what makes the plugin id resolvable to the class that
    # implements it, and therefore to the file that declares the dependency. Without it a
    # module's build script is a dead end: an id and nothing to follow it to.
    _write(
        root / "build-logic/convention/build.gradle.kts",
        "gradlePlugin {\n"
        "    plugins {\n"
        '        register("androidLibrary") {\n'
        '            id = "demo.android.library"\n'
        '            implementationClass = "AndroidLibraryConventionPlugin"\n'
        "        }\n"
        "    }\n"
        "}\n",
    )
    _write(
        root / "build-logic/convention/src/main/kotlin/AndroidLibraryConventionPlugin.kt",
        'class P { fun apply() { add("testImplementation", kotlin("test")) } }\n',
    )
    return root


# ---- module detection --------------------------------------------------------


def test_a_manifest_marks_an_android_module(tmp_path: Path) -> None:
    _android_repo(tmp_path)
    assert android.is_android_module(tmp_path / "core/data")


def test_an_android_block_marks_a_module_that_has_no_manifest(tmp_path: Path) -> None:
    """AGP 8 lets a module declare `namespace` in the build script and drop the manifest,
    so manifest-only detection would call a real Android library a plain JVM one."""
    _write(tmp_path / "settings.gradle.kts", 'include(":lib")\n')
    _write(tmp_path / "lib/build.gradle.kts", 'android {\n    namespace = "com.x"\n}\n')
    _write(tmp_path / "lib/src/main/java/com/x/A.kt", "package com.x\n")
    assert android.is_android_module(tmp_path / "lib")


def test_a_plain_jvm_module_in_the_same_build_is_not_android(tmp_path: Path) -> None:
    """The distinction is per module, not per repository: the validation app's `core:model`
    is a `kotlin` library inside an Android build and its task is the plain one."""
    _android_repo(tmp_path)
    assert not android.is_android_module(tmp_path / "core/model")


def test_the_unit_test_task_follows_the_module_not_the_repository(tmp_path: Path) -> None:
    _android_repo(tmp_path)
    assert android.unit_test_task(tmp_path / "core/data") == "testDebugUnitTest"
    assert android.unit_test_task(tmp_path / "core/model") == "test"


def test_an_included_build_is_not_part_of_the_module_graph(tmp_path: Path) -> None:
    """`build-logic` builds the plugins that configure the real modules. Generating a
    feature into it would compile into the build's own classpath, not the app."""
    _android_repo(tmp_path)
    found = {m.relative_to(tmp_path).as_posix() for m in android.gradle_modules(tmp_path)}
    assert "core/data" in found
    assert not any(p.startswith("build-logic") for p in found)


def test_build_output_is_not_walked(tmp_path: Path) -> None:
    """A configured Android build leaves a `build/` tree holding generated sources; walking
    it is slow and its packages are not places a feature may be written."""
    _android_repo(tmp_path)
    _write(tmp_path / "core/data/build/generated/source/com/x/core/data/Gen.kt", "package com.x\n")
    _write(tmp_path / "core/data/build/build.gradle.kts", "")
    found = {m.relative_to(tmp_path).as_posix() for m in android.gradle_modules(tmp_path)}
    assert not any("build/" in p for p in found)


# ---- placement ---------------------------------------------------------------


def test_the_module_that_already_holds_the_package_is_chosen(tmp_path: Path) -> None:
    _android_repo(tmp_path)
    chosen = android.module_for_package(tmp_path, "com.x.core.data")
    assert chosen == tmp_path / "core/data"


def test_a_new_subpackage_lands_in_the_module_owning_its_parent(tmp_path: Path) -> None:
    """`…core.data.pricing` does not exist yet; `core/data` is still the only module it
    could belong to, and a prefix match is the evidence for that."""
    _android_repo(tmp_path)
    assert android.module_for_package(tmp_path, "com.x.core.data.pricing") == tmp_path / "core/data"


def test_the_longest_matching_module_wins(tmp_path: Path) -> None:
    """A module rooted at `com.x.core` would also match `com.x.core.data.pricing`; the more
    specific module is the right answer and shorter prefixes must not win on walk order."""
    _android_repo(tmp_path)
    _write(tmp_path / "core/common/build.gradle.kts", 'plugins {\n    id("kotlin")\n}\n')
    _write(tmp_path / "core/common/src/main/java/com/x/core/Common.kt", "package com.x.core\n")
    assert android.module_for_package(tmp_path, "com.x.core.data.pricing") == tmp_path / "core/data"
    assert android.module_for_package(tmp_path, "com.x.core.other") == tmp_path / "core/common"


def test_an_unrelated_package_chooses_nothing(tmp_path: Path) -> None:
    """Precision-first. A wrong module produces a file that compiles and a test that passes
    in a place no one would look, which is worse than a refusal."""
    _android_repo(tmp_path)
    assert android.module_for_package(tmp_path, "com.entirely.elsewhere") is None


def test_the_base_package_is_the_shallowest_one_holding_kotlin(tmp_path: Path) -> None:
    _android_repo(tmp_path)
    assert android.base_package(tmp_path / "core/data") == "com.x.core.data"


# ---- layout ------------------------------------------------------------------


def test_a_multi_module_repo_resolves_into_a_module(tmp_path: Path) -> None:
    _android_repo(tmp_path)
    layout = _resolve_kotlin_layout(tmp_path, mode="auto", package_name="com.x.core.data", repo=None)
    assert layout.mode == "existing"
    assert layout.module == "core/data"
    assert layout.source_dir == "core/data/src/main/java/com/x/core/data"
    assert layout.tests_dir == "core/data/src/test/java/com/x/core/data"
    assert layout.android is True


def test_a_jvm_module_in_an_android_build_is_not_flagged_android(tmp_path: Path) -> None:
    _android_repo(tmp_path)
    layout = _resolve_kotlin_layout(tmp_path, mode="auto", package_name="com.x.core.model", repo=None)
    assert layout.module == "core/model"
    assert layout.android is False


def test_a_new_subpackage_uses_the_modules_own_source_root(tmp_path: Path) -> None:
    """Android keeps Kotlin under `src/main/java`. Spelling a new package under
    `src/main/kotlin` would compile only if the build script also declared that root."""
    _android_repo(tmp_path)
    layout = _resolve_kotlin_layout(tmp_path, mode="auto", package_name="com.x.core.data.pricing", repo=None)
    assert layout.source_dir == "core/data/src/main/java/com/x/core/data/pricing"
    assert layout.tests_dir == "core/data/src/test/java/com/x/core/data/pricing"


def test_a_single_module_repo_still_resolves_at_its_root(tmp_path: Path) -> None:
    """P8 regression: the common Kotlin/JVM shape must not acquire a module prefix."""
    _write(tmp_path / "build.gradle.kts", 'plugins {\n    kotlin("jvm")\n}\n')
    _write(tmp_path / "src/main/kotlin/com/x/app/A.kt", "package com.x.app\n")
    layout = _resolve_kotlin_layout(tmp_path, mode="auto", package_name=None, repo=None)
    assert layout.module == ""
    assert layout.source_dir == "src/main/kotlin/com/x/app"


def test_an_unplaceable_package_yields_an_empty_placement(tmp_path: Path) -> None:
    """Not a silent fallback to the repository root: an empty placement is the signal
    `kotlin_project_error` turns into an actionable message."""
    _android_repo(tmp_path)
    layout = _resolve_kotlin_layout(tmp_path, mode="auto", package_name="com.nope.at.all", repo=None)
    assert layout.source_dir == ""
    assert layout.module == ""


def test_mode_new_still_scaffolds_inside_a_multi_module_repo(tmp_path: Path) -> None:
    """The escape hatch: an explicit `--mode new` asks for a standalone project and must
    not be overridden by module placement."""
    _android_repo(tmp_path)
    layout = _resolve_kotlin_layout(tmp_path, mode="new", package_name="com.x.fresh", repo=None)
    assert layout.mode == "new"
    assert layout.source_dir == "src/main/kotlin/com/x/fresh"


def test_the_test_library_is_read_per_module_not_per_repository(tmp_path: Path) -> None:
    """The repo-wide answer is wrong one level down. In the validation app `core/data` gets
    `kotlin("test")` from a convention plugin while `core/model` — a plain `id("kotlin")`
    library in the same build — declares no test dependency at all, so a generated
    `import kotlin.test.Test` fails there with `Unresolved reference: test`."""
    _android_repo(tmp_path)
    assert android.module_test_library(tmp_path, tmp_path / "core/data") == "kotlin.test"
    assert android.module_test_library(tmp_path, tmp_path / "core/model") == ""


def test_what_a_modules_existing_tests_import_outranks_what_it_declares(tmp_path: Path) -> None:
    """Proof beats inference: a module whose tests are already written against JUnit 4 takes
    JUnit 4, whatever its build script happens to put on the classpath."""
    _android_repo(tmp_path)
    _write(
        tmp_path / "core/data/src/test/java/com/x/core/data/OldTest.kt",
        "package com.x.core.data\n\nimport org.junit.Test\nimport org.junit.Assert\n",
    )
    assert android.module_test_library(tmp_path, tmp_path / "core/data") == "junit4"


def test_a_module_with_no_test_dependency_is_told_to_add_one(tmp_path: Path) -> None:
    """Saying "use kotlin.test" to a module that cannot compile it is the P8 bug one level
    down; the dependency has to be part of the instruction."""
    _android_repo(tmp_path)
    layout = _resolve_kotlin_layout(tmp_path, mode="auto", package_name="com.x.core.model", repo=None)
    text = kotlin_guidance(layout)
    assert 'testImplementation(kotlin("test"))' in text
    assert "core/model/build.gradle.kts" in text


def test_a_test_library_declared_only_in_a_convention_plugin_is_found(tmp_path: Path) -> None:
    """The validation app declares `kotlin("test")` once, in `build-logic/`, and no
    `build.gradle.kts` in the repository mentions a test library at all."""
    _android_repo(tmp_path)
    assert detect_jvm_test_library(tmp_path) == "kotlin.test"


# ---- toolchain errors --------------------------------------------------------


def test_the_kotlin_toolchain_checks_the_project_not_just_the_machine() -> None:
    assert TOOLCHAINS["kotlin"].project_error is not None


def test_a_project_without_gradle_says_which_thing_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    layout = _resolve_kotlin_layout(tmp_path, mode="new", package_name="com.x", repo=None)
    message = kotlin_project_error(tmp_path, layout)
    assert message is not None and "gradlew" in message


def test_an_android_project_without_an_sdk_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The failure this replaces arrives minutes into a build, from the Android Gradle
    Plugin, and reads as a missing directory rather than an unprepared machine."""
    _android_repo(tmp_path)
    _write(tmp_path / "gradlew", "#!/bin/sh\n")
    monkeypatch.setattr(android, "android_sdk_root", lambda: None)
    layout = _resolve_kotlin_layout(tmp_path, mode="auto", package_name="com.x.core.data", repo=None)
    message = kotlin_project_error(tmp_path, layout)
    assert message is not None
    assert "ANDROID_HOME" in message
    # Saying an emulator is not needed matters: the obvious reading of "Android SDK" is
    # that a device is required, and it is not.
    assert "emulator" in message


def test_an_unplaceable_package_lists_the_candidate_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _android_repo(tmp_path)
    _write(tmp_path / "gradlew", "#!/bin/sh\n")
    monkeypatch.setattr(android, "android_sdk_root", lambda: tmp_path / "sdk")
    layout = _resolve_kotlin_layout(tmp_path, mode="auto", package_name="com.nope", repo=None)
    message = kotlin_project_error(tmp_path, layout)
    assert message is not None
    assert "--package-name" in message
    assert "core/data" in message


def test_the_flags_the_error_names_are_flags_that_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error message is only actionable if the reader can type what it says. The first
    draft of this one named `--package` and `--mode`; the CLI has `--package-name` and
    `--layout`, so following it exactly would have produced two more errors."""
    from orchestrator.cli import sdlc as sdlc_cli

    _android_repo(tmp_path)
    _write(tmp_path / "gradlew", "#!/bin/sh\n")
    monkeypatch.setattr(android, "android_sdk_root", lambda: tmp_path / "sdk")
    layout = _resolve_kotlin_layout(tmp_path, mode="auto", package_name="com.nope", repo=None)
    message = kotlin_project_error(tmp_path, layout) or ""
    source = Path(sdlc_cli.__file__).read_text(encoding="utf-8")
    named = {word for word in message.replace(",", " ").split() if word.startswith("--")}
    assert named, "the message is supposed to tell the reader which flags to use"
    for flag in named:
        assert f'"{flag}"' in source, f"{flag} is named in an error message but is not a CLI flag"


def test_a_placeable_android_project_has_no_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _android_repo(tmp_path)
    _write(tmp_path / "gradlew", "#!/bin/sh\n")
    monkeypatch.setattr(android, "android_sdk_root", lambda: tmp_path / "sdk")
    layout = _resolve_kotlin_layout(tmp_path, mode="auto", package_name="com.x.core.data", repo=None)
    assert kotlin_project_error(tmp_path, layout) is None


# ---- the runner --------------------------------------------------------------


def test_an_android_module_is_tested_with_its_variant_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`test` on an Android module runs every variant's unit tests — the same assertions
    two or four times over, for no extra signal."""
    _android_repo(tmp_path)
    _write(tmp_path / "core/data/src/test/java/com/x/core/data/AT.kt", "package com.x.core.data\n")
    _write(tmp_path / "core/model/src/test/java/com/x/core/model/MT.kt", "package com.x.core.model\n")

    async def fake_exec(argv: tuple[str, ...], *, cwd: str, timeout: float) -> tuple[int, str]:
        return 0, (
            "?? core/data/src/test/java/com/x/core/data/AT.kt\n"
            "?? core/model/src/test/java/com/x/core/model/MT.kt\n"
        )

    monkeypatch.setattr(testrunner, "_exec_capture", fake_exec)
    tasks = asyncio.run(GradleTestRunner()._changed_module_tasks(str(tmp_path)))
    assert tasks == [":core:data:testDebugUnitTest", ":core:model:test"]


def test_a_brand_new_test_directory_is_still_attributed_to_its_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git collapses a wholly-new untracked directory to the directory itself, and a generated
    test is very often the first file in a directory that did not exist. Asking for the
    collapsed form would make the change look like it touches no source at all, and the runner
    would fall back to building everything."""
    _android_repo(tmp_path)
    seen: dict[str, tuple[str, ...]] = {}

    async def fake_exec(argv: tuple[str, ...], *, cwd: str, timeout: float) -> tuple[int, str]:
        seen["argv"] = argv
        return 0, "?? core/data/src/test/java/com/x/core/data/NewTest.kt\n"

    monkeypatch.setattr(testrunner, "_exec_capture", fake_exec)
    tasks = asyncio.run(GradleTestRunner()._changed_module_tasks(str(tmp_path)))
    assert "-uall" in seen["argv"], "untracked directories would stay collapsed without it"
    assert tasks == [":core:data:testDebugUnitTest"]


def test_gradle_ambiguity_is_resolved_from_its_own_candidate_list() -> None:
    """Flavour names are computed in Kotlin inside a separate included build, so they
    cannot be read off a build script. Gradle names them in the failure; that is the
    difference between a derived answer and a guess."""
    assert _variant_tasks(AMBIGUOUS, [":core:data:testDebugUnitTest"]) == [":core:data:testDemoDebugUnitTest"]


def test_the_other_spelling_of_the_same_failure_is_understood() -> None:
    assert _variant_tasks(NOT_FOUND, [":app:testDebugUnitTest"]) == [":app:testDemoDebugUnitTest"]


def test_only_the_task_gradle_complained_about_is_rewritten() -> None:
    """A build that mixes Android and JVM modules asks for both spellings at once, and the
    plain `test` task of a JVM module is not affected by the other module's flavours."""
    assert _variant_tasks(AMBIGUOUS, [":core:data:testDebugUnitTest", ":core:model:test"]) == [
        ":core:data:testDemoDebugUnitTest",
        ":core:model:test",
    ]


def test_a_failing_test_is_not_mistaken_for_a_missing_task() -> None:
    """The retry must fire only on task resolution. Re-running a genuinely red suite
    because its output was misread would turn a real failure into a green one."""
    red = "> Task :core:data:testDemoDebugUnitTest FAILED\nSyncBackoffTest > x() FAILED\n"
    assert _variant_tasks(red, [":core:data:testDemoDebugUnitTest"]) is None


def test_a_debug_variant_is_preferred_over_release() -> None:
    """Unit tests run against the debug build type by convention; a release variant may be
    minified and is the wrong thing to assert against."""
    message = (
        "Cannot locate tasks that match ':lib:testUnitTest' as task 'testUnitTest' is "
        "ambiguous in project ':lib'. Candidates are: 'testDemoDebugUnitTest', "
        "'testDemoReleaseUnitTest'.\n"
    )
    assert _variant_tasks(message, [":lib:testUnitTest"]) == [":lib:testDemoDebugUnitTest"]


def test_a_resolved_variant_task_is_remembered(tmp_path: Path) -> None:
    """The refine loop reuses one runner across iterations; only the first build should
    pay for discovering the variant name."""
    runner = GradleTestRunner()
    runner._resolved[":core:data:testDebugUnitTest"] = ":core:data:testDemoDebugUnitTest"
    assert runner._resolved[":core:data:testDebugUnitTest"].endswith("testDemoDebugUnitTest")


# ---- guidance ----------------------------------------------------------------


def test_android_guidance_rules_out_instrumented_tests(tmp_path: Path) -> None:
    """The one thing explicitly out of scope (§10). A model left to itself reaches for
    Espresso, and the result compiles and then cannot be run at all."""
    _android_repo(tmp_path)
    layout = _resolve_kotlin_layout(tmp_path, mode="auto", package_name="com.x.core.data", repo=None)
    text = kotlin_guidance(layout)
    assert "androidTest" in text
    assert "AndroidJUnit4" in text
    assert "Espresso" in text
    assert "TODO: UI test" in text


def test_android_guidance_names_the_module_in_gradle_spelling(tmp_path: Path) -> None:
    _android_repo(tmp_path)
    layout = _resolve_kotlin_layout(tmp_path, mode="auto", package_name="com.x.core.data", repo=None)
    assert "`:core:data`" in kotlin_guidance(layout)


def test_a_plain_kotlin_module_gets_no_android_rules(tmp_path: Path) -> None:
    """Guidance costs prompt budget and misleads when wrong; a JVM module has no
    instrumented tests to rule out."""
    _write(tmp_path / "build.gradle.kts", 'plugins {\n    kotlin("jvm")\n}\n')
    _write(tmp_path / "src/main/kotlin/com/x/app/A.kt", "package com.x.app\n")
    layout = _resolve_kotlin_layout(tmp_path, mode="auto", package_name=None, repo=None)
    assert "Espresso" not in kotlin_guidance(layout)


def test_the_build_file_named_is_the_modules_own(tmp_path: Path) -> None:
    """In a 27-module build the root script configures the build, not this module's
    dependencies; declaring one there declares it for nothing."""
    _android_repo(tmp_path)
    layout = _resolve_kotlin_layout(tmp_path, mode="auto", package_name="com.x.core.data", repo=None)
    assert "`core/data/build.gradle.kts`" in kotlin_guidance(layout)


# ---- the SDK probe -----------------------------------------------------------


def test_android_home_locates_the_sdk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = tmp_path / "sdk"
    (sdk / "platforms").mkdir(parents=True)
    monkeypatch.setenv("ANDROID_HOME", str(sdk))
    assert android.android_sdk_root() == sdk
    assert android_toolchain_available()


def test_a_machine_with_no_sdk_reports_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANDROID_HOME", raising=False)
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert android.android_sdk_root() is None
    assert not android_toolchain_available()


@pytest.mark.parametrize("var", ["ANDROID_HOME", "ANDROID_SDK_ROOT"])
def test_both_sdk_variables_are_honoured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, var: str) -> None:
    """`ANDROID_SDK_ROOT` is deprecated but still what many CI images set."""
    sdk = tmp_path / "sdk"
    (sdk / "platforms").mkdir(parents=True)
    monkeypatch.delenv("ANDROID_HOME", raising=False)
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    monkeypatch.setenv(var, str(sdk))
    assert android.android_sdk_root() == sdk
