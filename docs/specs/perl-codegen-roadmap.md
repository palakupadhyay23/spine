# Design + Plan: Perl codegen — `sdlc feature --language perl`, built and tested with `prove`

**Status:** C-0, C-1 and C-2 DONE — complete Perl machinery with real green/red proof; C-3 live model validation next. **Dependency satisfied:** the
comprehension track merged to `develop` on 2026-09-12 ([#359](https://github.com/synaptixs/spine/pull/359)).
**Adjusted 2026-09-13** after a readiness check: generic work moved ahead of the machinery, §5.2's
premise corrected, and the toolchain verified (C3). **Then C-0 was added** when the refactor's
stated safety net was measured at 3-of-7 detection — see §5.1 and C-0's own row. **Date:** 2026-09-13 ·
spine v3.33.2.
**Depends on:** [perl-support-roadmap.md](perl-support-roadmap.md) merged (P1 for grounding, P2 for
the call graph the generated code is grounded on). **Branch:** `feat/perl-codegen` off `develop`,
opened when that dependency lands. **Delivery: one MR** to `develop` when every phase in §3 is done
and tested. Same split as Java ([multi-language-java.md](multi-language-java.md) →
[java-codegen.md](java-codegen.md)) and TypeScript ([typescript-codegen.md](typescript-codegen.md)):
comprehension is its own track, codegen is its own track, both in scope.

> The toolchain is the whole story: `cpanm --installdeps .` then `prove -l t/`. One dependency
> installer that may be absent, one test runner that is always present with `perl`. No build
> step, no build-system detection, no TFM probing. Cheaper than C, Go or PHP; the design effort
> goes into **where generated code lands in an existing distribution** and into proving green
> **and** red against the real toolchain, the Go 4.2 / 4.5 lessons.

## Roadmap currency

Every phase row in §3 carries **Status · Started · Finished · Evidence**, updated in the same
commit as the work. DONE means the evidence column links a commit, a test, or a pasted result.

---

## 0. Decisions surfaced up front

| # | Decision | Options | Recommendation and why |
|---|---|---|---|
| **C1** | Greenfield layout | (a) `lib/<Dist>/…pm` + `t/*.t` + `cpanfile`, no build tool; (b) ExtUtils::MakeMaker (`Makefile.PL`); (c) Dist::Zilla | **(a).** `prove -l t/` needs nothing else, and a `cpanfile` is what `cpanm --installdeps .` reads. (b)/(c) are packaging for CPAN release, not for building and testing; a repo that has one keeps it (brownfield never adds or removes a build tool). `tests_dir` is `t/`, `source_dir` is `lib/` — distinct, unlike Go. |
| **C2** | Test runner | `ProveTestRunner`: `perl -c` on each changed `.pm`/`.pl`, then `prove -l t/` (whole suite before green); early-return on the first non-zero; `_clip`-ed output as the refine signal | The two-step shape of `CTestRunner`/`GoTestRunner`. A brownfield run targets the `t/` directory that owns the changed package's tests first, then the whole suite, so a change cannot pass untested (the Go 4.5 false-green).  **As built (C-1, maintainer decision 2026-09-13):** Temporal activities retain injected runners and existing worker defaults; the registry replaces existing factories without adding workflow/payload language selection.  **As built (C-2):** add `-r` to the whole-suite `prove -l` invocation when tests are nested; `prove -l t/` alone does not recurse and can miss failing tests. A real nested-red integration test pins this. |
| **C3** | Dependency install | `PerlToolEnvironment.ensure` runs `cpanm --installdeps . --notest` when a `cpanfile` exists **and** `cpanm` is on PATH; otherwise it says so and continues. **Toolchain verified on the development machine 2026-09-13:** `perl` 5.34.1, `prove` (TAP::Harness 3.43), `cpanm` 1.7049 resolving against live CPAN — so C-3/C-4's live proofs can exercise the real installer rather than only the skip path. The best-effort contract stays: CI and a contributor's machine may still lack `cpanm`, and `perl_toolchain_available()` must not start requiring it | `cpanm` is not core Perl. `perl_toolchain_available()` requires `perl` and `prove` only; a missing `cpanm` is a warning in the run log, never a silent pass and never a hard stop for a repo whose deps are already installed. |
| **C4** | Brownfield placement | the target package is read from the neighbouring `.pm` files' `package` lines and the `lib/` tree; the new module goes at `lib/<Package/Path>.pm` and its test at `t/<name>.t` | The Go 4.5 lesson (placement by an existing package clause, not by directory name). A repo with several `lib/` roots (monorepo of distributions) targets the one whose `cpanfile`/`Makefile.PL` is nearest the grounded landing site. |
| **C5** | Conventions | `perl-conventions` skill reads the repo: Moo/Moose when the repo `use`s it, else classic `bless`; `use strict; use warnings;` always; `Test::More` (or `Test2::V0` when present); a leading underscore for private subs; POD stub per public sub | Read, not assumed — the PHP conventions precedent. |
| **C6** | Preflight | `perl -c` per changed file, always; `perlcritic` only when a `.perlcriticrc` exists | `perl -c` is present wherever `perl` is; `perlcritic` is a CPAN install. Fills the Python-only preflight gap for Perl the way Go's `gofmt`/`go vet` was proposed to.  **As built (C-1):** the existing Temporal preflight remains injected; the registry owns the preflight factory, without changing when existing pipelines invoke it. |
| **C7** | `SUPPORTED_LANGUAGES` | `"perl"` added in the **first** commit that also adds layout, scaffold, environment and runner — never earlier | The silent-Python-scaffold trap the Go track closed: `--language perl` exits 2 until the whole set exists. `_resolve_language auto` → Perl when `.pm`/`.pl` are present and Python is not. |
| **C8** | Live proof | greenfield with a real model against a spec; brownfield into the Mojolicious validation repo | Both independently re-run (`prove` from a clean checkout) before a phase is DONE — the Go 4.4 false-green is the precedent this rule exists for. |

---

## 1. What is reused

| Built in… | Reused here |
|---|---|
| codegen language-branch pattern (C#, Go, PHP) | verbatim: layout / scaffold / testenv / testrunner / prompts / conventions |
| build-then-test runner shape (`CTestRunner`, `GoTestRunner`) | `perl -c` → `prove` |
| module-owning test targeting (Go 4.5) | the `t/` directory nearest the changed package |
| `--language` validation (Go) | `"perl"` enters the set with the machinery, not before |
| PKG grounding (`grounding.py`) | the Perl graph from the support track; the grounding fence language is Perl |

## 2. Design

| Piece | What |
|---|---|
| `sdlc/layout.py` | registry `source_ext="pm"`;  `detect_perl_layout` (existing `lib/` + `t/`, `cpanfile`/`Makefile.PL`/`Build.PL`/`dist.ini` as markers); `_resolve_perl_layout` — greenfield = `lib/` + `t/` + `cpanfile` (C1) |
| `sdlc/scaffold.py` | `_perl_files`: `cpanfile`, a stub `lib/<Dist>.pm` (`package`, `use strict; use warnings;`, `1;`), `t/00-load.t` (`use_ok`), README, `.gitignore` — an empty distribution is a green `prove` |
| `sdlc/testenv.py` | `PerlToolEnvironment` (C3); `perl_toolchain_available()` |
| `sdlc/testrunner.py` | `ProveTestRunner` (C2) |
| `sdlc/codegen.py` | Perl variants of the implement / tests / refine prompts; registry-selected Perl guidance (package name, `t/` naming, Moo vs classic per C5) |
| `catalog/catalog.py`, `skills.py` | `perl-conventions` capability + skill |
| `sdlc/preflight.py` | the Perl branch of the preflight dispatcher (C6) |
| `sdlc/feature_runner.py` | `SUPPORTED_LANGUAGES`, `_resolve_language`, the toolchain guard with a `FeatureRunError` hint naming `perl` and `prove` |

### 2.1 Testing the phases with Spine itself

Each phase ends with Spine's own surfaces, not only `prove`: `pkg_grounding` (MCP) on the
generated module's landing site to record how many characters of PKG context the run used and which
symbols it named; `blast_radius` on the generated sub after the run, which must list its new test as a
caller; `sdlc_run_result` / the build document for the evidence cell; and `regression_gaps` on the
brownfield repository before and after C-4, which must not grow. Before C-1 touches `sdlc/`,
`blast_radius` on the symbols in §2.2 is re-run and the table refreshed.

### 2.2 Blast radius — measured from the PKG

Measured through the configured Spine MCP stdio server before C-2 edits, at `2a4dc74` on 2026-09-13.

| Symbol | Callers | Touches | Finding |
|---|---:|---:|---|
| `py:orchestrator.sdlc.feature_runner._resolve_language` | 5 | 12 | MCP `blast_radius` |
| `py:orchestrator.sdlc.layout.resolve_layout` | 30 | 39 | MCP `blast_radius` |
| `py:orchestrator.sdlc.scaffold.scaffold` | 37 | 38 | MCP `blast_radius` |
| `py:orchestrator.sdlc.testenv.make_test_runner` | 16 | 18 | MCP `blast_radius` |
| `SUPPORTED_LANGUAGES` | — | — | Not represented as a graph node; inspect readers in source. |
| `py:orchestrator.sdlc.activities.SDLCActivities.preflight` | 0 | 1 | MCP `blast_radius` |
| `py:orchestrator.sdlc.deps.SDLCDeps.preflight` | 0 | 1 | MCP `blast_radius` |

Source inspection: runner selection lives in `testenv.make_test_runner`, called by `run_feature`; `activities` uses injected dependencies. The two PHP dispatch sites in `testenv.py` select environment and runner; availability is a separate `php_toolchain_available` guard. Nine supported languages include SQL. These are baseline facts, not behavior changes.

MCP `design_change` ran with C1–C8 as preservation intent and the C-0 mutation criterion. It returned a grounded heuristic design, no model, and no unverified references. Its suggested production edits are outside C-0: this phase changes characterization tests only. [Raw blast-radius and design evidence](../evidence/perl-codegen-c0-mcp.json).

---

## 3. Phases — the living table

| Phase | Work | Effort | Exit criteria | Status | Started | Finished | Evidence |
|---|---|---|---|---|---|---|---|
| **C-0 Characterize the dispatch** — **before any refactor** | Tests that pin the behaviour of every per-language branch the registry will replace, especially the **non-uniform** ones: C#'s `target_framework=detect_dotnet_tfm()` rewrite, C/C++'s shared branch (per-language availability probe, and the brownfield CMake-vs-Meson pick), PHP's *two* separate `codegen.py` branches, and the two `if language == "php"` sites in `testenv.py` (environment vs availability). Each new test must be shown to **fail** when its behaviour is mutated, not merely to pass today | ~0.5–1 d | **Measured, and this phase exists because of it:** the mutation set is checked in as `scripts/mutate-dispatch.py` — eight transcription errors a language-to-row flattening plausibly makes, each applied on its own with the whole `tests/sdlc` suite run against it. **Baseline 2026-09-13: caught 4 of 8.** The four misses: C#'s TFM detection, C++ getting C's toolchain probe, CMake forced on a Meson brownfield, and PHP's `codegen.py` guidance branch — **all passed with 0 failures**. (First run reported 3 of 7; the eighth mutation could not be located because `if language == "php":` occurs twice in `testenv.py`. The checked-in script anchors it to `make_test_environment`, and that one is caught — hence 4 of 8.) Exit: `python scripts/mutate-dispatch.py` reports **8 of 8** | ✅ | 2026-09-13 | 2026-09-13 | `scripts/mutate-dispatch.py`: **4/8 before → 8/8 after**, 0 skipped mutations; [baseline](../evidence/perl-codegen-c0-mutations-before.txt), [after](../evidence/perl-codegen-c0-mutations-after.txt). 11 new characterization cases; C# scaffold TFM, C/C++ distinct probes and Meson selection, PHP guidance and convention sampling. MCP blast-radius/design evidence: §2.2. [Validation](../evidence/perl-codegen-c0-validation.txt): 3,640 passed, 4 skipped, 51 deselected; lint/type/docs/roadmap/state gates pass; accuracy 0 regressions, verify 0 errors (1 warning), four shapes pass. |
| **C-1 Generic work** (§5.1, §5.2) — after C-0 | the toolchain registry, built on C-0's net and *before* Perl is wired so Perl is its first row rather than a language migrated onto it afterwards (moved ahead of the machinery in review: wiring Perl the old way and refactoring after means writing Perl's dispatch twice); the preflight row per §5.2 | ~2–3 d | `feature_runner` and existing factories select by one table; `activities` retains dependency injection (maintainer clarification); adding a language is one row + its classes; every existing language's codegen tests pass unchanged | ✅ | 2026-09-13 | 2026-09-13 | Language tests **391 passed, 2 skipped before and after** (PHP/Composer unavailable; PostgreSQL opt-in). Mutations **8/8**, 0 skipped. [MCP blast-radius/design](../evidence/perl-codegen-c1-mcp.json); [validation](../evidence/perl-codegen-c1-validation.txt). Full suite **3,655 passed, 4 skipped, 51 deselected**; lint/type/docs/roadmap/state gates pass, accuracy 0 regressions, verify 0 errors (1 warning), all four shapes pass. Temporal injection and worker defaults preserved. |
| **C-2 Machinery** | §2 in full; unit tests: `test_scaffold_perl_*` (+ idempotency), `test_perl_toolchain_available` (monkeypatched `which`), layout detection, runner argv, the `FeatureRunError` hint; `tests/sdlc/test_perl_integration.py` gated on `perl_toolchain_available()` — scaffold → real `prove` **green and red** | ~3–4 d | integration test green and red against real `perl`/`prove`; `--language perl` validated; gate green | ✅ | 2026-09-13 | 2026-09-13 | [MCP blast-radius/design](../evidence/perl-codegen-c2-mcp.json); [validation](../evidence/perl-codegen-c2-validation.txt). **Model:** none (deterministic runner proof); **command:** `uv run --frozen` + CI extras, `pytest -q tests/sdlc/test_perl_codegen.py tests/sdlc/test_perl_integration.py` — 23 pass; **independent clean-checkout rerun:** committed green/red fixtures cloned separately, real `prove -l t/` passes/fails respectively; **grounding:** 0 characters for runner fixture. Full suite **3,679 passed, 4 skipped, 51 deselected**; mutations **8/8**, no skips; all static/docs/status/graph gates pass (verify 0 errors, 1 warning). |
| **C-3 Greenfield live-proven** | `sdlc feature --language perl` from a spec with a real model, `--safe`; `perl-conventions` selected; grounded on the Perl graph | ~1–2 d | `prove` green, independently re-run from a clean checkout; the run's build document names the grounding used | ⬜ | | | |
| **C-4 Brownfield on the Mojolicious validation repo** | placement per C4 into an existing `lib/` tree; the owning `t/` targeted first, then the suite | ~2–3 d | `prove` green on the changed package **and** the whole suite, independently re-run; no package clause mismatch; grounding measured (chars of PKG context) | ⬜ | | | |
| **C-5 Preflight + docs + MR** | C6; every row of §6; `/review-pr`; one MR to `develop` | ~1–2 d | preflight runs `perl -c` on a changed file and fails on a syntax error (tested); docs audit clean; verdict "mergeable" | ⬜ | | | |

**Rough total: ~10–15 days.** Delivery is one MR. The reorder adds a day to C-1 (the registry now lands before Perl needs it, and the other languages migrate onto it under their own tests) and removes the rework it was going to cost later.

---

## 4. Validation

- **Greenfield:** a small spec (a `Shop::Cart` distribution with `subtotal`/`total` and a tax
  rate) — the same shape the corpus `plain` case uses, so the generated code is checkable against
  the graph the support track already labelled.
- **Brownfield:** `mojolicious/mojo` (ephemeral, docs-only name): add a helper to an existing
  package under `lib/Mojo/` with a `t/` test; `prove -l t/mojo/<name>.t` then `prove -l t/`.
  Hypothesis to confirm, the Go-style one: the Mojolicious suite is hermetic and green from a
  clean clone in under a minute, so the brownfield loop needs no environmental wall.

## 5. Generic work — built here, reused by every later codegen track

### 5.1 `sdlc/toolchains.py` — one registry instead of five if-chains *(Perl builds it, C-1 — first)*
Today a language's codegen is wired by `elif lang == "go"` branches in `feature_runner.py`
(`_resolve_language`, the toolchain guard), `activities.py` (runner selection), `layout.py`,
`scaffold.py`, `preflight.py` and `codegen.py`'s prompt maps — the PHP merge touched 38 files to add
one language. **Precondition, added 2026-09-13: C-0 first.** This section's original plan said the existing
languages "migrate in the same phase, with their tests as the regression net". That net was
measured and **it is not there**: `scripts/mutate-dispatch.py` applies eight realistic transcription
errors one at a time and runs the whole `tests/sdlc` suite against each. **Baseline: 4 of 8.** The four
misses are all in the non-uniform branches — C#'s TFM rewrite, C++'s toolchain probe, the C/C++
brownfield build-tool pick, and PHP's codegen guidance — each passing with **zero** failures. Refactoring eight
working languages behind a net with 50% detection is the one genuinely unsafe thing in this track,
so C-0 builds the net first and proves it by re-running the same mutations.

A `Toolchain` record per language (`source_ext`, `layout`, `scaffold`, `environment`,
`runner`, `available()`, `preflight`, prompt set, conventions skill id) in one table, with the
call sites reading the table, makes the next language one row plus its classes. Perl's row is the
first written against it; the existing languages migrate in the same phase, with their tests as the
regression net. **Exit:** `grep -c 'lang == "' src/orchestrator/sdlc/*.py` drops to zero in the
dispatch paths; `test_language_validation` proves an unknown language still exits 2.

### 5.2 Preflight, as a row in §5.1's registry
**Re-checked 2026-09-13, and this item's original premise is stale.** It was written against the
Go roadmap's "preflight is Python-only and skipped-as-pass everywhere else" gap. That is no longer
true: the PHP codegen track added a real per-language preflight (`preflight.py`'s PHP runner shells
`php -l` over the changed files via `sdlc/php.py`'s `changed_php_files`). So Perl **extends an
existing pattern rather than creating one** — `perl -c` per changed file, the same shape, one more
row in the registry §5.1 builds. Cheaper than this section assumed; the remaining work is making
the selection table-driven rather than another branch.

### 5.3 The build-then-test runner template *(shared, exists)*
`CTestRunner` → `GoTestRunner` → `PhpUnitTestRunner` → `ProveTestRunner` all have the shape
"step 1 must pass, then step 2, early-return, `_clip`-ed output". Record it in the codegen section
of `docs/reviewing/language-frontend-checklist.md` as the template with its four exit tests
(green, red, missing toolchain hint, idempotent scaffold), so a runner PR is reviewed against it.

### 5.4 Live-proof evidence format *(shared)*
Every codegen track's "live-proven" claim carries the same four fields in its evidence cell:
model, command, the independent re-run's command and result, and the grounding size. The Go 4.4
false-green is the reason the second field is not optional.

## 6. User-facing documentation — updated in the phase that makes each row true

| Document | What must change | Phase |
|---|---|---|
| `FEATURES.md` | a Perl codegen row (`sdlc feature --language perl`; `prove`; `cpanm` optional) | C-2 |
| `USER_GUIDE.md` | the toolchain passage (Perl codegen needs `perl` and `prove`; `cpanm` optional); the "Multi-language" blockquote's codegen sentence | C-2 |
| `CLAUDE_GUIDE.md`, `CODEX_GUIDE.md` | a Perl row in the toolchain tables | C-2 |
| `SETUP.md` | toolchain prerequisites | C-2 |
| `CLI_REFERENCE.md` | `--language perl` in the `sdlc feature` reference | C-2 |
| `docs/specs/STATE-OF-SPINE.md`, `SPEC-INDEX.md`, [perl-support-roadmap.md](perl-support-roadmap.md) D9 | status lines updated to the phase reached | every phase |
| `docs/reviewing/language-frontend-checklist.md`, `CONTRIBUTING.md` | the codegen section: the toolchain registry row a new language adds, the runner template and its four exit tests (§5.1, §5.3) | C-1 |
| `CHANGELOG.md` | one entry under Unreleased per phase | every phase |

## 7. Risks and gotchas

- **`cpanm` absent or offline** — best effort, logged, never a silent pass (C3). Present on the development machine as of 2026-09-13, so the live proofs can exercise the installer; the guard is for CI and contributor machines, and `perl_toolchain_available()` must keep requiring only `perl` + `prove`.
- **`@INC` and `-l`** — `prove -l` adds `lib/`; a repo with `blib/` or a custom `-I` in its
  test harness needs `PERL5LIB` read from `.proverc`/`dist.ini` — read it, do not guess.
- **Test naming** — `t/` files are numbered by convention (`00-load.t`, `10-cart.t`); generated
  tests follow the repo's existing numbering or, greenfield, start at `00-load.t`.
- **Never an XS build** — a distribution with `.xs` sources is compiled by `make`, which this
  track does not run; the run says so and stops.
- **Never commit `episteme/`;** the validation repository's name stays in `docs/`.

## 8. Sequence

```
depends on perl-support-roadmap.md merged (P1 + P2 at least) — SATISFIED 2026-09-12 (#359)
C-0 characterize     → pin the 8 languages' dispatch; mutate-dispatch.py 8/8 (is 4/8 today)
C-1 generic work     → the toolchain registry, on C-0's net; every language one row
C-2 machinery        → Perl as the registry's first row; prove green + red on real perl
C-3 greenfield       → live-proven, independently re-run
C-4 brownfield       → into the Mojolicious repo, owning t/ then the suite
C-5 preflight + docs → /review-pr, then one MR to develop
```

## 9. C-0 execution evidence

The official mutation set is unchanged. Both runs used `uv run --frozen` with CI extras `dev mcp typescript java csharp c cpp go php perl`; `UV_NO_SYNC=1` preserved that installed environment for the script's nested `uv run --frozen pytest` commands. The script's final historical footer still says 3/7; its measured summary is the eight-case result above. C-0 edits no production implementation. PHP convention sampling was additionally disabled in a [separate probe](../evidence/perl-codegen-c0-php-conventions.txt) and its characterization test failed.

## 10. C-1 execution evidence

Maintainer clarification (2026-09-13): preserve the Temporal dependency-injection contract. `activities.py` has no per-language chain to replace: it consumes `SDLCDeps.tests` and `SDLCDeps.preflight`. Keep those and worker defaults unchanged. Centralize existing selection in registry-backed factories; no Temporal payload or workflow changes. The pre-migration language test command explicitly included `tests/sdlc/test_php_codegen.py` plus layout, scaffold, testenv, codegen, testrunner, feature_runner, sql_build and Java/TypeScript/Go integration modules: **391 passed, 2 skipped in 22.76s**.

C-1 implementation preserves existing dispatch details: SQL still uses the Python author-tests prompt and has no conventions capability; auto-detection still excludes SQL and defaults to Python when Python is present. C# SDK preparation remains a feature-runner step; C/C++ retain separate CMake compiler probes and the existing shared Meson probe. PHP retains both prompt guidance and convention sampling, environment/runner configuration, and its preflight invocation point. `language_guidance.py` holds the former guidance branches; text is unchanged. The eight mutation definitions now target the corresponding registry entries, and the measurement reads CI extras for every child pytest invocation. **After migration: 391 passed, 2 skipped in 27.49s**, matching the baseline counts and skip reasons.
C-1 review finding: direct `run_feature(language="unknown")` historically falls back to Python factories but skips the pytest-availability guard; registry fallback initially applied that guard. Preserved the original direct-call behavior and pinned it with a regression test. CLI unknown-language validation remains exit 2. Temporal injection is covered by a payload carrying another language while the injected test and preflight results still win. Guidance is byte-identical across 54 layout combinations, and all 27 phase prompt selections match C-0.

## 11. C-2 execution evidence

The complete registry row adds Perl layout/scaffold/environment/runner/prompts together. `perl_toolchain_available()` checks only `perl` and `prove`; missing `cpanm` is a warning in both logging and environment description. Changed sources compile first; owning tests precede every distribution suite, and nested suites recurse. Empty suites and XS builds fail explicitly. Package names and nearest distribution markers control monorepo placement. `.pm`/`.pl` require tests, `.t` is recognized throughout generation/review, and Perl grounding uses Perl fences.

Runner proof: **model: none (deterministic integration, no model call); command: `uv run --frozen` with CI extras, `pytest -q tests/sdlc/test_perl_codegen.py tests/sdlc/test_perl_integration.py`; independent re-run: the integration test commits and clones both green and red fixtures, then executes real `prove -l t/` from each clean checkout; grounding size: 0 characters for the runner fixture**. 23 tests pass, including real assertion-red, syntax-red, and nested-suite red. Live model evidence remains C-3/C-4.
