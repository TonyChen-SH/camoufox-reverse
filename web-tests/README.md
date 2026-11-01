# Native reCAPTCHA integration checks

This suite runs the already compiled Camoufox native tracer against Google's
official reCAPTCHA demo and documented v2 test-key integration. Chromium results
do not satisfy its checks. It measures browser compatibility and monitoring
evidence, not production challenge completion or hidden server risk rules.

The [workflow](../.github/workflows/recaptcha-native.yml) reuses the accepted Linux
artifact without rebuilding the browser:

| Identity | Value |
| --- | --- |
| Build run | `41064733272` |
| Artifact | `12984328007`, `camoufox-engine-trace-linux-x86_64` |
| Browser source | `dee8bb66c386e76cfe771c60e80c407d4827ed62` |
| Artifact ZIP digest | `beeeaccb365a74a0c06e59dfc9c8e6e683047e6425bba99937a816f550b8d52b` |
| Browser tar.zst SHA-256 | `ec424c65a9d0ecec0343c9df857ed36d97b4ae81a1bce70bc88ea2e87d3746c1` |
| Analysis source | `fabddde3cab06580f308ab9d4ba80a5b47a2895c` |
| Playwright | `1.62.0` |

Before launch, CI checks GitHub's artifact ID, name, digest, expiration and build
commit, verifies the extracted package's `SHA256SUMS` and the independently pinned
browser archive hash, and compares its baseline with the exact browser source
checkout. The runner records the actual executable and `libxul.so` hashes. A
missing or expired artifact fails; another browser is never substituted.

## Scenarios

Each scenario runs with tracing OFF, WebIDL binding tracing, and engine tracing:

