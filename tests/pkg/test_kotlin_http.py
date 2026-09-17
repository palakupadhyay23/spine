"""PKG: Retrofit is read as a **client**, not a provider.

P3 of kotlin-support-roadmap.md, D10. The central assertion is a negative one —
an Android app must not acquire ``Endpoint`` nodes for the routes it *calls* —
so most of these tests check that the graph stayed byte-identical while the
calls went somewhere else: ``unresolved_calls``, the side-channel ``pkg joins``
proposes cross-repository matches from.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, NodeKind
from orchestrator.pkg.kotlin_extractor import KotlinExtractor
from orchestrator.pkg.python_client import PendingCall

pytest.importorskip("tree_sitter_kotlin", reason="install the 'kotlin' extra")

API_KT = """\
package shop.net

import retrofit2.http.GET
import retrofit2.http.POST

private const val ORDERS = "orders"

interface ShopApi {
    @GET(value = "topics")
    suspend fun getTopics(): List<String>

    @POST("carts")
    suspend fun createCart(): String

    @GET(ORDERS)
    suspend fun getOrders(): List<String>
}
"""


def _run(tmp_path: Path, src: str = API_KT, name: str = "Api.kt") -> tuple[list[PendingCall], set[EdgeKind]]:
    """Extract one file; return its client-call candidates and the emitted edge kinds."""
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(src, encoding="utf-8")
    ex = KotlinExtractor()
    batch = ex.finalize(ex.extract(path=f, module=ex.module_name(f, tmp_path), rel=name))
    return ex.unresolved_calls, {e.kind for e in batch.edges}


def _calls(tmp_path: Path, src: str = API_KT, name: str = "Api.kt") -> set[tuple[str, str, str]]:
    pending, _ = _run(tmp_path, src, name)
    return {(c.verb, c.path, c.caller_id) for c in pending}


def test_retrofit_methods_become_call_candidates(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    assert ("GET", "/topics", "java:shop.net.ShopApi.getTopics") in calls
    assert ("POST", "/carts", "java:shop.net.ShopApi.createCart") in calls


def test_both_annotation_spellings_are_read(tmp_path: Path) -> None:
    """`@GET(value = "…")` and `@GET("…")` are the same call; real code uses both."""
    paths = {path for _, path, _ in _calls(tmp_path)}
    assert {"/topics", "/carts"} <= paths


def test_a_computed_path_yields_no_candidate(tmp_path: Path) -> None:
    """`@GET(ORDERS)` names no route this reader can know — so it claims none."""
    callers = {caller for _, _, caller in _calls(tmp_path)}
    assert "java:shop.net.ShopApi.getOrders" not in callers


def test_no_endpoint_and_no_consumes_reach_the_graph(tmp_path: Path) -> None:
    """D10, stated as an absence: an app that calls a route does not serve it.

    Emitting either would assert this repository serves `/topics`, and creating
    the endpoint node alongside the edge would make `pkg verify` report zero
    dangling while doing it — self-consistent invention.
    """
    f = tmp_path / "Api.kt"
    f.write_text(API_KT, encoding="utf-8")
    batch = RepoCodeExtractor().extract(tmp_path)
    assert [n for n in batch.nodes if n.kind is NodeKind.ENDPOINT] == []
    assert [e for e in batch.edges if e.kind is EdgeKind.CONSUMES] == []


def test_a_literal_base_url_prefixes_the_path(tmp_path: Path) -> None:
    src = """\
package shop.net

import retrofit2.Retrofit
import retrofit2.http.GET

interface ShopApi {
    @GET("topics")
    suspend fun getTopics(): List<String>
}

fun build(): Retrofit = Retrofit.Builder()
    .baseUrl("https://example.test/v1/")
    .build()
"""
    paths = {path for _, path, _ in _calls(tmp_path, src, "Net.kt")}
    assert paths == {"/v1/topics"}


def test_a_non_literal_base_url_leaves_the_call_path_only(tmp_path: Path) -> None:
    """What the validation app actually does — `baseUrl(NiaBaseUrl)` from BuildConfig.

    The host is build configuration, not a fact about the code, so the call stays
    path-only rather than acquiring a prefix that was guessed.
    """
    src = """\
package shop.net

import retrofit2.Retrofit
import retrofit2.http.GET

interface ShopApi {
    @GET("topics")
    suspend fun getTopics(): List<String>
}

fun build(): Retrofit = Retrofit.Builder()
    .baseUrl(BuildConfig.BACKEND_URL)
    .build()
