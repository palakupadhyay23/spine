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

## P4 — header routing

Routing tests plus the C, C++, profile and semantic tests:
`79 passed, 30 warnings in 0.46s`. Both new corpus cases have empty `missing` and
`unlabelled` lists and precision/recall 1.00 on all their populated cells:
`cpp/header_classes` and `c/header_unaffected`. Aggregate C++ CALLS becomes 5/5/5
because the new header fixture adds one independently labelled call.

Routing follows CST literal includes from `.cpp`, `.cc` and `.cxx`, transitively,
including uniquely resolved in-repo angle includes. Cycles terminate; ambiguous
header basenames are refused. Headers not reached remain C. Tests prove routing
works without clang, does not enter nested repositories/hidden fixture directories,
and keeps a C-only header's original ids. Both new fixture roots are `.repo/`.

P4's mypy, lint, format, four artifact checks and roadmap checks all pass.

## P5 — shipping decision required

**Do not treat the optional extra as ready to ship.** On the requested OpenCV fork,
the pass recovered **121 of 130,001 pending call sites (0.0931%)**. TinyXML-2
recovered **13 of 1,178 (1.1036%)**. The execution request explicitly requires a
user decision when the measured result is too weak to justify the extra; no
resolution changes have been made after measuring these results.

These are **recovery fractions of CST-unresolved sites**, not independently labelled
whole-repository recall. The real repositories have no gold call graph. Missing
system headers, unsupported USRs, indirect calls, macros and absent grounded
symbols all remain in the denominator; none have been exempted. The diagnostics
count does not isolate how many misses are attributable to system headers.

The target clone is branch `4.13.0-python`, commit
`b4c5ec4042f097e2a5b386b9d413ec7333d0a184`. TinyXML-2 remains at the P0 commit.
Both were copied without `.git` before running:

```sh
uv run --frozen python -u scripts/validate-frontend.py cpp /tmp/spine-clang-tinyxml2-validation /tmp/spine-clang-aiopencv-validation
```

### OpenCV fork — supplied baseline versus P5

| Measure | Supplied baseline | P5 |
|---|---|---|
| Nodes | 81,479 | 87,181 (80,422 grounded, 6,759 external) |
| Edges | 383,412 | 397,290 |
| C/C++ nodes | 69,999 (66,634 grounded) | 77,682 (72,101 grounded) |
| C/C++ edges | 359,703 | 374,747 |
| C/C++ CALLS with ungrounded targets | 175,385 / 239,211 (73.3%) | 177,532 / 243,181 (73.0041%) |
| Types sourced from `.h` | 352, all struct/union/enum | 1,517 |
| Extraction time | 33 s | 71.716 s (+38.716 s; 2.173×) |

The original baseline says “19,390 dangling” without defining its counting unit.
P5 reports **19,982 unique missing ids** and **190,485 edges with missing endpoints**
separately; these are different measures and should not be conflated.
The node and edge deltas include P4's CST header routing, not just clang enrichment.
The full Python test suite was running concurrently with P5; the observed timing
is end-to-end extraction under that load, not an isolated libclang overhead estimate.

```text
clang: resolved 121 of 130001 unresolved call sites in 1981 of 2468 TUs; 1950 with diagnostics, 0 failed
```

D6 skipped **487 of 2,468 TUs (19.7%)**. Diagnostics occurred in **1,950 of
1,981 parsed TUs (98.4%)**. `.cu` and `.mm` produced **zero C/C++ source nodes**
and neither suffix entered the TU set. Verification reports three errors
(dangling edges, Java orphan rate, Python orphan rate) and two warnings
(phantom modules and eight scope-bound CALLS). This is not a passing real-repo
verification result. The provided baseline did not record those verification
categories, so no claim is made that every category is pre-existing.

### TinyXML-2

| Measure | P0 | P5 |
|---|---|---|
| Nodes / edges | 311 / 1,395 | 425 / 1,598 |
| C/C++ CALLS with ungrounded targets | 408 / 1,002 | 412 / 1,068 |
| Types sourced from `.h` | 1 | 6 |
| Extraction time | 0.127 s | 0.405 s (+0.278 s) |

```text
clang: resolved 13 of 1178 unresolved call sites in 3 of 3 TUs; 3 with diagnostics, 0 failed
```

P5 still has one verification error and one warning: 581 dangling edges and
phantom modules. P0 already failed with 608 dangling edges. The state stack is
`cpp`, `javascript`, `python`; a call graph is available.

### P5 checks

The validation-script tests report `6 passed in 0.31s`. They check that recovery
and TU denominators stay distinct. P5 mypy, lint, format, all four artifact checks,
and the roadmap check pass. The documentation audit reports
`0 STALE/MISSING, 39 INFO`; final P6 documentation and review work is pending.

### Interrupted work at the decision point

The OpenCV extraction and verification above completed. The subsequent `state`
summary was interrupted after the shipping stop condition was established. Its
trace was inside `stats.summarise_store` → `store.callers_of`, after extraction,
so the **71.716-second extraction measurement is complete**. The entire smoke
script did not finish; no successful end-to-end state result is claimed for this
repository. [Captured output](clang-semantic-p5-output.txt) includes the interruption.

The broader pre-MR pytest run was also interrupted, with this **partial** summary:

```text
22 failed, 3308 passed, 4 skipped, 51 deselected, 182 warnings in 317.39s (0:05:17)
```

Failures include denied writes to the Spine and Go caches and a Jira DNS lookup.
This run used the ordinary sandbox, unlike the approved phase gates. It does not
establish a code regression, and it is not a green full-suite receipt. A complete
run with appropriate test-environment permissions remains required before an MR.
