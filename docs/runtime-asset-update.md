# Compose a generated runtime asset with a compiled browser

`tools/update_runtime_asset.py` can update only the loose file
`bin/modules/juggler/NetworkObserver.js`, generated from the pinned
`juggler/NetworkObserver.js` source. It does not build a browser, unpack or repack
an archive, publish an artifact, or run acceptance tests.

Use this only after authenticating the original artifact and extracting its
verified package. The tool accepts that authentication result from its caller;
it does not independently download or authenticate the artifact ZIP. Run it
from the target source checkout and supply that checkout's exact source commit.
The caller is responsible for verifying the commit-to-checkout association.

The package directory must contain the original `baseline.json` beside `bin/`.
All upstream pins, non-allowlisted source hashes, native overlay hashes and other
baseline fields must match the target checkout. Only the allowlisted source
entry and generated patch hash may differ. The original baseline stays intact;
the result must not be described as a browser compiled entirely from the target
commit.

Create an identity JSON document from the authenticated artifact evidence:

```json
{
  "artifact": {
    "id": 123,
    "digest": "sha256:<64 lowercase hexadecimal characters>",
    "workflow_run": {
      "id": 456,
      "head_sha": "<40 lowercase hexadecimal characters>"
    }
  },
  "baseline_sha256": "<original baseline.json SHA-256>",
  "core_sha256": {
    "bin/camoufox-bin": "<authenticated executable SHA-256>",
    "bin/libxul.so": "<authenticated library SHA-256>"
  }
}
```

The example placeholders must be replaced with actual evidence. Do not derive
the expected hashes from an unverified or already modified extraction.

```bash
python3 tools/update_runtime_asset.py \
  --package /path/to/extracted-package \
  --source /path/to/pristine-pinned-inputs \
  --base-identity /path/to/authenticated-identity.json \
  --target-source-commit "$target_source_commit" \
  --receipt /path/to/new-composition.json
```

The receipt must be new and outside the original package. The tool rejects
missing assets, package symlinks and special files, unknown current asset bytes,
and an additional packed copy in an `.ja` archive. It validates the regenerated
patch, target asset hash and native overlays before replacing the asset
atomically. It inventories every package file and directory before and after
the update, preserving all other bytes and modes, including both compiled core
files and the original baseline. A failed post-update check or receipt write
restores the original asset. Repeated application is rejected without changing
the package or existing receipt.

The separate composition receipt records the original artifact/build identity,
target source commit and baseline hash, old/new asset hashes, unchanged core
hashes and complete package inventory digests. Its acceptance state is always
`not_run`. Preserve it alongside the original compiled baseline and later
runtime evidence. Existing archive checksums continue to describe the original
archive; create new archive checksums when a separate packaging step packages
the composed browser.

Before distributing a composed browser, run all native, binding, engine,
upstream, response-body and web/MCP acceptance gates on that exact composed
package with fresh profiles. A successful updater invocation is not browser
acceptance.

## Explicit CI composition mode

When `runtime-base.json` is present, the existing native browser workflow uses
its exact repository, run ID, source commit and artifact name. It requires one
unexpired matching artifact and records its GitHub artifact ID and SHA-256
digest. Missing, ambiguous or incompatible inputs fail the job; the workflow
does not fall back to a different browser or to compilation. Removing the file
explicitly selects the existing compile/cache path.

The official download action extracts the selected artifact. Because that
action reports a digest mismatch as a warning, a separate check streams the
original ZIP, requires its exact API-reported digest, and compares every ZIP
file with the official extraction. Only the initial fixed GitHub API request
contains the bearer token: redirects are disabled there, and the signed HTTPS
storage URL is fetched with a new unauthenticated request. The package's
`SHA256SUMS` and its compiled baseline are then checked against the exact base
source checkout.

The workflow preserves `baseline.json` from that compiled artifact and writes
the target source baseline as `runtime-baseline.json`. It includes
`runtime-composition.json`, `runtime-base.json`, `base-artifact-identity.json`
and `base-artifact-authentication.json` in the delivery checksums. Before any
runtime tests, it unpacks the new archive and rechecks the full inventory,
compiled core hashes and generated asset against the receipt and target
baseline. All existing binding, engine, upstream guard and build-tester gates
remain required for the final delivery.

The small `runtime-composition-proof` artifact contains these metadata files
and `SHA256SUMS`, without the browser archive. It lets reviewers identify the
new archive and unchanged native core hashes without downloading the browser.
Its checksums describe the complete candidate package, so files named in that
manifest are intentionally absent from the proof-only download. The runtime
evidence artifact also retains the original compiled baseline, target baseline
and composition metadata. Runtime reports remain separate from the immutable
composition receipt's `not_run` state.
