# Explicit VisibleV8 optimizer repair

`camoufox-visiblev8-optimizer.1` is a source repair candidate for the pinned
VisibleV8/Chrome 155 reference. It requires a new native build and runtime gate
before it can be described as verified. The original runtime failures remain
failures. The original browser reference reached its 330-minute job limit after
27,218 of 40,248 build steps; it did not reach runtime conformance. That timeout
and the original shell's runtime failures remain separate evidence.

`visiblev8-optimizer.yml` runs the repaired shell as an independent native job
with four compile jobs on the observed four-core Linux runner. It is published
on a separate branch and pull request from the original headless resource work,
with its own concurrency group. This separation matters because pull request
path filters evaluate the cumulative diff. It restores compatible compiler
cache entries and saves under a unique run/attempt key, with actual cache
configuration, per-build statistics, disk usage, native provenance and runtime
traces retained as artifacts. Native failures remain failures.

The immutable inputs are VisibleV8
[`068c617f`](https://github.com/wspr-ncsu/visiblev8/tree/068c617f28686f5c43d27c891803cd21510aa141),
V8 [`82b65fdd`](https://github.com/v8/v8/tree/82b65fdd7b5847a41e76c9eeb04351ebec267785),
version 15.5.35.14. The original VisibleV8 checkout, its patch, the nine golden
logs and their fixture sources stay unchanged. Repairs are generated in an
owned build tree after applying the original patch. Every source input is
checked by SHA-256; the generator emits a review diff and before/after manifest.
Original V8 and VisibleV8 notices are delivered in `LICENSE.v8` and
`LICENSE.visiblev8` and retained in generated source.

## Actual original failures

The [original source run](https://github.com/TonyChen-SH/camoufox-reverse/actions/runs/41018506596)
compiled the shell successfully, then failed runtime conformance. Its ELF has
SHA-256 `a47a0d9eb8c4a1af4a2fc7f00b000ed962de473d4431a1e5f5e8689f9d1dc138`.
This is the recorded extra-debugging-features build configuration in the
artifact provenance; these observations do not establish the behavior of every
possible upstream Release configuration.

All nine exact goldens failed. An exhaustive field comparison found one
embedder difference: the same shell global receiver identity has class `Object`
instead of the golden's `global`. All other fields and record ordering matched.
That comparison describes the difference; it does not turn exact failures into
passes or rewrite the expected logs.

| Golden | Actual and expected records | Receiver class differences | Other differences |
| --- | ---: | ---: | ---: |
| binder | 14 | 10 | 0 |
| colons | 6 | 2 | 0 |
| escapes | 5 | 1 | 0 |
| evil | 28 | 7 | 0 |
| funobj | 7 | 3 | 0 |
| obj_func | 5 | 1 | 0 |
| objobj | 7 | 1 | 0 |
| pi | 104 | 100 | 0 |
| props | 4,021 | 4,017 | 0 |

The separate default optimized `props.js` run executed every print: `42`, then
`0..1337`. It recorded `s=1339`, `g=1134`, `c=1134`. All 1,338 `bar` stores were
present, but `bar` values **1133..1337** had no corresponding get/call records.
The first 1,133 reflected stores used source offset **128**; the final 205
degraded to **0**. The intact cold sites are `s@58`, `g@77`, `c@64`; loop sites
should be `s@128`, `g@167`, `c@153`.

Running the actual downloaded ELF locally reproduced variable default losses
(`g/c=1125` and `1114`, with `s=1339`). `--trace-opt --trace-osr --trace-deopt`
proved actual Maglev OSR entry, with `g/c=1131`. An explicitly nonoptimizing
control captured all 1,339; that control is not a repair. Additional controls
isolated the source-coordinate failure: `--detailed-line-info` retained tables
but left hot stores at offset **84**, the loop location. Combining that flag
with `--no-maglev-loop-peeling` restored all stores to **128** but still lost
get/call records. Neither flag combination is accepted as a repaired gate.

`turbofan-probe.js` independently reproduced the Reflect.get loss with real
TurboFan code. Two warm calls establish feedback; a forced optimization runs
the 1,339-iteration hot call. The fixed provider's `OptimizationStatus` bits
verify both `kOptimized` and `kTurboFanned`; the process exits successfully and
does not deoptimize. The original records all 1,341 stores and native calls but
only the **two warm gets**, losing every hot get. This diagnostic uses V8 native
testing syntax and is not injected into browser pages.

## Changes and limits

The generator preserves instrumented builtin calls for Maglev's known API
JSFunctions and Reflect.get, and TurboFan's Reflect.get. It leaves the tiers
and Wasm enabled. V8 can still optimize the surrounding JavaScript. The original
VisibleV8 patch already preserves the TurboFan API-call boundary.

Runtime loggers obtain their source position from the optimized caller's PC
table. The profile retains those tables while property tracing is enabled.
It also restores the source iterator and node provenance when Maglev's loop
peeler rewinds the bytecode iterator. The pinned peeler otherwise reuses the
position left at its preceding `JumpLoop`, explaining the observed offset 84.
Loop peeling remains enabled.

The separate `visiblev8-shell-fixture-compat.1` embedder profile supplies the
shell global template with a named constructor using V8's public
`FunctionTemplate::SetClassName` and `ObjectTemplate::New` APIs. It addresses
only the shell's stale `global` class expectation. It does not rename arbitrary
objects or change browser globals. The standalone generator enables this part
only with `--shell-fixture`; the shell build wrapper requests it explicitly.
The exact unchanged goldens must demonstrate the resulting behavior.

This is not an implicit-call expansion for every lazy WebIDL getter. Existing
FunctionTemplateInfo accessor and DOM callback paths retain their separate
reference semantics. Passing these fixtures cannot establish Android native
emission, every browser API or all optimizer transformations.

## Build and verify

Use the same Linux prerequisites and pinned original checkout as
[`reference/README.md`](../reference/README.md). This wrapper reuses the original
toolchain, GN arguments and include compatibility profile and adds its separate
optimizer manifest to the delivered binary provenance:

```bash
python3 optimizer-profile/build.py --reference original-visiblev8 \
  --work .reference-optimizer-work --output optimizer-evidence --jobs 2
sudo ln -s "$PWD/original-visiblev8/tests/src" /testsrc
python3 optimizer-profile/conformance.py --reference original-visiblev8 \
  --shell optimizer-evidence/bin/v8_shell --output optimizer-evidence/runtime
```

The wrapper supports `--phase prepare` and `--phase build` for ccache restoration
between setup and compilation. Preparation runs the source generator tests on
the exact original patched inputs before generating the repair. Source-only
tests verify anchors, file hashes, preflight rejection and reversible diffs;
they are not native runtime verification.

The runtime gate retains all nine exact original goldens and the original
default 1,339-event check. Additional checks require complete values and precise
offsets in the default run, an actually entered Maglev OSR run, and a TurboFan
run that remains optimized. An executable Wasm module must return 42.
Tier-diagnostic flags schedule compilation sooner;
they do not disable a tier, Wasm, inlining globally or loop peeling. Counts,
offsets, actual outputs and tier evidence are reported separately. Every new
failure fails the gate, including an absent or mismatched `/testsrc` fixture.
