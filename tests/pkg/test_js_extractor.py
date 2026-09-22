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
            # `twice` is exported too — a decoy, so a binding by the local name would land on a
            # real node. Without it `finalize` dropped the wrong edge anyway and this passed
            # whatever the binding did.
            "util.js": "exports.double = (x) => x * 2;\nexports.twice = (x) => x * 2;\n",
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
    """Each export form in a file that never replaces `module.exports`."""
    batch = _repo(
        tmp_path,
        {
            "m.js": "exports.a = function () {};\nmodule.exports.b = () => {};\n",
            "n.js": "module.exports = { c() {}, d: function () {}, e: () => {} };\n",
        },
    )
    functions = {n.id for n in batch.nodes if n.kind is NodeKind.FUNCTION}
    assert {"ts:m.a", "ts:m.b", "ts:n.c", "ts:n.d", "ts:n.e"} <= functions


def test_members_set_before_module_exports_is_replaced_are_not_exported(tmp_path: Path) -> None:
    """`require` returns the *new* object. An earlier version of this test asserted all five were
    exported — it encoded the naive reading, not CommonJS: `a` and `b` were set on the object
    `module.exports = {…}` threw away."""
    batch = _repo(
        tmp_path,
        {
            "m.js": (
                "exports.a = function () {};\n"
                "module.exports.b = () => {};\n"
                "module.exports = { c() {} };\n"
                "module.exports.d = () => {};\n"
            )
        },
    )
    functions = {n.id for n in batch.nodes if n.kind is NodeKind.FUNCTION}
    assert {"ts:m.c", "ts:m.d"} <= functions
    assert not {"ts:m.a", "ts:m.b"} & functions


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


def _exposes(batch: FactBatch) -> set[tuple[str, str]]:
    return _edges(batch, EdgeKind.EXPOSES)


def test_a_router_bound_through_module_exports_is_read(tmp_path: Path) -> None:
    """`var app = module.exports = express()` — 18 of express's 28 example apps."""
    batch = _repo(
        tmp_path,
        {
            "app.js": "var express = require('express');\nvar app = module.exports = express();\n"
            "function home(req, res) {}\napp.get('/', home);\n"
        },
    )
    assert ("ts:endpoint:GET /", "ts:app.home") in _exposes(batch)


def test_a_handler_named_through_a_require_namespace_is_exposed(tmp_path: Path) -> None:
    """`app.get('/', site.index)` — express's route-separation idiom."""
    batch = _repo(
        tmp_path,
        {
            "site.js": "exports.index = function (req, res) {};\n",
            "app.js": "const express = require('express');\nconst site = require('./site');\n"
            "const app = express();\napp.get('/', site.index);\n",
        },
    )
    assert ("ts:endpoint:GET /", "ts:site.index") in _exposes(batch)


def test_a_handler_named_through_this_modules_exports_is_exposed(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "app.js": "const express = require('express');\nconst app = express();\n"
            "exports.version = function (req, res) {};\napp.get('/v', exports.version);\n"
        },
    )
    assert ("ts:endpoint:GET /v", "ts:app.version") in _exposes(batch)


def test_an_exposes_to_a_handler_nothing_declares_is_dropped(tmp_path: Path) -> None:
    """The route is real, so its Endpoint stays; the handler is not, so its edge goes."""
    batch = _repo(
        tmp_path,
        {
            "users.js": "exports.list = function () {};\n",
            "app.js": "const express = require('express');\nconst users = require('./users');\n"
            "const app = express();\napp.post('/u', users.missing);\n",
        },
    )
    assert "ts:endpoint:POST /u" in _ids(batch)
    assert not _exposes(batch)


def test_typescript_does_not_bind_member_handlers(tmp_path: Path) -> None:
    """TypeScript has no `finalize` check on what it names, so it must not resolve by name:
    a module without `list` would leave a dangling EXPOSES. Wiring this in for TypeScript needs
    that check first — this test is what should stop it arriving without one."""
    batch = _repo(
        tmp_path,
        {
            "handlers.ts": "export function list(): void {}\n",
            "app.ts": "import express from 'express';\nimport * as handlers from './handlers';\n"
            "const app = express();\napp.get('/', handlers.list);\n",
        },
    )
    assert "ts:endpoint:GET /" in _ids(batch)
    assert not _exposes(batch)


