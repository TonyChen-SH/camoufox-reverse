"""Run the pinned Juggler response storage against real compressed bytes."""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from generate_patch import transform

PATH = "juggler/NetworkObserver.js"

MODEL = r'''
const assert = require('node:assert/strict');
const zlib = require('node:zlib');
class EncodedChannel {}
class CacheChannel extends EncodedChannel {}
const Ci = {nsIEncodedChannel: EncodedChannel, nsICacheInfoChannel: CacheChannel,
            nsIInputStreamPump: {}};
const Cr = {NS_BINDING_ABORTED: 1, NS_ERROR_FAILURE: 2};
const Components = {isSuccessCode: status => status === 0,
                    Exception: (message, result) => Object.assign(new Error(message), {result})};
function BinaryInputStream(stream) { this.readBytes = count => stream.readBytes(count); }
const Cc = {'@mozilla.org/network/input-stream-pump;1': {createInstance() {
  return {init(stream, size, count, close) {
    assert.equal(close, true); this.stream = stream; this.status = 0;
  }, cancel(status) { this.status = status; }, asyncRead(listener) {
    if (this.stream.initFailure) throw new Error('pump setup failed');
    listener.onStartRequest(this);
    const next = () => {
      const remaining = this.stream.data.length - this.stream.offset;
      if (!remaining || this.status) {
        this.stream.close(); listener.onStopRequest(this, this.status || this.stream.failure || 0); return;
      }
      try { listener.onDataAvailable(this, this.stream, this.stream.offset, Math.min(this.stream.chunkSize || 17, remaining)); }
      catch (error) { this.status = error.result || error || 2; }
      queueMicrotask(next);
    };
    queueMicrotask(next);
  }};
}}};
function originalStream(bytes, options = {}) {
  return {data: bytes.toString('latin1'), offset: 0, closed: false, ...options,
    reads: [], readBytes(count) { this.reads.push(count); const data = this.data.slice(this.offset, this.offset + count); this.offset += data.length; return data; },
    close() { this.closed = true; }};
}
function cacheRequest(id, bytes, encoding, deferred = false, options = {}) {
  const stream = bytes === null ? null : originalStream(bytes, options);
  const channel = new CacheChannel();
  Object.assign(channel, {alternativeDataType: 'javascript-bytecode', contentEncodings: !!encoding,
    applyConversion: true, hasContentDecompressed: true, calls: 0,
    getResponseHeader() { return encoding; },
    getOriginalInputStream(receiver) {
      ++this.calls;
      if (options.openFailure) throw new Error('cache unavailable');
      const deliver = () => receiver.onInputStreamReady(stream);
      if (deferred) queueMicrotask(deliver); else deliver();
    }});
  return {requestId: id, httpChannel: channel, stream};
}
const conversions = [];
function convertString(body, encoding, destination) {
  assert.equal(destination, 'uncompressed');
  conversions.push(encoding);
  const bytes = Buffer.from(body, 'latin1');
  if (encoding === 'gzip') return zlib.gunzipSync(bytes).toString('latin1');
  if (encoding === 'deflate') return zlib.inflateSync(bytes).toString('latin1');
  throw new Error('Unexpected converter: ' + encoding);
}
function btoa(body) { return Buffer.from(body, 'latin1').toString('base64'); }
'''

