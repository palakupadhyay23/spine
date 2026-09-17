"""Razor (Blazor) components as line-aligned synthetic C# — the C# front-end reads the rest.

A ``.razor`` file is C# plus markup. Its declarations live in a handful of directives
(``@using``, ``@namespace``, ``@inject``) and in ``@code { … }`` / ``@functions { … }`` blocks
whose contents are ordinary class members. Nothing here needs a Razor grammar: rewrite each
directive to the C# statement it stands for **on the same line**, open a ``partial class``
named after the file where the members begin, blank every markup line, and hand the result to
the C# parser. Every symbol then reports its true ``.razor`` line, because no line ever moved.

Why it exists: a Blazor application's whole UI layer was outside the graph (the front-end read
``.cs`` only), so on NSS-1209 five of the six files the work landed in could not be proposed,
and the one that could was flagged "absent" by a separate bug. Two answers were weighed —
``php_extractor`` skips ``.blade.php`` outright as "a template, not PHP source" — and Razor
earns the opposite one: a component holds real C# logic, and it is where the work lands.

Deterministic and stdlib-only. Inline ``@expression`` references in markup are **not** read
(the track's D9): they need a real expression scanner, and a guessed reference is worse than a
missing one. Markup lines become blank lines, nothing more.
"""

from __future__ import annotations

import re
from pathlib import Path

_DIRECTIVE_RE = re.compile(r"^\s*@(\w+)(?:\s+(.*?))?\s*$")
_NAMESPACE_RE = re.compile(r"^\s*@namespace\s+([\w.]+)", re.M)
_CODE_RE = re.compile(r"^\s*@(?:code|functions)\b(.*)$")
_IDENT_CLEAN_RE = re.compile(r"[^A-Za-z0-9_]")


def component_namespace(source: str) -> str:
    """The ``@namespace`` a component declares, or ``""`` — the file's module, like a ``.cs``."""
    m = _NAMESPACE_RE.search(source)
    return m.group(1) if m else ""


def component_class_name(rel: str) -> str:
    """The class Blazor generates for ``rel``: the file stem, made a valid C# identifier."""
    stem = _IDENT_CLEAN_RE.sub("_", Path(rel).stem) or "Component"
    return stem if not stem[0].isdigit() else f"_{stem}"


def razor_to_csharp(source: str, rel: str) -> str:
    """``source`` (a ``.razor`` file) as C# with every line number preserved.

    Line by line:

    - ``@using X``            → ``using X;``
    - ``@namespace X``        → ``namespace X;`` (file-scoped)
    - ``@inject T Name``      → ``partial class <Stem> { T Name; }`` — one line, one field
    - ``@code {`` / ``@functions {`` → ``partial class <Stem> {`` + whatever followed the brace;
      the block's own lines and closing brace pass through verbatim
    - anything else (markup, ``@page``, ``@inherits``, ``@layout``, an inline expression) → ``""``

    A component with no code block and no injection still gets a ``partial class <Stem> { }``
    on its first blank line, so the component exists as a ``Type`` — a grid a ticket names is
    a symbol, whether or not it has members. Partial declarations merge in the front-end.
    """
    cls = component_class_name(rel)
    out: list[str] = []
    in_code = False
    depth = 0
    pending_brace = False
    class_opened = False

    for raw in source.splitlines():
        if in_code:
            out.append(raw)
            depth += raw.count("{") - raw.count("}")
            if pending_brace and "{" in raw:
                pending_brace = False
            if not pending_brace and depth <= 0:
                in_code = False
            continue

        code = _CODE_RE.match(raw)
        if code:
            rest = code.group(1)
            brace = rest.find("{")
            class_opened = True
            in_code = True
            if brace == -1:
                out.append(f"partial class {cls}")
                depth = 0
                pending_brace = True
            else:
                after = rest[brace + 1 :]
                out.append(f"partial class {cls} {{{after}")
                depth = 1 + after.count("{") - after.count("}")
                pending_brace = False
                if depth <= 0:
                    in_code = False
            continue

        directive = _DIRECTIVE_RE.match(raw)
        if directive:
            name, arg = directive.group(1), (directive.group(2) or "").strip().rstrip(";")
            if name == "using" and arg:
                out.append(f"using {arg};")
                continue
            if name == "namespace" and arg:
                out.append(f"namespace {arg};")
                continue
            if name == "inject" and arg:
                parts = arg.split()
                if len(parts) >= 2:
                    field_type, field_name = " ".join(parts[:-1]), parts[-1]
                    out.append(f"partial class {cls} {{ {field_type} {field_name}; }}")
                    class_opened = True
                    continue
        out.append("")

    if not class_opened:
        for i, line in enumerate(out):
            if not line:
                out[i] = f"partial class {cls} {{ }}"
                break
        else:
            out.append(f"partial class {cls} {{ }}")
    return "\n".join(out) + "\n"


__all__ = ["component_class_name", "component_namespace", "razor_to_csharp"]