# ── review pass 2: precision rules added after the maintainer review of #435 ──────────


def test_a_member_call_through_a_rebound_namespace_name_does_not_resolve(tmp_path: Path) -> None:
    """`const user = require('./user')`, then a function that rebinds `user`: its `user.save()`
    reaches whatever that function was handed, not the module's export. Byte-accurate, so a
    one-liner is covered too."""
    batch = _repo(
        tmp_path,
        {
            "user.js": "exports.save = function () {};\n",
            "api.js": (
                "const user = require('./user');\n"
                "function direct() { user.save(); }\n"
                "function viaParam(user) { user.save(); }\n"
                "function viaLocal() { const user = makeUser(); user.save(); }\n"
                "function viaCallback(users) { users.forEach(function (user) { user.save(); }); }\n"
            ),
        },
    )
    assert {src for src, dst in _edges(batch, EdgeKind.CALLS) if dst == "ts:user.save"} == {"ts:api.direct"}


def test_typescript_still_calls_a_namespace_import(tmp_path: Path) -> None:
    """`import * as moment` then `moment()` is legal for an `export =` module. Refusing it is
    CommonJS-only; TypeScript resolves it as it always has."""
    batch = _repo(
        tmp_path, {"a.ts": "import * as moment from 'moment';\nexport function f(): void { moment(); }\n"}
    )
    assert ("ts:a.f", "ts:moment:moment") in _edges(batch, EdgeKind.CALLS)


def test_function_prototype_members_on_a_require_binding_name_no_export(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {"a.js": "const EventEmitter = require('events');\nfunction Foo() { EventEmitter.call(this); }\n"},
    )
    assert "ts:events:call" not in _ids(batch)
    assert not _edges(batch, EdgeKind.CALLS)


def test_requiring_the_package_root_reaches_the_root_module(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "index.js": "exports.f = function () {};\n",
            "lib/a.js": "const r = require('..');\nconst i = require('../index');\nfunction g() { r.f(); }\n",
        },
    )
    assert ("ts:lib/a.g", "ts:<root>.f") in _edges(batch, EdgeKind.CALLS)
    assert not {"ts:.", "ts:index"} & _ids(batch)


def test_a_renamed_export_routes_the_call_to_what_is_exported(tmp_path: Path) -> None:
    """`module.exports = { run: helper }` beside a private `function run`: `m.run()` is `helper`."""
    batch = _repo(
        tmp_path,
        {
            "m.js": "function run() {}\nfunction helper() {}\nmodule.exports = { run: helper };\n",
            "api.js": "const m = require('./m');\nfunction go() { m.run(); }\n",
        },
    )
    calls = _edges(batch, EdgeKind.CALLS)
    assert ("ts:api.go", "ts:m.helper") in calls
    assert ("ts:api.go", "ts:m.run") not in calls


def test_a_renamed_export_routes_the_endpoint_to_what_is_exported(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "site.js": (
                "function index(req, res) {}\n"
                "function realIndex(req, res) {}\n"
                "module.exports = { index: realIndex };\n"
            ),
            "app.js": (
                "const express = require('express');\nconst site = require('./site');\n"
                "const app = express();\napp.get('/', site.index);\n"
            ),
        },
    )
    assert _exposes(batch) == {("ts:endpoint:GET /", "ts:site.realIndex")}


@pytest.mark.parametrize(
    "module",
    [
        pytest.param("var o = {};\nexports = o;\no.run = function run() {};\n", id="bare-exports-rebind"),
        pytest.param(
            "var a = {}, b = {};\nmodule.exports = a;\na.run = function run() {};\nmodule.exports = b;\n",
            id="module-exports-twice",
        ),
        pytest.param(
            "let app = module.exports = {};\napp = {};\napp.run = function run() {};\n", id="alias-reassigned"
        ),
        pytest.param("module.exports = function run() {};\n", id="function-default-export"),
    ],
)
def test_an_export_the_file_undoes_is_not_an_export(tmp_path: Path, module: str) -> None:
    batch = _repo(
        tmp_path, {"m.js": module, "api.js": "const m = require('./m');\nfunction go() { m.run(); }\n"}
    )
    assert not {dst for src, dst in _edges(batch, EdgeKind.CALLS) if src == "ts:api.go"}


