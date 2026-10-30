# Original VisibleV8 reference gates

These gates build and execute the BSD-3-Clause VisibleV8 instrumentation at
`068c617f28686f5c43d27c891803cd21510aa141`, with the explicitly identified
Linux build compatibility repair described below. They do not substitute a Python
implementation for its Go aggregators or its Rust adblock engine.

The source profile is Chromium **155.0.8059.30**, V8 **15.5.35.14**.
`visiblev8.json` pins the V8 and depot_tools revisions declared by that exact
Chromium DEPS, the upstream patch, Go, Rust and the isolated PostgreSQL source.
No moving Docker tag or older browser release is accepted as this profile.

The build explicitly selects V8's supported Torque instance-type generator
(`v8_use_metagen_instance_types=false`). In this pin, the default metagen
harvest suppresses generated enum definitions while headers still reference
them; the original shell build failed on undeclared Wasm/Builtin types. The
pin retains Torque as the reference generator used by metagen's own comparison
mode. This configuration choice is recorded in the binary provenance; it does
not disable Wasm, JIT tiers or logging, and all runtime assertions remain in
force. The failed default-metagen build is retained as separate evidence.

The next actual source compile identified an upstream include cycle:
`isolate.h` includes `runtime-utils.h` under `DEBUG`, while VisibleV8's patched
header includes `api-inl.h` and therefore uses `Isolate` before its definition.
The `visiblev8-linux-build-compat.1` profile moves that entire implementation
include block into `runtime-utils.cc`; the header keeps its original object
include, the required vector declaration and all original logger declarations.
No logging function body or optimization setting changes. The generator verifies
both exact input hashes, emits a reviewable diff and before/after manifest, and
records the profile in binary provenance. The upstream VisibleV8 checkout and
patch remain intact; this source build is not described as unmodified upstream.
Both failed default builds remain separate evidence. The small pristine-header
test fixture is V8 blob `345e326eed8d564e252373ec3ea714a2c3ea03d0` at the same
V8 pin; its BSD notice is retained in `fixtures/LICENSE.v8`.

The source-build workflow runs two independent jobs:

- The original standalone V8 shell is built from the pinned V8 source. All
  nine upstream `trace-apis-obj` goldens are compared with the upstream
  relabeler, using the same `--no-maglev --no-turbofan` flags as its runner.
  A separate normal-default optimized run checks the 1,339 get/set/call loop
  counts. Missing executable or source prerequisites fail.
- One Linux `headless_shell` target is built from the pinned Chromium source.
  Controlled localhost scripts test method references, native call variants,
  construction, eval/Function, object arguments and Workers. Its optimized
  hot paths require exactly 6,000 property reads and 4,096 native calls.

The independent `visiblev8-postprocessors.yml` workflow verifies the original
Go, fixed-rule Rust and PostgreSQL contracts alongside the explicitly identified
`camoufox-adapter.1` repaired profile. It preserves the raw original failures
and accepts only their exact known signatures as `expected_upstream_failure`;
unknown original failures and every repaired supported-profile failure fail the
gate. This includes the three observed original SQL failures, incorrect entity
column values and swallowed adblock errors. The source-build workflow delegates
postprocessing to that independent gate. The postprocessor fixture uses fixed interface,
entity and ABP-rule data, not a production ownership or EasyList data set.

The two source builds run separately from this postprocessing gate, so a slow
browser compile does not prevent format/analysis feedback. Compiler cache keys
include the reference lock, driver and compatibility generator; ccache validates source
and compiler content when restoring a prior compatible cache. The jobs retain
binaries, provenance, original notices, runtime traces and evidence for 14 days.
Small log/runtime/provenance artifacts are also published separately, so raw
traces remain downloadable without first downloading a full Chromium binary.
No API credentials are written
to source checkouts. The upstream reference's sandbox changes apply only to
this isolated CI reference; Camoufox's sandbox is unchanged by these jobs.

