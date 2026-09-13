"""Real Perl/prove green, assertion-red and syntax-red proof, including clean-checkout reruns."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator.sdlc.layout import resolve_layout
from orchestrator.sdlc.preflight import PerlPreflightRunner
from orchestrator.sdlc.scaffold import scaffold
from orchestrator.sdlc.testenv import perl_toolchain_available
from orchestrator.sdlc.testrunner import ProveTestRunner

pytestmark = pytest.mark.skipif(not perl_toolchain_available(), reason="perl/prove unavailable")


async def test_perl_real_green_red_and_clean_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "dist"
    repo.mkdir()
    scaffold(repo, resolve_layout(repo, language="perl", package_name="Shop::Cart"))
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    assert (await ProveTestRunner().run(path=str(repo))).passed
    clean = tmp_path / "clean"
    subprocess.run(["git", "clone", "-q", str(repo), str(clean)], check=True)
    proof = subprocess.run(["prove", "-l", "t/"], cwd=clean, capture_output=True, text=True)
    assert proof.returncode == 0, proof.stdout + proof.stderr
    (repo / "t/10-red.t").write_text(
        "use strict; use warnings; use Test::More; is(1, 2, 'real assertion'); done_testing;\n"
    )
    red = await ProveTestRunner().run(path=str(repo))
    assert not red.passed and red.returncode != 0 and "Failed test" in red.output
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "red fixture",
        ],
        check=True,
    )
    clean_red = tmp_path / "clean-red"
    subprocess.run(["git", "clone", "-q", str(repo), str(clean_red)], check=True)
    red_proof = subprocess.run(["prove", "-l", "t/"], cwd=clean_red, capture_output=True, text=True)
    assert red_proof.returncode != 0
    (repo / "lib/Shop/Cart.pm").write_text("package Shop::Cart; use strict; my $x = ; 1;\n")
    syntax = await ProveTestRunner().run(path=str(repo))
    assert not syntax.passed and "syntax error" in syntax.output
    assert "# (.) prove" not in syntax.output
    assert not (await PerlPreflightRunner().run(path=str(repo))).passed


async def test_perl_real_nested_suite_cannot_hide_red(tmp_path: Path) -> None:
    scaffold(tmp_path, resolve_layout(tmp_path, language="perl", package_name="Example"))
    (tmp_path / "t/nested").mkdir()
    (tmp_path / "t/nested/red.t").write_text("use Test::More; ok(0, 'nested failure'); done_testing;\n")
    result = await ProveTestRunner().run(path=str(tmp_path))
    assert not result.passed and "nested failure" in result.output
