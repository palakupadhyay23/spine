# Clang Step 4.5 — gap review

Technical review: Codex (implementing agent). Reviewed candidate:
`0c39f6b74f21c9c64dd971f7b5dddcaad3a7cc56`. This review addresses gaps before the
maintainer's disposition; it does not supply independent approval or accept the
remaining loss/cost tradeoffs on the maintainer's behalf.

## Gap register

| Gap | Action / evidence | Disposition |
|---|---|---|
| Ten unresolved CodeQL discussions despite green checks | Reviewed every `py/cyclic-import` note, import locations and delayed back-imports; twenty isolated import/extraction checks below | Technical triage complete: bounded maintainability debt. Discussion resolution is tracked on MR #379; native-library laziness preserved. No scanner suppression or alert dismissal. |
| Canonical plan still said final CI was pending | Verified all checks at `0c39f6b`; pinned URLs copied into the readiness report and status indexes reconciled | Closed for the reviewed candidate; any new commit has its own CI receipt on the MR |
| Wheel receipt's `source_candidate` named the build's HEAD while README edits were uncommitted | Verified exact wheel hash, all 365 source Python files in both directions, UTF-8 README metadata, all seven expected corpus roots and frozen benchmark inputs | Closed by [candidate provenance receipt](clang-semantic-step45-package.json); original receipt retained as history |
| Three partial-AST losses lacked a small include-root reproducer | Four context parses plus 52 leave-one-root-out/control parses and eight small-root controls | Narrowed to the combinations below. Losses remain; deeper header/type-recovery cause is not isolated. No guessed mapper fix |
| Follow-ups did not distinguish blocking acceptance from optional improvements | Added owner roles and exit evidence below | Decision checklist is actionable; no invented assignee, review approval or implementation commitment |

## CodeQL discussions

The ten comments are **severity `note`, rule `py/cyclic-import`**, concerning
maintainability/modularity/reliability. The import cycles exist. Native compiler
loading is separate and remains deferred until eligible extraction.

