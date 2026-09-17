"""The Cypher export, loaded into a real Neo4j and counted back out.

**Why this is opt-in and the reconciliation test is not.** Two things can go wrong with a
Cypher export and they need different guards. The script can be *syntactically* wrong — only a
real database can say — and it can be *incomplete*, which is what
``test_cypher_emits_one_row_per_fact`` in ``test_graph_export.py`` checks without any database
at all. Completeness is the failure that hides (every count still looks plausible), so that
guard lives in the default gate; syntax fails loudly the first time anyone loads a file, so
this one is gated behind ``RUN_NEO4J_IT=1`` rather than pulling a ~1 GB image on every CI run
for a single test. Same shape as ``tests/sdlc/test_sql_build.py``'s Postgres integration test.

Run it with::

    RUN_NEO4J_IT=1 uv run pytest tests/pkg/test_cypher_roundtrip.py -q

The assertions are the three queries the format exists for: a variable-length traversal, a
cycle search, and an aggregate — none of which the flat projections can answer.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid

import pytest

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.graph_export import export_cypher

_IMAGE = "neo4j:5-community"
_PASSWORD = "spine-roundtrip-probe"


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(("docker", "info"), capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_NEO4J_IT") or not _docker_ready(),
    reason="set RUN_NEO4J_IT=1 with Docker running (pulls neo4j:5-community)",
)


def _batch() -> FactBatch:
    """A graph with a call chain, an import cycle, and parallel call sites.

    Deliberately not the flat fixture: each shape here is one a query below has to find, and
    the parallel edges are the ones an idiomatic ``MERGE`` would collapse.
    """
    b = FactBatch()
    for name in ("a", "b", "c", "d"):
        b.add_node(Node(id=f"py:m.{name}", kind=NodeKind.FUNCTION, name=name, language="python"))
    for mod in ("x", "y"):
        b.add_node(Node(id=f"py:{mod}", kind=NodeKind.MODULE, name=mod, language="python"))

    # A chain a -> b -> c -> d, so a depth-3 traversal from d finds exactly three callers.
    for src, dst, line in (("a", "b", 1), ("b", "c", 2), ("c", "d", 3)):
        b.add_edge(
            Edge(
                src=f"py:m.{src}",
                dst=f"py:m.{dst}",
                kind=EdgeKind.CALLS,
                provenance=Provenance(file="m.py", line=line),
            )
        )
    # Three distinct call sites a -> d. One relationship each, or D2 has regressed.
    for line in (10, 20, 30):
        b.add_edge(
            Edge(
                src="py:m.a",
                dst="py:m.d",
                kind=EdgeKind.CALLS,
                provenance=Provenance(file="m.py", line=line),
            )
        )
    # A two-module import cycle.
    b.add_edge(
        Edge(src="py:x", dst="py:y", kind=EdgeKind.IMPORTS, provenance=Provenance(file="x.py", line=1))
    )
    b.add_edge(
        Edge(src="py:y", dst="py:x", kind=EdgeKind.IMPORTS, provenance=Provenance(file="y.py", line=1))
    )
    return b


@pytest.fixture(scope="module")
def neo4j() -> str:  # type: ignore[misc]
    """A throwaway Neo4j on a random high port. Torn down even if a test fails."""
    name = f"spine-cypher-it-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        ("docker", "run", "-d", "--name", name, "-e", f"NEO4J_AUTH=neo4j/{_PASSWORD}", "-P", _IMAGE),
        check=True,
        capture_output=True,
    )
    try:
        for _ in range(120):
            probe = subprocess.run(
                ("docker", "exec", name, "cypher-shell", "-u", "neo4j", "-p", _PASSWORD, "RETURN 1"),
                capture_output=True,
            )
            if probe.returncode == 0:
                break
            time.sleep(1)
        else:  # pragma: no cover — only on a pathologically slow host
            pytest.fail("neo4j did not become ready within 120s")
        yield name
    finally:
        subprocess.run(("docker", "rm", "-f", name), capture_output=True)


def _query(container: str, cypher: str) -> str:
    out = subprocess.run(
        (
            "docker",
            "exec",
            container,
            "cypher-shell",
            "-u",
            "neo4j",
            "-p",
            _PASSWORD,
            "--format",
            "plain",
            cypher,
        ),
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip().splitlines()[-1].strip()


@pytest.fixture(scope="module")
def loaded(neo4j: str, tmp_path_factory: pytest.TempPathFactory) -> tuple[str, dict[str, int]]:
    """Export the fixture and load it. Every test below reads the same loaded graph."""
    path = tmp_path_factory.mktemp("cypher") / "g.cypher"
    counts = export_cypher(_batch(), path)
    subprocess.run(("docker", "cp", str(path), f"{neo4j}:/tmp/g.cypher"), check=True, capture_output=True)
    subprocess.run(
        ("docker", "exec", neo4j, "cypher-shell", "-u", "neo4j", "-p", _PASSWORD, "--file", "/tmp/g.cypher"),
        check=True,
        capture_output=True,
    )
    return neo4j, counts


def test_the_script_parses_and_every_fact_survives_the_round_trip(
    loaded: tuple[str, dict[str, int]],
) -> None:
    """The load itself proves the syntax; the counts prove nothing was collapsed."""
    container, counts = loaded
    assert _query(container, "MATCH (n:Symbol) RETURN count(n);") == str(counts["nodes"])
    assert _query(container, "MATCH ()-[r]->() RETURN count(r);") == str(counts["edges"])


def test_parallel_call_sites_survive_as_separate_relationships(
    loaded: tuple[str, dict[str, int]],
) -> None:
    """The D2 assertion, against a real database rather than against the emitted text.

    Three ``a -> d`` call sites must be three relationships. An idiomatic
    ``MERGE (a)-[:CALLS]->(b)`` stores one, and every other count in this file still passes.
    """
    container, _ = loaded
    got = _query(
        container,
        "MATCH (:Symbol {id: 'py:m.a'})-[r:CALLS]->(:Symbol {id: 'py:m.d'}) RETURN count(r);",
    )
    assert got == "3"


def test_variable_length_traversal_answers_what_flat_projections_cannot(
    loaded: tuple[str, dict[str, int]],
) -> None:
    """Transitive callers — the reason this format exists at all."""
    container, _ = loaded
    got = _query(
        container,
        "MATCH (c:Symbol)-[:CALLS*1..3]->(:Symbol {id: 'py:m.d'}) RETURN count(DISTINCT c);",
    )
    assert got == "3"  # a, b, c — d is not its own caller


def test_import_cycles_are_findable(loaded: tuple[str, dict[str, int]]) -> None:
    """Cycle detection, the other query class the flat projections cannot express."""
    container, _ = loaded
    got = _query(
        container,
        "MATCH (m:Symbol:Module)-[:IMPORTS*2..4]->(m) RETURN count(DISTINCT m);",
    )
    assert got == "2"


def test_kind_becomes_a_label_beside_the_universal_one(loaded: tuple[str, dict[str, int]]) -> None:
    """D4: ``:Symbol`` carries the constraint, the kind stays queryable as a label."""
    container, _ = loaded
    assert _query(container, "MATCH (n:Symbol:Function) RETURN count(n);") == "4"
    assert _query(container, "MATCH (n:Symbol:Module) RETURN count(n);") == "2"
