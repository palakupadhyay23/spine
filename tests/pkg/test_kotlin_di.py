"""PKG: Hilt/Dagger bindings as ``PROVIDES``, and the blast radius it unlocks (P4, D15).

``PROVIDES`` earned a place in a closed enum by carrying a fact nothing else does:
*which* implementation is wired behind an interface. These tests pin that claim —
including the measurement that motivated it, that ``blast_radius`` on a repository
implementation returns **nothing** without this edge, because dependency injection
means no call site ever names the implementation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg import FactStore
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, FactBatch

pytest.importorskip("tree_sitter_kotlin", reason="install the 'kotlin' extra")

DI_KT = """\
package shop.di

import dagger.Binds
import dagger.Module
import dagger.Provides
import javax.inject.Named

interface CartRepository {
    fun items(): List<String>
}

class OfflineCartRepository : CartRepository {
    override fun items(): List<String> = emptyList()
}

class FakeCartRepository : CartRepository {
    override fun items(): List<String> = emptyList()
}

class Clock

@Module
interface DataModule {
    @Binds
    fun bindsCartRepository(impl: OfflineCartRepository): CartRepository

    @Binds
    @Named("fake")
    fun bindsFake(impl: FakeCartRepository): CartRepository
}

@Module
object ClockModule {
    @Provides
    fun providesClock(): Clock = Clock()
}

class StrayBinding {
    @Binds
    fun bindsClock(impl: Clock): Clock = impl
}

class CartViewModel(private val repository: CartRepository) {
    fun load() {
        repository.items()
    }
}
"""


def _facts(tmp_path: Path, src: str = DI_KT, name: str = "Di.kt") -> FactBatch:
    (tmp_path / name).write_text(src, encoding="utf-8")
    return RepoCodeExtractor().extract(tmp_path)


def _provides(batch: FactBatch) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.PROVIDES}


def test_binds_points_from_the_implementation_to_the_interface(tmp_path: Path) -> None:
    """The implementation is the source because it is what a reader changes."""
    assert (
        "java:shop.di.OfflineCartRepository",
        "java:shop.di.CartRepository",
    ) in _provides(_facts(tmp_path))


def test_provides_points_from_the_factory_function(tmp_path: Path) -> None:
    """`@Provides` has no implementation type — the factory is the provider."""
    assert (
        "java:shop.di.ClockModule.providesClock",
        "java:shop.di.Clock",
    ) in _provides(_facts(tmp_path))


def test_two_qualified_providers_both_keep_their_edge(tmp_path: Path) -> None:
    """Choosing one would mean modelling Dagger's component graph (D15)."""
    provides = _provides(_facts(tmp_path))
    sources = {src for src, dst in provides if dst == "java:shop.di.CartRepository"}
    assert sources == {"java:shop.di.OfflineCartRepository", "java:shop.di.FakeCartRepository"}


def test_a_binding_outside_a_module_is_not_wiring(tmp_path: Path) -> None:
    """`StrayBinding` carries a real `@Binds`; its class carries no `@Module`."""
    assert not any(src.startswith("java:shop.di.StrayBinding") for src, _ in _provides(_facts(tmp_path)))
    assert ("java:shop.di.Clock", "java:shop.di.Clock") not in _provides(_facts(tmp_path))


def test_implements_alone_cannot_answer_the_question(tmp_path: Path) -> None:
    """The argument for the new edge kind, made concrete.

    Both implementations satisfy `IMPLEMENTS`, so it is true of the wired one and
    the fake alike. `PROVIDES` is what records that a module wired them — and here
    both are wired, which is exactly why the answer is "two providers", not one.
    """
    batch = _facts(tmp_path)
    implements = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    assert len(implements) == 2  # IMPLEMENTS cannot distinguish them
    assert len(_provides(batch)) == 3  # PROVIDES records what each module declares


def test_blast_radius_reaches_the_injecting_code(tmp_path: Path) -> None:
    """P4's exit criterion, and the measurement that justified the edge.

    Nothing calls `OfflineCartRepository` — every call site was handed the
    interface — so its inbound edges are empty and the blast radius without
    `PROVIDES` is nothing at all. Following it outbound to the interface, and on to
    the interface's members, reaches the code that actually injects it.
    """
    batch = _facts(tmp_path)
    store = FactStore(batch)
    reached = {node.id for node, _ in store.impact_of("java:shop.di.OfflineCartRepository", max_depth=3)}
    assert "java:shop.di.CartRepository" in reached
    assert "java:shop.di.CartViewModel.load" in reached


def test_a_repo_with_no_di_gains_no_provides_edges(tmp_path: Path) -> None:
    src = "package shop.plain\n\ninterface Repo\n\nclass Impl : Repo\n"
    assert _provides(_facts(tmp_path, src, "Plain.kt")) == set()
