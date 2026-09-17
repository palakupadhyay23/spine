"""Razor (Blazor) components as line-aligned synthetic C# — the C# front-end reads the rest.

A ``.razor`` file is C# plus markup. Its declarations live in a handful of directives
(``@using``, ``@namespace``, ``@inject``) and in ``@code { … }`` / ``@functions { … }`` blocks
whose contents are ordinary class members. Nothing here needs a Razor grammar: rewrite each
directive to the C# statement it stands for **on the same line**, open a ``partial class``
named after the file where the members begin, blank every markup line, and hand the result to
the C# parser. Every symbol then reports its true ``.razor`` line, because no line ever moves.

Why it exists: a Blazor application's whole UI layer was outside the graph (the front-end read
``.cs`` only), so on NSS-1209 five of the six files the work landed in could not be proposed,
and the one that could was flagged "absent" by a separate bug. Two answers were weighed —
``php_extractor`` skips ``.blade.php`` outright as "a template, not PHP source" — and Razor
earns the opposite one: a component holds real C# logic, and it is where the work lands.

What the rewrite refuses to invent, each learned from a real component in review:

- ``@* … *@`` comments are lexed first, so commented-out code declares nothing.
- Braces are counted through a small lexer — a ``}`` inside a string or a ``//`` comment does
  not end the class and does not turn the markup after it into C#.
- A file of nothing but directives (``_Imports.razor``) declares no class: Blazor generates
  none for it, and a class placed past the last line would carry a provenance no line has.
- Inline ``@expression`` references in markup are **not** read (the track's D9): they need a
  real expression scanner, and a guessed reference is worse than a missing one.

Deterministic and stdlib-only.
"""

from __future__ import annotations

import re
from pathlib import Path

_DIRECTIVE_RE = re.compile(r"^\s*@(\w+)(?:\s+(.*?))?\s*$")
_NAMESPACE_DIRECTIVE_RE = re.compile(r"^\s*@namespace\s+([\w.]+)", re.M)
_CS_NAMESPACE_RE = re.compile(r"^\s*namespace\s+([\w.]+)", re.M)
_CODE_RE = re.compile(r"^\s*@(?:code|functions)\s*(\{.*)?$")
_IDENT_CLEAN_RE = re.compile(r"[^A-Za-z0-9_]")

#: Razor files that are not components. Blazor reads ``_Imports.razor`` for shared directives
#: and generates no class for it; Razor Pages does the same with ``_ViewImports``.
_RESERVED_STEMS = frozenset({"_Imports", "_ViewImports", "_ViewStart"})