MAIN = r'''
(async () => {
const source = Buffer.concat([
  Buffer.from('window.responseBody = "complete 🦊";\n', 'utf8'),
  Buffer.from(Array.from({length: 256}, (_, index) => index)),
]);
const cases = [];
for (const encodings of [[], ['gzip'], ['deflate'], ['gzip', 'deflate'], ['deflate', 'gzip']]) {
  let encoded = source;
  for (const encoding of encodings)
    encoded = encoding === 'gzip' ? zlib.gzipSync(encoded) : zlib.deflateSync(encoded);
  for (const alreadyDecoded of ['no', 'applyConversion', 'hasContentDecompressed', 'fulfilled']) {
    const channel = new EncodedChannel();
    Object.assign(channel, {
      contentEncodings: encodings.length > 0,
      applyConversion: alreadyDecoded === 'applyConversion',
      hasContentDecompressed: alreadyDecoded === 'hasContentDecompressed',
      getResponseHeader(name) {
        assert.equal(name, 'Content-Encoding');
        return ['identity', ...encodings, 'x-identity'].join(', ');
      },
    });
    const request = {requestId: 'request', httpChannel: channel,
                     _fulfilled: alreadyDecoded === 'fulfilled'};
    const storage = new ResponseStorage(10000, 10000);
    storage.addResponseBody(request, (alreadyDecoded === 'no' ? encoded : source).toString('latin1'));
    const storedEncodings = [...storage._responses.get('request').encodings];
    for (let read = 0; read < 2; ++read) {
      conversions.length = 0;
      const response = await storage.getBase64EncodedResponse('request');
      assert.deepEqual(Buffer.from(response.base64body, 'base64'), source);
      assert.deepEqual(conversions, alreadyDecoded === 'no' ? [...encodings].reverse() : []);
      assert.deepEqual(storage._responses.get('request').encodings, storedEncodings);
    }
    cases.push({encodings, alreadyDecoded});
  }
}
console.log(JSON.stringify({cases: cases.length, reads: cases.length * 2}));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''

CACHE_MAIN = r'''
(async () => {
  const source = Buffer.from('document.body.textContent = "original 🦊";\n');
  let checked = 0;
  for (const encoding of ['', 'gzip', 'gzip, deflate']) {
    const bytes = encoding === '' ? source : encoding === 'gzip' ? zlib.gzipSync(source) : zlib.deflateSync(zlib.gzipSync(source));
    for (const deferred of [false, true]) {
      const request = cacheRequest('cache', bytes, encoding, deferred);
      const storage = new ResponseStorage(10000, 10000);
      storage.addResponseBody(request, 'NOT SOURCE: serialized stencil');
      const results = await Promise.all([storage.getBase64EncodedResponse('cache'), storage.getBase64EncodedResponse('cache')]);
      for (const result of results) assert.deepEqual(Buffer.from(result.base64body, 'base64'), source);
      assert.equal(request.httpChannel.calls, 1);
      assert.equal(request.stream.closed, true);
      assert.equal(storage._totalSize, bytes.length);
      ++checked;
    }
  }
  console.log(JSON.stringify({cached_cases: checked}));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''

FAILURE_MAIN = r'''
(async () => {
  for (const options of [{nullStream: true}, {openFailure: true}, {initFailure: true}, {failure: 2}]) {
    const storage = new ResponseStorage(100, 100);
    const request = cacheRequest('error', options.nullStream ? null : Buffer.from('primary'), '', true, options);
    storage.addResponseBody(request, 'stencil');
    for (let index = 0; index < 2; ++index)
      await assert.rejects(storage.getBase64EncodedResponse('error'));
    assert.equal(storage._totalSize, 0);
    assert.equal(request.httpChannel.calls, 1);
    if (request.stream && !options.openFailure) assert.equal(request.stream.closed, true);
  }
  for (const limit of [0, 20, 34]) {
    const storage = new ResponseStorage(100, limit);
    const request = cacheRequest('large', Buffer.alloc(80, 97), '', true);
    storage.addResponseBody(request, 'tiny stencil');
    assert.deepEqual(await storage.getBase64EncodedResponse('large'), {base64body: '', evicted: true});
    assert.equal(storage._totalSize, 0);
    assert.equal(request.stream.closed, true);
    assert.ok(request.stream.offset <= limit + 17);
  }
  const storage = new ResponseStorage(30, 100);
  const first = cacheRequest('first', Buffer.alloc(25, 97), '', true);
  const second = cacheRequest('second', Buffer.alloc(25, 98), '', true);
  storage.addResponseBody(first, 'stencil-a');
  storage.addResponseBody(second, 'stencil-b');
  const result = await Promise.all([storage.getBase64EncodedResponse('first'), storage.getBase64EncodedResponse('second')]);
  assert.deepEqual(result[0], {base64body: '', evicted: true});
  assert.deepEqual(Buffer.from(result[1].base64body, 'base64'), Buffer.alloc(25, 98));
  assert.equal(storage._totalSize, 25);
  assert.equal(first.stream.closed, true);
  assert.equal(second.stream.closed, true);
  // Eviction can precede a deferred original-stream callback.
  const pending = new ResponseStorage(10, 100);
  const request = cacheRequest('pending', Buffer.alloc(20), '', true);
  pending.addResponseBody(request, 'stencil');
  pending.addResponseBody({requestId: 'other', httpChannel: new EncodedChannel()}, 'x'.repeat(11));
  assert.deepEqual(await pending.getBase64EncodedResponse('pending'), {base64body: '', evicted: true});
  assert.equal(request.stream.closed, true);
  assert.equal(request.stream.offset, 0);
  // A large pump callback must not produce an unbounded single read allocation.
  const bounded = new ResponseStorage(400000, 400000);
  const large = cacheRequest('bounded', Buffer.alloc(200000, 97), '', false, {chunkSize: 200000});
  bounded.addResponseBody(large, 'stencil');
  assert.equal(Buffer.from((await bounded.getBase64EncodedResponse('bounded')).base64body, 'base64').length, 200000);
  assert.ok(Math.max(...large.stream.reads) <= 65536);
  // Fulfilled responses contain source bytes even if the channel advertises alt-data.
  const fulfilled = cacheRequest('fulfilled', null, 'gzip');
  fulfilled._fulfilled = true;
  bounded.addResponseBody(fulfilled, 'fulfilled source');
  assert.equal(Buffer.from((await bounded.getBase64EncodedResponse('fulfilled')).base64body, 'base64').toString(), 'fulfilled source');
  assert.equal(fulfilled.httpChannel.calls, 0);
  await assert.rejects(bounded.getBase64EncodedResponse('missing'), /not found/);
  console.log(JSON.stringify({failures: 4, per_response_limits: 3, global_limit: true, deferred_eviction: true,
                              bounded_chunks: true, fulfilled: true, missing: true}));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''


class ResponseStorageTests(unittest.TestCase):
    def test_cached_reads_preserve_failures_and_enforce_storage_limits(self):
        source = (Path(os.environ["FIREFOX_SOURCE"]) / PATH).read_bytes()
        text = transform(PATH, source).decode()
        start = text.index("class ResponseStorage {")
        end = text.index("\nfunction responseHead(", start)
        result = subprocess.run([os.environ.get("NODE", "node"), "-e",
                                 MODEL + text[start:end] + FAILURE_MAIN], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"failures": 4, "per_response_limits": 3,
            "global_limit": True, "deferred_eviction": True, "bounded_chunks": True,
            "fulfilled": True, "missing": True})

    def test_cached_stencil_reads_primary_cache_body_once(self):
        source = (Path(os.environ["FIREFOX_SOURCE"]) / PATH).read_bytes()
        text = transform(PATH, source).decode()
        start = text.index("class ResponseStorage {")
        end = text.index("\nfunction responseHead(", start)
        result = subprocess.run([os.environ.get("NODE", "node"), "-e",
                                 MODEL + text[start:end] + CACHE_MAIN], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"cached_cases": 6})

    def test_real_storage_decodes_in_reverse_order_and_preserves_repeated_reads(self):
        source = (Path(os.environ["FIREFOX_SOURCE"]) / PATH).read_bytes()
        text = transform(PATH, source).decode()
        start = text.index("class ResponseStorage {")
        end = text.index("\nfunction responseHead(", start)
        result = subprocess.run([os.environ.get("NODE", "node"), "-e",
                                 MODEL + text[start:end] + MAIN],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"cases": 20, "reads": 40})

    def test_changed_response_decoder_anchor_fails_loudly(self):
        source = (Path(os.environ["FIREFOX_SOURCE"]) / PATH).read_bytes()
        changed = source.replace(b"for (const encoding of response.encodings)",
                                 b"for (const encoding of otherEncodings)")
        with self.assertRaisesRegex(ValueError, "anchor"):
            transform(PATH, changed)


if __name__ == "__main__":
    unittest.main()
