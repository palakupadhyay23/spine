"""Codegen language registration, with lazy adapters to avoid import cycles.

Factories resolve their implementation at call time: optional toolchains stay optional,
and applications/tests can still inject or replace the existing adapters. Temporal
activities continue to consume their injected dependencies; registration does not
change worker defaults or introduce language selection into workflow payloads.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from orchestrator.sdlc.contracts import PreflightRunner, TestEnvironment, TestRunner
    from orchestrator.sdlc.contracts import ToolchainLayout as TargetLayout
    from orchestrator.sdlc.process import ExecCapture


def _load(module: str, name: str) -> Any:
    """Resolve only registry-owned names, after the caller's module has loaded."""
    return getattr(importlib.import_module(f"orchestrator.sdlc.{module}"), name)


def _layout(name: str, *, python: bool = False) -> Callable[..., TargetLayout]:
    def resolve(
        root: Path, *, mode: str, package_name: str | None, repo: str | None, src_layout: bool = True
    ) -> TargetLayout:
        kwargs: dict[str, Any] = {"mode": mode, "package_name": package_name, "repo": repo}
        if python:
            kwargs["src_layout"] = src_layout
        return cast("TargetLayout", _load("layout", name)(root, **kwargs))

    return resolve


def _scaffold(name: str) -> Callable[[TargetLayout], dict[str, str]]:
    def files(layout: TargetLayout) -> dict[str, str]:
        return cast("dict[str, str]", _load("scaffold", name)(layout))

    return files


def _environment(name: str, default: str = "") -> Callable[[str], TestEnvironment]:
    def create(build_tool: str) -> TestEnvironment:
        args = (build_tool or default,) if default else ()
        return cast("TestEnvironment", _load("testenv", name)(*args))

    return create


def _python_environment(build_tool: str) -> TestEnvironment:
    name = (
        "LocalTestEnvironment"
        if os.getenv("SDLC_TEST_ISOLATION", "venv").lower() == "local"
        else "VenvTestEnvironment"
    )
    return cast("TestEnvironment", _load("testenv", name)())


def _runner(name: str) -> Callable[[TestEnvironment], TestRunner]:
    def create(env: TestEnvironment) -> TestRunner:
        return cast("TestRunner", _load("testrunner", name)())

    return create


def _python_runner(env: TestEnvironment) -> TestRunner:
    return cast("TestRunner", _load("testrunner", "SubprocessTestRunner")(python=env.python))


def _node_runner(env: TestEnvironment) -> TestRunner:
    return cast(
        "TestRunner",
        _load("testrunner", "NodeTestRunner")(package_manager=getattr(env, "package_manager", "npm")),
    )


def _native_runner(env: TestEnvironment) -> TestRunner:
    name = "MesonTestRunner" if getattr(env, "build_tool", "cmake") == "meson" else "CTestRunner"
    return cast("TestRunner", _load("testrunner", name)())


def _php_runner(env: TestEnvironment) -> TestRunner:
    return cast(
        "TestRunner",
        _load("testrunner", "PhpUnitTestRunner")(
            php=getattr(env, "php", "php"), phpunit=getattr(env, "phpunit", None)
        ),
    )


def _sql_runner(env: TestEnvironment) -> TestRunner:
    name = (
        "PostgresSqlTestRunner"
        if os.getenv("SDLC_SQL_ENGINE", "sqlite").lower() == "postgres"
        else "SqlTestRunner"
    )
    return cast("TestRunner", _load("testrunner", name)(dialect=getattr(env, "dialect", "postgres")))


def _probe(name: str, *, with_build_tool: bool = False) -> Callable[[str], bool]:
    def available(build_tool: str) -> bool:
        args = (build_tool or "npm",) if with_build_tool else ()
        return cast("bool", _load("testenv", name)(*args))

    return available


def _always_available(build_tool: str) -> bool:
    return True