def _lines(source: str) -> list[str]:
    """``source`` as lines the way tree-sitter counts them: on ``\\n`` only.

    ``str.splitlines`` also breaks on form feeds, ``\\u2028`` and a lone ``\\r``, which the parser
    does not count as rows — every such character would shift every later line by one.
    """
    lines = [line.rstrip("\r") for line in source.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def strip_razor_comments(lines: list[str]) -> list[str]:
    """``lines`` with every ``@* … *@`` span blanked, line count preserved.

    A component's history is often kept in a comment — the old block, commented out. Read
    without lexing it, the old block's namespace, class and members become grounded nodes of
    a component that no longer has them.
    """
    out: list[str] = []
    in_comment = False
    for line in lines:
        kept: list[str] = []
        rest = line
        while rest:
            if in_comment:
                end = rest.find("*@")
                if end == -1:
                    rest = ""
                else:
                    in_comment = False
                    rest = rest[end + 2 :]
            else:
                start = rest.find("@*")
                if start == -1:
                    kept.append(rest)
                    rest = ""
                else:
                    kept.append(rest[:start])
                    in_comment = True
                    rest = rest[start + 2 :]
        out.append("".join(kept))
    return out


def component_namespace(source: str) -> str:
    """The ``@namespace`` a component declares, or ``""`` — read outside comments."""
    text = "\n".join(strip_razor_comments(_lines(source)))
    m = _NAMESPACE_DIRECTIVE_RE.search(text)
    return m.group(1) if m else ""


def code_behind_namespace(path: Path) -> str:
    """The namespace of ``<component>.razor.cs`` beside ``path``, or ``""``.

    The standard Blazor layout splits a component across ``Grid.razor`` and ``Grid.razor.cs``,
    one ``partial class`` in a namespace only the second file declares. Reading it from there is
    not a guess — the code-behind states it — and it is what lets the two halves merge onto one
    ``Type`` node instead of a namespaced class and a global one the program never has.
    """
    behind = path.with_name(path.name + ".cs")
    try:
        text = behind.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""
    m = _CS_NAMESPACE_RE.search(text)
    return m.group(1) if m else ""


def component_class_name(rel: str) -> str:
    """The class Blazor generates for ``rel``: the file stem, made a valid C# identifier."""
    stem = _IDENT_CLEAN_RE.sub("_", Path(rel).stem) or "Component"
    return stem if not stem[0].isdigit() else f"_{stem}"


class _BraceLexer:
    """Counts ``{``/``}`` in C# text while ignoring the ones inside strings and comments.

    State survives across lines for ``/* … */`` and ``@"…"`` verbatim strings, which may span
    them. Interpolated strings' inner braces are counted as ordinary text — ``$"{x}"`` balances
    itself, and the rare ``$"{{"`` is a literal brace the lexer would also see balanced.
    """

    def __init__(self) -> None:
        self.block_comment = False
        self.verbatim = False

    def delta(self, line: str) -> int:
        depth = 0
        i, n = 0, len(line)
        while i < n:
            ch = line[i]
            nxt = line[i + 1] if i + 1 < n else ""
            if self.block_comment:
                if ch == "*" and nxt == "/":
                    self.block_comment = False
                    i += 2
                else:
                    i += 1
                continue
            if self.verbatim:
                if ch == '"':
                    if nxt == '"':
                        i += 2
                        continue
                    self.verbatim = False
                i += 1
                continue
            if ch == "/" and nxt == "/":
                break
            if ch == "/" and nxt == "*":
                self.block_comment = True
                i += 2
                continue
            if ch == "@" and nxt == '"':
                self.verbatim = True
                i += 2
                continue
            if ch == "$" and nxt == "@" and line[i + 2 : i + 3] == '"':
                self.verbatim = True
                i += 3
                continue
            if ch == '"':
                i += 1
                while i < n and line[i] != '"':
                    i += 2 if line[i] == "\\" else 1
                i += 1
                continue
            if ch == "'":
                i += 1
                while i < n and line[i] != "'":
                    i += 2 if line[i] == "\\" else 1
                i += 1
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        return depth


def razor_to_csharp(source: str, rel: str, *, namespace: str = "") -> str:
    """``source`` (a ``.razor`` file) as C# with every line number preserved.

    Line by line, after ``@* … *@`` comments are blanked:

    - ``@using X``            → ``using X;``
    - ``@namespace X``        → blank; the namespace is hoisted to **line 1** as ``namespace X;``
      (file-scoped), because C# applies a file-scoped namespace only to what follows it and a
      component may declare it after ``@page`` or after an ``@inject``
    - ``@inject T Name``      → ``partial class <Stem> { T Name; }`` — one line, one field
    - ``@code {`` / ``@functions {`` → ``partial class <Stem> {`` + whatever followed the brace;
      the block's own lines and closing brace pass through verbatim, braces counted through
      the lexer so a ``}`` in a string or a comment ends nothing
    - anything else (markup, ``@page``, ``@inherits``, ``@layout``, an inline expression) → ``""``

    ``namespace`` is the fallback when the file declares none — the code-behind's, read by the
    caller. A component with no code block and no injection still gets ``partial class <Stem>
    { }`` on its first blank line, so the component exists as a ``Type``; a file with no blank
    line at all is nothing but directives and declares no class. Partial declarations merge in
    the front-end — within the file, and with a code-behind whose namespace was supplied.
    """
    stem = Path(rel).stem
    cls = component_class_name(rel)
    lines = strip_razor_comments(_lines(source))
    ns = component_namespace("\n".join(lines)) or namespace
    declares_class = stem not in _RESERVED_STEMS

    out: list[str] = []
    in_code = False
    depth = 0
    pending_brace = False
    class_opened = False
    lexer = _BraceLexer()

    for raw in lines:
        if in_code:
            out.append(raw)
            depth += lexer.delta(raw)
            if pending_brace and depth > 0:
                pending_brace = False
            if not pending_brace and depth <= 0:
                in_code = False
            continue

        code = _CODE_RE.match(raw) if declares_class else None
        if code:
            rest = code.group(1) or ""
            class_opened = True
            in_code = True
            lexer = _BraceLexer()
            if not rest:
                out.append(f"partial class {cls}")
                depth = 0
                pending_brace = True
            else:
                after = rest[1:]
                out.append(f"partial class {cls} {{{after}")
                depth = 1 + lexer.delta(after)
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
            if name == "inject" and arg and declares_class:
                arg = arg.split("//", 1)[0].strip()
                parts = arg.split()
                if len(parts) >= 2:
                    field_type, field_name = " ".join(parts[:-1]), parts[-1]
                    out.append(f"partial class {cls} {{ {field_type} {field_name}; }}")
                    class_opened = True
                    continue
        out.append("")

    if declares_class and not class_opened:
        for i, line in enumerate(out):
            if not line:
                out[i] = f"partial class {cls} {{ }}"
                break
    if ns and out:
        out[0] = f"namespace {ns}; {out[0]}".rstrip()
    return "\n".join(out) + "\n"


__all__ = [
    "code_behind_namespace",
    "component_class_name",
    "component_namespace",
    "razor_to_csharp",
    "strip_razor_comments",
]
