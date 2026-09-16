# Clang semantic support — release readiness

Date: **2026-09-15**. Technical reviewer: **Codex (implementing agent)**, not an
independent reviewer. Decision owner: **repository maintainer**; acceptance pending.
[Step 4 plan](../specs/parsing-and-the-pkg.md#step-4--release-readiness) ·
[draft MR #379](https://github.com/synaptixs/spine/pull/379).

## Candidate and status

Implementation candidate: `3b0eea825e1c196fc50cc942a26e270ecaf455b7`.
Starting review revision: `001b5285a5f5669911b6db40cd406fe7cbcd90c8`.
Step 4 changes documentation and validation receipts only; no production code,
labels, dependency declarations, version, or D1–D6 changes. The final documentation
revision and its remote gate results are recorded on MR #379.

| Work item | Status |
|---|---|
| 4.1 Support contract | Complete; claims and omission path reconciled below and in user documentation |
| 4.2 Correctness / five losses | Technical review complete; proposed limitations below await decision-owner acceptance |
| 4.3 Operational cost | Technical review complete; profile-specific recommendation below awaits acceptance |
| 4.4 Candidate validation | Local gates and isolated wheel checks passed; final remote gates recorded on MR #379 |
| 4.5 Maintainer decision | Pending; no merge or release approval inferred |

Technical recommendation: retain **optional, repository-dependent enrichment**
with the limits below. Readiness is held until final remote gates pass and the maintainer
explicitly accepts the five losses and cost. This is not a claim of broad semantic
coverage, independent population precision, or low-latency C++ analysis.

## Support contract

| Surface | Contract / limit | Evidence |
|---|---|---|
| Activation and omission (D1, D5) | `[clang]` and `[all]` automatically activate the post-pass when bundled libclang and matching grammars are available. `[languages]` omits clang. There is no disable flag. Use a fresh `[c,cpp]` or `[languages]` environment to omit it; installing fewer extras into an existing environment does not uninstall libclang. | [Package receipt](clang-semantic-step4-package.json), [SETUP](../../SETUP.md#optional-extras), [packaging declaration](../../pyproject.toml), `test_unavailable_clang_and_failed_parse_preserve_batch` |
| Native dependency | Wheel-local libclang; no host LLVM/Xcode search or fallback. Tested version 18.1.1; declared `>=18` does not validate every later version. | [loader](../../src/orchestrator/pkg/clang_link.py), isolated native smoke below |
| Deterministic parse inputs (D2) | C11/C++17, `-nostdinc`, `-target x86_64-unknown-linux-gnu`, `-ferror-limit=0`; repository headers and conservatively inferred literal include roots only. No compile database, host SDK, standard library headers, generated stubs or computed-include inference. | `test_checkout_paths_and_compile_database_do_not_change_facts`; [include-root tests](../../tests/pkg/test_clang_includes.py) |
| Grounded identity (D3) | Adds CALLS only between existing grounded functions; node IDs and CST facts remain. Exact caller source/scope checks, with the existing class/header overload rule; ordinary overloads remain name-collapsed. Virtual calls name the static target, not runtime dispatch. | `test_member_reference_pointer_virtual_and_same_line`, `test_signature_qualified_methods_preserve_name_keyed_overloads`, `test_out_of_line_constructor_keeps_header_grounded_overload`; [mapper tests](../../tests/pkg/test_clang_link.py) |
| Supported caller shapes | Ordinary representable functions/methods, file-static C++ functions, exact destructors and `operator()` callers. | `test_file_static_callers_preserve_scope_and_source_file`, `test_exact_destructor_and_call_operator_callers` |
| Refused identities | Template/local/anonymous/operator targets and namespace/scope identities the CST nodes cannot represent; ambiguous roots, conflicting candidates, wrong caller/source and unsafe macro ranges. No node repair or guessed retargeting. | USR refusal tests, local-callable regressions, macro/caller regressions; [Step 3b audits](clang-semantic-step3b.md#source-audit-and-required-precision-correction) |
| Routing / denominator (D4, D6) | Existing narrow `.h` routing retained. Only TUs selected by unresolved CST sites (including reached header sites) are parsed; pending byte ranges and TU selection are unchanged. | `test_header_sites_select_including_tu`, `test_only_pending_tus_are_parsed_and_reuse_clears_state`; A/B/C invariant receipts |
| Missing headers / partial AST | Diagnostics can coexist with useful edges; a parsed TU is not proof of coverage. Unsupported, unresolved or failed sites remain unresolved. Recovery fractions describe the fixed pending-site denominator, not whole-repository recall. | `test_missing_headers_and_external_targets_add_no_nodes`, five-repository reports |
| macOS arm64 | Native runtime tested on macOS 26.6.2, Python 3.12.7, libclang 18.1.1: isolated wheel smoke, local suite and five-repository measurements. | [Package receipt](clang-semantic-step4-package.json), [Step 3b measurements](clang-semantic-step3b-results.json) |
| Linux | Native semantic tests run in the Ubuntu/Python 3.12 CI job with `[clang]` installed. Final revision's CI must pass; this is not a five-repository Linux timing benchmark. | [CI configuration](../../.github/workflows/ci.yml), final MR gate receipt |
| Windows / other architectures | Runtime unverified here. Historical Linux ARM and Windows wheel availability is packaging evidence only; no Windows or cross-platform performance claim. | [P0 wheel receipt](clang-semantic-validation.md#wheel-receipt) |

Test names above refer to [test_clang_link.py](../../tests/pkg/test_clang_link.py)
unless otherwise linked. No new front-end, CLI/MCP surface, node/edge kind or
codegen toolchain is introduced. The [semantic-pass checklist](../reviewing/language-frontend-checklist.md#semantic-post-passes)
and [documentation matrix](../reviewing/docs-matrix.md) apply: README, SETUP,
USER_GUIDE, parser spec and status indexes are reconciled. Existing MR changes
already cover CHANGELOG, extras/doctor/fingerprint and CI integration.

## Correctness review and proposed loss dispositions

These are **proposed accepted limitations**, pending the maintainer decision.
Keep conservative refusal; do not broaden source matching or change D1–D6 to
recover these relationships without a separate fix and revalidation.
Source links use OpenCV's frozen commit `b4c5ec4042f097e2a5b386b9d413ec7333d0a184`.

| Audit ID | Correct relationship / pinned source | Evidence and proposed disposition |
|---|---|---|
| I215 (old O023) | `CvCascadeBoostTrainData::CvCascadeBoostTrainData → CvFeatureEvaluator::getMaxCatCount`, [boost.cpp:479](https://github.com/synaptixs/aiopencv/blob/b4c5ec4042f097e2a5b386b9d413ec7333d0a184/apps/traincascade/boost.cpp#L479) | Typed receiver is correct. MAX expansion has a zero-width clang range at 15820 versus CST 15828–15862. Accept missing edge under exact-range matching; do not guess a macro source range. |
| I216 (old O024) | `CvCascadeBoostTrainData::setData → CvFeatureEvaluator::getMaxCatCount`, [boost.cpp:544](https://github.com/synaptixs/aiopencv/blob/b4c5ec4042f097e2a5b386b9d413ec7333d0a184/apps/traincascade/boost.cpp#L544) | Same documented range failure: clang 18108 versus CST 18116–18150. Accept missing edge with the same restriction. |
| I217 | `cv::dnn::darknet::ReadDarknetFromCfgStream → cv::dnn::darknet::setLayersParams::setConvolution`, [darknet_io.cpp:766](https://github.com/synaptixs/aiopencv/blob/b4c5ec4042f097e2a5b386b9d413ec7333d0a184/modules/dnn/src/darknet/darknet_io.cpp#L766) | Explicit typed receiver; no matching CALL_EXPR in candidate partial AST after additional headers. Deeper cause **not isolated**. Accept unresolved relationship, with a diagnosis follow-up before claiming improved coverage. |
| I218 | Same caller → `setLayersParams::setCrop`, [darknet_io.cpp:816](https://github.com/synaptixs/aiopencv/blob/b4c5ec4042f097e2a5b386b9d413ec7333d0a184/modules/dnn/src/darknet/darknet_io.cpp#L816) | Same observed absence, independently reviewed call; deeper cause **not isolated**. Accept unresolved relationship under the partial-AST limitation. |
| I220 | `cv::eigenNonSymmetric → cv::EigenvalueDecomposition::compute`, [lda.cpp:974](https://github.com/synaptixs/aiopencv/blob/b4c5ec4042f097e2a5b386b9d413ec7333d0a184/modules/core/src/lda.cpp#L974) | Local receiver is explicitly typed; no matching CALL_EXPR. Deeper cause **not isolated**. Accept unresolved relationship under the same limitation. |

Diagnostic receipts: [old pass](clang-semantic-step3b-loss-B.json),
[candidate](clang-semantic-step3b-loss-C.json), [cursor evidence](clang-semantic-step3b-loss-cursors.jsonl).
The I219 Torch loss is a wrong namespace identity and is correctly refused.
The seven fmt removals omit `fmt::v11` and are correctly refused.

The [fixed OpenCV audit](clang-semantic-step3b-audit.jsonl) retains 198 correct
relationships from its fixed 200-new-edge sample and refuses its two wrong ones;
the sample was not refilled. Twelve wrong lambda self-edges are absent, including
four outside the old audit. All 54 GoogleTest additions were source-reviewed.
The original Step 2 audit retains 164 of 166 previously correct relationships;
all 31 incorrect and one ambiguous relationship remain absent. All 27 Step 3
additions remain. Every removal has a disposition; no reviewed incorrect or
ambiguous addition remains unresolved. These are bounded, non-independent
reviews and do not establish population-wide precision.

## Operational cost and profile decisions

Reuse the [45 frozen runs](clang-semantic-step3b-results.json): three fresh
processes for each configuration/repository, fixed interleaving and hashes.
Same macOS host; pre-read inputs, not guaranteed cold filesystem caches.
A = clang absent, B = previous pass, C = current candidate. Seconds below are
median [min–max]; extraction includes native parsing, not subsequent verification.

| Repository | A | B | C | Proposed use / decision |
|---|---|---|---|---|
| OpenCV | 29.501 [29.012–29.565] | 58.381 [58.167–60.171] | 300.296 [292.208–304.315] | Batch only when +1,844 relationships versus B justify +241.914 s (5.14× B, 10.18× A). Five correct losses disclosed. Do not recommend for low-latency extraction. |
| TinyXML-2 | 0.107 [0.106–0.112] | 0.335 [0.331–0.348] | 0.300 [0.297–0.310] | Useful small-project enrichment: 410 edges over A; no new edges over B. |
| GoogleTest | 1.148 [1.137–1.153] | 1.736 [1.716–2.364] | 6.284 [6.256–6.304] | Narrow batch contribution: 54 reviewed edges for +4.548 s versus B; not broad coverage. |
| pugixml | 0.236 [0.234–0.240] | 2.798 [2.797–2.849] | 1.703 [1.691–1.709] | No recovered edges; do not install clang for this measured profile alone. |
| fmt | 0.826 [0.825–0.833] | 1.185 [1.178–1.190] | 4.797 [4.790–4.822] | Zero fmt-owned contribution; 24 bundled GoogleTest edges only. Do not install clang for the formatting API alone. |

Frozen supported-label presence: OpenCV 48/50, TinyXML-2 50/50, pugixml 0/6
(44-label shortfall), fmt 1/50 (bundled tests), GoogleTest 2/50. Recovery is
2,597/135,633; 416/1,379; 0/1,637; 24/3,440; 54/5,599 sites respectively.
These samples are not whole-repository recall. All A/B/C nodes, routing, pending
sites, CST edges and verification findings match; existing repository verifier
errors remain, as recorded in the [evaluation](clang-semantic-step3b.md#tu-bounds-graph-preservation-and-verification).

`[all]` users incur this cost automatically. Omitting clang in a fresh environment
is the supported alternative. The existing cache fingerprints parser source,
available grammars and libclang version and bypasses dirty/non-git inputs;
persistence regressions cover invalidation. **No measured cache-hit latency or
cached OpenCV usability claim is made here**, and caching is not used to justify
acceptance of fresh-extraction cost.

## Candidate validation

### Isolated wheel / omission proof

[Machine-readable receipt](clang-semantic-step4-package.json),
[smoke reproducer](clang-semantic-step4-smoke.txt),
[comparison reproducer](clang-semantic-step4-package-check.txt).
Built a wheel locally; nothing published. Exported `[c,cpp]` dependencies from
`uv.lock`, installed the wheel into two fresh virtual environments outside the
checkout, with libclang 18.1.1 only in the present environment. Missing cached
runtime dependencies were downloaded from PyPI; no shared environment changed.

- All 365 wheel Python files match the checkout. All frozen Step 3b input hashes
  match, so the 45-run benchmark remains applicable without rerunning it.
- Imports resolve inside each environment's `site-packages`; no editable import.
  Importing the extractor does not import `clang.cindex`.
- Physical absence is checked by both module lookup and distribution metadata.
  The prefixed-header fixture resolves 0/1 without clang and 1/1 with it, with
  identical nodes and zero parse failures.
- All seven C/C++ corpus roots preserve nodes and all CST edges. The two added
  corpus edges have grounded endpoints. Other-language corpus coverage belongs
  to the full suite, not this C/C++ wheel smoke.
- Fingerprints differ with physical dependency presence. Wheel metadata includes
  libclang in `[clang]` and `[all]`, and excludes it from `[languages]`.

Reproduction uses `uv build --wheel`, `uv export --frozen --no-dev --extra c
--extra cpp --no-emit-project --format requirements-txt`, then `uv venv` and
`uv pip install --python <environment>/bin/python -r <export> <wheel>[c,cpp]`;
add `[clang]` and `libclang==18.1.1` for the present environment. Run the smoke
from outside the checkout with arguments `absent|present <absolute-corpus-path>`.
The comparison reproducer records the fixture, corpus and source-hash assertions.

### Gate receipt

[Local gate output](clang-semantic-step4-gates.txt):

- Focused semantic/include-root/persistence tests: **109 passed**, 30 warnings.
- Full suite: **3798 passed, 4 skipped, 51 deselected, 182 warnings in 221.97s**.
  Skips: E2B credentials absent, pytesseract absent, PHP/Composer unavailable,
  opt-in PostgreSQL/Docker integration disabled. Default integration/real-LLM
  marker exclusions account for the deselections.
- mypy: 723 source files clean. Ruff lint and format: pass (760 files).
- All 14 gated state claims, matrix count, both SVGs, roadmap currency and MCP
  inventory: pass. Nine documentation-binding trends are recorded, not gates.
- Accuracy: zero gated regressions. All four SDLC repository shapes pass.
  Self-verification: zero errors, one existing phantom-module warning.
- The saved fixed OpenCV selection and final graph/refusal assertions rechecked
  successfully: 219 retained, 13 refused incorrect, five known correct losses.

The first restricted full run was interrupted: 19 failures were cache writes
blocked by sandbox permissions, and npm installation stalled. The authorized
rerun above passed without source changes. Workspace files were frozen throughout
each full run. This interruption is not counted as a passing run.

The documentation audit and pre-commit hooks run on the committed review package;
final remote CI (including native Linux tests and the `understand` build) is checked
on that revision. Exact revision, outcomes and CI URLs are recorded on MR #379;
no ready disposition is permitted while a required check is pending or failing.

## Decision and follow-up ownership

**Current disposition: awaiting maintainer decision; readiness held.**
Reviewer: Codex, implementing agent, 2026-09-15. No independent maintainer review
or acceptance is implied by this technical recommendation.

The decision owner must choose ready for merge/release review, hold for specified
fixes, or defer support for the pinned implementation and final documentation
revision. A ready decision explicitly accepts all five losses, the above profile
limits, automatic `[all]` activation and measured runtime cost. It does not
approve merging or publishing.

| Follow-up | Owner / completion condition |
|---|---|
| Final readiness acceptance | Repository maintainer; record dated decision, candidate revision and accepted limitations on MR #379 |
| Partial-AST diagnosis for I217/I218/I220 | Maintainer to assign before a recovery-expansion track; isolate a reproducer before naming a cause or changing flags |
| Macro range recovery for I215/I216 | Maintainer to assign if prioritized; retain negative macro/caller regressions and audit every new edge |
| Runtime improvement | Maintainer to assign if prioritized; preserve D1–D6 and rerun all five profiles for shared traversal/include/mapper changes |
| Windows / additional libclang runtime coverage | Maintainer to assign before expanding tested-platform/version claims; wheel availability alone is insufficient |

These follow-ups are not implemented fixes or accepted commitments by an unnamed
contributor. No new recovery target, labels, denominator or platform guarantee is
introduced to obtain a ready outcome.
