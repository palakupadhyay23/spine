"""PKG: Blazor `.razor` components enter the graph through the C# front-end (NSS-1209).

`pkg.razor` rewrites a component into line-aligned C# — directives in place, markup blanked,
`@code` opened as a `partial class` — and the C# parser reads the rest. tree-sitter is an
optional extra, so the extraction tests skip cleanly when it is absent; the rewrite itself is
pure Python and always runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, NodeKind
from orchestrator.pkg.razor import component_class_name, component_namespace, razor_to_csharp

GRID = """\
@page "/auctions/products"
@using Commercial.Secondary.Sales.Shared.Enums
@using Commercial.Secondary.Sales.Features.Common.Products.Model
@inject IProductService ProductService

<h3>Products</h3>
<GridColumn Title="Group">@context.ProductDetails!.ProductGroup</GridColumn>

@code {
    private List<Product> _products = new();

    private string DisplayGroup(string code)
    {
        return ProductGroupHelper.GetDisplayNameFromAcronym(code);
    }
}
"""


def _line_of(src: str, needle: str) -> int:
    return next(i for i, line in enumerate(src.splitlines(), 1) if needle in line)


# ---- the rewrite ----------------------------------------------------------------------------


def test_every_line_keeps_its_number() -> None:
    out = razor_to_csharp(GRID, "WebApp/Features/AuctionProductsGrid.razor").splitlines()
    assert len(out) == len(GRID.splitlines())
    assert out[_line_of(GRID, "@using Commercial.Secondary.Sales.Shared.Enums") - 1] == (
        "using Commercial.Secondary.Sales.Shared.Enums;"
    )
    assert out[_line_of(GRID, "@inject") - 1] == (
        "partial class AuctionProductsGrid { IProductService ProductService; }"
    )
    assert out[_line_of(GRID, "@code {") - 1] == "partial class AuctionProductsGrid {"
    assert out[_line_of(GRID, "<h3>") - 1] == ""  # markup is blank, not dropped
    assert out[_line_of(GRID, "@page") - 1] == ""
    assert (
        out[_line_of(GRID, "private string DisplayGroup") - 1]
        == "    private string DisplayGroup(string code)"
    )


def test_a_markup_only_component_still_declares_its_class() -> None:
    out = razor_to_csharp("<h1>Hello</h1>\n<p>@Name</p>\n", "Pages/Banner.razor")
    assert out.splitlines() == ["partial class Banner { }", ""]


def test_a_code_block_with_its_brace_on_the_next_line() -> None:
    src = "@code\n{\n    int X;\n}\n<p/>\n"
    out = razor_to_csharp(src, "A.razor").splitlines()
    assert out == ["partial class A", "{", "    int X;", "}", ""]


def test_namespace_and_class_name() -> None:
    assert component_namespace('@page "/x"\n@namespace My.App.Pages\n') == "My.App.Pages"
    assert component_namespace("<p/>") == ""
    assert component_class_name("Pages/Order-Details.razor") == "Order_Details"
    assert component_class_name("Pages/1Up.razor") == "_1Up"


# ---- extraction through the C# front-end -----------------------------------------------------


def _needs_csharp() -> None:
    pytest.importorskip("tree_sitter_c_sharp", reason="install the 'csharp' extra")


def test_a_component_s_symbols_report_their_true_razor_lines(tmp_path: Path) -> None:
    _needs_csharp()
    rel = "WebApp/Features/AuctionProductsGrid.razor"
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text(GRID, encoding="utf-8")

    store_batch = RepoCodeExtractor().extract(tmp_path)
    by_id = {n.id: n for n in store_batch.nodes}

    grid = by_id["csharp:AuctionProductsGrid"]
    assert grid.kind is NodeKind.TYPE and grid.provenance is not None
    assert grid.provenance.file == rel
    method = by_id["csharp:AuctionProductsGrid.DisplayGroup"]
    assert method.kind is NodeKind.FUNCTION
    assert method.provenance is not None and method.provenance.line == _line_of(
        GRID, "private string DisplayGroup"
    )
    field = by_id["csharp:AuctionProductsGrid._products"]
    assert field.provenance is not None and field.provenance.line == _line_of(GRID, "_products")
    injected = by_id["csharp:AuctionProductsGrid.ProductService"]
    assert injected.kind is NodeKind.FIELD and injected.provenance is not None
    assert injected.provenance.line == _line_of(GRID, "@inject")


def test_a_component_s_usings_are_imports_and_its_module_is_its_path(tmp_path: Path) -> None:
    _needs_csharp()
    rel = "WebApp/Features/AuctionProductsGrid.razor"
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text(GRID, encoding="utf-8")

    batch = RepoCodeExtractor().extract(tmp_path)
    module_id = f"csharp:{rel}"
    assert any(n.id == module_id and n.kind is NodeKind.MODULE for n in batch.nodes)
    imports = {e.dst for e in batch.edges if e.src == module_id and e.kind is EdgeKind.IMPORTS}
    assert imports == {
        "csharp:Commercial.Secondary.Sales.Shared.Enums",
        "csharp:Commercial.Secondary.Sales.Features.Common.Products.Model",
    }
    assert any(
        e.src == module_id and e.dst == "csharp:AuctionProductsGrid" and e.kind is EdgeKind.CONTAINS
        for e in batch.edges
    )


def test_a_declared_namespace_qualifies_the_component(tmp_path: Path) -> None:
    _needs_csharp()
    src = "@namespace My.App.Pages\n@code {\n    public int Count;\n}\n"
    (tmp_path / "Counter.razor").write_text(src, encoding="utf-8")

    batch = RepoCodeExtractor().extract(tmp_path)
    ids = {n.id for n in batch.nodes}
    assert "csharp:My.App.Pages" in ids
    assert "csharp:My.App.Pages.Counter" in ids
    assert "csharp:My.App.Pages.Counter.Count" in ids


def test_nss_1209_the_grids_resolve_and_are_not_flagged_absent(tmp_path: Path) -> None:
    """The five render locations of NSS-1209 were invisible; now a design naming them resolves."""
    _needs_csharp()
    from orchestrator.pkg import FactStore
    from orchestrator.sdlc.impact import blast_radius, unverified_references

    for name in ("AuctionProductsGrid", "ProductsGrid"):
        (tmp_path / f"{name}.razor").write_text(GRID, encoding="utf-8")
    store = FactStore(RepoCodeExtractor().extract(tmp_path))
    br = blast_radius(store, ["AuctionProductsGrid.razor", "ProductsGrid.razor", "Ghost.razor"])
    assert unverified_references(br) == ["Ghost.razor"]


def test_a_warm_cache_cannot_serve_a_razor_less_graph(tmp_path: Path) -> None:
    """D8: no grammar changed, so nothing in `_GRAMMAR_MODULES` did — the fingerprint must
    still move, and it does, because it hashes every `pkg/` module's source."""
    import shutil

    from orchestrator.pkg import persistence

    src = Path(persistence.__file__).parent
    copy = tmp_path / "pkg"
    shutil.copytree(src, copy, ignore=shutil.ignore_patterns("__pycache__"))
    before = persistence.extractor_fingerprint(package_dir=copy)
    with (copy / "razor.py").open("a", encoding="utf-8") as fh:
        fh.write("\n# a one-byte change to the rewrite\n")
    assert persistence.extractor_fingerprint(package_dir=copy) != before


def test_the_invention_oracle_scopes_the_rewrite_not_the_markup(tmp_path: Path) -> None:
    """A component's sibling call is a `CALLS` edge; the oracle must examine it against the
    line-aligned C# the front-end parsed, not raw markup, or every bound name looks invented."""
    _needs_csharp()
    from orchestrator.pkg.invention import MEASURED, find_invented_calls

    src = (
        "<p>@Total()</p>\n@code {\n"
        "    private int Total() { return Sub(); }\n"
        "    private int Sub() { return 1; }\n}\n"
    )
    (tmp_path / "Totals.razor").write_text(src, encoding="utf-8")
    batch = RepoCodeExtractor().extract(tmp_path)
    assert any(e.kind is EdgeKind.CALLS and e.src == "csharp:Totals.Total" for e in batch.edges)

    report = find_invented_calls(batch, tmp_path)
    row = next(r for r in report.by_language if r.language == "csharp")
    assert row.status == MEASURED and row.examined >= 1
    assert row.invented == ()
