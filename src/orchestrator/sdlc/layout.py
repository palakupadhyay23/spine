"""Target-layout resolution for SDLC codegen.

The codegen adapter writes files into a worktree. Without a declared layout it
invents paths in greenfield repos (e.g. leaking ``src/orchestrator/pkg/...`` into
an unrelated project) and drifts run-to-run. ``TargetLayout`` is a small,
deterministic contract — package name + source/tests dirs — computed once per run
and threaded into the codegen prompts so placement is project-appropriate.

Modes (``--layout``):
- ``auto``     — existing recognizable package → ``existing``; else → ``new``.
- ``new``      — scaffold a fresh ``src/<package>/`` structure, then generate into it.
- ``existing`` — never scaffold; follow the repo's current package layout.

Deterministic and read-only (filesystem reads + string munging, no LLM).
"""

from __future__ import annotations

import keyword
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from orchestrator.pkg.extractor import DEFAULT_IGNORE_DIRS

# Top-level dirs that are never a project's source package.
_NON_PACKAGE_DIRS = {"tests", "test", "docs", "doc", "examples", "scripts", "build", "dist"}

_FALLBACK_PACKAGE = "app"
_JAVA_GROUP = "org.example"  # default reverse-DNS group for greenfield Java

# Go package names can't be a reserved keyword (or `init`); guard the derived slug.
_GO_KEYWORDS = frozenset(
    {
        "break",
        "case",
        "chan",
        "const",
        "continue",
        "default",
        "defer",
        "else",
        "fallthrough",
        "for",
        "func",
        "go",
        "goto",
        "if",
        "import",
        "interface",
        "map",
        "package",
        "range",
        "return",
        "select",
        "struct",
        "switch",
        "type",
        "var",
        "init",
    }
)


@dataclass(frozen=True)
class TargetLayout:
    """Where generated code goes, and how it's imported.

    ``mode`` is ``"new"`` (scaffold a structure) or ``"existing"`` (follow the
    repo). ``scaffolded`` is set by the runner once the skeleton is actually
    written (so an idempotent no-op scaffold still reads ``False``).
    """

    package_name: str
    source_dir: str
    tests_dir: str
    src_layout: bool
    mode: str
    scaffolded: bool = False
    language: str = "python"
    build_tool: str = ""  # "maven"|"gradle" (Java) | "npm"|"yarn"|"pnpm" (TypeScript) | ""
    # Greenfield C# target-framework moniker (e.g. "net8.0"/"net10.0"). Empty → the
    # scaffold's default; the runner sets it from the installed SDK so the generated
    # project both builds AND runs (a TFM with no matching runtime fails at test host).
    target_framework: str = ""
    test_suffix: str = "Test.php"
    test_bootstrap: str = ""
    # Which assertion library the repo's tests already use ("kotlin.test", "junit5", …).
    # Brownfield JVM codegen needs this: a generated `import kotlin.test.Test` does not
    # compile in a project that depends on JUnit and nothing else, and the failure is a
    # compile error the refine loop then has to spend a pass undoing. Empty → the
    # language's greenfield default.
    test_library: str = ""
    # The Gradle module the generated files belong to, repo-relative and `/`-separated
    # ("core/data"), or "" for a single-module build. P9, D13: an Android repo has no sources
    # at its root at all — the validation app is 27 modules — so "which module" is a separate
    # question from "which package", and the runner needs the answer to name a task
    # (`:core:data:testDemoDebugUnitTest`) that exists.
    module: str = ""
    # True when that module is built by the Android Gradle Plugin. It changes what a generated
    # *test* may contain, not just where it goes: an instrumented test needs a device and Spine
    # never runs one, so the guidance has to rule them out rather than let the model reach for
    # Espresso and produce a suite that cannot run.
    android: bool = False

    def module_rel_path(self, module: str) -> str:
        """Worktree-relative path for a new source module/class (no leading dir)."""
        from orchestrator.sdlc.toolchains import get_toolchain

        toolchain = get_toolchain(self.language)
        return f"{self.source_dir}/{toolchain.module_name(module)}.{toolchain.source_ext}"


def derive_package_name(name: str) -> str:
    """Sanitize a repo URL / directory name into a valid Python package name.

    ``Example-Service.`` → ``example_service``. Lowercase,
    drop a trailing ``.git``, map every run of non-alphanumerics to ``_``, strip
    leading/trailing ``_``, and guard against empty / digit-leading / keyword
    results.
    """
    base = name.rstrip("/").rsplit("/", 1)[-1]
    if base.endswith(".git"):
        base = base[: -len(".git")]
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", base.lower()).strip("_")
    if not slug:
        return _FALLBACK_PACKAGE
    if slug[0].isdigit():
        slug = f"pkg_{slug}"
    if keyword.iskeyword(slug):
        slug = f"{slug}_pkg"
    return slug


