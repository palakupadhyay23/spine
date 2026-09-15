"""Precision boundary of the optional clang semantic pass."""

from __future__ import annotations

import pytest

from orchestrator.pkg.clang_link import usr_to_id


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
