"""PKG: what counts as a *constant* in Kotlin source, and what does not.

``kotlin_names.string_value`` is one helper with five readers downstream of it — Ktor
routes, Compose navigation, Room, and both Gradle readers — so its answer is the single
place where "this path/table/module name is computed" has to be recognised. It was not:
the first version joined a literal's ``string_content`` children and dropped everything
between them, handing every caller a plausible constant assembled out of a computed one.

The awkward part, and the reason these are parametrised over both string flavours:
tree-sitter-kotlin 1.1.0 tags only *some* interpolations. ``${x}`` in a raw string is an
``interpolation`` node; ``$it`` in an ordinary string is two bare ``string_content``
children with no marker at all.
"""

from __future__ import annotations

import pytest

from orchestrator.pkg.kotlin_names import string_value

pytest.importorskip("tree_sitter_kotlin", reason="install the 'kotlin' extra")


def _literal(source: str) -> object:
    from orchestrator.pkg.kotlin_extractor import _kotlin_parser

    raw = f"package p\nval x = {source}\n".encode()
    tree = _kotlin_parser().parse(raw)
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in ("string_literal", "multiline_string_literal"):
            return string_value(node, raw)
        stack.extend(node.named_children)
    raise AssertionError(f"no string literal parsed out of {source!r}")


@pytest.mark.parametrize(
    "source",
    [
        pytest.param('"/api/${cfg.version}"', id="qualified-expression"),
        pytest.param('"/users/${user.id}/detail"', id="interpolation-between-two-chunks"),
        pytest.param('"$it"', id="short-form-untagged-by-the-grammar"),
        pytest.param('":core:$it"', id="short-form-inside-a-gradle-path"),
        pytest.param('"""raw ${x} tail"""', id="raw-string-tagged-interpolation"),
        pytest.param('"""raw $y tail"""', id="raw-string-short-form"),
    ],
)
def test_an_interpolated_string_is_not_a_constant(source: str) -> None:
    """Each of these produced a wrong literal rather than ``None``.

    ``"/api/${cfg.version}"`` became the path ``/api/`` and ``"/users/${user.id}/detail"``
    became ``/users//detail`` — an endpoint that exists at no version of that service.
    """
    assert _literal(source) is None


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param('"plain"', "plain", id="plain"),
        pytest.param('""', "", id="empty-is-a-real-value"),
        pytest.param(
            '"""SELECT * FROM t WHERE id = :id"""', "SELECT * FROM t WHERE id = :id", id="room-query"
        ),
        pytest.param(r'"costs \$5 today"', "costs $5 today", id="escaped-dollar-is-literal-text"),
        pytest.param(r'"a\nb"', "a\nb", id="decoded-escape"),
        pytest.param(r'"A"', "A", id="decoded-unicode-escape"),
    ],
)
def test_a_constant_string_keeps_its_exact_value(source: str, expected: str) -> None:
    """Escapes are **decoded**, not dropped.

    The first version kept only ``string_content``, so an escape vanished: ``"costs \\$5"``
    came back as ``costs 5`` — a literal that is silently wrong rather than refused, which
    for a Room table name or a route path is the same class of defect as the interpolation
    one above.
    """
    assert _literal(source) == expected
