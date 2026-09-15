"""Precision boundary of the optional clang semantic pass."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestrator.pkg.clang_link import usr_to_id
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, FactBatch


@pytest.mark.parametrize(
    ("usr", "language", "rel", "expected"),
    [
        ("c:@F@subtotal", "c", "src/s.c", "c:subtotal"),
        ("c:s.c@F@helper", "c", "src/s.c", "c:src/s.c::helper"),
        ("c:@S@Handler@F@run#", "cpp", "src/dispatch.cpp", "cpp:Handler::run"),
        ("c:@N@geo@S@Circle@F@area#", "cpp", "geo.hpp", "cpp:geo::Circle::area"),
        ("c:@N@one@N@two@F@call#", "cpp", "a.cpp", "cpp:one::two::call"),
        ("c:@F@plain#", "cpp", "a.cpp", "cpp:plain"),
        ("c:@F@extern_c", "cpp", "a.cpp", "cpp:extern_c"),
    ],
)
def test_usr_maps_supported_functions(usr: str, language: str, rel: str, expected: str) -> None:
    assert usr_to_id(usr, language=language, rel=rel) == expected


@pytest.mark.parametrize(
    "usr",
    [
        "",
        "c:@S@Handler",
        "c:@S@Handler@FI@field",
        "c:@ST>1#T@Box@F@get#",
        "c:@F@run<#I>#I#",
        "c:@F@run#I#",
        "c:@S@Handler@F@run#d#",
        "c:@aN@F@hidden#",
        "c:a.cpp@aN@F@hidden#",
        "c:a.cpp@12@F@f#@Sa@F@operator()#1",
        "c:@F@operator+#I#",
        "c:@S@Handler@F@~Handler#",
        "c:@S@Handler@F@run#garbage",
        "c:@F@run#\n",
        "c:@N@@F@run#",
        "c:@N@a@T@Alias@F@run#",
    ],
)
def test_usr_refuses_unsupported_identities(usr: str) -> None:
    assert usr_to_id(usr, language="cpp", rel="src/a.cpp") is None


@pytest.mark.parametrize("rel", ["", "/tmp/a.c", "../a.c", "src/../../a.c", "C:/a.c", "src\\a.c"])
def test_usr_requires_repository_relative_path(rel: str) -> None:
    assert usr_to_id("c:@F@f", language="c", rel=rel) is None


def test_static_uses_declaration_path_and_checks_basename() -> None:
    assert usr_to_id("c:a.c@F@f", language="c", rel="one/a.c") == "c:one/a.c::f"
    assert usr_to_id("c:a.c@F@f", language="c", rel="two/a.c") == "c:two/a.c::f"
    assert usr_to_id("c:a.c@F@f", language="c", rel="two/b.c") is None
    assert usr_to_id("c:a.c@F@f", language="cpp", rel="a.c") is None
    assert usr_to_id("c:@F@f", language="java", rel="A.java") is None


@pytest.fixture
def clang_ready() -> None:
    pytest.importorskip("clang.cindex")
    pytest.importorskip("tree_sitter_cpp")
    pytest.importorskip("tree_sitter_c")


def _calls(batch: FactBatch) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}


def test_member_reference_pointer_virtual_and_same_line(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "calls.cpp").write_text(
        "struct Base { virtual int run() { return 1; } };\n"
        "struct Child : Base { int run() { return 2; } };\n"
        "int use(Base& b, Base* p) { return b.run() + p->run(); }\n"
        "int local() { Child c; return c.run(); }\n"
    )
    ex = RepoCodeExtractor()
    b = ex.extract(tmp_path)
    calls = _calls(b)
    assert ("cpp:use", "cpp:Base::run") in calls
    assert ("cpp:use", "cpp:Child::run") not in calls
    assert ("cpp:local", "cpp:Child::run") in calls
    assert ex.clang_report.resolved == ex.clang_report.pending == 3
    assert ex.clang_report.parsed_tus == ex.clang_report.total_tus == 1


def test_only_pending_tus_are_parsed_and_reuse_clears_state(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "a.cpp").write_text("struct A { int f() { return 1; } }; int use(A& a) { return a.f(); }")
    (tmp_path / "b.cpp").write_text("int clean() { return 1; }")
    (tmp_path / "ignored.cu").write_text("this is deliberately not C++")
    (tmp_path / "ignored.mm").write_text("this is deliberately not Objective-C++")
    ex = RepoCodeExtractor()
    ex.extract(tmp_path)
    assert ex.clang_report.parsed_tus == 1
    assert ex.clang_report.total_tus == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    b = ex.extract(empty)
    assert not b.nodes and not b.edges
    assert ex.clang_report.pending == ex.clang_report.parsed_tus == 0


def test_missing_headers_and_external_targets_add_no_nodes(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "a.cpp").write_text(
        "#include <not_a_real_header.hpp>\n"
        "struct A { int good() { return 1; } int external(); };\n"
        "int use(A& a) { return a.good() + a.external(); }\n"
    )
    ex = RepoCodeExtractor()
    b = ex.extract(tmp_path)
    assert ("cpp:use", "cpp:A::good") in _calls(b)
    assert ("cpp:use", "cpp:A::external") not in _calls(b)
    assert ex.clang_report.resolved == 1 and ex.clang_report.pending == 2
    assert ex.clang_report.diagnostic_tus == 1


def test_c_function_pointer_is_reported_but_not_invented(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "a.c").write_text("void use(void (*cb)(void)) { cb(); }")
    ex = RepoCodeExtractor()
    b = ex.extract(tmp_path)
    assert not _calls(b)
    assert ex.clang_report.pending == ex.clang_report.parsed_tus == 1
    assert ex.clang_report.resolved == 0


def test_unavailable_clang_and_failed_parse_preserve_batch(
    tmp_path: Path, clang_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clang import cindex

    from orchestrator.pkg import clang_link

    (tmp_path / "a.cpp").write_text("struct A { void f() {} }; void use(A& a) { a.f(); }")
    monkeypatch.setattr(clang_link, "clang_available", lambda: False)
    ex = RepoCodeExtractor()
    before = ex.extract(tmp_path)
    assert not ex.clang_report.available and ex.clang_report.pending == 1
    monkeypatch.setattr(clang_link, "clang_available", lambda: True)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise cindex.TranslationUnitLoadError("test parse failure")

    monkeypatch.setattr(cindex.Index, "parse", fail)
    after = ex.extract(tmp_path)
    assert after.nodes == before.nodes and after.edges == before.edges
    assert ex.clang_report.failed_tus == 1


def test_header_sites_select_including_tu(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "api.hpp").write_text(
        "struct A { int f() { return 1; } }; inline int use(A& a) { return a.f(); }"
    )
    (tmp_path / "a.cpp").write_text('#include "api.hpp"\n')
    ex = RepoCodeExtractor()
    assert ("cpp:use", "cpp:A::f") in _calls(ex.extract(tmp_path))
    assert ex.clang_report.resolved == ex.clang_report.parsed_tus == 1


def test_checkout_paths_and_compile_database_do_not_change_facts(tmp_path: Path, clang_ready: None) -> None:
    batches = []
    for dirname in ("first", "second"):
        root = tmp_path / dirname
        root.mkdir()
        (root / "a.cpp").write_text("struct A { void f() {} }; void use(A& a) { a.f(); }")
        (root / "compile_commands.json").write_text(
            '[{"directory":"/' + dirname + '","command":"clang -DWRONG=1"}]'
        )
        batches.append(RepoCodeExtractor().extract(root))
    assert batches[0].nodes == batches[1].nodes
    assert batches[0].edges == batches[1].edges


def test_corpus_is_additive_only(tmp_path: Path, clang_ready: None, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from orchestrator.pkg import clang_link

    corpus = Path(__file__).resolve().parents[2] / "corpus"
    for spec_path in sorted(corpus.glob("*/*/expected.json")):
        spec = json.loads(spec_path.read_text())
        roots = list(spec.get("roots", {}).values()) or [spec.get("root", ".repo")]
        for rel in roots:
            root = spec_path.parent / rel
            monkeypatch.setattr(clang_link, "clang_available", lambda: False)
            before = RepoCodeExtractor().extract(root)
            monkeypatch.setattr(clang_link, "clang_available", lambda: True)
            after = RepoCodeExtractor().extract(root)
            assert before.nodes == after.nodes, str(spec_path)
            assert set(before.edges) <= set(after.edges), str(spec_path)
            grounded = {n.id for n in before.nodes if n.grounded}
            assert all(e.src in grounded and e.dst in grounded for e in set(after.edges) - set(before.edges))
