# Additional native runtime profiles

These profiles reuse the exact source-availability browser candidate from
[native run 15](https://github.com/TonyChen-SH/camoufox-reverse/actions/runs/41064733272).
The workflow verifies its complete archive checksums and the source baseline
before execution. These are additional controlled localhost validations; their
settings are not production browser defaults. Missing prerequisites or missing
native evidence fail the profile.

## Cross-process frames

Camoufox's pinned configuration sets `fission.autostart=true` and
`fission.webContentIsolationStrategy=0`, which keeps ordinary content in one
process. A correct same-process cross-window result therefore cannot establish
coverage of remote Window proxies.

`fission.py` uses the supported startup `CAMOU_PREFS_1` configuration to set the
isolation strategy to `1`. It requires every one of the existing 22 engine
checks to pass, then requires actual separate main/cross-site native PIDs and
both `Window.closed` reads to retain the `Window` class. The existing fixture
also requires the live frames to report `closed=false`. An absent or shared
PID fails; a configured preference alone is insufficient evidence.

```bash
xvfb-run -a python runtime-profiles/fission.py \
  --binary /absolute/path/to/camoufox-bin --output evidence/fission
```

Run from the repository root with the analysis package, its actual MCP SDK and
Playwright 1.62.0 installed. Output directories must be new. The complete
OFF/ON captures, page observations, source/manifest/SDK results and separate
profile report are retained.

The configuration is documented by the pinned
[Camoufox startup preferences](https://github.com/daijro/camoufox/blob/b6205b0ee1e4c2e8e4bb9eb0e084472028a9c6a3/browser/settings/camoufox.cfg#L529)
and Firefox's native
[site-isolation strategy](https://github.com/mozilla-firefox/firefox/blob/9378fbc58660847fbc02c3486a0ecd6049978e25/dom/ipc/ProcessIsolation.cpp#L468).

## Actual Ion execution

An iteration count alone does not establish that Firefox entered Ion. The
default Ion threshold is 1,500 and is subject to script/loop scaling and
off-thread completion. `ion.py` uses the release browser's native startup and
normal-shutdown profiler with `js,stackwalk`, a 0.5 ms sample interval and a
16 MiB ring buffer. It changes no JIT preferences and exposes no page debugging
API.

Three owned synchronous functions exercise host reads, writes and actual
Storage calls. Each runs two fixed 5,000-iteration warmups with a fixed yield,
then 10,000 measured iterations. Both warmup and measured phases require exactly
10,000 of each operation: host get, host set, method reference and native call.
The runner preserves all 80,000 loop API events and checks OFF/ON page values
against an independent arithmetic result. Its explicit limits are 300,000
events, the production reader's 200,000-record limit and 67,043,328 collected output bytes; hitting any limit
fails the profile.

Acceptance additionally requires native samples whose referenced stack chains
contain each named function, the exact owned script URL and implementation
`ion`. An unused Ion frame-table row, another site's URL or a baseline-only
sample cannot pass. This proves sampled Ion execution of those functions;
individual trace events do not carry an execution-tier field.

```bash
xvfb-run -a python runtime-profiles/ion.py \
  --binary /absolute/path/to/camoufox-bin --output evidence/ion
```

The output retains separate OFF/ON raw captures, page values, normal-shutdown
profiler JSON and `ion-report.json`. Missing shutdown profiles, absent Ion
samples, changed page values or any missing/extra required event fail without
retry or skip. The profiler environment parser is fixed by
[platform.cpp](https://github.com/mozilla-firefox/firefox/blob/9378fbc58660847fbc02c3486a0ecd6049978e25/tools/profiler/core/platform.cpp#L6308),
including its supported `MiB` suffix. Native
[frame serialization](https://github.com/mozilla-firefox/firefox/blob/9378fbc58660847fbc02c3486a0ecd6049978e25/tools/profiler/core/ProfileBufferEntry.cpp#L548)
provides the implementation field; the validator follows actual sample and
stack-table indices rather than searching the JSON text.