1. Load the [official demo](https://www.google.com/recaptcha/api2/demo) and observe
   widget readiness, frame/worker lifecycle, script hashes and network outcomes.
2. Use Google's public v2 test key in a local integration page to check the
   callback, response presence and reset behavior.
3. Interrupt `api.js` on the local integration page to verify that a network
   failure remains visible and does not report the widget as ready.

The default local matrix runs these nine cases. The additional
`--scenarios demo-activation` scenario clicks the official
demo checkbox once, then observes the checkbox and visible challenge-frame state
for up to 20 seconds. It does not select challenge images, switch or solve audio,
or submit the form. `checkbox_checked` describes client UI only;
`bframe_visible_unclassified` does not assert that a challenge was solved or
that a visible frame contains a specific challenge type. A completed observation
is separate from the UI outcome and from server-side acceptance.

The original nine-case evidence is retained in
[run 41279483288](https://github.com/TonyChen-SH/camoufox-reverse/actions/runs/41279483288).
All nine functional page checks passed; the binding test-key capture and engine
script-failure capture reported incomplete evidence, so that run failed its
monitoring gate. The activation follow-up does not replace or relabel that run.

The same compiled browser and launch configuration are used across modes. CI
uses Xvfb on Linux and keeps the browser content sandbox enabled. The suite does
not solve production image/audio challenges. It does not rotate identities,
adjust risk signals, or tune responses to obtain a production pass.

The producer, collector and reader limits remain explicit in results. Hitting a
limit is incomplete evidence, not an empty successful capture. Failed checks and
missing prerequisites fail; the suite does not retry, skip a prerequisite or
weaken a threshold to turn a run green. Network-dependent differences and live
script updates must be assessed from the retained evidence. Single observations
do not establish timing overhead or server-side causality.

## Local execution

Install this repository's pinned analysis checkout and Playwright in a Python
3.12 environment. On Linux, install the runtime packages listed in the workflow.
Verify and unpack the artifact before running; `.browser/bin/camoufox-bin` must be
the accepted native tracer, and `dist/baseline.json` must match its source.

```bash
python -m pip install --no-deps -e /path/to/camoufox-reverse-mcp
python -m pip install 'playwright==1.62.0'
python -m unittest discover -s web-tests -p 'test_*.py' -v
xvfb-run -a python web-tests/run.py \
  --binary .browser/bin/camoufox-bin \
  --output evidence \
  --private-output /tmp/recaptcha-private \
  --recipient web-tests/evidence-recipient.pem \
  --source-baseline dist/baseline.json
```

`--private-output` is a private capture destination outside `--output`. Native
parameters, full source text, URL query values and session material must not be
published in plaintext. `--recipient` supplies a public X.509 encryption
certificate. The runner uses OpenSSL CMS to encrypt the private captures into
`evidence/private-evidence.cms`; its matching private key is held outside this
repository and CI. Local runs require a recipient certificate, and inspecting
the encrypted capture requires its matching private key. Neither the private
key nor plaintext captures belong in the uploaded directory.

The runner also produces a separate public report containing bounded counts,
statuses, URL paths, source hashes, frame/request relationships and screenshots
that exclude response values. CI uploads only `evidence/` for seven days and
removes its private plaintext session captures afterward. The public report is
a derived summary; it is not represented as a lossless raw trace export. Only
the encrypted archive retains the private raw evidence for the recipient.
CI keeps artifact provenance in the top-level `evidence/` directory and passes
`--output evidence/run` to the runner, so its scenario reports, screenshots and
`private-evidence.cms` appear under `evidence/run/` in the uploaded artifact.

## Interpretation

Google's [FAQ](https://developers.google.com/recaptcha/docs/faq) documents that
the v2 test key always passes. This makes it useful for integration checks, but
its success is not production anti-bot performance evidence. See the official
[display API](https://developers.google.com/recaptcha/docs/display) for callback
and reset behavior, and the [verification API](https://developers.google.com/recaptcha/docs/verify)
for server-side verification requirements.

API activity, fetched script bytes and observed registered source are separate
measurements. A network error is not automatically a risk rejection, and
`no_known_loss` is not proof of a complete producer stream tail. This suite does
not supply production `siteverify` responses, Enterprise assessments or v3 scores.

## Transport diagnostics and MCP observations

The repair follow-up retains the original failed runs and uses the same verified
browser. The current workflow validates the session capacity profile using
`test-key` and `demo-activation` in binding mode. Each
case saves two bounded private byte streams: native stderr before the driver and
forwarded stderr before collector decoding. Each has a 64 MiB limit, digest,
byte counts and an explicit completion receipt. A missing, truncated or failed
receipt fails the case. The native launcher preserves the browser PID, arguments
and inherited Juggler descriptors; it does not replace the browser binary.

Every case also starts the pinned `camoufox-reverse-mcp` server over MCP stdio,
requests its summary, and exhausts event and script pagination. Private tool
receipts preserve full results. `mcp-observations.json` publishes bounded groups
with counts, script hashes, source locations and observed execution context;
unknown or dynamic member names are hashed in public output. API categories
suggest possible signal families, not confirmed server risk rules. There is no
server-side score or causal attribution. Page and native clocks are not assumed
to be synchronized. Trace loss remains a failed monitoring gate even when MCP
pagination succeeds. Script-body read exceptions are retained privately, with
only their hashes in public evidence.

## Bounded session profile

`--capture-profile baseline` retains the original per-process producer defaults
(10,000 binding events, no engine record cap) for reproducing the failed capture.
`--capture-profile session` uses the declared reader event budget (100,000) as
both the per-process binding event cap and engine logical-record cap. Both
profiles retain the 150-second collection deadline, 64 MiB input/raw limits,
collector space reserved for its final outcome, and 200,000 reader record cap.
The profile and all effective limits are included in each result.

Producer limits apply per process, while consumer limits apply to the complete
capture. Concurrent processes or large sources can exhaust the consumer first;
that remains an explicit failure. The session profile provides a bounded budget
for longer interactions; it does not repair or relabel previously capped traces.

After the matrix finishes, `report.html` provides a searchable Chinese evidence
table and `observations.csv` provides a spreadsheet export. They distinguish
page checks, capture integrity and MCP transport success. Comparisons inspect
observed script URL/hash sets across modes and flag live-script changes or
missing body hashes, rather than attributing different counts to tracing alone.
