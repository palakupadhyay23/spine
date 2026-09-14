# C-5 manual review

Verdict: **mergeable** after fixes and the complete local gate. MR #365 remains draft until hosted checks confirm the pushed result.

Scope: develop `e8a0da55691b942d3205a801701ed459c08d9560` to the C-5 worktree on
`feat/perl-codegen`. Review follows CONTRIBUTING, docs-matrix.md and
language-frontend-checklist.md. No /review-pr skill was assumed.

## Findings and resolutions

1. SQL still had a language branch for its single-phase policy. The registry now owns
   `author_tests=False`; SQL's existing prompt and execution behavior are preserved.
2. Perl `lib/` root symlinks could bypass os.walk's no-follow policy. A new regression
   failed before the fix. Layout rejects these roots and the walker refuses them.
3. C6 required configured Perl::Critic checks. Perl preflight compiles changed sources,
   runs critic only with an owning/root profile, and reports configured missing tools or
   critic failures as red. Prove consumes this registered preflight before tests.
   Clean checkouts retain compilation of all source files.
4. MR CodeQL reported two callable-argument findings and seventeen explicit import cycles.
   Callable dataclass defaults are now explicit fields. Shared contracts and sanitized
   subprocess execution moved to independent modules; the old public imports re-export
   those contracts. PHP and Perl runner capture callables are explicitly passed to preflight.
   The old runner capture seam remains available. No alert suppression, workflow filtering,
   or dismissal was introduced.
5. Temporal continues to consume SDLCDeps.tests and SDLCDeps.preflight. Worker defaults and
   workflow payloads are unchanged. Its injection test remains part of the final full suite.

The six moved contract classes and the sanitized subprocess function have identical ASTs to C-4. Fresh-process imports succeed in both registry-first and preflight-first order; the registry loads no concrete adapter modules. The source-level explicit import graph, including function-local and TYPE_CHECKING imports,
is acyclic across sdlc modules after item 4. All changed adapter modules import successfully.
This structural check is additional evidence; final hosted CodeQL still must confirm its
own result after push.

## Documentation matrix walk

| Trigger | Documents inspected | Result |
|---|---|---|
| User-visible codegen and preflight | CHANGELOG.md:9, FEATURES.md:41, USER_GUIDE.md:729 | Perl invocation, optional cpanm, configured critic, owning/full suites and limitations match implementation. |
| Existing CLI flag gains Perl | CLI_REFERENCE.md:1118, USER_GUIDE.md:731, CLAUDE_GUIDE.md:793, CODEX_GUIDE.md:707 | Choice and required toolchain are documented; ten comprehension front-ends remain ten. |
| Toolchain prerequisites | SETUP.md:213, both assistant guides' toolchain tables | perl/prove required, cpanm optional, configured critic required. No new service or environment variable. |
| Existing public language claims | README.md:428, FEATURES.md:51, USER_GUIDE.md:78 and :365 | Removed obsolete comprehension-only codegen claims. |
| Spec progress | perl-codegen-roadmap.md, SPEC-INDEX.md, STATE-OF-SPINE.md, perl-support-roadmap.md | Final phase status and evidence are updated together before commit. |
| Contribution process and mutation tooling | CONTRIBUTING.md:156, .github/pull_request_template.md:14, language-frontend-checklist.md:75 | Registry completeness, Temporal injection, explicit PHP test command and 8/8 measurement documented. |
| Parser, grammar extra, MCP tool, graph kinds, release, registry UI | Whole branch diff | No such registration changes; no new documentation obligations for those rows. |

## Language registration checklist walk

| Surface | Source checked | Result |
|---|---|---|
| Complete language registration | sdlc/toolchains.py:349; feature_runner.py:588 | Perl enters SUPPORTED_LANGUAGES only in C-2, together with all factories and prompts. |
| Layout, scaffold and monorepo placement | layout.py:732 and :758; scaffold.py:72; perl.py | Package clauses, nearest distribution marker, explicit package disambiguation, idempotent scaffold; XS and unsafe includes fail explicitly. |
| Environment and availability | testenv.py:373 and :431 | Availability needs perl and prove only. Missing/failed cpanm is logged; no cpanfile is explained. |
| Runner and preflight | testrunner.py:350; preflight.py:145 | Syntax failure stops before tests; owning tests precede full recursive suites; empty suites red; optional configured critic follows C6. |
| Generation and conventions | codegen.py Perl prompts; conventions.py:191; catalog/catalog.py:63; catalog/skills.py:147 | Anchored edits and named behavioral test subs are explicit; observed object/test frameworks inform prompts. |
| Grounding and coverage | grounding.py:149; coverage.py:28; review.py:50 | Perl fences, .t coverage and review extensions are registered. C-3 live evidence exposed and proved the .t correction. |
| Existing languages | All registry rows, language_guidance.py, C-0/C-1 evidence and final suite | C# TFM, C/C++ probes and Meson, both PHP guidance branches and environment/runner selections remain pinned. SQL single-phase is retained. |
| Comprehension, packaging and corpus | Existing Perl extractor registration, pyproject extras, CI sync, scope, profile, grammar fingerprint and corpus tests | Already landed in comprehension dependency #359; this branch introduces no grammar or extra. Accuracy and full suite recheck the existing registrations. |
| Real runner template | tests/sdlc/test_perl_integration.py; test_perl_codegen.py | Real green/red clean clones, syntax red, nested red, missing-toolchain hint and idempotent scaffold. |
| Live proof | C-3 and C-4 roadmap evidence cells | Model, full command, independent clean checkout result and grounding size present. Brownfield gaps 116 to 116; new helper has a named test caller. |

## Remaining limitations

The local PHP/Composer integration test cannot run without that toolchain; its named unit
module passes. PostgreSQL and other integration opt-ins retain their existing skip/deselect
policy. Brownfield upstream optional-feature skips are unchanged and documented in C-4.
No model rerun is needed for the shared-contract move; the final full suite reruns the real
Perl/prove green and red proofs. Generic intent-substring and judge-excerpt findings from
C-4 remain recorded rather than changing unrelated pipeline behavior.

## Final validation

The full CONTRIBUTING gate passes: **3,688 passed, 4 skipped, 51 deselected**, 152 warnings, 207.39 seconds; lint/format/type/artifact/docs/status checks pass, accuracy has zero gated regressions, graph verify has zero errors and one existing phantom-module warning, all four shapes pass, and understand builds 91 files. Final named language/registry tests: **437 passed, 2 skipped**. Mutation measurement: **8 of 8 caught, 0 skipped**. See [command output](perl-codegen-c5-validation.txt). Generated episteme is excluded.
