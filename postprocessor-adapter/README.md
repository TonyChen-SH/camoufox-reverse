# Separately identified VisibleV8 postprocessor repairs

The original source is fixed at `068c617f28686f5c43d27c891803cd21510aa141`.
The **camoufox-adapter.2** profile repairs observed defects in that source;
it is not presented as an untouched original build. Its binary version is
`068c617f28686f5c43d27c891803cd21510aa141+camoufox-adapter.2`.

The original controlled integration run failed three PostgreSQL modes:

- `scripts` passed a Go `core.Origin` structure to a SQL text parameter.
- `causality` emitted `static_iframe`, missing from its declared SQL enum.
- `causality_graphml` selected the causality tuple columns rather than the
  declared GraphML columns, passed a buffer instead of XML text and ignored
  its first insertion error.

The original adblock aggregator returned before joining stdout ingestion and
discarded the child exit status and reader error. Real original-binary probes
also accepted malformed, missing, extra and incomplete results with exit 0.
The repair drains synchronously, verifies the result count/required fields,
propagates scanner and child errors, and reaps the child on a read failure.
Rules are still evaluated by the original fixed Rust engine with its lockfile.

Strict SQL value checks additionally identified swapped script/origin entity
columns and swapped root/first-origin booleans in the `fptp` writer. The repair
aligns values with the declared columns. An integration case deliberately uses
a different root domain, so equal booleans cannot hide the mistake.

Actual configured Firefox 158 IDL revealed another defect in both the original
and adapter.1: `HTMLDocument.createElement` normalizes to its defining interface
`Document.createElement`, but the element aggregator accepted only the former.
Adapter.2 accepts the normalized Document name and preserves the legacy
HTMLDocument fallback. A call without its first argument creates no tag row;
the generic feature aggregator still records the attempted call. Incomplete
record shapes return a diagnostic instead of indexing past the fields.

`fixtures/firefox158-document-idl.json` is a verbatim four-interface ancestor
projection of the actual configured Linux metadata from [the metadata build](https://github.com/TonyChen-SH/camoufox-reverse/pull/11),
whose complete IDL SHA-256 is recorded in its provenance. The wire records are
explicitly synthetic. Real original/adapter.1 binaries reproduced both lost
inherited-name rows and the legacy missing-argument panic. Go regressions and
stdout/PostgreSQL integration now require `div` at offset 0 with count 2 and
`span` at offset 31 with count 1, with exact feature-attempt counts and no tags
invented for missing, undefined, numeric or unrelated calls.

`repair.py` verifies the original Git pin and patch hashes, reads only tracked
files from that commit and writes a new owned output directory. Exact source
anchors generate the six source/schema changes. It emits a generated
`repairs.patch` and before/after SHA-256 manifest for review; no patch is edited
by hand. The original checkout remains unchanged. The repaired SQL schema
includes the missing enum values and standalone `submissions`, `idlapis` and
GraphML tables, rather than hiding these prerequisites in test setup.

The independent `visiblev8-postprocessors.yml` workflow builds both original
and repaired binaries. It records all five failing Go regression cases against
a separate pristine test copy, then requires the repaired Go tests to pass
under the race detector. Real stdout, fixed-rule Rust and PostgreSQL checks
verify rows and values, including complete script blobs, eval parents, XML,
constructors, polymorphic counts and adblock decisions/error propagation.

Original reports and stderr remain intact. A separate classification accepts
only the observed exact original failure signatures as
`expected_upstream_failure`; those results are never called passing. Unknown
original failures and any repaired supported-profile failure fail the gate.

The original profile lacks stdout writers for `poly_features`, `blobs` and
`idlapis`, and a PostgreSQL writer for `callargs`. These combinations remain
explicitly unsupported; no fabricated payload is emitted. Native Android,
browser instrumentation and risk attribution are separate profiles.

```bash
python3 postprocessor-adapter/repair.py --reference original-visiblev8 \
  --output adapter-evidence/repaired-source
```

The source copy retains the upstream BSD-3-Clause license. Go/Rust/PostgreSQL
versions and the complete build commands are fixed in the workflow and the
shared reference lock. PostgreSQL listens only on loopback in its disposable
test runner. No unrestricted process execution or database writes are added
to MCP tools.