def detect_existing_package(root: Path) -> tuple[str, str] | None:
    """Find the repo's source package, if it has a recognizable one.

    Returns ``(package_name, source_dir)`` or ``None``. Prefers a ``src/<pkg>/``
    layout, then a top-level ``<pkg>/__init__.py`` (excluding tests/docs/etc.).
    """
    src = root / "src"
    if src.is_dir():
        for child in sorted(src.iterdir()):
            if child.is_dir() and (child / "__init__.py").is_file():
                return child.name, f"src/{child.name}"
    for child in sorted(root.iterdir()):
        if (
            child.is_dir()
            and child.name not in _NON_PACKAGE_DIRS
            and not child.name.startswith(".")
            and child.name not in DEFAULT_IGNORE_DIRS
            and (child / "__init__.py").is_file()
        ):
            return child.name, child.name
    return None


def derive_java_package(name: str) -> str:
    """Repo name → reverse-DNS Java package (``org.example.<slug>``)."""
    base = name.rstrip("/").rsplit("/", 1)[-1]
    if base.endswith(".git"):
        base = base[: -len(".git")]
    slug = re.sub(r"[^0-9a-zA-Z]+", "", base.lower())
    if not slug:
        slug = _FALLBACK_PACKAGE
    if slug[0].isdigit():
        slug = f"p{slug}"
    return f"{_JAVA_GROUP}.{slug}"


def _java_dirs(package: str) -> tuple[str, str]:
    path = package.replace(".", "/")
    return f"src/main/java/{path}", f"src/test/java/{path}"


