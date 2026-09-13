"""Authoritative layout guidance, selected by the codegen toolchain registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.sdlc.layout import TargetLayout


def java_guidance(layout: TargetLayout) -> str:
    return (
        "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
        f"- Java package is `{layout.package_name}`. Put each public class at "
        f"`{layout.source_dir}/<ClassName>.java`, one public class per file, starting "
        f"with `package {layout.package_name};`.\n"
        f"- Put JUnit 5 tests at `{layout.tests_dir}/<ClassName>Test.java` in the same package.\n"
        "- Declare any new dependency in `pom.xml` (edit it); don't invent unrelated paths.\n\n"
    )


def typescript_guidance(layout: TargetLayout) -> str:
    return (
        "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
        f"- Put new modules at `{layout.source_dir}/<name>.ts`. Use ES module "
        "`import`/`export`; import a sibling module by relative path with a `.js` "
        'extension (NodeNext), e.g. `import { x } from "./<name>.js"`.\n'
        f"- Put Vitest tests co-located beside the code as `{layout.tests_dir}/<name>.test.ts`.\n"
        "- Declare any new dependency in `package.json` (edit it); don't invent unrelated paths.\n\n"
    )


def csharp_guidance(layout: TargetLayout) -> str:
    tfm = layout.target_framework or "net8.0"
    return (
        "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
        f"- C# namespace is `{layout.package_name}`. Put each public type at "
        f"`{layout.source_dir}/<TypeName>.cs`, one public type per file, declaring "
        f"`namespace {layout.package_name};` (target {tfm}, nullable enabled).\n"
        f"- Put xUnit tests at `{layout.tests_dir}/<TypeName>Tests.cs` (the test "
        "project already references the source project).\n"
        f"- Declare any new dependency as a `<PackageReference>` in the source "
        "`.csproj` (edit it); don't invent unrelated paths.\n\n"
    )


def c_guidance(layout: TargetLayout) -> str:
    if layout.build_tool == "meson":
        build_line = (
            "- This project uses **Meson** (`meson.build`), which does NOT glob: "
            "register every new file — add new `.c` sources to the library/target "
            "source list, and add an `executable(...)` + `test(...)` for each new "
            f"`{layout.tests_dir}/test_<name>.c`. Edit `meson.build` to do so. "
            "Prefer extending existing files (no `meson.build` change needed) when you can."
        )
    else:
        build_line = (
            "- New `src/*.c` and `tests/*.c` are auto-discovered by CMake's glob; "
            "edit `CMakeLists.txt` only to add an external dependency."
        )
    return (
        "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
        f"- Put implementation at `{layout.source_dir}/<name>.c` and DECLARE its "
        f"public functions in a header `{layout.source_dir}/<name>.h` (with an "
        "`#ifndef`/`#define` guard); C11, standard library only.\n"
        f"- Put tests at `{layout.tests_dir}/test_<name>.c` — each a standalone "
        "`int main(void)` returning non-zero on failure, `#include`-ing the "
        f"header from `{layout.source_dir}/`.\n"
        f"{build_line} Don't invent unrelated paths.\n\n"
    )


def cpp_guidance(layout: TargetLayout) -> str:
    meson = layout.build_tool == "meson"
    build_line = (
        "- Meson (`meson.build`) does NOT glob: register new `.cpp` sources + an "
        "`executable()`+`test()` per new `tests/*.cpp` in `meson.build`."
        if meson
        else "- New `src/*.cpp` and `tests/*.cpp` are auto-discovered by CMake's glob; "
        "edit `CMakeLists.txt` only to add a dependency."
    )
    return (
        "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
        f"- Declare classes/functions in `{layout.source_dir}/<name>.hpp` (include "
        f"guard or `#pragma once`) and define them in `{layout.source_dir}/<name>.cpp`; "
        "modern C++17, RAII, standard library only.\n"
        f"- Put tests at `{layout.tests_dir}/test_<name>.cpp` — each a standalone "
        "`int main()` returning non-zero on failure, `#include`-ing the header from "
        f"`{layout.source_dir}/`.\n"
        f"{build_line} Don't invent unrelated paths.\n\n"
    )


def php_guidance(layout: TargetLayout) -> str:
    return (
        "PROJECT LAYOUT (authoritative):\n"
        f"- PHP {layout.mode} project, dependencies via {layout.build_tool}.\n"
        f"- Source directory: `{layout.source_dir}`; "
        f"namespace: `{layout.package_name or '(global; no namespace)'}`.\n"
        f"- Tests: `{layout.tests_dir}/<Name>{layout.test_suffix}`; class name matches filename.\n"
        f"- Bootstrap: `{layout.test_bootstrap or '(none; use require_once with __DIR__)'}`.\n"
        "- Preserve existing namespaces, require_once imports and file naming. "
        "Use strict_types only in greenfield. New files end in .php.\n\n"
    )


def go_guidance(layout: TargetLayout) -> str:
    pkg = layout.package_name.rstrip("/").rsplit("/", 1)[-1]
    loc = "the module root" if layout.source_dir == "." else f"`{layout.source_dir}/`"
    prefix = "" if layout.source_dir == "." else f"{layout.source_dir}/"
    return (
        "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
        f"- Put new Go source at `{prefix}<name>.go` in {loc}; every file MUST start "
        f"with `package {pkg}` (match the other files already in that directory).\n"
        f"- Put tests co-located beside the code as `{prefix}<name>_test.go` "
        f"(same `package {pkg}`), using the standard `testing` package.\n"
        "- Declare any new dependency in `go.mod` (edit it); standard library only "
        "otherwise. Don't invent unrelated paths.\n\n"
    )


def sql_guidance(layout: TargetLayout) -> str:
    return (
        "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
        f"- Target SQL dialect: **{layout.build_tool}**. Write standard DDL for it.\n"
        f"- Put migration files under `{layout.source_dir}/` named with a zero-padded "
        f"order prefix, e.g. `{layout.source_dir}/001_<feature>.sql`; they apply in "
        "filename order (define referenced tables before they are referenced).\n"
        "- No application code, no test files, no build files — the migration is the "
        "artifact, validated by applying it to a database. Don't invent unrelated paths.\n\n"
    )


def python_guidance(layout: TargetLayout) -> str:
    return (
        "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
        f"- Source package is `{layout.package_name}` under `{layout.source_dir}/`. "
        f"Put new modules at `{layout.source_dir}/<module>.py`.\n"
        f"- Import source as `from {layout.package_name}.<module> import ...` "
        "(the test runner puts the source root on the path).\n"
        f"- Put tests under `{layout.tests_dir}/` as `{layout.tests_dir}/test_<name>.py`.\n"
        f"- Put NEW code and tests under `{layout.source_dir}/` and `{layout.tests_dir}/` "
        "only, and do NOT invent unrelated top-level paths or parallel package trees.\n"
        # The ban used to be absolute — "do NOT create files outside src/ and tests/" —
        # which is right about invented paths and wrong about the repo's own docs. It
        # made every documentation criterion unsatisfiable: the judge required a
        # USER_GUIDE note, this line forbade touching USER_GUIDE.md, and the model
        # obeyed and said so ("outside the allowed src/tests paths so the doc note was
        # not added"). Editing a file the repo already has is not inventing a path.
        "- You MAY edit files that already exist elsewhere in the repo — README, "
        "USER_GUIDE, CHANGELOG, pyproject.toml — when the ticket calls for it. Changing "
        "an existing file is not inventing a path; creating a new top-level one is.\n\n"
    )
