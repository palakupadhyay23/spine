"""`sdlc.source_paths`: the one regex and resolver behind `design._stated_paths` and
`codegen._paths_from`. Until they shared it, each carried a Python-only copy, and the file a
.NET ticket named (NSS-1231: `EBSOrderApiClient.cs`, bare, no directory) was invisible to both."""

from __future__ import annotations

from pathlib import Path

from orchestrator.sdlc.source_paths import PATH_RE, find_by_basename, named_paths, resolve


def test_every_front_end_suffix_is_a_path_and_prose_is_not() -> None:
    text = (
        "Edit `EBSOrderApiClient.cs`, src/orchestrator/cli.py, WebApp/Grid.razor, api/handler.go and "
        "pkg/Foo.java; see README.md, https://example.com and version 3.36.0. Don't touch e.g. c.h."
    )
    assert named_paths(text) == [
        "EBSOrderApiClient.cs",
        "src/orchestrator/cli.py",
        "WebApp/Grid.razor",
        "api/handler.go",
        "pkg/Foo.java",
    ]


def test_windows_separators_are_normalised_and_duplicates_collapse() -> None:
    assert named_paths(r"Shared\Enums\ProductGroup.cs and Shared/Enums/ProductGroup.cs") == [
        "Shared/Enums/ProductGroup.cs"
    ]
    assert named_paths("./src/a.py and src/a.py") == ["src/a.py"]


def test_the_regex_does_not_bleed_into_neighbouring_words() -> None:
    assert PATH_RE.findall("filename.cs.bak") == ["filename.cs"]
    assert PATH_RE.findall("x.pyc") == []


def _tree(root: Path, *files: str) -> None:
    for rel in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("//\n", encoding="utf-8")


def test_a_written_path_that_exists_is_taken_as_written(tmp_path: Path) -> None:
    _tree(tmp_path, "FunctionsApp/Shared/Utils/EBSOrderApiClient.cs")
    assert resolve("FunctionsApp/Shared/Utils/EBSOrderApiClient.cs", tmp_path) == (
        "FunctionsApp/Shared/Utils/EBSOrderApiClient.cs"
    )
    assert resolve(r"FunctionsApp\Shared\Utils\EBSOrderApiClient.cs", tmp_path) == (
        "FunctionsApp/Shared/Utils/EBSOrderApiClient.cs"
    )


def test_nss_1231_a_bare_basename_resolves_to_its_one_location(tmp_path: Path) -> None:
    _tree(tmp_path, "FunctionsApp/Shared/Utils/EBSOrderApiClient.cs", "FunctionsApp/Program.cs")
    assert resolve("EBSOrderApiClient.cs", tmp_path) == "FunctionsApp/Shared/Utils/EBSOrderApiClient.cs"


def test_an_ambiguous_basename_is_a_guess_and_is_dropped(tmp_path: Path) -> None:
    _tree(tmp_path, "a/Product.cs", "b/Product.cs")
    assert find_by_basename(tmp_path, "Product.cs") == ["a/Product.cs", "b/Product.cs"]
    assert resolve("Product.cs", tmp_path) is None


def test_a_path_that_does_not_exist_is_dropped_and_a_directory_path_is_never_searched(tmp_path: Path) -> None:
    _tree(tmp_path, "src/real.py")
    assert resolve("src/ghost.py", tmp_path) is None
    assert resolve("elsewhere/real.py", tmp_path) is None  # has a directory: not a bare name


def test_the_walk_skips_what_the_extractors_skip(tmp_path: Path) -> None:
    _tree(tmp_path, "src/Only.cs", "node_modules/Only.cs", ".hidden/Only.cs", "vendored/Only.cs")
    (tmp_path / "vendored" / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")  # a submodule
    assert find_by_basename(tmp_path, "Only.cs") == ["src/Only.cs"]
