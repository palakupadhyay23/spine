"""The JavaScript front-end: CommonJS on top of the TypeScript reading (javascript-support-roadmap)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_typescript", reason="install the 'typescript' extra")

from orchestrator.pkg.extractor import RepoCodeExtractor  # noqa: E402
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind  # noqa: E402
from orchestrator.pkg.js_extractor import JavaScriptExtractor  # noqa: E402
from orchestrator.pkg.typescript_extractor import TypeScriptExtractor  # noqa: E402


def _repo(tmp_path: Path, files: dict[str, str]) -> FactBatch:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return RepoCodeExtractor(extractors=[TypeScriptExtractor(), JavaScriptExtractor()]).extract(tmp_path)


def _edges(batch: FactBatch, kind: EdgeKind) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is kind}


def _ids(batch: FactBatch) -> set[str]:
    return {n.id for n in batch.nodes}


def test_nodes_are_tagged_javascript_under_typescript_ids(tmp_path: Path) -> None:
    """The tag is what accuracy and the capability matrix count by; the id prefix is shared (D4)."""
    batch = _repo(tmp_path, {"lib/util.js": "function helper() {}\n"})
    node = next(n for n in batch.nodes if n.id == "ts:lib/util.helper")
    assert node.language == "javascript"
    assert {n.language for n in batch.nodes} == {"javascript"}


def test_module_name_strips_every_javascript_suffix(tmp_path: Path) -> None:
    batch = _repo(tmp_path, {"a.mjs": "function f() {}\n", "b.cjs": "function g() {}\n"})
    assert {"ts:a", "ts:b", "ts:a.f", "ts:b.g"} <= _ids(batch)


def test_require_namespace_member_call_resolves(tmp_path: Path) -> None:
    """`const m = require('./m')` binds the whole module, so `m.f()` is the export `f`."""
    batch = _repo(
        tmp_path,
        {
            "lib/util.js": "exports.double = function (x) { return x * 2; };\n",
            "lib/api.js": "const util = require('./util');\nfunction run(x) { return util.double(x); }\n",
        },
    )
    assert ("ts:lib/api", "ts:lib/util") in _edges(batch, EdgeKind.IMPORTS)
    assert ("ts:lib/api.run", "ts:lib/util.double") in _edges(batch, EdgeKind.CALLS)


def test_destructured_require_call_resolves(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "util.js": "exports.double = (x) => x * 2;\n",
            "api.js": "const { double } = require('./util');\nfunction run(x) { return double(x); }\n",
        },
    )
    assert ("ts:api.run", "ts:util.double") in _edges(batch, EdgeKind.CALLS)


def test_member_of_a_require_binds_under_the_export_name(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "util.js": "exports.double = (x) => x * 2;\n",
            "api.js": "const double = require('./util').double;\nfunction run(x) { return double(x); }\n",
        },
    )
    assert ("ts:api.run", "ts:util.double") in _edges(batch, EdgeKind.CALLS)


def test_a_renamed_destructuring_records_the_import_and_binds_nothing(tmp_path: Path) -> None:
    """Resolution names the target by the local, so `{double: twice}` would send `twice()` to
    `ts:util.twice`. The import is still real; the binding is not trusted."""
    batch = _repo(
        tmp_path,
        {
            "util.js": "exports.double = (x) => x * 2;\n",
            "api.js": "const { double: twice } = require('./util');\nfunction run(x) { return twice(x); }\n",
        },
    )
    assert ("ts:api", "ts:util") in _edges(batch, EdgeKind.IMPORTS)
    assert not {dst for src, dst in _edges(batch, EdgeKind.CALLS) if src == "ts:api.run"}


def test_calling_a_whole_module_draws_no_edge(tmp_path: Path) -> None:
    """`m()` reaches whatever the module assigned to `module.exports` — not `ts:m.m`."""
    batch = _repo(
        tmp_path,
        {
            "m.js": "function m() {}\nmodule.exports = { other() {} };\n",
            "api.js": "const m = require('./m');\nfunction run() { return m(); }\n",
        },
    )
    assert ("ts:api.run", "ts:m.m") not in _edges(batch, EdgeKind.CALLS)


def test_a_bare_require_is_an_import_with_no_binding(tmp_path: Path) -> None:
    batch = _repo(tmp_path, {"polyfill.js": "\n", "app.js": "require('./polyfill');\n"})
    assert ("ts:app", "ts:polyfill") in _edges(batch, EdgeKind.IMPORTS)


def test_a_computed_require_is_not_guessed(tmp_path: Path) -> None:
    batch = _repo(tmp_path, {"app.js": "const m = require(path.join(dir, 'x'));\n"})
    assert not _edges(batch, EdgeKind.IMPORTS)


def test_a_package_require_is_an_external_module(tmp_path: Path) -> None:
    batch = _repo(tmp_path, {"app.js": "const fs = require('fs');\n"})
    fs = next(n for n in batch.nodes if n.id == "ts:fs")
    assert fs.external and fs.kind is NodeKind.MODULE


def test_commonjs_exports_are_the_modules_surface(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "m.js": (
                "exports.a = function () {};\n"
                "module.exports.b = () => {};\n"
                "module.exports = { c() {}, d: function () {}, e: () => {} };\n"
            )
        },
    )
    functions = {n.id for n in batch.nodes if n.kind is NodeKind.FUNCTION}
    assert {"ts:m.a", "ts:m.b", "ts:m.c", "ts:m.d", "ts:m.e"} <= functions
    assert {("ts:m", f"ts:m.{x}") for x in "abcde"} <= _edges(batch, EdgeKind.CONTAINS)


def test_a_named_default_export_is_emitted_and_an_anonymous_one_is_not(tmp_path: Path) -> None:
    named = _repo(tmp_path / "n", {"m.js": "module.exports = function build() {};\n"})
    anon = _repo(tmp_path / "a", {"m.js": "module.exports = function () {};\n"})
    assert "ts:m.build" in _ids(named)
    assert not [n for n in anon.nodes if n.kind is NodeKind.FUNCTION]


def test_calls_inside_an_exported_function_are_extracted(tmp_path: Path) -> None:
    batch = _repo(tmp_path, {"m.js": "function helper() {}\nexports.run = function () { helper(); };\n"})
    assert ("ts:m.run", "ts:m.helper") in _edges(batch, EdgeKind.CALLS)


def test_a_call_to_an_export_nothing_declares_is_dropped(tmp_path: Path) -> None:
    """`m.missing()` names `ts:m.missing`. Only the whole graph knows it is not there, so
    `finalize` drops the edge rather than leave it dangling."""
    batch = _repo(
        tmp_path,
        {
            "m.js": "exports.present = function () {};\n",
            "api.js": "const m = require('./m');\nfunction run() { m.present(); m.missing(); }\n",
        },
    )
    calls = _edges(batch, EdgeKind.CALLS)
    assert ("ts:api.run", "ts:m.present") in calls
    assert ("ts:api.run", "ts:m.missing") not in calls
    known = _ids(batch)
    assert all(dst in known for _, dst in calls)


def test_a_renamed_object_export_is_not_invented(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "m.js": "function helper() {}\nmodule.exports = { run: helper };\n",
            "api.js": "const m = require('./m');\nfunction go() { m.run(); }\n",
        },
    )
    assert "ts:m.run" not in _ids(batch)
    assert ("ts:api.go", "ts:m.run") not in _edges(batch, EdgeKind.CALLS)


def test_jsx_inside_a_js_file_parses(tmp_path: Path) -> None:
    """Under the plain TypeScript grammar this mis-parses into a `type_assertion` (D2)."""
    batch = _repo(
        tmp_path,
        {"App.js": "export const App = () => <div className='x'>{greet()}</div>;\nfunction greet() {}\n"},
    )
    assert ("ts:App.App", "ts:App.greet") in _edges(batch, EdgeKind.CALLS)


def test_class_inheritance_across_a_require(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "base.js": "class Base {}\nmodule.exports = { Base };\n",
            "impl.js": "const { Base } = require('./base');\nclass Impl extends Base {}\n",
        },
    )
    assert ("ts:impl.Impl", "ts:base.Base") in _edges(batch, EdgeKind.IMPLEMENTS)


@pytest.mark.parametrize("importer", ["api.ts", "api.js"])
def test_an_explicit_js_extension_names_the_real_module(tmp_path: Path, importer: str) -> None:
    """ESM requires the extension — in TypeScript too. Stripping only `.ts`/`.tsx` minted
    `ts:mod.js`, a first-party-looking module no file declares, and a CALLS target on it."""
    batch = _repo(
        tmp_path,
        {
            "mod.js": "export function go() {}\n",
            importer: "import { go } from './mod.js';\nexport function run() { go(); }\n",
        },
    )
    assert "ts:mod.js" not in _ids(batch)
    assert ("ts:api.run", "ts:mod.go") in _edges(batch, EdgeKind.CALLS)


def test_typescript_importing_javascript_resolves(tmp_path: Path) -> None:
    """A gradual migration — the payoff for sharing the `ts:` namespace (D4)."""
    batch = _repo(
        tmp_path,
        {
            "legacy.js": "export function old() {}\n",
            "modern.ts": "import { old } from './legacy';\nexport function run(): void { old(); }\n",
        },
    )
    assert ("ts:modern.run", "ts:legacy.old") in _edges(batch, EdgeKind.CALLS)


def test_a_chained_exports_alias_is_the_exports_object(tmp_path: Path) -> None:
    """Express's `lib/application.js` shape — 43 of its 49 exported functions are spelled so."""
    batch = _repo(
        tmp_path,
        {
            "application.js": "var app = exports = module.exports = {};\napp.init = function init() {};\n",
            "server.js": (
                "const application = require('./application');\nfunction boot() { application.init(); }\n"
            ),
        },
    )
    assert "ts:application.init" in _ids(batch)
    assert ("ts:server.boot", "ts:application.init") in _edges(batch, EdgeKind.CALLS)


def test_an_object_assigned_to_module_exports_is_the_exports_object(tmp_path: Path) -> None:
    """Express's `lib/response.js` shape: declare the object, export it, then augment it."""
    batch = _repo(
        tmp_path,
        {
            "response.js": (
                "var res = Object.create(null);\nmodule.exports = res;\nres.send = function send() {};\n"
            )
        },
    )
    assert "ts:response.send" in _ids(batch)


def test_an_object_merely_read_from_exports_is_not_an_alias(tmp_path: Path) -> None:
    """`const e = module.exports` points at the *current* object; a later `module.exports = …`
    leaves it behind, so augmenting `e` does not export anything."""
    batch = _repo(
        tmp_path,
        {"m.js": "const e = module.exports;\nmodule.exports = {};\ne.stale = function () {};\n"},
    )
    assert "ts:m.stale" not in _ids(batch)
