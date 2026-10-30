# Firefox IDL metadata

`idl_inventory.py` produces the original VisibleV8 Go processor's `idldata.json`
interface map for the configured Firefox build. It uses Firefox's own
`mozwebidlcodegen.create_build_system_manager(...).config`, WebIDL parser,
`Configuration`, and `Codegen.PropertyArrays`. It does not parse IDL independently
or substitute Chromium metadata.

The build's `dom/bindings/file-lists.json` selects the inputs, including
preprocessed and generated IDL. The original parser resolves inheritance,
partials, mixins, iterable interfaces and aliases. Property arrays provide actual
generated names, including stringifiers and synthesized methods. Native class
names come from the provider's class-name methods and emitted prototype/namespace
JSClass definitions. Legacy constructor names and native class names receive
`aliasFor` entries. Each alias also retains its canonical member lists and parent
because some upstream Go feature modes do not follow `aliasFor`.

The output keeps the Go schema: each top-level name maps to `parent`, `aliasFor`,
`aliases`, `members`, `methods`, and `properties`. Provenance belongs in the
separate `idldata-manifest.json`, including constructor declarations, hashes of
every actual IDL input, provider blobs, the supplied mozconfig, `config.status`,
`Bindings.conf`, and the generated binding inventory. Unknown provider versions,
unresolved parents, conflicting aliases, missing inputs and unmatched generated
hooks fail before replacing an existing IDL delivery.

## Export from a prepared build

Use the source, object directory and mozconfig from the same pinned Camoufox
Linux build. Keep the mozconfig unchanged while exporting. The feature's
`tools/apply.py` must already have been applied to that source tree so the
generated bindings contain the trace hooks to cross-check.

Set these paths to that existing build and the metadata destination. The
mozconfig must configure the indicated object directory:

```bash
REVERSE_ROOT=/path/to/camoufox-reverse
FIREFOX_SOURCE=/path/to/prepared/firefox
FIREFOX_OBJDIR=/path/to/that/build/objdir
MOZCONFIG=/path/to/that/build/mozconfig
METADATA_OUTPUT=/path/to/metadata-output
export MOZCONFIG
cd "$FIREFOX_SOURCE"
./mach configure
./mach build export
```

At the pinned Firefox revision, `dom/bindings/Makefile.in` declares
`export:: webidl.stub`. The export tier generates IDL and binding C++ without
building the full browser. It still needs the prepared source tree, configured
toolchain and normal Firefox build Python environment. A packaged `dist/bin`
archive and a binding inventory alone do not retain enough information to
recover interface parents.

