# Clang semantic pass — the small-repository control

Status: complete. Adds one repository to the [Step 3b five-repository
evaluation](clang-semantic-step3b.md) and **corrects a claim shipped in 3.35.0**.

## Why this exists

`STATE-OF-SPINE.md` shipped in 3.35.0 with this caveat on the clang recovery figures:

> *all five are large or STL-heavy repositories, so a low recovery rate cannot be attributed
> between this pass's own limits and those repositories' scale and macro style. A small C++
> repository with in-repo class headers and light STL use was never measured alongside them.*

**The first sentence is false and the second is misleading.** The Step 3b
[manifest](clang-semantic-step3b-manifest.json) records `tinyxml2` (273 files) as *"Small C++
comparison"* and `pugixml` (191 files) as *"Compact, self-contained core implementation"*. Two
of the five were already small, deliberately chosen as such. Only `opencv` is large.

So the open question was never "what happens on a small repository". It was narrower, and worth
answering: `tinyxml2` recovers **30.17%** while every other repository recovers under 2%. What
explains a 15× gap?

## The control repository

`yaml-cpp` at [`8eb618e`](https://github.com/jbeder/yaml-cpp), a gitless snapshot of 404 files.
It fills a cell none of the five occupied: **small, with many class-declaring `.h` headers (the
header-routing shape), and real STL use throughout**. `tinyxml2` has one such header; `pugixml`
has none at all.

## Measurement

A = clang off (extra uninstalled); C = clang on. Same snapshot, same commit, fresh process.

| | A (clang off) | C (clang on) |
|---|---:|---:|
| Grounded nodes | 4,854 | 4,854 |
| External nodes | 425 | 425 |
| Total edges | 39,556 | **39,715** |
| `CALLS` | 28,723 | **28,882** |
| Extraction, median of 2 | **2.05 s** | **8.89 s** |

`clang: resolved 160 of 8,488 unresolved call sites in 124 of 160 TUs; 124 with diagnostics,
0 failed.` → **1.885% recovery.**

**Node sets are byte-identical** (7,173 ids including externals, `on == off`), and edges are
**+159 / −0** — no CST edge displaced. This is D3's additive-only guarantee holding on a real
repository rather than on a corpus fixture.

160 resolved sites produced **159** distinct edge facts: one recovered site duplicated an edge
already present at the same caller, callee, file and line, and deduplicated through
`Edge.key()`. The 159 facts span **99** distinct caller→callee pairs — the rest are parallel
call sites between an already-connected pair.

## What actually predicts recovery

Repository shape and `std::` density, measured identically across the comparable repositories
(`std::` occurrences per 1,000 lines of `.h`/`.hpp`/`.cpp`/`.cc`/`.cxx`):

| Repository | Files | `.h` declaring a class | `.hpp` | `std::` per 1k | Recovery |
|---|---:|---:|---:|---:|---:|
| tinyxml2 | 273 | 1 | 0 | **0.4** | **30.17%** |
| pugixml | 191 | 0 | 6 | 15.6 | 0.00% |
| **yaml-cpp** | **404** | **75** | **0** | **51.0** | **1.89%** |
| googletest | 252 | 34 | 0 | 57.0 | 0.96% |
| fmt | 145 | 19 | 0 | 66.6 | 0.70% |

Excluding `pugixml`, the four remaining repositories are **monotonic**: recovery falls as
`std::` density rises, across a 15× spread. `opencv` (7,654 files, macro- and generated-header
heavy per the manifest) recovers 1.91% — statistically indistinguishable from `yaml-cpp`'s
1.89% despite being **19× larger**.

`pugixml` is an identifiable separate case rather than noise: it has **no `.h` files at all**,
so header routing never applies, and its Step 3b supported-label set was 6 rather than 50.

**Size is not the explanatory variable. Standard-library density is.** `tinyxml2` recovers 30%
because it deliberately avoids the STL (0.4 per 1k — three occurrences in 8,390 lines), so
almost every receiver type is a first-party class clang can resolve from repository sources
alone. This is the documented ceiling behaving exactly as designed: the wheel-bundled libclang
ships no builtin headers and reads no SDK, so a receiver whose type is `std::string` or
`std::vector<T>` is error-typed and its call site is correctly dropped rather than guessed.

## Limits

- **n = 5, one control, correlation not causation.** `std::` per 1,000 lines is a crude proxy
  for "fraction of receivers whose type is unresolvable"; it counts textual occurrences, not
  call receivers. It predicts this set well and should not be quoted as a law.
- Recovery here counts distinct pending byte ranges, matching Step 3b's denominator. It is not
  whole-repository `CALLS` recall, and no precision claim is made from it — the Step 3b
  [correctness audit](clang-semantic-correctness-audit.md) remains the precision evidence.
- The 4.3× extraction cost (2.05 s → 8.89 s) is the bounded-pass cost on a repository where
  124 of 160 TUs carry diagnostics. It is not a projection for repositories that parse cleanly.

## Reproduction

```bash
git clone --depth 1 https://github.com/jbeder/yaml-cpp   # 8eb618e
rm -rf yaml-cpp/.git
uv run --frozen orchestrator pkg extract yaml-cpp        # C, with --extra clang synced
uv pip uninstall libclang
uv run --frozen --no-sync orchestrator pkg extract yaml-cpp   # A
```

Node and edge parity was checked by exporting both configurations with
`pkg export --format json` and diffing id sets and `(src, dst, kind, file, line)` keys.
