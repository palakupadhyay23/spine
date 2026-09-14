"""Perl distribution discovery and changed-file targeting, without executing build files."""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterator
from pathlib import Path

from orchestrator.pkg.extractor import DEFAULT_IGNORE_DIRS
from orchestrator.sdlc.process import ExecCapture, exec_capture

MARKERS = ("cpanfile", "Makefile.PL", "Build.PL", "dist.ini")
_PACKAGE = re.compile(r"^\s*package\s+([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\b", re.M)


def perl_files(root: Path, suffixes: tuple[str, ...] = (".pm", ".pl", ".t")) -> Iterator[Path]:
    if root.is_symlink():
        return
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.startswith(".")
            and d not in DEFAULT_IGNORE_DIRS
            and d not in {"blib", "local"}
            and not (Path(directory) / d).is_symlink()
        )
        for name in sorted(files):
            file = Path(directory) / name
            if file.suffix in suffixes and not file.is_symlink():
                yield file


def package_in(file: Path) -> str | None:
    match = _PACKAGE.search(file.read_text(encoding="utf-8", errors="replace"))
    return match.group(1) if match else None


def distribution_for(file: Path, root: Path) -> Path:
    """Nearest distribution marker; an unmarked lib/ tree also identifies its owner."""
    for parent in (file.parent, *file.parents):
        if not parent.is_relative_to(root):
            break
        if any((parent / marker).is_file() for marker in MARKERS):
            return parent
        if parent == root:
            break
    for parent in file.parents:
        if parent == root or not parent.is_relative_to(root):
            break
        if parent.name == "lib":
            return parent.parent
    return root


def distributions(root: Path) -> list[Path]:
    found = {distribution_for(file, root) for file in perl_files(root, (".pm", ".pl"))}
    if any((root / marker).is_file() for marker in MARKERS) or (root / "lib").is_dir():
        found.add(root)
    return sorted(found)


def package_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*", value):
        raise ValueError(f"Invalid Perl package name: {value!r}; use a name such as Shop::Cart")
    return value


def include_args(dist: Path) -> tuple[str, ...]:
    """Use declared include directories for perl -c; prove reads .proverc itself."""
    includes = ["lib"]
    config = dist / ".proverc"
    if config.is_file():
        tokens = shlex.split(config.read_text(), comments=True)
        for i, token in enumerate(tokens):
            if token in {"-I", "--I"} and i + 1 < len(tokens):
                includes.append(tokens[i + 1])
            elif token.startswith("-I") and len(token) > 2:
                includes.append(token[2:])
    config = dist / "dist.ini"
    if config.is_file():
        for value in re.findall(r"^\s*PERL5LIB\s*=\s*(.+)$", config.read_text(), re.M):
            includes.extend(value.strip().split(os.pathsep))
    args: list[str] = []
    for value in dict.fromkeys(includes):
        path = Path(value)
        if path.is_absolute() or not (dist / path).resolve().is_relative_to(dist.resolve()):
            raise ValueError(f"Perl include directory must stay inside the distribution: {value!r}")
        args.extend(("-I", value))
    return tuple(args)


async def changed_perl_files(root: Path, *, capture: ExecCapture | None = None) -> list[Path]:
    run_capture = capture or exec_capture

    rc, output = await run_capture(
        ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all"), cwd=str(root), timeout=60
    )
    if rc:
        if (root / ".git").exists():
            raise RuntimeError(f"Cannot identify changed Perl files: {output.strip()}")
        return list(perl_files(root))
    records = iter(output.split("\0"))
    changed: set[Path] = set()
    for record in records:
        if not record:
            continue
        status, name = record[:2], record[3:]
        if "R" in status or "C" in status:
            next(records, None)
        file = root / name
        if file.suffix not in {".pm", ".pl", ".t"} or not file.is_file():
            continue
        if not file.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Perl file escapes the repository: {name!r}")
        if not DEFAULT_IGNORE_DIRS.isdisjoint(file.relative_to(root).parts):
            continue
        changed.add(file)
    return sorted(changed)


def owning_tests(file: Path, dist: Path) -> Path:
    """A changed test owns itself; module directories follow the existing t/ tree."""
    tests = dist / "t"
    if file.suffix == ".t" and file.is_relative_to(tests):
        return file
    lib = dist / "lib"
    if file.is_relative_to(lib):
        relative = file.relative_to(lib)
        for parts in (relative.with_suffix(".t").parts, relative.parent.parts):
            current = tests
            for part in parts:
                hits = (
                    [p for p in current.iterdir() if p.name.lower() == part.lower()]
                    if current.is_dir()
                    else []
                )
                if len(hits) != 1:
                    break
                current = hits[0]
            else:
                if current.is_file() or list(current.rglob("*.t")):
                    return current
    return tests