def _project_probe(name: str) -> Callable[[Path, TargetLayout], str | None]:
    """A per-repository check that explains itself — see ``Toolchain.project_error``."""

    def check(root: Path, layout: TargetLayout) -> str | None:
        return cast("str | None", _load("testenv", name)(root, layout))

    return check


def _identity_layout(layout: TargetLayout) -> TargetLayout:
    return layout


def _identity_name(module: str) -> str:
    return module


def _perl_module_name(module: str) -> str:
    return module.replace("::", "/")


def _dotnet_layout(layout: TargetLayout) -> TargetLayout:
    return replace(layout, target_framework=_load("testenv", "detect_dotnet_tfm")())


def _conventions(root: Path, layout: TargetLayout | None) -> str:
    return cast("str", _load("conventions", "extract_conventions")(root).prompt_block())


def _php_conventions(root: Path, layout: TargetLayout | None) -> str:
    if layout is None:
        return _conventions(root, layout)
    return cast("str", _load("conventions", "php_convention_block")(root, layout))


def _perl_conventions(root: Path, layout: TargetLayout | None) -> str:
    return cast("str", _load("conventions", "perl_convention_block")(root, layout))


def _preflight(name: str, *, argument: str = "") -> Callable[..., PreflightRunner]:
    def create(*, executable: str | None = None, capture: ExecCapture | None = None) -> PreflightRunner:
        kwargs: dict[str, Any] = {argument: executable} if argument and executable is not None else {}
        if capture is not None:
            kwargs["capture"] = capture
        return cast("PreflightRunner", _load("preflight", name)(**kwargs))

    return create


@dataclass(frozen=True)
class PromptSet:
    """Names of the existing prompt constants; resolve after codegen finishes importing."""

    implement: str
    tests: str
    refine: str

    def text(self, phase: str) -> str:
        return cast("str", _load("codegen", getattr(self, phase)))


def _prompts(suffix: str = "", *, tests_suffix: str | None = None) -> PromptSet:
    return PromptSet(
        "_IMPLEMENT_SYSTEM" + suffix,
        "_TESTS_SYSTEM" + (suffix if tests_suffix is None else tests_suffix),
        "_REFINE_SYSTEM" + suffix,
    )


@dataclass(frozen=True)
class Toolchain:
    source_ext: str
    layout: Callable[..., TargetLayout]
    scaffold: Callable[[TargetLayout], dict[str, str]]
    environment: Callable[[str], TestEnvironment]
    runner: Callable[[TestEnvironment], TestRunner]
    prompts: PromptSet
    guidance: str
    available: Callable[[str], bool] = field(default=_always_available)
    #: A second probe that gets the worktree **root**, for a toolchain a repository can
    #: carry with it. Gradle is the case: a committed `./gradlew` downloads the version
    #: the project pins, so a wrapper-only repo is perfectly buildable on a machine with
    #: no Gradle at all — and a PATH-only probe would reject the majority of real Gradle
    #: projects. Checked *after* `available`, so it narrows rather than replaces it.
    # A per-repository check, as opposed to ``available``'s machine-wide one. Whether the
    # language's toolchain is installed is a different question from whether *this* checkout
    # can be built: Gradle ships in the repo, the Android SDK is external, and module
    # placement depends on the target package. Each needs its own sentence to be actionable,
    # so this returns the message rather than a bool.
    project_error: Callable[[Path, TargetLayout], str | None] | None = None
    preflight: Callable[..., PreflightRunner] = _preflight("StubPreflightRunner")
    conventions: Callable[[Path, TargetLayout | None], str] = field(default=_conventions)
    prepare_layout: Callable[[TargetLayout], TargetLayout] = field(default=_identity_layout)
    build_ignores: tuple[str, ...] = ()
    auto_priority: int | None = None
    missing_hint: str = ""
    native_label: str = ""
    requires_pytest: bool = False
    author_tests: bool = True
    module_name: Callable[[str], str] = field(default=_identity_name)

    def layout_guidance(self, layout: TargetLayout) -> str:
        return cast("str", _load("language_guidance", self.guidance)(layout))

    def availability_error(self, root: Path, layout: TargetLayout) -> str | None:
        """Keep the native brownfield build-system guard as well as its compiler probe."""
        if not self.native_label:
            if not self.available(layout.build_tool):
                return self.missing_hint.format(build_tool=layout.build_tool or "npm")
            if self.project_error is not None:
                return self.project_error(root, layout)
            return None
        label = self.native_label
        build_tool = layout.build_tool if layout.mode == "existing" else "cmake"
        if build_tool == "meson":
            if not _probe("meson_toolchain_available")(build_tool):
                return (
                    f"Meson {label} codegen needs meson + ninja + a compiler on PATH "
                    "(install them, then retry)."
                )
        elif build_tool in ("cmake", ""):
            if not self.available(build_tool):
                return f"{label} codegen needs CMake + a {label} compiler on PATH (install both, then retry)."
            if layout.mode == "existing" and not (root / "CMakeLists.txt").is_file():
                return (
                    f"{label} codegen builds with CMake or Meson, but this repo has neither a "
                    "CMakeLists.txt nor a recognized meson.build."
                )
        else:
            return (
                f"{label} codegen builds with CMake or Meson, but this repo uses "
                f"{build_tool} (not supported yet)."
            )
        return None