"""
    paths = {path for _, path, _ in _calls(tmp_path, src, "Net.kt")}
    assert paths == {"/topics"}


def test_a_call_joins_when_something_in_scope_serves_it(tmp_path: Path) -> None:
    """The other half: when a provider *is* present, the edge lands.

    Same monorepo, a Java JAX-RS resource beside the Kotlin app. The candidate
    stops being a candidate and becomes a real ``CONSUMES`` edge — which is what
    ``pkg joins`` does across repositories, done here in one.
    """
    pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")
    src = tmp_path / "src" / "main" / "java" / "shop"
    src.mkdir(parents=True)
    (src / "ShopApi.kt").write_text(
        "package shop\n\nimport retrofit2.http.GET\n\n"
        'interface ShopApi {\n    @GET("v1/topics")\n    suspend fun getTopics(): List<String>\n}\n',
        encoding="utf-8",
    )
    (src / "TopicResource.java").write_text(
        "package shop;\n\nimport jakarta.ws.rs.GET;\nimport jakarta.ws.rs.Path;\n\n"
        '@Path("/v1")\npublic class TopicResource {\n    @GET\n    @Path("/topics")\n'
        "    public String list() { return null; }\n}\n",
        encoding="utf-8",
    )
    batch = RepoCodeExtractor().extract(tmp_path)
    consumes = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CONSUMES}
    assert ("java:shop.ShopApi.getTopics", "java:endpoint:GET /v1/topics") in consumes


def test_join_to_endpoints_reports_how_many_matched(tmp_path: Path) -> None:
    """The count separates 'served here' from 'a cross-repo candidate'."""
    from orchestrator.pkg.facts import FactBatch, Provenance
    from orchestrator.pkg.kotlin_http import ClientState, join_to_endpoints

    state = ClientState()
    state.calls.append(
        PendingCall(
            verb="GET",
            path="/topics",
            caller_id="java:a.B.c",
            provenance=Provenance("Api.kt", 1),
        )
    )
    unmatched, joined = join_to_endpoints(state, FactBatch())
    assert joined == 0 and len(unmatched) == 1


def test_a_room_dao_query_is_not_mistaken_for_a_retrofit_call(tmp_path: Path) -> None:
    """Both frameworks spell an annotation `@Query`, and they mean opposite things.

    Room's is a method annotation holding SQL; Retrofit's is a *parameter*
    annotation naming a URL query key. Nothing here should reach the HTTP reader.
    """
    src = """\
package shop.db

import androidx.room.Dao
import androidx.room.Query

@Dao
interface TopicDao {
    @Query(value = "SELECT * FROM topics")
    fun all(): List<String>
}
"""
    pending, _ = _run(tmp_path, src, "Dao.kt")
    assert pending == []


# ---- §11 finding 21: `@GET` is not Retrofit's until an import says it is ----


def test_a_get_annotation_from_another_library_is_not_a_retrofit_call(tmp_path: Path) -> None:
    """JAX-RS puts `@GET` on exactly this kind of method, and so do several others.

    Without the check every such method became a `pkg joins` candidate — a claim that this
    repository calls an endpoint at that path. `jvm_routes.resolves_into_spring` gets the
    identical question right fifteen lines away in a sibling module.
    """
    f = tmp_path / "Api.kt"
    f.write_text(
        """\
package app.api

import javax.ws.rs.GET
import javax.ws.rs.Path

interface Resource {
    @GET
    @Path("/topics")
    fun topics(): String
}
""",
        encoding="utf-8",
    )
    ex = KotlinExtractor()
    ex.finalize(ex.extract(path=f, module="app.api", rel="Api.kt"))
    assert ex.unresolved_calls == []


def test_a_retrofit_wildcard_import_is_enough(tmp_path: Path) -> None:
    """`import retrofit2.http.*` is how real Retrofit code is written."""
    f = tmp_path / "Api.kt"
    f.write_text(
        """\
package app.api

import retrofit2.http.*

interface Api {
    @GET("topics")
    suspend fun topics(): String
}
""",
        encoding="utf-8",
    )
    ex = KotlinExtractor()
    ex.finalize(ex.extract(path=f, module="app.api", rel="Api.kt"))
    assert [(c.verb, c.path) for c in ex.unresolved_calls] == [("GET", "/topics")]


def test_join_candidates_do_not_leak_between_repositories(tmp_path: Path) -> None:
    """§11 finding 19. `ClientState.clear()` deliberately preserves `unmatched`, so the
    front-end's accumulator grew across repositories: `reset_unresolved()` cleared the
    list the attribute pointed at, and the next `finalize` rebuilt it from the state that
    still held the previous repository's calls — proposing repo A's calls as repo B's."""
    from orchestrator.pkg.extractor import RepoCodeExtractor

    for name in ("a", "b"):
        d = tmp_path / name
        d.mkdir()
        (d / "Api.kt").write_text(
            f"""\
package svc.{name}

import retrofit2.http.GET

interface Api {{
    @GET("{name}/topics")
    suspend fun topics(): String
}}
""",
            encoding="utf-8",
        )
    ex = RepoCodeExtractor()
    ex.extract(tmp_path / "a")
    ex.reset_unresolved()
    ex.extract(tmp_path / "b")
    assert [c.path for c in ex.unresolved_calls] == ["/b/topics"]
