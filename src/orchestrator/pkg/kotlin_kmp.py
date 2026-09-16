"""Kotlin Multiplatform — source sets, and ``expect``/``actual`` as a contract.

P7 of docs/specs/kotlin-support-roadmap.md, D17.

A multiplatform module declares one API in ``commonMain`` and implements it once per
target: ``expect abstract class ViewModel()`` beside ``actual abstract class ViewModel``
in ``androidMain`` and again in ``iosMain``. Three declarations, same package, same name —
and therefore, under the package rule every other Kotlin declaration follows, **the same
id**. ``FactBatch`` de-duplicates by id, so two of the three would simply vanish, taking
their provenance with them, and nothing would report it.

So an ``actual`` declaration's id carries the source set that declares it:
``java:co.touchlab.kampkit.models.ViewModel@androidMain``. The ``expect`` keeps the plain
id, which is the right way round — common code is written against the contract, so a type
reference from ``commonMain`` resolving to the plain id resolves to the thing the source
actually names.

**``IMPLEMENTS`` actual → expect.** An ``actual`` fulfils a contract exactly as a class
fulfils an interface, and the graph already has an edge for that. It is emitted in
``finalize`` because the ``expect`` is in a different file, and only when that ``expect``
is present in the tree: an ``actual`` whose contract was never scanned gets **no** edge
rather than one pointing at a node nobody has seen.

**Only ``actual`` declarations are suffixed, and only at the top level.** A member of an
``actual`` class inherits the suffix through its parent id, so nothing is doubled. And a
declaration that carries no ``actual`` keeps its plain id even in a platform source set —
suffixing every platform declaration would rename ids that never collided, and break the
ordinary case where ``androidMain`` code simply calls its own helper.

**What this does *not* fix.** D17 was written expecting the same suffix to resolve Android
product *flavours* (``src/demo`` and ``src/prod`` declaring one class in one package). It
cannot: a flavour variant carries no keyword to key off, and suffixing by directory alone
would rename declarations that do not collide while leaving calls into flavour code
pointing at an id nothing declares. That gap stays measured and recorded, not papered over.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, NodeKind, Provenance

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

#: `…/src/<sourceSet>/kotlin/…` — Gradle's layout, for every JVM and KMP module alike.
_SOURCE_SET = re.compile(r"(?:^|/)src/([^/]+)/")

#: The source set whose presence marks a Gradle module as multiplatform.
COMMON_MAIN = "commonMain"

#: Source sets that are the default for their layout and so add nothing to an id.
_DEFAULT_SOURCE_SETS = frozenset({"main", "test", COMMON_MAIN, "commonTest"})

#: The separator between a declaration id and the source set that declares it.
SOURCE_SET_MARK = "@"


def source_set_of(file: str) -> str:
    """The Gradle source-set directory a file sits in — ``""`` when it has none.

    ``shared/src/androidMain/kotlin/co/touchlab/Foo.kt`` → ``androidMain``.
    """
    match = _SOURCE_SET.search(file)
    return match.group(1) if match else ""


def platform_modifier(node: TSNode, source: bytes) -> str:
    """``"expect"``, ``"actual"``, or ``""`` — read off the declaration's modifiers.

    The grammar gives these their own node type (``platform_modifier``), so this is a
    reading rather than a keyword hunt through the declaration's text.
    """
    modifiers = next((c for c in node.named_children if c.type == "modifiers"), None)
    if modifiers is None:
        return ""
    for child in modifiers.named_children:
        if child.type == "platform_modifier":
            return source[child.start_byte : child.end_byte].decode("utf-8", "replace").strip()
    return ""


def id_suffix(node: TSNode, source: bytes, source_set: str) -> str:
    """``"@androidMain"`` for an ``actual`` in a non-default source set, else ``""``.

    A default source set (``main``, ``commonMain``) adds nothing: there is only one of
    it, so there is nothing for the suffix to disambiguate.
    """
    if not source_set or source_set in _DEFAULT_SOURCE_SETS:
        return ""
    if platform_modifier(node, source) != "actual":
        return ""
    return f"{SOURCE_SET_MARK}{source_set}"


def link_actuals(batch: FactBatch) -> int:
    """Emit ``IMPLEMENTS`` from every suffixed ``actual`` to its ``expect``. Returns the count.

    Runs in ``finalize``, when every declaration in the repository is known. The
    ``expect``'s id is this one with the suffix removed, which is why the suffix is a
    marker rather than a rename: the contract is recoverable from the implementation.
    """
    known = {node.id for node in batch.nodes}
    linked = 0
    for node in list(batch.nodes):
        if node.kind not in (NodeKind.TYPE, NodeKind.FUNCTION):
            continue
        contract, mark, tail = node.id.rpartition(SOURCE_SET_MARK)
        # The suffix must be the *end* of the id. A member of an `actual` class inherits
        # it through its parent (`…ViewModel@iosMain.clear`) without carrying the keyword
        # itself — and need not: `iosMain`'s `clear` has no counterpart in the `expect`
        # at all. Only the declaration the source marked `actual` fulfils the contract.
        if not mark or "." in tail:
            continue
        if contract not in known:
            # No `expect` in this tree. Saying nothing is the honest answer: the
            # contract may be real, but this run has not seen it.
            continue
        provenance = node.provenance or Provenance("", 0)
        batch.add_edge(Edge(node.id, contract, EdgeKind.IMPLEMENTS, provenance))
        linked += 1
    return linked


def multiplatform_modules(files: set[str], module_paths: tuple[str, ...]) -> frozenset[str]:
    """Gradle modules that have a ``commonMain`` source set, so are multiplatform.

    Derived from provenance paths already in the graph rather than from the build
    scripts: a module with a ``commonMain`` directory *is* multiplatform, and no
    plugin block has to be interpreted to say so. This is what keeps source-set
    grouping off every ordinary Android module, whose source sets are
    ``main``/``test``/``androidTest`` and whose areas should stay whole.
    """
    out: set[str] = set()
    for file in files:
        if source_set_of(file) != COMMON_MAIN:
            continue
        for module_path in module_paths:  # longest-first, as built
            if file.startswith(f"{module_path}/"):
                out.add(module_path)
                break
    return frozenset(out)


__all__ = [
    "COMMON_MAIN",
    "SOURCE_SET_MARK",
    "id_suffix",
    "link_actuals",
    "multiplatform_modules",
    "platform_modifier",
    "source_set_of",
]
