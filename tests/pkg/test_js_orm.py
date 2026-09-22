"""Sequelize models in JavaScript → Entity / Field / REFERENCES (javascript-support-roadmap P4)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_typescript", reason="install the 'typescript' extra")

from orchestrator.pkg.data_layer_link import link_data_layer  # noqa: E402
from orchestrator.pkg.extractor import RepoCodeExtractor  # noqa: E402
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind  # noqa: E402
from orchestrator.pkg.js_extractor import JavaScriptExtractor  # noqa: E402


def _repo(tmp_path: Path, files: dict[str, str]) -> FactBatch:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return RepoCodeExtractor(extractors=[JavaScriptExtractor()]).extract(tmp_path)


def _entities(batch: FactBatch) -> set[str]:
    return {n.id for n in batch.nodes if n.kind is NodeKind.ENTITY}


def _fields(batch: FactBatch) -> set[str]:
    return {n.id for n in batch.nodes if n.kind is NodeKind.FIELD and n.id.startswith("ts:entity:")}


def _refs(batch: FactBatch) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.REFERENCES}


_USER = (
    "const { DataTypes } = require('sequelize');\n"
    "module.exports = (sequelize) => {\n"
    "  sequelize.define('user', {\n"
    "    id: { primaryKey: true, type: DataTypes.INTEGER },\n"
    "    username: { type: DataTypes.STRING, validate: { len: [3] } },\n"
    "  });\n"
    "};\n"
)


def test_a_model_defined_inside_a_function_is_an_entity(tmp_path: Path) -> None:
    """The official example's shape: the connection is a parameter, the define is in its body."""
    batch = _repo(tmp_path, {"models/user.js": _USER})
    assert _entities(batch) == {"ts:entity:user"}
    assert ("ts:models/user", "ts:entity:user") in {
        (e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CONTAINS
    }


def test_columns_are_the_attribute_keys_and_not_their_options(tmp_path: Path) -> None:
    """`type` and `validate` are properties *of* a column, not columns."""
    batch = _repo(tmp_path, {"models/user.js": _USER})
    assert _fields(batch) == {"ts:entity:user.id", "ts:entity:user.username"}


def test_define_without_the_sequelize_import_is_not_a_model(tmp_path: Path) -> None:
    """No ORM marker, no Entity: `define` is an ordinary method name."""
    batch = _repo(tmp_path, {"registry.js": "function setup(r) { r.define('user', { id: 1 }); }\n"})
    assert not _entities(batch)


def test_a_class_extending_sequelizes_model_is_an_entity(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "post.js": (
                "const { Model, DataTypes } = require('sequelize');\n"
                "class Post extends Model {}\n"
                "Post.init({ title: DataTypes.STRING, body: DataTypes.TEXT }, { sequelize });\n"
                "module.exports = { Post };\n"
            )
        },
    )
    assert _entities(batch) == {"ts:entity:Post"}
    assert _fields(batch) == {"ts:entity:Post.title", "ts:entity:Post.body"}


def test_a_base_merely_named_model_is_not_sequelizes(tmp_path: Path) -> None:
    """Objection.js names its base `Model` too. The marker is the binding, not the name."""
    batch = _repo(
        tmp_path,
        {"person.js": "const { Model } = require('objection');\nclass Person extends Model {}\n"},
    )
    assert not _entities(batch)


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        ("instrument.belongsTo(orchestra);", ("ts:entity:instrument", "ts:entity:orchestra")),
        ("orchestra.hasMany(instrument);", ("ts:entity:instrument", "ts:entity:orchestra")),
        ("orchestra.hasOne(instrument);", ("ts:entity:instrument", "ts:entity:orchestra")),
    ],
)
def test_references_follow_the_foreign_key(tmp_path: Path, call: str, expected: tuple[str, str]) -> None:
    """`belongsTo` keys the receiver; `hasMany`/`hasOne` key the argument."""
    batch = _repo(tmp_path, _two_models(call))
    assert _refs(batch) == {expected}


def test_the_redundant_pair_states_one_foreign_key(tmp_path: Path) -> None:
    batch = _repo(tmp_path, _two_models("orchestra.hasMany(instrument);\ninstrument.belongsTo(orchestra);"))
    assert _refs(batch) == {("ts:entity:instrument", "ts:entity:orchestra")}


def test_a_models_registry_access_names_the_model(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path, _two_models("", setup="sequelize.models.instrument.belongsTo(sequelize.models.orchestra);")
    )
    assert _refs(batch) == {("ts:entity:instrument", "ts:entity:orchestra")}


def test_belongs_to_many_draws_no_edge(tmp_path: Path) -> None:
    """Its keys live on a join table the source need not declare."""
    batch = _repo(tmp_path, _two_models("orchestra.belongsToMany(instrument, { through: 'x' });"))
    assert not _refs(batch)


def test_an_association_naming_an_undefined_model_is_dropped(tmp_path: Path) -> None:
    """Both ends are names. `finalize` keeps the edge only when both are entities."""
    batch = _repo(tmp_path, _two_models("ghost.belongsTo(orchestra);\ninstrument.belongsTo(phantom);"))
    assert not _refs(batch)


def test_the_orm_entity_collapses_onto_a_sql_table(tmp_path: Path) -> None:
    """`data_layer_link` needs nothing JavaScript-specific — it matches by name. The schema is
    authoritative, so the ORM entity folds onto `sql:users`."""
    pytest.importorskip("sqlglot", reason="install the 'sql' extra")
    for rel, text in {
        "models/user.js": _USER,
        "schema.sql": "CREATE TABLE users (id INT PRIMARY KEY, username TEXT);\n",
    }.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    batch = link_data_layer(RepoCodeExtractor().extract(tmp_path))
    entities = _entities(batch)
    assert any(e.startswith("sql:") for e in entities)
    assert "ts:entity:user" not in entities


def _two_models(associations: str, *, setup: str = "") -> dict[str, str]:
    define = (
        "const {{ DataTypes }} = require('sequelize');\n"
        "module.exports = (sequelize) => {{ sequelize.define('{name}', {{ id: DataTypes.INTEGER }}); }};\n"
    )
    body = setup or f"const {{ instrument, orchestra }} = sequelize.models;\n{associations}"
    return {
        "models/orchestra.js": define.format(name="orchestra"),
        "models/instrument.js": define.format(name="instrument"),
        "setup.js": (
            f"function applyExtraSetup(sequelize) {{\n{body}\n}}\nmodule.exports = {{ applyExtraSetup }};\n"
        ),
    }