def _detect_build_tool(root: Path) -> str:
    if (root / "pom.xml").is_file():
        return "maven"
    if (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file():
        return "gradle"
    return ""


def detect_java_layout(root: Path) -> tuple[str, str, str] | None:
    """If ``src/main/java`` holds a package, return ``(package, source_dir, tests_dir)``.

    The package is the first dir under ``src/main/java`` that directly contains
    ``.java`` files (path → dotted)."""
    main = root / "src" / "main" / "java"
    if not main.is_dir():
        return None
    for dirpath, _dirs, files in os.walk(main):
        if any(f.endswith(".java") for f in files):
            rel = Path(dirpath).relative_to(main)
            package = str(rel).replace(os.sep, ".")
            return package, f"src/main/java/{rel.as_posix()}", f"src/test/java/{rel.as_posix()}"
    return None


def _resolve_java_layout(
    root: Path, *, mode: str, package_name: str | None, repo: str | None
) -> TargetLayout:
    existing = detect_java_layout(root)
    derived = package_name or derive_java_package(repo or str(root))
    build_tool = _detect_build_tool(root) or "maven"
    if mode == "existing" or (mode == "auto" and existing is not None):
        if existing is not None:
            pkg, source_dir, tests_dir = existing
            return TargetLayout(
                package_name=package_name or pkg,
                source_dir=source_dir,
                tests_dir=tests_dir,
                src_layout=True,
                mode="existing",
                language="java",
                build_tool=build_tool,
            )
        src, tst = _java_dirs(derived)
        return TargetLayout(derived, src, tst, True, "existing", language="java", build_tool=build_tool)
    src, tst = _java_dirs(derived)
    return TargetLayout(derived, src, tst, True, "new", language="java", build_tool=build_tool)


def detect_kotlin_layout(root: Path) -> tuple[str, str, str] | None:
    """If a Kotlin source tree holds a package, return ``(package, source_dir, tests_dir)``.

    Both Gradle spellings are searched, in the order a Kotlin project actually uses them:
    ``src/main/kotlin`` for a Kotlin/JVM project, and ``src/main/java`` because Android and
    every JVM project converted from Java keeps Kotlin files in the Java source root — the
    validation app has 263 ``.kt`` files and not one of them is under ``src/main/kotlin``.
    Looking only in the obvious place would find nothing in the common case.

    The tests directory mirrors whichever root matched, so a ``src/main/kotlin`` project
    gets ``src/test/kotlin`` and an Android-shaped one gets ``src/test/java``.
    """
    for source_root in ("kotlin", "java"):
        main = root / "src" / "main" / source_root
        if not main.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(main):
            if any(f.endswith(".kt") for f in files):
                rel = Path(dirpath).relative_to(main)
                package = str(rel).replace(os.sep, ".")
                return (
                    package,
                    f"src/main/{source_root}/{rel.as_posix()}",
                    f"src/test/{source_root}/{rel.as_posix()}",
                )
    return None


def _kotlin_dirs(package: str) -> tuple[str, str]:
    """Greenfield dirs. A new project gets ``src/main/kotlin`` — the Kotlin/JVM default —
    even though a brownfield one is more often under ``src/main/java``."""
    path = package.replace(".", "/")
    return f"src/main/kotlin/{path}", f"src/test/kotlin/{path}"


def detect_jvm_test_library(root: Path) -> str:
    """Which assertion library this project's tests already use.

    ``kotlin("test")`` in any build script means ``kotlin.test`` is on the test classpath;
    a ``junit-jupiter`` dependency without it means JUnit 5 and *not* ``kotlin.test``.
    The distinction is load-bearing for brownfield codegen: the Spring validation repo
    declares ``junit-jupiter-api`` and no ``kotlin("test")``, so a generated
    ``import kotlin.test.Test`` fails to compile there.
    """
    texts: list[str] = []
    scripts = [*root.rglob("build.gradle.kts"), *root.rglob("build.gradle")]
    # Android's dominant idiom hides the test dependency from every build script that uses it:
    # the validation app declares `kotlin("test")` once, inside a convention plugin in
    # `build-logic/`, and each module then applies `id("<product>.android.library")`.
    # Reading only `build.gradle*` sees no test library anywhere in a repo that has one.
    for conventions in (root / "build-logic", root / "buildSrc"):
        if conventions.is_dir():
            scripts.extend(conventions.rglob("*.kt"))
    for script in scripts:
        try:
            texts.append(script.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    joined = "\n".join(texts)
    if 'kotlin("test")' in joined or "kotlin-test" in joined:
        return "kotlin.test"
    if "junit-jupiter" in joined or "junit.jupiter" in joined:
        return "junit5"
    return ""


def _module_layout(
    root: Path, *, package_name: str | None, derived: str, build_tool: str, test_library: str
) -> TargetLayout | None:
    """Placement inside a multi-module Gradle build (P9, D13), or ``None`` if not one.

    Returns a layout whose ``module`` is ``""`` when the build *is* multi-module but no module
    claims the package. That is not the same as "not applicable": it is an unresolved placement,
    and ``kotlin_project_error`` turns it into an actionable message naming the candidates.
    Guessing instead would write a repository into a module nobody would look in, where it
    compiles and its test passes.
    """
    from orchestrator.sdlc import android

    modules = [m for m in android.gradle_modules(root) if m != root]
    if not modules:
        return None
    target = package_name or derived
    module = android.module_for_package(root, target)
    if module is None and package_name is None:
        # No package was asked for and the repo-derived one matches nothing. A build with a
        # single Kotlin module still has exactly one honest answer; more than one does not.
        with_kotlin = [m for m in modules if android.base_package(m)]
        module = with_kotlin[0] if len(with_kotlin) == 1 else None
        if module is not None:
            # …and the package is that module's, not the repo-derived one. Found in
            # review: falling back to the single Kotlin module while keeping the
            # derived name wrote `app/src/main/java/org/example/myrepo` into a module
            # rooted at `com.acme.app` — a package invented for a brownfield repo,
            # which is exactly what D13/P9 says the placement must never do. Every
            # placement test passed `--package-name` explicitly, so nothing covered
            # the default path this fallback exists to serve.
            target = android.base_package(module)
    if module is None:
        return TargetLayout(
            package_name=target,
            source_dir="",
            tests_dir="",
            src_layout=True,
            mode="existing",
            language="kotlin",
            build_tool=build_tool,
            test_library=test_library,
        )
    rel = module.relative_to(root).as_posix()
    placed = android.package_dir(module, target)
    if placed is None:
        # The module was matched on a package prefix, so the exact package is new. Spell the new
        # directories inside the source root the module already uses rather than the Kotlin/JVM
        # default: an Android module keeps Kotlin under `src/main/java`, and a second root would
        # compile only if the build script also declared it.
        base = android.base_package(module)
        existing_dirs = android.package_dir(module, base) if base else None
        source_root = "java"
        if existing_dirs is not None:
            source_root = existing_dirs[0].split("/")[2]
        path = target.replace(".", "/")
        placed = (f"src/main/{source_root}/{path}", f"src/test/{source_root}/{path}")
    source_dir, tests_dir = placed
    # Per module, not per repository. In the validation app `core/data` gets `kotlin("test")`
    # from a convention plugin while `core/model`, a plain `id("kotlin")` library in the same
    # build, declares no test dependency at all — so one repo-wide answer compiles in the first
    # and fails with `Unresolved reference: test` in the second.
    return TargetLayout(
        package_name=target,
        source_dir=f"{rel}/{source_dir}",
        tests_dir=f"{rel}/{tests_dir}",
        src_layout=True,
        mode="existing",
        language="kotlin",
        build_tool=build_tool,
        test_library=android.module_test_library(root, module),
        module=rel,
        android=android.is_android_module(module),
    )


def _resolve_kotlin_layout(
    root: Path, *, mode: str, package_name: str | None, repo: str | None
) -> TargetLayout:
    """Where Kotlin code goes (P8, D13; multi-module placement P9). Gradle always — Kotlin has
    no Maven era."""
    existing = detect_kotlin_layout(root)
    test_library = detect_jvm_test_library(root)
    derived = package_name or derive_java_package(repo or str(root))
    # Kotlin projects are Gradle projects. `_detect_build_tool` can still say "maven" for a
    # polyglot repo with a pom.xml, and that is worth keeping rather than overriding: the runner
    # needs to know, and a Kotlin module inside a Maven build is a real if rare shape.
    build_tool = _detect_build_tool(root) or "gradle"
    if mode != "new":
        if existing is not None:
            pkg, source_dir, tests_dir = existing
            return TargetLayout(
                package_name=package_name or pkg,
                source_dir=source_dir,
                tests_dir=tests_dir,
                src_layout=True,
                mode="existing",
                language="kotlin",
                build_tool=build_tool,
                test_library=test_library,
            )
        # No sources at the root. Either this is a multi-module build — the Android shape, where
        # every source file lives in a submodule — or an empty repo to scaffold into.
        placed = _module_layout(
            root,
            package_name=package_name,
            derived=derived,
            build_tool=build_tool,
            test_library=test_library,
        )
        if placed is not None:
            return placed
        if mode == "existing":
            src, tst = _kotlin_dirs(derived)
            return TargetLayout(
                derived,
                src,
                tst,
                True,
                "existing",
                language="kotlin",
                build_tool=build_tool,
                test_library=test_library,
            )
    src, tst = _kotlin_dirs(derived)
    return TargetLayout(
        derived,
        src,
        tst,
        True,
        "new",
        language="kotlin",
        build_tool=build_tool,
        test_library=test_library,
    )


def derive_npm_package(name: str) -> str:
    """Repo name → a valid npm package name (lowercase, hyphen-separated).

    ``Example-Service.`` → ``example-service``. npm names
    are url-safe lowercase and may contain hyphens (unlike Python's underscores)."""
    base = name.rstrip("/").rsplit("/", 1)[-1]
    if base.endswith(".git"):
        base = base[: -len(".git")]
    slug = re.sub(r"[^0-9a-z]+", "-", base.lower()).strip("-")
    return slug or _FALLBACK_PACKAGE


def _detect_node_pm(root: Path) -> str:
    """Package manager from the lockfile (pnpm > yarn > npm); default ``npm``."""
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (root / "yarn.lock").is_file():
        return "yarn"
    return "npm"


def _read_package_json_name(root: Path) -> str | None:
    pkg = root / "package.json"
    if not pkg.is_file():
        return None
    try:
        import json

        name = json.loads(pkg.read_text(encoding="utf-8")).get("name")
    except (OSError, ValueError):
        return None
    return name if isinstance(name, str) and name else None


def detect_typescript_layout(root: Path) -> tuple[str, str, str] | None:
    """If the repo is a recognizable Node/TS project (has ``package.json``), return
    ``(package_name, source_dir, tests_dir)``. Tests are co-located with source
    (``*.test.ts`` beside the code), so ``tests_dir == source_dir``."""
    if not (root / "package.json").is_file():
        return None
    name = _read_package_json_name(root) or derive_npm_package(str(root))
    source_dir = "src" if (root / "src").is_dir() else "."
    return name, source_dir, source_dir


def _resolve_typescript_layout(
    root: Path, *, mode: str, package_name: str | None, repo: str | None
) -> TargetLayout:
    existing = detect_typescript_layout(root)
    pm = _detect_node_pm(root)
    derived = package_name or derive_npm_package(repo or str(root))
    if mode == "existing" or (mode == "auto" and existing is not None):
        if existing is not None:
            pkg, source_dir, tests_dir = existing
            return TargetLayout(
                package_name=package_name or pkg,
                source_dir=source_dir,
                tests_dir=tests_dir,
                src_layout=source_dir.startswith("src"),
                mode="existing",
                language="typescript",
                build_tool=pm,
            )
        return TargetLayout(derived, "src", "src", True, "existing", language="typescript", build_tool=pm)
    return TargetLayout(derived, "src", "src", True, "new", language="typescript", build_tool=pm)


def derive_csharp_namespace(name: str) -> str:
    """Repo name → a PascalCase .NET root namespace / project name.

    ``example-service.`` → ``ExampleService``. .NET names are PascalCase
    identifiers; map every run of non-alphanumerics to a word boundary, capitalize
    each word, and guard against empty / digit-leading results."""
    base = name.rstrip("/").rsplit("/", 1)[-1]
    if base.endswith(".git"):
        base = base[: -len(".git")]
    words = [w for w in re.split(r"[^0-9a-zA-Z]+", base) if w]
    slug = "".join(w[:1].upper() + w[1:] for w in words)
    if not slug:
        return "App"
    if slug[0].isdigit():
        slug = f"App{slug}"
    return slug


def _csharp_dirs(project: str) -> tuple[str, str]:
    """Source + test dirs for a greenfield C# project (src + xUnit test project)."""
    return f"src/{project}", f"tests/{project}.Tests"


def detect_csharp_layout(root: Path) -> tuple[str, str, str] | None:
    """If the repo is a recognizable .NET project, return ``(project, source_dir,
    tests_dir)``. The project is the first ``*.csproj`` whose name doesn't look
    like a test project; ``source_dir`` is that project's directory. Tests go to a
    sibling ``<Project>.Tests`` project when one exists, else a derived one."""
    csprojs = sorted(root.rglob("*.csproj"))
    if not csprojs:
        return None
    # Prefer the first non-test project as the source project.
    src_proj = next(
        (p for p in csprojs if not p.stem.lower().endswith(("test", "tests"))),
        csprojs[0],
    )
    project = src_proj.stem
    source_dir = src_proj.parent.relative_to(root).as_posix()
    test_proj = next(
        (p for p in csprojs if p.stem.lower().endswith(("test", "tests"))),
        None,
    )
    tests_dir = (
        test_proj.parent.relative_to(root).as_posix() if test_proj is not None else _csharp_dirs(project)[1]
    )
    return project, source_dir, tests_dir


def _resolve_csharp_layout(
    root: Path, *, mode: str, package_name: str | None, repo: str | None
) -> TargetLayout:
    existing = detect_csharp_layout(root)
    derived = package_name or derive_csharp_namespace(repo or str(root))
    if mode == "existing" or (mode == "auto" and existing is not None):
        if existing is not None:
            pkg, source_dir, tests_dir = existing
            return TargetLayout(
                package_name=package_name or pkg,
                source_dir=source_dir,
                tests_dir=tests_dir,
                src_layout=source_dir.startswith("src"),
                mode="existing",
                language="csharp",
                build_tool="dotnet",
            )
        src, tst = _csharp_dirs(derived)
        return TargetLayout(derived, src, tst, True, "existing", language="csharp", build_tool="dotnet")
    src, tst = _csharp_dirs(derived)
    return TargetLayout(derived, src, tst, True, "new", language="csharp", build_tool="dotnet")


def _detect_c_build_tool(root: Path) -> str:
    if (root / "CMakeLists.txt").is_file():
        return "cmake"
    if (root / "meson.build").is_file():
        return "meson"
    if (root / "Makefile").is_file() or (root / "makefile").is_file():
        return "make"
    return ""


# Source globs that mark a directory as a native (C / C++) source dir.
_C_SRC_GLOBS = ("*.c",)
_CPP_SRC_GLOBS = ("*.cpp", "*.cc", "*.cxx")


def _detect_native_layout(root: Path, src_globs: tuple[str, ...]) -> tuple[str, str, str] | None:
    """Shared C/C++ layout detection: a recognizable CMake/Meson/Make project →
    ``(package, source_dir, tests_dir)``. ``source_dir`` is ``src`` when it holds
    matching source files, else the repo root."""
    if _detect_c_build_tool(root) == "":
        return None
    package = _read_cmake_project_name(root) or derive_package_name(str(root))
    src = root / "src"
    source_dir = "src" if src.is_dir() and any(src.glob(g) for g in src_globs) else "."
    tests_dir = "tests" if (root / "tests").is_dir() else source_dir
    return package, source_dir, tests_dir


def detect_c_layout(root: Path) -> tuple[str, str, str] | None:
    """``(package, source_dir, tests_dir)`` for a recognizable C (CMake/Meson/Make) repo."""
    return _detect_native_layout(root, _C_SRC_GLOBS)


def detect_cpp_layout(root: Path) -> tuple[str, str, str] | None:
    """``(package, source_dir, tests_dir)`` for a recognizable C++ (CMake/Meson) repo."""
    return _detect_native_layout(root, _CPP_SRC_GLOBS)


def _read_cmake_project_name(root: Path) -> str | None:
    cmake = root / "CMakeLists.txt"
    if not cmake.is_file():
        return None
    m = re.search(r"project\s*\(\s*([A-Za-z_][\w-]*)", cmake.read_text(encoding="utf-8", errors="replace"))
    return m.group(1) if m else None


def _resolve_native_layout(
    root: Path,
    *,
    mode: str,
    package_name: str | None,
    repo: str | None,
    language: str,
    detect: Callable[[Path], tuple[str, str, str] | None],
) -> TargetLayout:
    existing = detect(root)
    derived = package_name or derive_package_name(repo or str(root))
    build_tool = _detect_c_build_tool(root) or "cmake"
    if mode == "existing" or (mode == "auto" and existing is not None):
        if existing is not None:
            pkg, source_dir, tests_dir = existing
            return TargetLayout(
                package_name=package_name or pkg,
                source_dir=source_dir,
                tests_dir=tests_dir,
                src_layout=source_dir.startswith("src"),
                mode="existing",
                language=language,
                build_tool=build_tool,
            )
        return TargetLayout(
            derived, "src", "tests", True, "existing", language=language, build_tool=build_tool
        )
    return TargetLayout(derived, "src", "tests", True, "new", language=language, build_tool="cmake")


def _resolve_c_layout(root: Path, *, mode: str, package_name: str | None, repo: str | None) -> TargetLayout:
    return _resolve_native_layout(
        root, mode=mode, package_name=package_name, repo=repo, language="c", detect=detect_c_layout
    )


def detect_sql_layout(root: Path) -> bool:
    """True if the repo already has a migrations directory holding ``.sql`` files."""
    for name in ("migrations", "migration", "db/migrate", "db/migrations"):
        d = root / name
        if d.is_dir() and any(d.glob("*.sql")):
            return True
    return False


def _resolve_sql_layout(root: Path, *, mode: str, package_name: str | None, repo: str | None) -> TargetLayout:
    """SQL greenfield/brownfield layout: ordered DDL under ``migrations/``.

    There is no source/test split — generated migrations *are* the artifact, and
    "tests" run by applying them to an ephemeral database. ``build_tool`` carries
    the SQL **dialect** (default ``postgres``), which threads into the SQL test
    environment/runner (transpiled to SQLite on apply)."""
    derived = package_name or derive_package_name(repo or str(root))
    dialect = "postgres"
    existing = detect_sql_layout(root)
    mode_out = "existing" if (mode == "existing" or (mode == "auto" and existing)) else "new"
    return TargetLayout(
        package_name=derived,
        source_dir="migrations",
        tests_dir="migrations",
        src_layout=True,
        mode=mode_out,
        language="sql",
        build_tool=dialect,
    )


def _resolve_cpp_layout(root: Path, *, mode: str, package_name: str | None, repo: str | None) -> TargetLayout:
    return _resolve_native_layout(
        root, mode=mode, package_name=package_name, repo=repo, language="cpp", detect=detect_cpp_layout
    )


def derive_go_module(name: str) -> str:
    """Repo name → a valid Go module path that is also a valid package identifier.

    ``Example-Service.`` → ``exampleservice``. A single lowercase word doubles as the
    greenfield ``go.mod`` module path and the ``package`` clause; guarded against empty,
    digit-leading, and reserved-keyword results."""
    base = name.rstrip("/").rsplit("/", 1)[-1]
    if base.endswith(".git"):
        base = base[: -len(".git")]
    slug = re.sub(r"[^0-9a-z]+", "", base.lower())
    if not slug:
        slug = _FALLBACK_PACKAGE
    if slug[0].isdigit():
        slug = f"pkg{slug}"
    if slug in _GO_KEYWORDS:
        slug = f"{slug}pkg"
    return slug


def _read_go_module(root: Path) -> str | None:
    """The module path from ``go.mod``'s ``module`` directive, if present."""
    gomod = root / "go.mod"
    if not gomod.is_file():
        return None
    m = re.search(r"^\s*module\s+(\S+)", gomod.read_text(encoding="utf-8", errors="replace"), re.M)
    return m.group(1) if m else None


# Path segments whose packages are never a good brownfield placement target (example /
# demo apps, fixtures). ``main`` packages are filtered separately (by package clause).
_GO_SKIP_DIR_SEGMENTS = frozenset({"demo", "example", "examples", "testdata", "vendor"})


def _go_package_of_dir(d: Path) -> str:
    """The ``package`` clause of a directory's first non-test ``.go`` file (``""`` if none)."""
    for f in sorted(d.glob("*.go")):
        if f.name.endswith("_test.go"):
            continue
        m = re.search(r"^\s*package\s+(\w+)", f.read_text(encoding="utf-8", errors="replace"), re.M)
        if m:
            return m.group(1)
    return ""


def _nearest_go_module_dir(start: Path, root: Path) -> Path | None:
    """The nearest ancestor of ``start`` (inclusive, within ``root``) holding a ``go.mod``."""
    d = start
    while True:
        if (d / "go.mod").is_file():
            return d
        if d == root:
            return None
        d = d.parent
        if d != root and root not in d.parents:
            return None


def _pick_go_source_dir(root: Path) -> tuple[str, str] | None:
    """Pick a brownfield placement: ``(source_dir, package_clause)`` for a **library**
    package (not ``package main``), preferring one in the repo-**root** module so the
    generated code lands where ``go build``/``go test`` on that module actually reaches it
    (a ``main`` / ``demo`` / nested-module dir is what produced 4.4's false green). Skips
    demo/example/testdata trees. Deterministic: root-module first, then shallowest, then path."""
    root_mod = root if (root / "go.mod").is_file() else None
    best_key: tuple[int, int, str] | None = None
    best: tuple[str, str] | None = None
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_IGNORE_DIRS and not d.startswith(".")]
        rel = Path(dirpath).relative_to(root)
        if any(seg in _GO_SKIP_DIR_SEGMENTS for seg in rel.parts):
            continue
        if not any(f.endswith(".go") and not f.endswith("_test.go") for f in files):
            continue
        pkg = _go_package_of_dir(Path(dirpath))
        if not pkg or pkg == "main":
            continue
        in_root = _nearest_go_module_dir(Path(dirpath), root) == root_mod
        key = (0 if in_root else 1, len(rel.parts), rel.as_posix())
        if best_key is None or key < best_key:
            best_key = key
            best = (rel.as_posix() if rel.parts else ".", pkg)
    return best


def detect_go_layout(root: Path) -> tuple[str, str, str] | None:
    """``(package_clause, source_dir, tests_dir)`` for a recognizable Go module (has
    ``go.mod``). ``package_clause`` is the **existing** ``package`` name of the chosen
    placement dir (so brownfield codegen matches it, not the module's last path element —
    the 4.4 package-conflict bug). Go tests are co-located, so ``tests_dir == source_dir``."""
    if not (root / "go.mod").is_file():
        return None
    picked = _pick_go_source_dir(root)
    if picked is not None:
        source_dir, pkg = picked
        return pkg, source_dir, source_dir
    # go.mod but only main packages / no lib package: default to the module root.
    root_pkg = (
        _go_package_of_dir(root) or (_read_go_module(root) or derive_go_module(str(root))).rsplit("/", 1)[-1]
    )
    return root_pkg, ".", "."


def _resolve_go_layout(root: Path, *, mode: str, package_name: str | None, repo: str | None) -> TargetLayout:
    """Go layout. Greenfield = a single package at the module root (``go.mod`` + `.go`
    files beside it), the simplest module ``go build ./...`` / ``go test ./...`` accept.
    Brownfield = a library package in the root module, using that dir's existing ``package``
    clause. Co-located tests → ``tests_dir == source_dir``. ``build_tool`` is ``go``."""
    existing = detect_go_layout(root)
    derived = package_name or derive_go_module(repo or str(root))
    if mode == "existing" or (mode == "auto" and existing is not None):
        if existing is not None:
            pkg_clause, source_dir, tests_dir = existing
            return TargetLayout(
                package_name=package_name or pkg_clause,
                source_dir=source_dir,
                tests_dir=tests_dir,
                src_layout=False,
                mode="existing",
                language="go",
                build_tool="go",
            )
        return TargetLayout(derived, ".", ".", False, "existing", language="go", build_tool="go")
    return TargetLayout(derived, ".", ".", False, "new", language="go", build_tool="go")


def detect_php_layout(root: Path) -> tuple[str, str, str] | None:
    """Composer, PHPUnit config, or loose PHP source marks an existing project."""
    from orchestrator.sdlc.php import php_files, read_composer, read_phpunit_config, safe_relative

    manifest = read_composer(root)
    config = read_phpunit_config(root)
    if manifest is None and config.path is None and next(php_files(root), None) is None:
        return None
    package, source = "", "."
    if manifest is not None:
        autoload = manifest.get("autoload", {})
        psr4 = autoload.get("psr-4", {}) if isinstance(autoload, dict) else {}
        if isinstance(psr4, dict) and psr4:
            namespace, directory = next(iter(psr4.items()))
            if isinstance(directory, list):
                directory = directory[0] if directory else "."
            if isinstance(directory, str):
                source = safe_relative(root, directory)
                package = namespace.rstrip("\\")
    return package, source, config.tests_dir


def _resolve_php_layout(root: Path, *, mode: str, package_name: str | None, repo: str | None) -> TargetLayout:
    from orchestrator.sdlc.php import read_phpunit_config

    existing = detect_php_layout(root)
    if mode == "existing" or (mode == "auto" and existing is not None):
        package, source, tests = existing or ("", ".", "tests")
        config = read_phpunit_config(root)
        return TargetLayout(
            package_name=package_name or package,
            source_dir=source,
            tests_dir=tests,
            src_layout=source.startswith("src"),
            mode="existing",
            language="php",
            build_tool="composer" if (root / "composer.json").is_file() else "phar",
            test_suffix=config.suffix,
            test_bootstrap=config.bootstrap,
        )
    return TargetLayout(
        package_name or "App",
        "src",
        "tests",
        True,
        "new",
        language="php",
        build_tool="composer",
        test_bootstrap="vendor/autoload.php",
    )


def is_effectively_empty(root: Path) -> bool:
    """True when the worktree holds no source the model could extend (a fresh
    clone of an empty repo is just ``.git`` + maybe a README/LICENSE). Used to
    warn when ``new`` scaffolds into a repo that already has loose files."""
    for child in root.iterdir():
        if child.name.startswith(".") or child.name in DEFAULT_IGNORE_DIRS:
            continue
        if child.is_file() and child.suffix.lower() in {".md", ".rst", ".txt"}:
            continue  # README / LICENSE / docs text don't count as source
        return False
    return True


def resolve_layout(
    root: Path | str,
    *,
    mode: str = "auto",
    package_name: str | None = None,
    repo: str | None = None,
    src_layout: bool = True,
    language: str = "python",
) -> TargetLayout:
    """Resolve the target layout for a worktree.

    ``mode`` ∈ {``auto``, ``new``, ``existing``}. ``language`` ∈ {``python``,
    ``java``, ``typescript``, ``csharp``} selects the layout convention (Python
    ``src/<pkg>``; Java ``src/main/java/<pkg path>`` + Maven/Gradle; TypeScript
    ``src/`` with co-located ``*.test.ts`` + npm/yarn/pnpm; C# ``src/<Project>`` +
    ``tests/<Project>.Tests`` xUnit project, built with ``dotnet``).
    ``package_name`` overrides the
    derived name; ``repo`` (clone URL) seeds derivation. Deterministic; the caller
    scaffolds when ``mode == "new"``.
    """
    from orchestrator.sdlc.toolchains import get_toolchain

    return cast(
        TargetLayout,
        get_toolchain(language).layout(
            Path(root), mode=mode, package_name=package_name, repo=repo, src_layout=src_layout
        ),
    )


def _resolve_python_layout(
    root_path: Path, *, mode: str, package_name: str | None, repo: str | None, src_layout: bool = True
) -> TargetLayout:
    existing = detect_existing_package(root_path)
    derived = package_name or derive_package_name(repo or str(root_path))

    if mode == "existing" or (mode == "auto" and existing is not None):
        if existing is not None:
            pkg, source_dir = existing
            return TargetLayout(
                package_name=package_name or pkg,
                source_dir=source_dir,
                tests_dir="tests",
                src_layout=source_dir.startswith("src/"),
                mode="existing",
            )
        # mode=existing but nothing recognizable: best-effort defaults, no scaffold.
        source_dir = f"src/{derived}" if src_layout else derived
        return TargetLayout(derived, source_dir, "tests", src_layout, "existing")

    # mode == "new", or auto with no existing package → scaffold a fresh structure.
    source_dir = f"src/{derived}" if src_layout else derived
    return TargetLayout(derived, source_dir, "tests", src_layout, "new")


__all__ = [
    "TargetLayout",
    "derive_csharp_namespace",
    "derive_go_module",
    "derive_java_package",
    "derive_npm_package",
    "derive_package_name",
    "detect_c_layout",
    "detect_cpp_layout",
    "detect_csharp_layout",
    "detect_existing_package",
    "detect_go_layout",
    "detect_php_layout",
    "detect_java_layout",
    "detect_typescript_layout",
    "is_effectively_empty",
    "resolve_layout",
]


def detect_perl_layout(root: Path, package_name: str | None = None) -> tuple[str, str, str] | None:
    from orchestrator.sdlc.perl import distributions, package_in, perl_files

    candidates = distributions(root)
    if any((dist / "lib").is_symlink() for dist in candidates):
        raise ValueError("Perl lib/ roots must not be symlinks; select a distribution with a real lib/ tree.")
    matches = [
        (dist, file, package_in(file)) for dist in candidates for file in perl_files(dist / "lib", (".pm",))
    ]
    if package_name:
        selected = [
            m for m in matches if m[2] == package_name or (m[2] or "").startswith(package_name + "::")
        ]
        if selected:
            matches = selected
            candidates = sorted({m[0] for m in selected})
    if len(candidates) > 1:
        raise ValueError("Several Perl distributions found; select the existing package with --package-name.")
    if not candidates:
        return None
    dist = candidates[0]
    named = [(file, name) for owner, file, name in matches if owner == dist and name]
    name = str(min(named, key=lambda item: (len(item[0].parts), str(item[0])))[1]) if named else "App"
    return name, (dist / "lib").relative_to(root).as_posix(), (dist / "t").relative_to(root).as_posix()


def _resolve_perl_layout(
    root: Path, *, mode: str, package_name: str | None, repo: str | None
) -> TargetLayout:
    from orchestrator.sdlc.perl import package_name as validate_name

    if package_name:
        validate_name(package_name)
    existing = detect_perl_layout(root, package_name) if mode != "new" else None
    if mode == "existing" and existing is None:
        raise ValueError(
            "No Perl distribution found: expected lib/ or a cpanfile/Makefile.PL/Build.PL/dist.ini marker."
        )
    if existing is not None:
        name, source, tests = existing
        return TargetLayout(package_name or name, source, tests, False, "existing", language="perl")
    derived = "::".join(
        part.capitalize() for part in derive_package_name(repo or str(root)).split("_") if part
    )
    return TargetLayout(validate_name(package_name or derived), "lib", "t", False, "new", language="perl")