def test_the_chained_exports_alias_to_a_declared_object(tmp_path: Path) -> None:
    """`exports = module.exports = res` — express's `lib/response.js` rebinds both."""
    batch = _repo(
        tmp_path,
        {
            "r.js": "var res = {};\nexports = module.exports = res;\nres.send = function send() {};\n",
            "api.js": "const r = require('./r');\nfunction go() { r.send(); }\n",
        },
    )
    assert ("ts:api.go", "ts:r.send") in _edges(batch, EdgeKind.CALLS)


def test_a_route_handler_through_an_exports_alias(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "app.js": (
                "const express = require('express');\nvar app = module.exports = express();\n"
                "app.home = function home(req, res) {};\napp.get('/', app.home);\n"
            )
        },
    )
    assert ("ts:endpoint:GET /", "ts:app.home") in _exposes(batch)


def test_compiled_javascript_beside_its_typescript_is_skipped(tmp_path: Path) -> None:
    """`tsc` output: both map to `ts:foo`, and the `.js` sorts first and would take it over."""
    batch = _repo(
        tmp_path,
        {"foo.ts": "export function bar(): void {}\n", "foo.js": "function bar() {}\nexports.bar = bar;\n"},
    )
    foo = next(n for n in batch.nodes if n.id == "ts:foo")
    assert foo.language == "typescript" and foo.provenance is not None and foo.provenance.file == "foo.ts"


def test_a_string_key_no_call_site_can_spell_is_not_an_export(tmp_path: Path) -> None:
    batch = _repo(tmp_path, {"m.js": "module.exports = { 'a-b': function () {}, ok: function () {} };\n"})
    functions = {n.id for n in batch.nodes if n.kind is NodeKind.FUNCTION}
    assert "ts:m.ok" in functions and "ts:m.a-b" not in functions


def test_a_destructuring_default_binds_under_the_export_name(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "u.js": "exports.double = (x) => x * 2;\n",
            "api.js": "const { double = null } = require('./u');\nfunction run(x) { return double(x); }\n",
        },
    )
    assert ("ts:api.run", "ts:u.double") in _edges(batch, EdgeKind.CALLS)


def test_a_member_require_bound_under_another_name_records_the_import_only(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "u.js": "exports.double = (x) => x * 2;\nexports.twice = (x) => x * 2;\n",
            "api.js": "const twice = require('./u').double;\nfunction run(x) { return twice(x); }\n",
        },
    )
    assert ("ts:api", "ts:u") in _edges(batch, EdgeKind.IMPORTS)
    assert not {dst for src, dst in _edges(batch, EdgeKind.CALLS) if src == "ts:api.run"}


def test_externals_a_javascript_file_mints_are_tagged_javascript(tmp_path: Path) -> None:
    batch = _repo(tmp_path, {"a.js": "const fs = require('fs');\n"})
    assert next(n for n in batch.nodes if n.id == "ts:fs").language == "javascript"


def test_the_existence_check_leaves_typescript_edges_alone(tmp_path: Path) -> None:
    """Only this front-end's name-based edges are checked; TypeScript's are its own business."""
    batch = _repo(tmp_path, {"a.ts": "import { x } from './nowhere';\nexport function f(): void { x(); }\n"})
    assert ("ts:a.f", "ts:nowhere.x") in _edges(batch, EdgeKind.CALLS)


def test_a_typescript_router_bound_through_module_exports_is_read(tmp_path: Path) -> None:
    """The chain walk runs for TypeScript too — an additive change, recorded as such."""
    batch = _repo(
        tmp_path,
        {
            "app.ts": (
                "import express from 'express';\n"
                "const app = module.exports = express();\n"
                "function h(): void {}\n"
                "app.get('/x', h);\n"
            )
        },
    )
    assert ("ts:endpoint:GET /x", "ts:app.h") in _exposes(batch)
