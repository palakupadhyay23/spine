"""Perl machinery contracts, including optional installs and owning-distribution placement."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from orchestrator.sdlc.codegen import LLMCodegenAdapter, _has_testable_source, _is_test_file
from orchestrator.sdlc.conventions import perl_convention_block
from orchestrator.sdlc.feature_runner import _is_test_path, _resolve_language, unsupported_language_error
from orchestrator.sdlc.layout import resolve_layout
from orchestrator.sdlc.perl import include_args, owning_tests
from orchestrator.sdlc.scaffold import scaffold
from orchestrator.sdlc.testenv import (
    PerlToolEnvironment,
    make_test_environment,
    make_test_runner,
    perl_toolchain_available,
)
from orchestrator.sdlc.testrunner import ProveTestRunner


def write(root: Path, name: str, text: str) -> Path:
    file = root / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)
    return file


def test_scaffold_perl_idempotent(tmp_path: Path) -> None:
    layout = resolve_layout(tmp_path, language="perl", package_name="Shop::Cart")
    assert (layout.source_dir, layout.tests_dir, layout.mode) == ("lib", "t", "new")
    assert layout.module_rel_path("Shop::Cart") == "lib/Shop/Cart.pm"
    assert set(scaffold(tmp_path, layout)) == {
        "cpanfile",
        "lib/Shop/Cart.pm",
        "t/00-load.t",
        "README.md",
        ".gitignore",
    }
    assert scaffold(tmp_path, layout) == []
    assert "package Shop::Cart;" in (tmp_path / "lib/Shop/Cart.pm").read_text()


@pytest.mark.parametrize("marker", ["cpanfile", "Makefile.PL", "Build.PL", "dist.ini"])
def test_perl_brownfield_preserves_packaging(tmp_path: Path, marker: str) -> None:
    write(tmp_path, marker, "existing packaging\n")
    write(tmp_path, "lib/Company/Domain.pm", "package Company::Domain;\n1;\n")
    layout = resolve_layout(tmp_path, language="perl")
    assert (layout.package_name, layout.source_dir, layout.tests_dir, layout.mode) == (
        "Company::Domain",
        "lib",
        "t",
        "existing",
    )
    assert (tmp_path / marker).read_text() == "existing packaging\n"
    assert not (tmp_path / "cpanfile").exists() or marker == "cpanfile"


def test_perl_monorepo_selects_package_owner(tmp_path: Path) -> None:
    for name in ("Orders", "Invoices"):
        write(tmp_path, f"packages/{name}/cpanfile", "")
        write(tmp_path, f"packages/{name}/lib/Company/{name}.pm", f"package Company::{name};\n1;\n")
    with pytest.raises(ValueError, match="Several Perl distributions"):
        resolve_layout(tmp_path, language="perl")
    layout = resolve_layout(tmp_path, language="perl", package_name="Company::Invoices")
    assert layout.source_dir == "packages/Invoices/lib"
    assert layout.tests_dir == "packages/Invoices/t"


def test_perl_invalid_package_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid Perl package"):
        resolve_layout(tmp_path, language="perl", package_name="../outside")


@pytest.mark.parametrize(
    "missing, expected", [(None, True), ("cpanm", True), ("perl", False), ("prove", False)]
)
def test_perl_toolchain_available(
    monkeypatch: pytest.MonkeyPatch, missing: str | None, expected: bool
) -> None:
    monkeypatch.setattr(
        "orchestrator.sdlc.testenv.shutil.which", lambda name: None if name == missing else f"/bin/{name}"
    )
    assert perl_toolchain_available() is expected


async def test_perl_missing_cpanm_warns_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    write(tmp_path, "cpanfile", "")
    monkeypatch.setattr(
        "orchestrator.sdlc.testenv.shutil.which", lambda name: None if name == "cpanm" else f"/bin/{name}"
    )
    run = AsyncMock()
    monkeypatch.setattr("orchestrator.sdlc.testrunner._exec_capture", run)
    env = PerlToolEnvironment()
    await env.ensure(tmp_path)
    assert "cpanm is unavailable" in caplog.text
    assert "WARNING" in env.describe()
    run.assert_not_called()


@pytest.mark.parametrize("rc", [0, 1])
async def test_perl_install_is_best_effort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rc: int) -> None:
    write(tmp_path, "cpanfile", "requires 'Example';\n")
    monkeypatch.setattr("orchestrator.sdlc.testenv.shutil.which", lambda name: f"/bin/{name}")
    run = AsyncMock(return_value=(rc, "installer output"))
    monkeypatch.setattr("orchestrator.sdlc.testrunner._exec_capture", run)
    env = PerlToolEnvironment()
    await env.ensure(tmp_path)
    run.assert_awaited_once_with(
        ("/bin/cpanm", "--installdeps", ".", "--notest"), cwd=str(tmp_path.resolve()), timeout=600
    )
    assert ("WARNING" in env.describe()) is bool(rc)


async def test_perl_xs_is_rejected_before_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path, "Native.xs", "/* native */")
    monkeypatch.setattr("orchestrator.sdlc.testenv.shutil.which", lambda name: f"/bin/{name}")
    with pytest.raises(RuntimeError, match="XS builds"):
        await PerlToolEnvironment().ensure(tmp_path)


def test_perl_conventions_are_observed(tmp_path: Path) -> None:
    write(tmp_path, "lib/Widget.pm", "package Widget;\nuse Moo;\n1;\n")
    write(tmp_path, "t/10-widget.t", "use Test2::V0;\ndone_testing;\n")
    text = perl_convention_block(tmp_path, resolve_layout(tmp_path, language="perl"))
    assert "object style: Moo; tests: Test2::V0" in text
    assert "package Widget;" in text
    assert "use strict; use warnings" in text


def test_perl_full_registration_and_source_test_classification(tmp_path: Path) -> None:
    write(tmp_path, "lib/Example.pm", "package Example; 1;\n")
    assert _resolve_language(tmp_path, "auto") == "perl"
    assert unsupported_language_error("perl") is None
    env = make_test_environment("perl")
    assert isinstance(env, PerlToolEnvironment)
    assert isinstance(make_test_runner("perl", env), ProveTestRunner)
    assert _has_testable_source([Path("lib/Example.pm")])
    assert _is_test_file(Path("t/10-example.t")) and _is_test_path("t/10-example.t")
    assert not _has_testable_source([Path("t/10-example.t")])


def test_perl_owning_tests_and_declared_include_paths(tmp_path: Path) -> None:
    source = write(tmp_path, "lib/Example/Widget.pm", "package Example::Widget; 1;\n")
    test = write(tmp_path, "t/example/widget.t", "use Test::More; done_testing;\n")
    assert owning_tests(source, tmp_path) == test
    write(tmp_path, ".proverc", "-I support\n")
    assert include_args(tmp_path) == ("-I", "lib", "-I", "support")


async def test_perl_runner_owning_then_whole_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = write(tmp_path, "lib/Example/Widget.pm", "package Example::Widget; 1;\n")
    write(tmp_path, "cpanfile", "")
    write(tmp_path, "t/example/widget.t", "use Test::More; ok(1); done_testing;\n")
    monkeypatch.setattr("orchestrator.sdlc.perl.changed_perl_files", AsyncMock(return_value=[source]))
    run = AsyncMock(return_value=(0, "Result: PASS"))
    monkeypatch.setattr("orchestrator.sdlc.testrunner._exec_capture", run)
    assert (await ProveTestRunner().run(path=str(tmp_path))).passed
    assert [call.args[0] for call in run.await_args_list] == [
        ("perl", "-I", "lib", "-c", str(source)),
        ("prove", "-l", "t/example/widget.t"),
        ("prove", "-l", "-r", "t"),
    ]


async def test_perl_runner_refuses_empty_suite(tmp_path: Path) -> None:
    result = await ProveTestRunner().run(path=str(tmp_path))
    assert not result.passed and result.returncode == 5
    assert "No Perl tests found" in result.output


def test_perl_phase_prompts_and_grounding(tmp_path: Path) -> None:
    from orchestrator.sdlc.grounding import PKGCodegenGrounder

    write(tmp_path, "lib/Shop/Cart.pm", "package Shop::Cart;\nsub subtotal { return 42; }\n1;\n")
    adapter = LLMCodegenAdapter(Mock(), layout=resolve_layout(tmp_path, language="perl"))
    for prompt in (adapter._impl_system(), adapter._tests_system(), adapter._refine_system()):
        assert "Perl" in prompt and "submit_files" in prompt
    grounding = PKGCodegenGrounder.from_repo(tmp_path, use_cache=False).context_for_spec(
        {"title": "Shop Cart subtotal", "summary": "Extend subtotal"}
    )
    assert "```perl" in grounding
    assert "sub subtotal" in grounding