| Comment | Import | Reviewed back-import boundary |
|---|---|---|
| [4019088999](https://github.com/synaptixs/spine/pull/379#discussion_r4019088999) | `c_extractor → cpp_extractor` | `_cpp_parser` is imported inside `cpp_header_paths`, called after dispatcher initialization |
| [4019089039](https://github.com/synaptixs/spine/pull/379#discussion_r4019089039) | `extractor → c_extractor` | Header-routing import is inside `RepoCodeExtractor.extract`; front-end registration also uses local imports |
| [4021363538](https://github.com/synaptixs/spine/pull/379#discussion_r4021363538) | `c_extractor → clang_link` | Top-level import loads `PendingMemberCall`; the return path through include inference is function-local |
| [4021363555](https://github.com/synaptixs/spine/pull/379#discussion_r4021363555) | `clang_includes → extractor` | Walker helpers are imported inside `infer_include_roots` |
| [4021363562](https://github.com/synaptixs/spine/pull/379#discussion_r4021363562) | `clang_includes → cpp_extractor` | Parser import is inside `infer_include_roots`, after grammar availability selection |
| [4021363568](https://github.com/synaptixs/spine/pull/379#discussion_r4021363568) | `clang_includes → c_extractor` | Same deferred C-parser fallback |
| [4021363575](https://github.com/synaptixs/spine/pull/379#discussion_r4021363575) | `clang_link → clang_includes` | Import is inside `link_clang`, after availability checks and native index creation |
| [4021363584](https://github.com/synaptixs/spine/pull/379#discussion_r4021363584) | `cpp_extractor → clang_link` | Loads `PendingMemberCall`; include-inference back-import remains deferred |
| [4021363597](https://github.com/synaptixs/spine/pull/379#discussion_r4021363597) | `extractor → clang_link` | `ClangReport` import is inside the extractor constructor |
| [4021363602](https://github.com/synaptixs/spine/pull/379#discussion_r4021363602) | `extractor → clang_link` | Report/pass imports are inside `extract`, after module initialization |

[Fresh-process evidence](clang-semantic-step45-import-review.json) and
[reproducer](clang-semantic-step45-import-review.txt): five modules, each imported
first in forward and reverse order, in both physical libclang-absent and present
wheel environments: **20/20 passed**. Every process verifies `clang.cindex` remains
unloaded after imports, then extracts a prefixed `.h` fixture, verifies C++ header
routing and checks zero/one recovered call according to native availability.
This is a bounded entry-order test, not an exhaustive proof of all permutations.
The existing focused regressions additionally cover C-only include inference.

Technical disposition: the reviewed lazy cycles do not establish an import-time
failure in the supported tested configurations. Retain them as explicit architecture
debt for this candidate; do not suppress CodeQL or describe the notes as false
positives. A separate refactor could move shared site/report types, walker helpers
and grammar factories into lower-level modules; its acceptance check must cover
import order, physical absence, header routing, graph parity and fingerprints.
No such refactor is necessary to repair an observed runtime failure here.

## Partial-AST loss isolation

[Context evidence](clang-semantic-step45-loss-context.json) /
[reproducer](clang-semantic-step45-loss-context.txt),
[root-ablation evidence](clang-semantic-step45-loss-ablation.json) /
[reproducer](clang-semantic-step45-loss-ablation.txt).
All probes use the frozen OpenCV snapshot, libclang 18.1.1 and recorded D2 flags.
Only the additional repository include-root list changes in diagnostic probes;
production configuration, sources, labels and denominators are untouched.

| Loss | Observation | Include-root controls |
|---|---|---|
| I220, `lda.cpp:974` | Enclosing `eigenNonSymmetric` function and body remain. Exact bytes 34713–34747 change from `CALL_EXPR compute` to `UNEXPOSED_EXPR`; a `MEMBER_REF_EXPR compute` remains. | Of the 25 single-root omissions, only omitting `modules/core/include` restores the call. Adding only that root to B reproduces the loss. |
| I217/I218, `darknet_io.cpp:766,816` | Enclosing `ReadDarknetFromCfgStream` and its body remain, but neither selected line has a cursor in C. Both exact calls are present in B. | Omitting either `modules/core/include` or `modules/dnn/include` restores both calls. Adding either alone to B retains both; adding the pair reproduces both losses. |

Each B/C context parse reports the same first fatal missing generated header:
`opencv2/opencv_modules.hpp` for core and `cvconfig.h` for dnn. Thus "the header is
missing" alone does not explain the difference. The controls isolate include-root
sensitivity; they do **not** isolate the precise declaration/type-recovery cause
inside the newly available headers. That deeper cause remains unresolved.

Do not remove valid include roots to regain these three calls: those roots support
other measured relationships, and no full-graph benefit/cost case for removal was
made. Do not treat `UNEXPOSED_EXPR` or a member reference as an authoritative call.
The five original correct losses remain unchanged, including the two macro-range
losses; no new recovery or precision number is claimed.

## Decision ownership and remaining work

| Item | Owner role | Blocking / completion evidence |
|---|---|---|
| Technical gap review and receipts | Codex, current implementation task | Completed above; subsequent MR revision must pass applicable checks |
| Loss/cost/support disposition | Repository maintainer (user) | **Blocks ready:** explicitly accept all five losses, automatic `[all]` activation and per-profile costs, or specify hold/defer. Record date and reviewed commit |
| Independent maintainer review / merge | Repository maintainer under CONTRIBUTING | Separate from this self-review; green CI and resolved bot threads do not supply approval |
| Deeper I217/I218/I220 diagnosis | Technical implementer, assigned when maintainer chooses this as a blocking fix or next track | Use the two root combinations above; isolate a smaller header/type reproducer before changing flags or matching. Preserve D1–D6 |
| Macro recovery, runtime, architecture refactor | Technical implementer, assigned when prioritized | Optional improvements unless the maintainer makes one blocking; preserve negative cases and revalidate affected graph/cost evidence |
| Wider platform/version claims | Technical implementer, assigned before expanding support | Native runtime proof on the additional platform/version; wheel availability alone does not suffice |

No further maintainer decision is inferred from “address any gaps.” Step 4.5 is
**in progress**; the request authorizes this diagnosis and corrective evidence,
not acceptance of unresolved tradeoffs or a release.

## Local validation of this review update

- Focused semantic/include/persistence regressions: **109 passed, 30 warnings
  in 1.98 s**. Twenty physical absence/presence import probes passed.
- The combined root-ablation reproducer regenerated all 60 control records;
  the four context parses are recorded separately. Package provenance assertions pass.
- Source/test/dependency files are unchanged from `0c39f6b`; its full local suite
  (3,798 passed) and five-repository measurements remain the implementation evidence.
- State-number and matrix checks pass; roadmap status and `git diff --check` pass.
  Commit hooks, post-commit docs audit and this documentation revision's remote
  checks are recorded on MR #379.
