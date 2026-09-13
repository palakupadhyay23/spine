#!/usr/bin/env python3
"""Measure whether the test suite would catch a bad language-dispatch refactor.

The SDLC package selects a language's layout, scaffold, tool environment, test runner
and codegen guidance through ~48 ``if language == "..."`` branches across five modules.
`perl-codegen-roadmap.md` §5.1 proposes replacing them with one table. That refactor
rewrites the dispatch of **eight working languages** at once, and its stated safety net
was "the existing languages migrate in the same phase, with their tests as the
regression net".

**That net was measured on 2026-09-13 and it caught 4 of 8.** This script is the
measurement, checked in so the roadmap's C-0 exit criterion ("re-run that mutation set
and catch 8 of 8") is a command rather than a description.

Each mutation is a transcription error a language-to-row flattening plausibly makes.
The script applies one, runs the SDLC tests, restores the file, and reports whether
anything failed. A mutation that passes is a behaviour no test pins — exactly the kind
that ships a language which scaffolds and builds but is subtly wrong.

    python scripts/mutate-dispatch.py           # run them all, print a table
    python scripts/mutate-dispatch.py --list    # show the set without running

Not a pytest test on purpose: it edits source files and shells out to the suite per
mutation (about a minute each), which belongs in a deliberate run, not in CI.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SDLC = ROOT / "src" / "orchestrator" / "sdlc"

# The suite a refactorer would trust. `test_php_codegen.py` is listed explicitly: PHP's
# codegen tests live in their own file with their own fixtures, so a pattern match over
# the shared test modules misses them.
TESTS = [
    "tests/sdlc/test_layout.py",
    "tests/sdlc/test_scaffold.py",
    "tests/sdlc/test_testenv.py",
    "tests/sdlc/test_codegen.py",
    "tests/sdlc/test_testrunner.py",
    "tests/sdlc/test_feature_runner.py",
    "tests/sdlc/test_php_codegen.py",
]


@dataclass(frozen=True)
class Mutation:
    """One transcription error, and where it bites.

    ``anchor`` disambiguates a pattern that occurs more than once: the replacement is
    applied to the single occurrence that follows it. PHP needs this — ``testenv.py``
    dispatches on ``language == "php"`` twice, once in ``make_test_environment`` and
    once in ``make_test_runner``, and flattening either one alone
    is a different bug.
    """

    name: str
    path: str
    old: str
    new: str
    anchor: str | None = None
    was: str = ""  # baseline result (2026-09-13), for comparison after C-0


LEGACY_MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        "csharp: lose the target-framework rewrite",
        "feature_runner.py",
        '    if lang == "csharp":\n'
        "        # Target the installed SDK so a greenfield scaffold builds AND runs (a TFM\n"
        "        # with no matching runtime fails at the test host, not at build).\n"
        "        layout = replace(layout, target_framework=detect_dotnet_tfm())\n",
        "",
        was="MISSED",
    ),
    Mutation(
        "c/cpp: give C++ C's availability probe",
        "feature_runner.py",
        'cmake_ok = cpp_toolchain_available if lang == "cpp" else c_toolchain_available',
        "cmake_ok = c_toolchain_available",
        was="MISSED",
    ),
    Mutation(
        "c/cpp: force cmake on a Meson brownfield",
        "feature_runner.py",
        'build_tool = layout.build_tool if layout.mode == "existing" else "cmake"',
        'build_tool = "cmake"',
        was="MISSED",
    ),
    Mutation(
        "codegen: drop PHP's guidance branch",
        "codegen.py",
        '        if layout.language == "php":',
        '        if layout.language == "php" and False:',
        was="MISSED",
    ),
    Mutation(
        "layout: TypeScript row falls through",
        "layout.py",
        '    if language == "typescript":',
        '    if language == "typescript" and False:',
        was="caught",
    ),
    Mutation(
        "scaffold: Go row falls through",
        "scaffold.py",
        '    elif layout.language == "go":',
        '    elif layout.language == "go" and False:',
        was="caught",
    ),
    Mutation(
        "codegen: drop SQL's guidance branch",
        "codegen.py",
        '        if layout.language == "sql":',
        '        if layout.language == "sql" and False:',
        was="caught",
    ),
    Mutation(
        "testenv: PHP test environment falls through",
        "testenv.py",
        '    if language == "php":',
        '    if language == "php" and False:',
        anchor="def make_test_environment",
        was="caught",  # the first run could not locate it; the anchor above fixes that
    ),
)


# Same eight behavioral errors, relocated to the registry by C-1. Keep the original
# definitions for inspecting/replaying the pre-registry baseline; no mutation was removed.
_REGISTRY_EDITS = (
    ("prepare_layout=_dotnet_layout", "prepare_layout=_identity_layout"),
    ('available=_probe("cpp_toolchain_available")', 'available=_probe("c_toolchain_available")'),
    ('build_tool = layout.build_tool if layout.mode == "existing" else "cmake"', 'build_tool = "cmake"'),
    ('"php_guidance"', '"python_guidance"'),
    ('_layout("_resolve_typescript_layout")', '_layout("_resolve_python_layout", python=True)'),
    ('_scaffold("_go_files")', '_scaffold("_python_files")'),
    ('"sql_guidance"', '"python_guidance"'),
    ('_environment("PhpToolEnvironment")', "_python_environment"),
)
MUTATIONS = (
    tuple(
        Mutation(m.name, "toolchains.py", old, new, was=m.was)
        for m, (old, new) in zip(LEGACY_MUTATIONS, _REGISTRY_EDITS, strict=True)
    )
    if (SDLC / "toolchains.py").is_file()
    else LEGACY_MUTATIONS
)


def _ci_extras() -> list[str]:
    """Keep the measurement's child pytest environment identical to CI's sync."""
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    install = workflow.split("uv sync --extra", 1)[1].split("\n\n", 1)[0]
    return [
        part for name in re.findall(r"--extra ([a-z-]+)", "--extra" + install) for part in ("--extra", name)
    ]


def _apply(m: Mutation, text: str) -> str | None:
    """The mutated text, or ``None`` when the pattern is not uniquely locatable."""
    if m.anchor is not None:
        start = text.find(m.anchor)
        if start < 0:
            return None
        head, tail = text[:start], text[start:]
        if tail.count(m.old) < 1:
            return None
        return head + tail.replace(m.old, m.new, 1)
    if text.count(m.old) != 1:
        return None
    return text.replace(m.old, m.new)


def run_one(m: Mutation) -> tuple[str, str]:
    """``(verdict, detail)`` for one mutation, leaving the tree exactly as found."""
    path = SDLC / m.path
    original = path.read_text(encoding="utf-8")
    mutated = _apply(m, original)
    if mutated is None:
        return "SKIP", "pattern not uniquely locatable — the source moved"
    path.write_text(mutated, encoding="utf-8")
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                "uv",
                "run",
                "--frozen",
                *_ci_extras(),
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "-o",
                "addopts=",
                "--no-header",
                *TESTS,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
    finally:
        path.write_text(original, encoding="utf-8")
    summary = next(
        (line for line in reversed(proc.stdout.splitlines()) if "passed" in line or "failed" in line), ""
    )
    return ("CAUGHT" if "failed" in summary else "MISSED"), summary.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="print the set without running it")
    args = ap.parse_args()

    if args.list:
        for i, m in enumerate(MUTATIONS, 1):
            print(f"{i}. [{m.was}] {m.name}  ({m.path})")
        return 0

    print(f"{len(MUTATIONS)} mutations, each against {len(TESTS)} test modules. A few minutes.\n")
    results = []
    for m in MUTATIONS:
        verdict, detail = run_one(m)
        results.append((verdict, m))
        flag = "   " if verdict == "CAUGHT" else "!! "
        print(f"{flag}{verdict:6s} {m.name}\n          {detail}")

    caught = sum(1 for v, _ in results if v == "CAUGHT")
    applied = sum(1 for v, _ in results if v != "SKIP")
    print(f"\ncaught {caught} of {applied} applied ({len(results) - applied} skipped)")
    print("baseline on 2026-09-13: 4 of 8 — perl-codegen-roadmap.md C-0 exits at 8 of 8")
    # Deliberately always 0: this reports a measurement, it does not gate a build.
    return 0


if __name__ == "__main__":
    sys.exit(main())