The fixed provider contracts are
[the build manager](https://github.com/mozilla-firefox/firefox/blob/9378fbc58660847fbc02c3486a0ecd6049978e25/dom/bindings/mozwebidlcodegen/__init__.py),
[the binding export target](https://github.com/mozilla-firefox/firefox/blob/9378fbc58660847fbc02c3486a0ecd6049978e25/dom/bindings/Makefile.in),
and [the selected-input producer](https://github.com/mozilla-firefox/firefox/blob/9378fbc58660847fbc02c3486a0ecd6049978e25/python/mozbuild/mozbuild/backend/common.py).

Generate and cross-check metadata from the same export. Run `mach python` from
the Firefox source directory:

```bash
python3 "$REVERSE_ROOT/tools/binding_inventory.py" \
  --root "$FIREFOX_OBJDIR/dom/bindings" \
  --output "$METADATA_OUTPUT/binding-inventory.json"
./mach python --virtualenv build "$REVERSE_ROOT/metadata/idl_inventory.py" \
  --source "$FIREFOX_SOURCE" \
  --objdir "$FIREFOX_OBJDIR" \
  --mozconfig "$MOZCONFIG" \
  --binding-inventory "$METADATA_OUTPUT/binding-inventory.json" \
  --output "$METADATA_OUTPUT/idldata.json"
```

Archive both JSON outputs, `binding-inventory.json`, and a copy of the actual
mozconfig with the browser build identity. Do not regenerate the metadata with a
different set of build flags or a source directory glob. The tool only parses
the selected inputs; it does not call the provider's browser build or binding
generation routines.

## Independent GitHub export

[Firefox IDL inventory export](../.github/workflows/firefox-idl-inventory.yml)
runs for metadata pull requests and manual dispatches. It uses the native
browser job's pinned Camoufox checkout, `BUILD_TARGET=linux,x86_64`, preparation
runner and generated mozconfig. It applies the existing trace patch, configures
the complete source and builds only the export tier. The job never restores a
packaged browser or invokes the full browser build.

The job verifies the saved `.mozconfig.json` against the actual source, object
directory, mozconfig path and Linux target. It runs the provider tests, exports
metadata, and then scans all generated C++ again to require an identical binding
inventory. Input hashes must still match the export manifest when packaging.
Any mismatch or missing prerequisite fails the job.

The `firefox-idldata-linux-x86_64` artifact contains:

| File | Contents |
| --- | --- |
| `idldata.json` | Original Go interface map |
| `idldata-manifest.json` | Selected input hashes, aliases and constructor metadata, cross-check counts |
| `binding-inventory.json` | Original generated-C++ trace inventory |
| `metadata-inputs.tar.gz` | Every selected IDL, the actual providers and vendored dependencies, mozconfig, saved expanded configure state, build file list and export tooling |
| `generated-bindings.tar.gz` | Every C++ file scanned by the inventory |
| `export-provenance.json` | Checkout identities, Firefox archive hash, exact commands, Python/runner identity and hashes of all archived files |
| `baseline.json`, `LICENSE`, `SHA256SUMS` | Browser baseline identity, license and delivery checksums |

`firefox-idldata-export-evidence` retains preparation, configure, export,
extraction, packaging and test logs even when a step fails. Match metadata to a
browser using the upstream pins, actual mozconfig, selected IDL/provider hashes
and generated binding inventory. The artifact's baseline identifies the metadata
job's source. Its provenance describes the configure/export result; browser
binary identity comes from the browser's actual build artifacts.

After a local export using the commands above, the same packaging validation is
available with the original pinned Camoufox checkout:

```bash
python3 "$REVERSE_ROOT/metadata/package_inventory.py" \
  --source "$FIREFOX_SOURCE" \
  --objdir "$FIREFOX_OBJDIR" \
  --mozconfig "$MOZCONFIG" \
  --upstream /path/to/the/pinned/camoufox \
  --output "$METADATA_OUTPUT"
cd "$METADATA_OUTPUT"
sha256sum --check SHA256SUMS
```

## Validation and limits

From the camoufox-reverse repository:

```bash
FIREFOX_SOURCE=/path/to/the/pinned/firefox \
  python3 -m unittest discover -s tests_metadata -v
```

Tests require the real pinned parser, configuration and generator, plus their
existing vendored PLY and perfecthash modules. They also apply this repository's
actual trace transform when the provider is pristine, accept the verified
already-patched provider, generate real binding C++, scan it with
`binding_inventory.py`, and verify the resulting operations against the
metadata. Packaging tests check saved configuration identity, archived bytes,
determinism and rejection of changed inputs. Missing prerequisites fail. These
tests validate extraction and code-generation integration; they do not compile
or run a browser.

The inventory describes configured declarations, including ChromeOnly and
conditional members. Preferences, principals and realms govern actual runtime
availability. The original Go schema combines static and instance members and
cannot represent those conditions. The cross-check verifies each generated hook
against an actual descriptor's kind, name and scope; it does not claim that every
declared member is hooked or runtime reachable. Binding-only internal operation
names remain explicit in the manifest rather than being invented as exposed
members. Non-WebIDL engine builtins, custom native classes and application
properties are outside this metadata. Generic `Proxy` is not a `Window` alias;
the event producer must identify an actual WindowProxy using native APIs.
