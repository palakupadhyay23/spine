"""Optional semantic enrichment beside the C/C++ CST front-ends.

A second parser may contribute edges only between nodes the primary parser already
grounded. USRs are identities to check, never authority to invent PKG nodes. This
module deliberately declines templates, anonymous scopes and signature-qualified
identities that the existing name-keyed graph cannot distinguish.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from orchestrator.pkg.facts import EdgeKind, FactBatch, Provenance
from orchestrator.pkg.finalize_names import declared_ids, resolve_or_drop

_IDENTIFIER = r"[A-Za-z_][A-Za-z_0-9]*"
_CPP_FUNCTION = re.compile(rf"c:((?:@(?:N|S)@{_IDENTIFIER})*)@F@({_IDENTIFIER})#?")
_C_FUNCTION = re.compile(rf"c:@F@({_IDENTIFIER})")
_C_STATIC = re.compile(rf"c:([^@]+)@F@({_IDENTIFIER})")


def usr_to_id(usr: str, *, language: str, rel: str) -> str | None:
    """Map only recognised, name-keyed function USRs; reject everything else.

    ``rel`` is the declaration's repository-relative path, not the caller's TU.
    C statics use that full path (the USR itself contains only the basename).
    Function parameter encodings are deliberately refused: stripping arbitrary USR
    suffixes could turn a template/overload identity into a plausible wrong name.
    The caller must still verify that the result is a grounded function.
    """
    path = PurePosixPath(rel)
    if not rel or path.is_absolute() or ".." in path.parts or "\\" in rel or ":" in rel:
        return None
    if language == "c":
        match = _C_FUNCTION.fullmatch(usr)
        if match:
            return f"c:{match[1]}"
        match = _C_STATIC.fullmatch(usr)
        if match and match[1] == path.name:
            return f"c:{path.as_posix()}::{match[2]}"
    elif language == "cpp":
        match = _CPP_FUNCTION.fullmatch(usr)
        if match:
            parents = re.findall(r"@(?:N|S)@([^@]+)", match[1])
            return "cpp:" + "::".join([*parents, match[2]])
    return None


# The side-channel names call *sites*, not symbol guesses. Byte offsets distinguish
# two calls on one line and match clang's source ranges without text heuristics.
@dataclass(frozen=True)
class PendingMemberCall:
    caller: str
    file: str
    offset: int
    line: int


@dataclass
class ClangReport:
    available: bool = False
    pending: int = 0
    resolved: int = 0
    total_tus: int = 0
    parsed_tus: int = 0
    failed_tus: int = 0
    diagnostic_tus: int = 0

    def summary(self) -> str:
        availability = "" if self.available else " (extra unavailable)"
        return (
            f"clang: resolved {self.resolved} of {self.pending} unresolved call sites "
            f"in {self.parsed_tus} of {self.total_tus} TUs{availability}; "
            f"{self.diagnostic_tus} with diagnostics, {self.failed_tus} failed"
        )


def clang_available() -> bool:
    """Only the bundled distribution counts; never fall back to a system libclang."""
    try:
        importlib.metadata.version("libclang")
        return importlib.util.find_spec("clang") is not None
    except (ImportError, importlib.metadata.PackageNotFoundError):
        return False


def _repo_file(path: str, root: Path) -> str | None:
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return None


def _tu_files(batch: FactBatch) -> tuple[dict[str, str], dict[str, set[str]], set[str]]:
    modules = {
        n.id: n for n in batch.nodes if n.grounded and n.kind.value == "Module" and n.language in {"c", "cpp"}
    }
    files = {n.provenance.file: n.language or "c" for n in modules.values() if n.provenance}
    includes: dict[str, set[str]] = {}
    for e in batch.edges:
        if e.kind.value == "IMPORTS" and e.src in modules and e.dst in modules:
            src, dst = modules[e.src].provenance, modules[e.dst].provenance
            if src and dst:
                includes.setdefault(src.file, set()).add(dst.file)
    tus = {f: lang for f, lang in files.items() if Path(f).suffix in {".c", ".cpp", ".cc", ".cxx"}}
    return tus, includes, set(files)


def link_clang(
    batch: FactBatch,
    root: Path,
    *,
    pending: list[PendingMemberCall],
    report: ClangReport | None = None,
) -> FactBatch:
    """Enrich grounded CST facts with bounded, optional semantic CALLS edges.

    Parse only source TUs with pending sites, including sites in their reachable
    headers. Synthesised flags use only repository header directories. Missing
    includes cost recall; host SDKs and compilation databases are never consulted.
    Reports are side-channel metadata, so facts stay deterministic and edge-only.
    """
    report = report if report is not None else ClangReport()
    sites = {(p.file, p.offset): p for p in pending}
    report.pending = len(sites)
    tus, includes, files = _tu_files(batch)
    report.total_tus = len(tus)
    report.available = clang_available()
    if not sites or not report.available:
        return batch
    from clang import cindex

    try:
        # Explicitly select the wheel's library; LIBCLANG_LIBRARY_PATH must not
        # select a different compiler on another checkout or machine.
        library = Path(cindex.__file__).parent / "native"
        filename = (
            "libclang.dll"
            if sys.platform == "win32"
            else "libclang.dylib"
            if sys.platform == "darwin"
            else "libclang.so"
        )
        if not cindex.Config.loaded:
            cindex.Config.set_library_file(str(library / filename))
        elif Path(cindex.conf.get_filename()).resolve() != (library / filename).resolve():
            report.available = False
            return batch
        index = cindex.Index.create()
    except (OSError, cindex.LibclangError):
        report.available = False
        return batch
    root = root.resolve()
    grounded = {n.id: n for n in batch.nodes if n.grounded and n.kind.value == "Function"}
    declared = declared_ids(batch)
    header_dirs = sorted(
        {str((root / f).parent) for f in files if Path(f).suffix in {".h", ".hpp", ".hh", ".hxx"}}
    )
    resolved: set[tuple[str, int]] = set()
    # A shared header may be parsed in multiple TUs. Conflicting static targets
    # are refused rather than letting TU iteration order choose the graph.
    candidates: dict[tuple[str, int], set[str]] = {}
    for file, language in sorted(tus.items()):
        reachable: set[str] = set()
        stack = [file]
        while stack:
            item = stack.pop()
            if item not in reachable:
                reachable.add(item)
                stack.extend(sorted(includes.get(item, ())))
        wanted = {key for key in sites if key[0] in reachable}
        if not wanted:
            continue
        report.parsed_tus += 1
        args = [
            "-x",
            "c" if language == "c" else "c++",
            "-std=c11" if language == "c" else "-std=c++17",
            "-nostdinc",
            "-target",
            "x86_64-unknown-linux-gnu",
            "-ferror-limit=0",
            *[f"-I{d}" for d in header_dirs],
        ]
        try:
            tu = index.parse(str(root / file), args=args)
        except (OSError, cindex.TranslationUnitLoadError):
            report.failed_tus += 1
            continue
        report.diagnostic_tus += bool(list(tu.diagnostics))
        cursors = [tu.cursor]
        while cursors:
            cursor = cursors.pop()
            if cursor.kind == cindex.CursorKind.LAMBDA_EXPR:
                continue
            if cursor.kind == cindex.CursorKind.CALL_EXPR and cursor.location.file:
                rel = _repo_file(cursor.location.file.name, root)
                key = (rel or "", cursor.extent.start.offset)
                if key in wanted:
                    target = cursor.referenced
                    if (
                        target
                        and target.kind in {cindex.CursorKind.CXX_METHOD, cindex.CursorKind.FUNCTION_DECL}
                        and target.location.file
                    ):
                        target_rel = _repo_file(target.location.file.name, root)
                        target_id = usr_to_id(target.get_usr(), language=language, rel=target_rel or "")
                        site = sites[key]
                        if target_rel in files and site.caller in grounded and target_id in grounded:
                            candidates.setdefault(key, set()).add(target_id)
            cursors.extend(cursor.get_children())
    for key, targets in sorted(candidates.items()):
        if len(targets) != 1:
            continue
        site = sites[key]
        if resolve_or_drop(
            batch,
            site.caller,
            sorted(targets),
            EdgeKind.CALLS,
            Provenance(site.file, site.line),
            declared=declared,
        ):
            resolved.add(key)
    report.resolved = len(resolved)
    return batch
