"""Restore primary response bytes when Gecko delivers an alternative cache entry."""

STORAGE = r'''class ResponseStorage {
  constructor(maxTotalSize, maxResponseSize) {
    this._totalSize = 0;
    this._maxResponseSize = maxResponseSize;
    this._maxTotalSize = maxTotalSize;
    this._responses = new Map();
  }

  addResponseBody(request, body) {
    const channel = request.httpChannel;
    // Cached script stencils are not HTTP response bodies, even when their
    // channel retains the original Content-Encoding header.
    const originalCache = !request._fulfilled &&
        channel instanceof Ci.nsICacheInfoChannel && channel.alternativeDataType;
    const response = {body: '', chunks: [], size: 0, encodings: []};
    this._responses.set(request.requestId, response);
    if (channel instanceof Ci.nsIEncodedChannel && channel.contentEncodings &&
        !request._fulfilled && (originalCache ||
        (!channel.applyConversion && !channel.hasContentDecompressed))) {
      response.encodings = channel.getResponseHeader('Content-Encoding')
          .split(/\s*\t*,\s*\t*/).filter(encoding => {
            const normalized = encoding.trim().toLowerCase();
            return normalized && normalized !== 'identity' && normalized !== 'x-identity';
          });
    }
    if (originalCache) {
      // Install the entry before invoking the receiver: the parent channel can
      // call it synchronously. Keep errors for protocol reads, not page loading.
      response.ready = this._readOriginalResponse(channel, response).catch(error => {
        this._clearResponse(response);
        response.error = error;
      });
    } else {
      this._appendResponse(response, body);
      this._finishResponse(response);
    }
  }

  _clearResponse(response) {
    this._totalSize -= response.size;
    response.size = 0;
    response.body = '';
    response.chunks = [];
  }

  _evictResponse(response) {
    this._clearResponse(response);
    response.evicted = true;
    if (response.pump)
      response.pump.cancel(Cr.NS_BINDING_ABORTED);
  }

  _appendResponse(response, data) {
    if (response.evicted)
      return;
    if (response.size + data.length > this._maxResponseSize) {
      this._evictResponse(response);
      return;
    }
    response.chunks.push(data);
    response.size += data.length;
    this._totalSize += data.length;
    if (this._totalSize > this._maxTotalSize) {
      for (const stored of this._responses.values()) {
        this._evictResponse(stored);
        if (this._totalSize < this._maxTotalSize)
          break;
      }
    }
  }

  _finishResponse(response) {
    if (!response.evicted)
      response.body = response.chunks.join('');
    response.chunks = [];
  }

  _readOriginalResponse(channel, response) {
    return new Promise((resolve, reject) => {
      channel.getOriginalInputStream({
        onInputStreamReady: stream => {
          if (!stream) {
            reject(new Error('Original cached response body is unavailable'));
            return;
          }
          if (response.evicted) {
            try {
              stream.close();
              resolve();
            } catch (error) {
              reject(error);
            }
            return;
          }
          try {
            const pump = Cc['@mozilla.org/network/input-stream-pump;1']
                .createInstance(Ci.nsIInputStreamPump);
            response.pump = pump;
            pump.init(stream, 0, 0, true);
            pump.asyncRead({
              onStartRequest() {},
              onDataAvailable: (request, inputStream, offset, count) => {
                const input = new BinaryInputStream(inputStream);
                // Bound each allocation and account pending bytes in the same
                // per-response and total budgets as completed response bodies.
                while (count) {
                  const size = Math.min(count, 64 * 1024);
                  this._appendResponse(response, input.readBytes(size));
                  count -= size;
                  if (response.evicted)
                    throw Components.Exception('Response body was evicted', Cr.NS_BINDING_ABORTED);
                }
              },
              onStopRequest: (request, status) => {
                response.pump = null;
                if (response.evicted) {
                  resolve();
                } else if (!Components.isSuccessCode(status)) {
                  reject(Components.Exception('Original cached response read failed', status));
                } else {
                  this._finishResponse(response);
                  resolve();
                }
              },
            });
          } catch (error) {
            response.pump = null;
            try {
              stream.close();
            } catch (closeError) {
              reject(closeError);
              return;
            }
            reject(error);
          }
        },
      });
    });
  }

  async getBase64EncodedResponse(requestId) {
    const response = this._responses.get(requestId);
    if (!response)
      throw new Error(`Request "${requestId}" is not found`);
    await response.ready;
    if (response.error)
      throw response.error;
    if (response.evicted)
      return {base64body: '', evicted: true};
    let result = response.body;
    // Decode in reverse application order without mutating stored headers.
    for (const encoding of response.encodings.slice().reverse())
      result = convertString(result, encoding, 'uncompressed');
    return {base64body: btoa(result)};
  }
}
'''


def transform(text, replace_once):
    # Assert the original decoding anchor as well as the complete pinned input
    # hash checked by generate_patch.render before replacing the storage class.
    replace_once(text, "      for (const encoding of response.encodings)\n", "")
    start = text.index("class ResponseStorage {\n")
    end = text.index("\nfunction responseHead(", start)
    return replace_once(text, text[start:end], STORAGE)