The original headless job in
[run 41018506596](https://github.com/TonyChen-SH/camoufox-reverse/actions/runs/41018506596)
reached **27,218/40,248** Ninja steps before its 330-minute job timeout. Its
compile log has no compiler failure and it did not reach browser runtime tests.
The log records 15 GiB RAM with 14 GiB available and 104 GiB free disk after
setup and swap, but only two compile jobs were used on the four-core runner.
The revised headless job records its processor count, uses four compile jobs and explicitly limits links to
one. It retains the same instrumentation, debugging-feature configuration,
JIT/Wasm settings and runtime assertions. It keeps the 330-minute budget; this
resource change is not a claim that the browser has already passed.

The timeout saved only **80,991,159 bytes** of compressed compiler cache, not a
complete checkpoint of the 27,218 outputs. The actual Ubuntu package was ccache
4.9.1-1. The pinned Chromium compile uses Clang modules; that version's
[module documentation](https://github.com/ccache/ccache/blob/v4.9.1/doc/MANUAL.adoc#c-modules)
explains why ordinary default ccache settings reject `-fmodules`. The old run
did not retain cache counters, so its exact uncacheable proportion is unknown.

The headless cache now enables the documented direct/depend/module profile and
additionally hashes both generated PCM files. Before the full build it builds
the real module prerequisites and one original Abseil C++ object, retaining
its actual Ninja response file. It executes that exact compiler invocation
twice, verifies that both calls were cacheable and at least one was a cache hit,
and records the commands, PCM hashes and original machine-readable counters.
A missing wrapper, module flag or usable cache fails this early gate. This
checks compiler-cache behavior; it does not replace browser runtime tests.

The cache limit is 4 GiB, within the observed disk budget. Unique run/attempt
save keys allow later runs to preserve additional work instead of reusing an
immutable existing save key. The previous cache prefix remains eligible for
restoration. Actual ccache version/configuration, per-build statistics, effective
GN arguments, output size and final disk/memory usage are retained for the next
resource decision. The output directory is not represented as a persisted
complete checkpoint; only verified cache entries are reused.

After the original native build succeeds, `browser_profiles.py` runs the original
browser gate as a subprocess and retains its exact exit code, stdout/stderr,
page observations, raw trace, binary and provenance. It then imports the repair
generator from the immutable provider commit recorded in
`optimizer-profile.json`, verifies its source hash and applies only the three
engine source repairs. The shell embedder change is prohibited for the browser.
The same source and `out/Reference` tree are reused for the incremental build;
the repaired binary, provenance, generated diff and runtime evidence are
delivered under `reference-browser-repaired/`. Original delivered files remain
unchanged and their binary/provenance hashes are checked again afterward.

Both browsers run the same strict 6,000-read/4,096-native-call gate. An original
failure is classified as `expected_upstream_failure` only for the optimizer
loss shape already reproduced in the native shell: successful page execution, all 6,000 hot reads,
missing hot native calls and all required record kinds, with the exact gate
error. Its raw report still says `failed`; counts are not relaxed. Any unknown
original failure makes the aggregate fail even if the repair succeeds. Every
repair build or runtime failure fails. A successful aggregate is named
`passed_repaired_profile`, never a claim that the original captured everything.
The browser outcomes are unknown until the actual native run completes.

Run the source stages on a Linux build machine with the same prerequisites as
the workflow, from the repository root:

```bash
git clone https://github.com/wspr-ncsu/visiblev8.git original-visiblev8
git -C original-visiblev8 checkout 068c617f28686f5c43d27c891803cd21510aa141
python3 reference/build_visiblev8_reference.py --reference original-visiblev8 \
  --work .reference-shell-work --output reference-shell --target v8-shell
```

Use `--target headless-shell` with a separate empty work directory for the
browser. A work directory is marked with the selected profile; the driver
refuses unrelated nonempty directories. `--phase prepare` and `--phase build`
allow compiler cache restoration between dependency setup and compilation.

Both source phases invoke the pinned depot_tools `ensure_bootstrap` entry point
with self-updates disabled and verify that its Git revision remains unchanged.
The provider must create its own Python-runtime marker and its real
`python-bin/python3 --version` command must succeed. Merely running gclient
with `DEPOT_TOOLS_UPDATE=0` does not establish that prerequisite. The test-only
`fixtures/depot_python3` is the unmodified wrapper from this exact tools pin,
Git blob `33016e32126b83518243094d3c40183f786214d7`; its upstream notice is
retained in `fixtures/LICENSE.depot_tools`.

The original shell goldens contain literal `/testsrc` filenames, including an
eval fixture that reads `/testsrc/fred.txt`. The CI runner creates that symlink;
the shell checker refuses an absent or mismatched link rather than rewriting
the golden or its source. The upstream parser and relabeler are loaded directly
from the verified pinned checkout.

The Go postprocessor has known incomplete modes. `poly_features` and `blobs`
do not emit stdout payloads; `idlapis` has no stdout dumper; `callargs` has no
PostgreSQL writer. The adblock aggregator starts an unjoined readout goroutine,
so its observed row count is reported as a limitation and is not counted as a
deterministic pass. The original Rust oracle is tested directly with fixed
rules. The independent gate requires the repaired adblock aggregator to finish
synchronously and strictly checks fixed decisions and error propagation. Original
failures remain failures in their raw report with a separate expected-failure
classification; an attempted mode is never silently called successful.

`report.json` identifies the tested product profile. Passing these Linux jobs
does not establish native Android Gin/WebView emission, every browser API,
every JS instruction or any website's risk decision. Standard-log import and
export can establish format compatibility without pretending that the two
browser engines have identical object models.
