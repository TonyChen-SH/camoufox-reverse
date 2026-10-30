# Original VisibleV8 reference gates

These gates build and execute the original BSD-3-Clause VisibleV8 source at
`068c617f28686f5c43d27c891803cd21510aa141`. They do not substitute a Python
implementation for its Go aggregators or its Rust adblock engine.

The source profile is Chromium **155.0.8059.30**, V8 **15.5.35.14**.
`visiblev8.json` pins the V8 and depot_tools revisions declared by that exact
Chromium DEPS, the upstream patch, Go, Rust and the isolated PostgreSQL source.
No moving Docker tag or older browser release is accepted as this profile.

The workflow runs three independent jobs:

- The original Go and Rust postprocessors run over a controlled full-origin
  wire fixture with fixed interface, entity and ABP-rule data. Every accepted
  mode is executed; return codes, output and database row counts are retained.
  The synthetic entity/rule data exercises engine semantics and is not a
  production tracker ownership or EasyList data set.
- The original standalone V8 shell is built from the pinned V8 source. All
  nine upstream `trace-apis-obj` goldens are compared with the upstream
  relabeler, using the same `--no-maglev --no-turbofan` flags as its runner.
  A separate normal-default optimized run checks the 1,339 get/set/call loop
  counts. Missing executable or source prerequisites fail.
- One Linux `headless_shell` target is built from the pinned Chromium source.
  Controlled localhost scripts test method references, native call variants,
  construction, eval/Function, object arguments and Workers. Its optimized
  hot paths require exactly 6,000 property reads and 4,096 native calls.

The two source builds run separately from postprocessing so a slow browser
compile does not prevent format/analysis feedback. Compiler cache keys include
the reference lock and driver. The jobs retain binaries, provenance, original
notices, runtime traces and evidence for 14 days. No API credentials are written
to source checkouts. The upstream reference's sandbox changes apply only to
this isolated CI reference; Camoufox's sandbox is unchanged by these jobs.

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
rules. Other upstream failures remain failures in the report; an attempted
mode is never silently called successful.

`report.json` identifies the tested product profile. Passing these Linux jobs
does not establish native Android Gin/WebView emission, every browser API,
every JS instruction or any website's risk decision. Standard-log import and
export can establish format compatibility without pretending that the two
browser engines have identical object models.