# Auto-detection preserves the old precedence (Python dominates; SQL is explicit only).
# A language is supported only when its complete row is registered here.
TOOLCHAINS: Mapping[str, Toolchain] = MappingProxyType(
    {
        "python": Toolchain(
            "py",
            _layout("_resolve_python_layout", python=True),
            _scaffold("_python_files"),
            _python_environment,
            _python_runner,
            _prompts(),
            "python_guidance",
            preflight=_preflight("SubprocessPreflightRunner", argument="python"),
            requires_pytest=True,
        ),
        "java": Toolchain(
            "java",
            _layout("_resolve_java_layout"),
            _scaffold("_java_files"),
            _environment("JavaToolEnvironment"),
            _runner("MavenTestRunner"),
            _prompts("_JAVA"),
            "java_guidance",
            available=_probe("java_toolchain_available"),
            auto_priority=0,
            missing_hint="Java codegen needs a JDK + Maven on PATH (install both, then retry).",
        ),
        "typescript": Toolchain(
            "ts",
            _layout("_resolve_typescript_layout"),
            _scaffold("_typescript_files"),
            _environment("NodeToolEnvironment", "npm"),
            _node_runner,
            _prompts("_TS"),
            "typescript_guidance",
            available=_probe("node_toolchain_available", with_build_tool=True),
            auto_priority=1,
            missing_hint=(
                "TypeScript codegen needs Node.js + {build_tool} on PATH (install both, then retry)."
            ),
        ),
        "csharp": Toolchain(
            "cs",
            _layout("_resolve_csharp_layout"),
            _scaffold("_csharp_files"),
            _environment("DotnetToolEnvironment"),
            _runner("DotnetTestRunner"),
            _prompts("_CSHARP"),
            "csharp_guidance",
            available=_probe("dotnet_toolchain_available"),
            prepare_layout=_dotnet_layout,
            build_ignores=("bin", "obj"),
            auto_priority=2,
            missing_hint="C# codegen needs the .NET SDK (`dotnet`) on PATH (install it, then retry).",
        ),
        "c": Toolchain(
            "c",
            _layout("_resolve_c_layout"),
            _scaffold("_c_files"),
            _environment("CToolEnvironment", "cmake"),
            _native_runner,
            _prompts("_C"),
            "c_guidance",
            available=_probe("c_toolchain_available"),
            build_ignores=("build",),
            auto_priority=6,
            native_label="C",
        ),
        "cpp": Toolchain(
            "cpp",
            _layout("_resolve_cpp_layout"),
            _scaffold("_cpp_files"),
            _environment("CToolEnvironment", "cmake"),
            _native_runner,
            _prompts("_CPP"),
            "cpp_guidance",
            available=_probe("cpp_toolchain_available"),
            build_ignores=("build",),
            auto_priority=5,
            native_label="C++",
        ),
        "go": Toolchain(
            "go",
            _layout("_resolve_go_layout"),
            _scaffold("_go_files"),
            _environment("GoToolEnvironment"),
            _runner("GoTestRunner"),
            _prompts("_GO"),
            "go_guidance",
            available=_probe("go_toolchain_available"),
            auto_priority=4,
            missing_hint="Go codegen needs the Go toolchain (`go`) on PATH (install it, then retry).",
        ),
        "kotlin": Toolchain(
            "kt",
            _layout("_resolve_kotlin_layout"),
            _scaffold("_kotlin_files"),
            _environment("KotlinToolEnvironment"),
            _runner("GradleTestRunner"),
            _prompts("_KOTLIN"),
            "kotlin_guidance",
            preflight=_preflight("GradlePreflightRunner", argument="gradle"),
            available=_probe("kotlin_toolchain_available"),
            # Gradle is checked against the worktree, not PATH: a committed `./gradlew`
            # makes a project buildable on a machine with no Gradle installed at all, and
            # that is how most real Kotlin repositories ship (D13, P8).
            project_error=_project_probe("kotlin_project_error"),
            # After Java: a mixed JVM repository with both `.java` and `.kt` is a Java
            # repository that adopted Kotlin, and its codegen conventions are Java's until
            # someone says otherwise. Kotlin wins only where Java is absent.
            auto_priority=5,
            missing_hint=(
                "Kotlin codegen needs a JDK plus Gradle — either a committed ./gradlew in "
                "the repo or `gradle` on PATH (add the wrapper with `gradle wrapper`, or "
                "install Gradle, then retry)."
            ),
            build_ignores=(".gradle", "build"),
        ),
        "php": Toolchain(
            "php",
            _layout("_resolve_php_layout"),
            _scaffold("_php_files"),
            _environment("PhpToolEnvironment"),
            _php_runner,
            _prompts("_PHP"),
            "php_guidance",
            available=_probe("php_toolchain_available"),
            preflight=_preflight("PhpPreflightRunner", argument="php"),
            conventions=_php_conventions,
            auto_priority=3,
            missing_hint="PHP codegen needs `php` on PATH (install it, then retry).",
        ),
        "perl": Toolchain(
            "pm",
            _layout("_resolve_perl_layout"),
            _scaffold("_perl_files"),
            _environment("PerlToolEnvironment"),
            _runner("ProveTestRunner"),
            _prompts("_PERL"),
            "perl_guidance",
            available=_probe("perl_toolchain_available"),
            preflight=_preflight("PerlPreflightRunner", argument="perl"),
            conventions=_perl_conventions,
            module_name=_perl_module_name,
            auto_priority=7,
            missing_hint="Perl codegen needs `perl` and `prove` on PATH (install both, then retry).",
        ),
        # author_tests previously falls back to Python's prompt for SQL; retain it.
        "sql": Toolchain(
            "sql",
            _layout("_resolve_sql_layout"),
            _scaffold("_sql_files"),
            _environment("SqlToolEnvironment", "postgres"),
            _sql_runner,
            _prompts("_SQL", tests_suffix=""),
            "sql_guidance",
            author_tests=False,
        ),
    }
)


def get_toolchain(language: str) -> Toolchain:
    """Factory helpers retain their historical Python fallback; the CLI validates first."""
    return TOOLCHAINS.get(language, TOOLCHAINS["python"])


def detect_language(languages: set[str] | frozenset[str]) -> str:
    if "python" in languages:
        return "python"
    candidates = [
        (row.auto_priority, name)
        for name, row in TOOLCHAINS.items()
        if name in languages and row.auto_priority is not None
    ]
    return min(candidates)[1] if candidates else "python"
