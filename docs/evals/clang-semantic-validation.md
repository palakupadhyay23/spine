# Clang semantic pass validation

This track adds an optional semantic post-pass to the existing C/C++ CST front-ends.
Decisions: repository-only synthesised flags; no compilation database; edges only
between grounded nodes; `.h` routing is in scope; `clang` joins `all`, not `languages`;
parse only translation units with unresolved CST call sites.

## P0 — 2026-09-15, base `94f106a`

The supplied OpenCV-fork baseline is preserved, not re-measured:
`https://github.com/synaptixs/aiopencv`, branch `4.13.0-python`:

| Measure | Recorded baseline |
|---|---|
| Total graph | 81,479 nodes / 383,412 edges / 19,390 dangling; 33 seconds |
| C/C++ graph | 69,999 nodes, 66,634 grounded / 359,703 edges |
| C/C++ CALLS with ungrounded targets | 175,385 / 239,211 (73.3%) |
| Types from `.h` | 352 struct/union/enum nodes; zero classes |
| C++ instance-call corpus | 4 expected / 3 emitted / 3 matched |

### Wheel receipt

`GET https://pypi.org/pypi/libclang/18.1.1/json` returned these wheel filenames:

```text
libclang-18.1.1-py2.py3-none-manylinux2010_x86_64.whl
libclang-18.1.1-py2.py3-none-manylinux2014_aarch64.whl
libclang-18.1.1-py2.py3-none-manylinux2014_armv7l.whl
libclang-18.1.1-py2.py3-none-win_amd64.whl
libclang-18.1.1-py2.py3-none-win_arm64.whl
```

### Smaller validation repository

[TinyXML-2](https://github.com/leethomason/tinyxml2), commit
`8224e427b655b83dae5e2298f1e6919523a78737`, has in-repository classes in `tinyxml2.h`
and three C++ source files. The baseline extraction takes 0.127 seconds:
311 nodes / 1,395 edges; 1,002 C/C++ CALLS, 408 with ungrounded targets;
one Type from `.h`. Verification already fails with 608 dangling edges, including
CONTAINS edges from missing `.h` classes. There are 14 phantom-module warnings.
This is useful evidence for the routing phase, not a clean verification baseline.

The new census adapter was run with:

```sh
uv run --with libclang==18.1.1 python scripts/parse-census.py clang /tmp/spine-clang-tinyxml2 --suffix .cpp --json
```

Result: 3 TUs scanned; 3 with errors, each reporting `'cctype' file not found`;
1,873 CALL_EXPR and 210 CXX_METHOD cursors in source files. Diagnostics measure
parse coverage, not member-call recall. No system include paths are supplied.

### Quality and impact receipts

CI extras were synced exactly as prescribed. P0 gate output:

```text
Success: no issues found in 719 source files
All checks passed!
756 files already formatted
state-numbers --check: OK — 14 gated claim(s) match; 9 trended.
46 capability rows
21 where Spine stands alone
3 where Spine is ❌
3 at 🟡
assets/spine-architecture.svg is current
assets/knowledge-foundation.svg is current, and its layout checks pass
```

The supplied roadmap leaves the full-suite, accuracy and Spine-verification baseline
receipts empty. They are not re-measured here, following the execution request;
those checks remain mandatory before the MR.

The planned `orchestrator blast-radius` command returns `No such command` on this
revision. The real method is `RepoCodeExtractor.extract`, not `extract_repo`.
Using `FactStore.callers_of` and `impact_of` (default depth 4): extraction reports
0 callers / 0 impact nodes, fingerprint 8 / 43, and resolve-or-drop 4 / 6. The zero
for extraction is a limit of the existing Python instance-call graph, not evidence
that no consumers exist; `understand`, `state`, exports and grounding consume it.

## P1–P3 — mapper, packaging, wired pass

P1 (`3afa37a`): 30 mapper cases passed, including refusal cases. P2 (`6b8e593`):
89 tests passed across mapper, persistence and doctor; `doctor` lists `clang`.
The fingerprint changes when the wheel is present and when its version changes.

P3 targeted result: `80 passed, 31 warnings in 1.92s`. The only corpus changes
against the pre-clang scoreboard are C++ CALLS emitted **3 → 4** and matched
**3 → 4**, with expected **4** unchanged. All other corpus cells are unchanged;
all populated cells retain precision 1.00. `pkg accuracy --check` reports
`OK — 0 gated regression(s), 0 improvement(s)` against the regenerated scoreboard.

`test_corpus_is_additive_only` compares every fixture root with the optional pass
disabled/enabled: identical node sequences, a superset of edges, and both endpoints
of every additional edge grounded. The remaining tests cover reference/pointer
receivers, static virtual dispatch, multiple sites on a line, unavailable headers,
external targets, function pointers, header call sites, parse failure, extractor
reuse, different checkout paths, ignored `.cu`/`.mm`, and ignored compilation DBs.

The instance-call fixture prints:

```text
clang: resolved 1 of 1 unresolved call sites in 1 of 1 TUs; 0 with diagnostics, 0 failed
```

All phase quality gates pass. Impact API check before the routing phase:
extract 0 callers / 0 impact nodes (existing instance-call limitation);
fingerprint 11 / 44; resolve-or-drop 5 / 8.
